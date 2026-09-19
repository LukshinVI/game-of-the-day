"""Regression gate for the SQLite feedback contour.

Covers src/feedback/feedback_api.py (storage + HTTP) and the persistence
boundary of src/feedback/queue_consumer.py. Stdlib only.

Safety: every test runs against its own temporary SQLite file. The DB path is
redirected before import and re-checked by a runtime guard (not `assert`, which
`python -O` strips) that refuses anything outside the system temp directory.
Not safe for parallel execution: tests patch the module-level DB_PATH.
"""

import ast
import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = PROJECT_ROOT / "src" / "db" / "schema.sql"
API_SOURCE = PROJECT_ROOT / "src" / "feedback" / "feedback_api.py"
SECRET = "test-secret"
TEMP_ROOT = os.path.realpath(tempfile.gettempdir())


def require_temp_db(path):
    resolved = os.path.realpath(str(path))
    if os.path.commonpath([resolved, TEMP_ROOT]) != TEMP_ROOT:
        raise RuntimeError(f"refusing to run tests against non-temporary DB: {resolved}")


_import_dir = tempfile.mkdtemp(prefix="feedback-import-")
os.environ["FEEDBACK_DB_PATH"] = str(Path(_import_dir) / "import-guard.db")
os.environ["FEEDBACK_API_SECRET"] = SECRET
for _name in ("CF_ACCOUNT_ID", "CF_STORAGE_QUEUE_ID", "CF_ENRICHED_QUEUE_ID", "CF_QUEUE_API_TOKEN"):
    os.environ.setdefault(_name, "dummy")  # queue_consumer reads these at import time
sys.path.insert(0, str(PROJECT_ROOT))

from src.feedback import feedback_api as api  # noqa: E402
from src.feedback import queue_consumer as consumer  # noqa: E402

require_temp_db(api.DB_PATH)


class FeedbackTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "test.db"
        require_temp_db(self.db_path)
        self._orig_db_path = api.DB_PATH
        api.DB_PATH = self.db_path
        self.addCleanup(setattr, api, "DB_PATH", self._orig_db_path)
        require_temp_db(api.DB_PATH)
        con = sqlite3.connect(self.db_path)
        con.executescript(SCHEMA.read_text())
        con.close()

    # -- helpers -----------------------------------------------------------
    def db(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        self.addCleanup(con.close)
        return con

    def add_game(self, game_id, legacy_id, date, sent=True, title=None):
        con = self.db()
        con.execute(
            "INSERT INTO games(id, legacy_id, game_date, title, skill, mechanics) VALUES (?,?,?,?,?,?)",
            (game_id, legacy_id, date, title or f"Game {legacy_id}", "speech", "point and name"),
        )
        con.execute("INSERT INTO deliveries(game_id, status) VALUES (?, ?)", (game_id, "sent" if sent else "pending"))
        con.commit()

    def feedback_rows(self):
        return self.db().execute("SELECT * FROM feedback ORDER BY id").fetchall()

    def feedback_row(self, event_id="e1", channel="vk"):
        return self.db().execute(
            "SELECT * FROM feedback WHERE channel = ? AND external_event_id = ?", (channel, event_id)
        ).fetchone()

    @staticmethod
    def raw(event_id="e1", text="liked it", **extra):
        return {"event_id": event_id, "channel": "vk", "raw_text": text, **extra}

    @staticmethod
    def analysis_payload(analysis, event_id="e1"):
        return {"event_id": event_id, "channel": "vk", "analysis": analysis}

    def assertApiError(self, status, code, fn, *args):
        with self.assertRaises(api.ApiError) as ctx:
            fn(*args)
        err = ctx.exception
        self.assertEqual((err.status, err.code), (status, code))
        self.assertIsInstance(err.message, str)
        self.assertTrue(err.message.strip(), "ApiError.message must be a non-empty string")
        self.assertEqual(str(err), err.message, "log line must carry the reason, not a status code")
        return err

    @staticmethod
    def race(callables):
        """Run callables simultaneously behind a barrier; return [(ok, value_or_exc)]."""
        barrier = threading.Barrier(len(callables))
        out = [None] * len(callables)

        def run(i, fn):
            barrier.wait()
            try:
                out[i] = (True, fn())
            except Exception as exc:  # noqa: BLE001 - outcome is asserted by callers
                out[i] = (False, exc)

        threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(callables)]
        [t.start() for t in threads]
        [t.join(timeout=30) for t in threads]
        if any(t.is_alive() for t in threads):
            raise AssertionError("worker thread did not finish within 30s")
        return out


class SaveRawContract(FeedbackTestCase):
    def test_creates_raw_and_returns_game_context(self):
        self.add_game(1, "001", "2026-09-01")
        result = api.save_raw(self.raw())
        self.assertEqual(result["status"], "created")
        self.assertFalse(result["processed"])
        self.assertEqual(result["game"], {"id": "001", "title": "Game 001", "skill": "speech", "mechanic": "point and name"})
        row = self.feedback_row()
        self.assertEqual((row["game_id"], row["processed"], row["raw_text"]), (1, 0, "liked it"))

    def test_duplicate_raw_is_idempotent(self):
        self.add_game(1, "001", "2026-09-01")
        first = api.save_raw(self.raw())
        second = api.save_raw(self.raw())
        self.assertEqual(second["status"], "existing")
        self.assertEqual(second["feedback_id"], first["feedback_id"])
        self.assertEqual(len(self.feedback_rows()), 1)

    def test_retry_keeps_original_game_after_newer_game_is_sent(self):
        self.add_game(1, "001", "2026-09-01")
        api.save_raw(self.raw())
        self.add_game(2, "002", "2026-09-02")
        retry = api.save_raw(self.raw())
        self.assertEqual((retry["status"], retry["game"]["id"]), ("existing", "001"))
        self.assertEqual(self.feedback_row()["game_id"], 1)

    def test_same_event_id_with_different_text_is_a_conflict_and_does_not_overwrite(self):
        self.add_game(1, "001", "2026-09-01")
        api.save_raw(self.raw(text="original"))
        self.assertApiError(409, "idempotency_conflict", api.save_raw, self.raw(text="changed"))
        self.assertEqual(self.feedback_row()["raw_text"], "original")

    def test_same_event_id_on_another_channel_is_a_different_event(self):
        self.add_game(1, "001", "2026-09-01")
        api.save_raw(self.raw())
        other = api.save_raw({**self.raw(), "channel": "tg", "game_id": 1})
        self.assertEqual(other["status"], "created")
        self.assertEqual(len(self.feedback_rows()), 2)

    def test_text_is_stripped_and_unicode_roundtrips(self):
        self.add_game(1, "001", "2026-09-01")
        api.save_raw(self.raw(text="  Понравилось, ещё раз! 🎈  "))
        self.assertEqual(self.feedback_row()["raw_text"], "Понравилось, ещё раз! 🎈")


class Concurrency(FeedbackTestCase):
    ROUNDS = 10

    def test_identical_events_produce_exactly_one_row_per_event(self):
        self.add_game(1, "001", "2026-09-01")
        for n in range(self.ROUNDS):
            outcomes = self.race([lambda n=n: api.save_raw(self.raw(f"r{n}", game_id=1)) for _ in range(8)])
            self.assertTrue(all(ok for ok, _ in outcomes), [v for ok, v in outcomes if not ok])
            self.assertEqual(sorted(v["status"] for _, v in outcomes), ["created"] + ["existing"] * 7)
        self.assertEqual(len(self.feedback_rows()), self.ROUNDS)

    def test_same_event_id_with_different_texts_accepts_exactly_one_payload(self):
        self.add_game(1, "001", "2026-09-01")
        for n in range(self.ROUNDS):
            event = f"c{n}"
            texts = ["A", "B"] * 4
            outcomes = self.race([lambda t=t: api.save_raw(self.raw(event, t, game_id=1)) for t in texts])
            created = [t for t, (ok, v) in zip(texts, outcomes) if ok and v["status"] == "created"]
            self.assertEqual(len(created), 1, outcomes)
            winner = created[0]
            self.assertEqual(self.feedback_row(event)["raw_text"], winner)
            for text, (ok, value) in zip(texts, outcomes):
                if text == winner:
                    self.assertTrue(ok and value["status"] in ("created", "existing"))
                else:
                    self.assertFalse(ok)
                    self.assertEqual((value.status, value.code), (409, "idempotency_conflict"))

    def test_analysis_racing_raw_yields_only_defined_outcomes(self):
        self.add_game(1, "001", "2026-09-01")
        analysis = {"decision": "NO_PREFERENCE"}
        for n in range(self.ROUNDS):
            event = f"a{n}"
            outcomes = self.race([
                lambda: api.save_raw(self.raw(event, game_id=1)),
                lambda: api.save_analysis(self.analysis_payload(analysis, event)),
            ])
            (raw_ok, raw_val), (an_ok, an_val) = outcomes
            self.assertTrue(raw_ok, raw_val)
            if not an_ok:  # analysis lost the race: must be the defined, retriable 404
                self.assertEqual((an_val.status, an_val.code), (404, "feedback_not_found"))
            row = self.feedback_row(event)
            self.assertEqual(row["processed"], 1 if an_ok else 0)
            self.assertEqual(row["analysis_json"] is not None, an_ok)


class TargetResolution(FeedbackTestCase):
    def test_explicit_game_id_wins_over_heuristic(self):
        self.add_game(1, "001", "2026-09-01")
        self.add_game(2, "002", "2026-09-02")
        self.assertEqual(api.save_raw(self.raw(game_id=1))["game"]["id"], "001")

    def test_explicit_legacy_id_wins_over_heuristic(self):
        self.add_game(1, "001", "2026-09-01")
        self.add_game(2, "002", "2026-09-02")
        self.assertEqual(api.save_raw(self.raw(game_legacy_id="001"))["game"]["id"], "001")

    def test_unknown_explicit_game_is_404_without_side_effects(self):
        self.assertApiError(404, "game_not_found", api.save_raw, self.raw(game_id=999))
        self.assertApiError(404, "game_not_found", api.save_raw, self.raw(event_id="e2", game_legacy_id="nope"))
        self.assertEqual(self.feedback_rows(), [])

    def test_heuristic_picks_newest_sent_game_without_feedback_and_ignores_unsent(self):
        self.add_game(1, "001", "2026-09-01")
        self.add_game(2, "002", "2026-09-02")
        self.add_game(3, "003", "2026-09-03", sent=False)
        self.assertEqual(api.save_raw(self.raw())["game"]["id"], "002")

    def test_KNOWN_LIMITATION_second_message_about_same_game_is_attributed_to_an_older_game(self):
        # Characterisation of the inherited Google-Sheets rule ("newest sent game without
        # feedback"). Pinned so that changing it is a conscious product decision.
        self.add_game(1, "001", "2026-09-01")
        self.add_game(2, "002", "2026-09-02")
        self.assertEqual(api.save_raw(self.raw("m1"))["game"]["id"], "002")
        self.assertEqual(api.save_raw(self.raw("m2"))["game"]["id"], "001")

    def test_no_eligible_game_is_a_clean_409_without_side_effects(self):
        self.assertApiError(409, "feedback_target_not_found", api.save_raw, self.raw())
        self.add_game(1, "001", "2026-09-01")
        api.save_raw(self.raw())
        self.assertApiError(409, "feedback_target_not_found", api.save_raw, self.raw(event_id="e2"))
        self.assertEqual(len(self.feedback_rows()), 1)

    def test_resolve_feedback_target_helper_errors_are_well_formed(self):
        con = api.connect_db()
        self.addCleanup(con.close)
        self.assertApiError(404, "game_not_found", api.resolve_feedback_target, con, 999)
        self.assertApiError(404, "game_not_found", api.resolve_feedback_target, con, None, "nope")


class SaveAnalysisContract(FeedbackTestCase):
    ANALYSIS = {"observations": [], "decision": "NO_PREFERENCE"}

    def setUp(self):
        super().setUp()
        self.add_game(1, "001", "2026-09-01")

    def test_analysis_before_raw_is_rejected(self):
        self.assertApiError(404, "feedback_not_found", api.save_analysis, self.analysis_payload(self.ANALYSIS))

    def test_saves_analysis_and_marks_processed(self):
        api.save_raw(self.raw())
        result = api.save_analysis(self.analysis_payload(self.ANALYSIS))
        self.assertEqual(result["status"], "updated")
        row = self.feedback_row()
        self.assertEqual(row["processed"], 1)
        self.assertEqual(json.loads(row["analysis_json"]), self.ANALYSIS)
        self.assertTrue(row["analyzed_at"])

    def test_identical_analysis_is_idempotent(self):
        api.save_raw(self.raw())
        api.save_analysis(self.analysis_payload(self.ANALYSIS))
        self.assertEqual(api.save_analysis(self.analysis_payload(self.ANALYSIS))["status"], "existing")

    def test_same_analysis_with_different_key_order_is_idempotent(self):
        api.save_raw(self.raw())
        api.save_analysis(self.analysis_payload({"a": 1, "b": [1, 2]}))
        again = api.save_analysis(self.analysis_payload({"b": [1, 2], "a": 1}))
        self.assertEqual(again["status"], "existing")

    def test_previously_stored_non_canonical_json_still_matches_its_retry(self):
        # Rows written before any canonicalisation fix keep their original key order.
        api.save_raw(self.raw())
        con = self.db()
        con.execute(
            "UPDATE feedback SET analysis_json = ?, processed = 1 WHERE external_event_id = 'e1'",
            ('{"b":2,"a":1}',),
        )
        con.commit()
        self.assertEqual(api.save_analysis(self.analysis_payload({"a": 1, "b": 2}))["status"], "existing")

    def test_different_analysis_is_a_conflict_and_keeps_the_first(self):
        api.save_raw(self.raw())
        api.save_analysis(self.analysis_payload(self.ANALYSIS))
        self.assertApiError(
            409, "analysis_conflict", api.save_analysis, self.analysis_payload({"decision": "PREFERENCE_DETECTED"})
        )
        self.assertEqual(json.loads(self.feedback_row()["analysis_json"]), self.ANALYSIS)

    def test_analysis_must_be_an_object(self):
        api.save_raw(self.raw())
        self.assertApiError(400, "invalid_request", api.save_analysis, self.analysis_payload([]))

    def test_duplicate_raw_after_processing_reports_processed(self):
        api.save_raw(self.raw())
        api.save_analysis(self.analysis_payload(self.ANALYSIS))
        again = api.save_raw(self.raw())
        self.assertEqual(again["status"], "existing")
        self.assertTrue(again["processed"])


class InputValidation(FeedbackTestCase):
    def setUp(self):
        super().setUp()
        self.add_game(1, "001", "2026-09-01")

    def test_rejects_invalid_fields_without_side_effects(self):
        for name, bad in (
            ("missing event_id", {"channel": "vk", "raw_text": "x"}),
            ("blank event_id", {"event_id": "  ", "channel": "vk", "raw_text": "x"}),
            ("event_id 257", {"event_id": "e" * 257, "channel": "vk", "raw_text": "x"}),
            ("missing channel", {"event_id": "e", "raw_text": "x"}),
            ("channel non-string", {"event_id": "e", "channel": 5, "raw_text": "x"}),
            ("channel 33", {"event_id": "e", "channel": "c" * 33, "raw_text": "x"}),
            ("empty raw_text", {"event_id": "e", "channel": "vk", "raw_text": ""}),
            ("whitespace raw_text", {"event_id": "e", "channel": "vk", "raw_text": " \n\t "}),
            ("raw_text 10001", {"event_id": "e", "channel": "vk", "raw_text": "x" * 10001}),
            ("raw_text non-string", {"event_id": "e", "channel": "vk", "raw_text": 5}),
            ("bool game_id", {"event_id": "e", "channel": "vk", "raw_text": "x", "game_id": True}),
        ):
            with self.subTest(name):
                self.assertApiError(400, "invalid_request", api.save_raw, bad)
        self.assertEqual(self.feedback_rows(), [])

    def test_accepts_exact_boundaries(self):
        api.save_raw({"event_id": "e" * 256, "channel": "c" * 32, "raw_text": "x" * 10000, "game_id": 1})
        self.assertEqual(len(self.feedback_rows()), 1)


class SchemaGuards(FeedbackTestCase):
    def test_unique_index_on_channel_and_external_event_id_exists_and_enforces(self):
        self.add_game(1, "001", "2026-09-01")
        con = self.db()
        indexes = {r["name"]: r for r in con.execute("PRAGMA index_list(feedback)")}
        self.assertIn("idx_feedback_channel_external_event_id", indexes)
        self.assertEqual(indexes["idx_feedback_channel_external_event_id"]["unique"], 1)
        columns = [r["name"] for r in con.execute("PRAGMA index_info(idx_feedback_channel_external_event_id)")]
        self.assertEqual(columns, ["channel", "external_event_id"])
        con.execute("INSERT INTO feedback(game_id, channel, external_event_id, raw_text) VALUES (1,'vk','x','t')")
        with self.assertRaises(sqlite3.IntegrityError):
            con.execute("INSERT INTO feedback(game_id, channel, external_event_id, raw_text) VALUES (1,'vk','x','t2')")


class ErrorContract(unittest.TestCase):
    """Static tripwire (not the contract itself; behavioural tests above are).

    ApiError signature is (status:int, code:str, message:str). It only recognises
    literal-argument call sites, so it guards the defect class found on 2026-09-19.
    """

    def test_every_ApiError_call_site_passes_literal_int_status_then_str_code(self):
        tree = ast.parse(API_SOURCE.read_text())
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ApiError":
                a = node.args
                ok = (
                    len(a) == 3
                    and isinstance(a[0], ast.Constant) and isinstance(a[0].value, int)
                    and isinstance(a[1], ast.Constant) and isinstance(a[1].value, str)
                )
                if not ok:
                    bad.append(f"line {node.lineno}")
        self.assertEqual(bad, [], "ApiError(status, code, message) violated at " + ", ".join(bad))


class _QuietHandler(api.Handler):
    def log_message(self, fmt, *args):
        pass


class HttpLayer(FeedbackTestCase):
    def setUp(self):
        super().setUp()
        self.add_game(1, "001", "2026-09-01")
        self.server = api.ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        self.port = self.server.server_port
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server_thread.join, 5)
        self.addCleanup(self.server.shutdown)  # LIFO: shutdown -> join -> server_close

    def request(self, path, body=None, secret=SECRET, method="POST", auth=None, raw_body=None):
        headers = {"Content-Type": "application/json"}
        if auth is not None:
            headers["Authorization"] = auth
        elif secret is not None:
            headers["Authorization"] = f"Bearer {secret}"
        data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())
        except (http.client.RemoteDisconnected, urllib.error.URLError, ConnectionError) as err:
            self.fail(f"connection dropped instead of a JSON response for POST {path}: {err!r}")

    post = request

    def test_health_needs_no_secret(self):
        self.assertEqual(self.request("/health", method="GET", secret=None), (200, {"status": "ok"}))

    def test_auth_is_required_on_both_write_endpoints(self):
        api.save_raw(self.raw())
        bodies = {"/v1/feedback/raw": self.raw("e9"), "/v1/feedback/analysis": self.analysis_payload({"d": 1})}
        for path, body in bodies.items():
            for label, kwargs in (
                ("missing", {"secret": None}),
                ("wrong", {"secret": "wrong"}),
                ("basic scheme", {"auth": "Basic dXNlcjpwYXNz"}),
                ("empty bearer", {"auth": "Bearer "}),
            ):
                with self.subTest(path=path, case=label):
                    status, payload = self.request(path, body, **kwargs)
                    self.assertEqual((status, payload["error"]), (401, "unauthorized"))
        self.assertEqual(len(self.feedback_rows()), 1)

    def test_unconfigured_secret_fails_closed(self):
        saved = os.environ.pop("FEEDBACK_API_SECRET")
        self.addCleanup(os.environ.__setitem__, "FEEDBACK_API_SECRET", saved)
        status, payload = self.request("/v1/feedback/raw", self.raw())
        self.assertEqual((status, payload["error"]), (503, "api_not_configured"))

    def test_created_then_existing_then_conflict(self):
        self.assertEqual(self.request("/v1/feedback/raw", self.raw())[0], 201)
        self.assertEqual(self.request("/v1/feedback/raw", self.raw())[0], 200)
        status, body = self.request("/v1/feedback/raw", self.raw(text="changed"))
        self.assertEqual((status, body["error"]), (409, "idempotency_conflict"))

    def test_analysis_endpoint_roundtrip(self):
        self.request("/v1/feedback/raw", self.raw())
        self.assertEqual(self.request("/v1/feedback/analysis", self.analysis_payload({"d": 1}))[0], 200)
        status, body = self.request("/v1/feedback/analysis", self.analysis_payload({"d": 2}))
        self.assertEqual((status, body["error"]), (409, "analysis_conflict"))

    def test_no_eligible_game_returns_json_409_not_a_dropped_connection(self):
        self.assertEqual(self.request("/v1/feedback/raw", self.raw())[0], 201)
        status, body = self.request("/v1/feedback/raw", self.raw(event_id="e2"))
        self.assertEqual((status, body["error"]), (409, "feedback_target_not_found"))
        self.assertTrue(body["message"])

    def test_unknown_endpoint_is_404(self):
        self.assertEqual(self.request("/v1/nope", {})[0], 404)

    def test_body_boundaries(self):
        for label, raw_body, expected in (
            ("malformed json", b"{not json", (400, "invalid_json")),
            ("invalid utf-8", b"\xff\xfe\x00", (400, "invalid_json")),
            ("json array", b"[1, 2]", (400, "invalid_request")),
            ("json scalar", b"42", (400, "invalid_request")),
            ("oversized", b"{" + b" " * (api.MAX_BODY_BYTES + 1) + b"}", (413, "invalid_body_size")),
        ):
            with self.subTest(label):
                status, payload = self.request("/v1/feedback/raw", raw_body=raw_body)
                self.assertEqual((status, payload["error"]), expected)
        self.assertEqual(self.feedback_rows(), [])

    def test_missing_and_zero_content_length(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        conn.putrequest("POST", "/v1/feedback/raw")
        conn.putheader("Authorization", f"Bearer {SECRET}")
        conn.endheaders()
        resp = conn.getresponse()
        self.assertEqual((resp.status, json.loads(resp.read())["error"]), (411, "length_required"))
        status, payload = self.request("/v1/feedback/raw", raw_body=b"")
        self.assertEqual((status, payload["error"]), (413, "invalid_body_size"))


def _envelope(event_id="e1", text="liked it", analysis=None):
    payload = {"version": "feedback-storage-v1", "event_id": event_id, "channel": "vk", "raw_text": text}
    if analysis is not None:
        payload["analysis"] = analysis
    return payload


def _message(payload, lease="lease-1"):
    return {"lease_id": lease, "body": json.dumps(payload), "metadata": {"CF-Content-Type": "json"}}


class ConsumerPersistenceBoundary(FeedbackTestCase):
    """queue_consumer.process_message: what gets acked, pushed and retried.

    The invariant: a message is acked only after its data is durably in SQLite;
    every failure must propagate (so run() retries) and must not ack.
    """

    def setUp(self):
        super().setUp()
        self.add_game(1, "001", "2026-09-01")
        self.ack = self._patch("ack")
        self.push = self._patch("push_enriched", return_value={"result": {}})

    def _patch(self, name, **kw):
        patcher = mock.patch.object(consumer, name, **kw)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_raw_only_message_pushes_enriched_with_game_and_acks(self):
        consumer.process_message(_message(_envelope()))
        self.push.assert_called_once()
        pushed = self.push.call_args[0][0]
        self.assertEqual((pushed["version"], pushed["event_id"], pushed["game"]["id"]), ("feedback-enriched-v1", "e1", "001"))
        self.ack.assert_called_once_with("lease-1")
        self.assertEqual(len(self.feedback_rows()), 1)

    def test_message_with_analysis_persists_processes_and_acks_without_enrichment(self):
        consumer.process_message(_message(_envelope(analysis={"decision": "NO_PREFERENCE"})))
        self.push.assert_not_called()
        self.ack.assert_called_once_with("lease-1")
        row = self.feedback_row()
        self.assertEqual(row["processed"], 1)

    def test_redelivery_is_idempotent(self):
        consumer.process_message(_message(_envelope()))
        consumer.process_message(_message(_envelope(), lease="lease-2"))
        self.assertEqual(len(self.feedback_rows()), 1)
        self.assertEqual([c.args for c in self.ack.call_args_list], [("lease-1",), ("lease-2",)])

    def test_no_target_propagates_a_well_formed_error_and_never_acks(self):
        con = self.db()
        con.execute("DELETE FROM deliveries")
        con.commit()
        with self.assertRaises(api.ApiError) as ctx:
            consumer.process_message(_message(_envelope()))
        self.assertEqual((ctx.exception.status, ctx.exception.code), (409, "feedback_target_not_found"))
        self.ack.assert_not_called()
        self.push.assert_not_called()
        self.assertEqual(self.feedback_rows(), [])

    def test_conflicting_redelivery_never_acks(self):
        consumer.process_message(_message(_envelope(text="original")))
        self.ack.reset_mock()
        with self.assertRaises(api.ApiError):
            consumer.process_message(_message(_envelope(text="changed"), lease="lease-2"))
        self.ack.assert_not_called()
        self.assertEqual(self.feedback_row()["raw_text"], "original")

    def test_enrichment_push_failure_keeps_raw_and_does_not_ack_then_recovers_on_retry(self):
        self.push.side_effect = consumer.QueueApiError("cloudflare down")
        with self.assertRaises(consumer.QueueApiError):
            consumer.process_message(_message(_envelope()))
        self.ack.assert_not_called()
        self.assertEqual(len(self.feedback_rows()), 1)  # raw is durable before the failure
        self.push.side_effect = None
        self.push.return_value = {"result": {}}
        consumer.process_message(_message(_envelope(), lease="lease-2"))
        self.ack.assert_called_once_with("lease-2")
        self.assertEqual(len(self.feedback_rows()), 1)

    def test_malformed_messages_fail_before_touching_the_db_and_never_ack(self):
        bad_messages = {
            "wrong version": _message({**_envelope(), "version": "v0"}),
            "wrong channel": _message({**_envelope(), "channel": "tg"}),
            "blank raw_text": _message(_envelope(text="  ")),
            "analysis not object": _message({**_envelope(), "analysis": []}),
            "not json": {"lease_id": "l", "body": "{oops", "metadata": {}},
            "body not string": {"lease_id": "l", "body": {"a": 1}, "metadata": {}},
            "missing lease": {"body": json.dumps(_envelope()), "metadata": {}},
        }
        for label, message in bad_messages.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    consumer.process_message(message)
        self.ack.assert_not_called()
        self.assertEqual(self.feedback_rows(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
