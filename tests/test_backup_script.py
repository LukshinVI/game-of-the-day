"""Tests for ops/backup/game-backup.sh against temporary databases.

Needs the sqlite3 CLI, gzip and a POSIX shell; skipped when sqlite3 is missing.
Nothing here touches any real database or backup directory.
"""

import gzip
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "backup" / "game-backup.sh"


@unittest.skipUnless(shutil.which("sqlite3"), "sqlite3 CLI is required")
class BackupScript(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.db = root / "data" / "game.db"
        self.dest = root / "backups"
        self.db.parent.mkdir()
        con = sqlite3.connect(self.db)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        con.executemany("INSERT INTO t(v) VALUES (?)", [(f"row{i}",) for i in range(50)])
        con.commit()
        con.close()

    def sh(self, mode="run", **overrides):
        env = {
            "PATH": os.environ["PATH"],
            "GAME_DB": str(self.db),
            "GAME_BACKUP_DIR": str(self.dest),
            "GAME_BACKUP_MIN_FREE_MB": "1",
        }
        env.update({k: str(v) for k, v in overrides.items()})
        return subprocess.run(
            ["bash", str(SCRIPT), mode], env=env, capture_output=True, text=True, timeout=60
        )

    def backups(self):
        return sorted(self.dest.glob("game-*.db.gz"))

    def age(self, path, hours):
        t = time.time() - hours * 3600
        os.utime(path, (t, t))

    def restore(self, gz):
        out = Path(self._tmp.name) / "restored.db"
        out.write_bytes(gzip.decompress(Path(gz).read_bytes()))
        return sqlite3.connect(out)

    # -- run ----------------------------------------------------------------
    def test_happy_path_publishes_a_verified_restorable_snapshot(self):
        r = self.sh()
        self.assertEqual(r.returncode, 0, r.stderr)
        (gz,) = self.backups()
        con = self.restore(gz)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(con.execute("SELECT COUNT(*) FROM t").fetchone()[0], 50)
        epoch, name = (self.dest / ".last-local-ok").read_text().split()
        self.assertEqual(name, gz.name)
        self.assertLess(abs(time.time() - int(epoch)), 60)
        self.assertEqual(list(self.dest.glob(".tmp-*")), [], "no temp files may remain")
        self.assertEqual(list(self.dest.glob(".last-*.tmp.*")), [], "no temp manifests may remain")
        self.assertEqual(oct(gz.stat().st_mode & 0o777), "0o600")

    def test_snapshot_is_consistent_while_a_writer_is_active(self):
        stop = threading.Event()

        def writer():
            con = sqlite3.connect(self.db, timeout=30)
            i = 0
            while not stop.is_set():
                con.execute("INSERT INTO t(v) VALUES (?)", (f"w{i}",))
                con.commit()
                i += 1
            con.close()

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            r = self.sh()
        finally:
            stop.set()
            thread.join(timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        con = self.restore(self.backups()[0])
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertGreaterEqual(con.execute("SELECT COUNT(*) FROM t").fetchone()[0], 50)

    def test_corrupt_source_publishes_nothing_and_leaves_no_temp_files(self):
        self.db.write_bytes(b"this is not a sqlite database" * 200)
        r = self.sh()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.backups(), [])
        self.assertFalse((self.dest / ".last-local-ok").exists())
        self.assertEqual(list(self.dest.glob(".tmp-*")), [])

    def test_missing_database_fails(self):
        self.db.unlink()
        self.assertNotEqual(self.sh().returncode, 0)

    def test_insufficient_free_space_fails_before_writing(self):
        r = self.sh(GAME_BACKUP_MIN_FREE_MB=10**9)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("free space", r.stderr)
        self.assertEqual(self.backups(), [])

    def test_stale_temp_files_are_cleaned_up(self):
        self.dest.mkdir()
        stale = self.dest / ".tmp-19990101T000000Z.db"
        stale.write_bytes(b"x")
        self.age(stale, 48)
        fresh = self.dest / ".tmp-fresh.db"
        fresh.write_bytes(b"x")
        self.assertEqual(self.sh().returncode, 0)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists(), "a young temp file may belong to a running backup")

    def test_invalid_numeric_configuration_is_rejected_before_anything_happens(self):
        for name, value in (
            ("GAME_BACKUP_MIN_KEEP", "0"),
            ("GAME_BACKUP_RETAIN_DAYS", "abc"),
            ("GAME_BACKUP_MAX_AGE_HOURS", "-5"),
            ("GAME_BACKUP_MIN_FREE_MB", "1.5"),
        ):
            with self.subTest(name=name, value=value):
                r = self.sh(**{name: value})
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(self.backups(), [])

    @unittest.skipUnless(shutil.which("flock"), "flock is required")
    def test_a_second_concurrent_run_is_refused(self):
        import fcntl

        self.dest.mkdir()
        with open(self.dest / ".backup.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            r = self.sh()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("in progress", r.stderr)
        self.assertEqual(self.backups(), [])

    # -- retention ----------------------------------------------------------
    def make_old_backups(self, n, hours):
        self.dest.mkdir(exist_ok=True)
        files = []
        for i in range(n):
            f = self.dest / f"game-2020010{i}T000000Z.db.gz"
            f.write_bytes(gzip.compress(b"old"))
            self.age(f, hours + i)
            files.append(f)
        return files

    def test_retention_prunes_old_files_but_keeps_the_minimum_count(self):
        old = self.make_old_backups(5, 24 * 30)
        r = self.sh(GAME_BACKUP_RETAIN_DAYS=14, GAME_BACKUP_MIN_KEEP=3)
        self.assertEqual(r.returncode, 0, r.stderr)
        remaining = self.backups()
        self.assertEqual(len(remaining), 3, [p.name for p in remaining])
        self.assertTrue(any(p not in old for p in remaining), "the new backup must survive")

    def test_retention_orders_by_file_name_not_by_mtime(self):
        self.dest.mkdir()
        newer_name = self.dest / "game-20200105T000000Z.db.gz"
        older_name = self.dest / "game-20200101T000000Z.db.gz"
        for f in (newer_name, older_name):
            f.write_bytes(gzip.compress(b"x"))
        self.age(newer_name, 24 * 40)   # newer name, older mtime
        self.age(older_name, 24 * 30)   # older name, newer mtime
        self.assertEqual(self.sh(GAME_BACKUP_RETAIN_DAYS=14, GAME_BACKUP_MIN_KEEP=2).returncode, 0)
        names = [p.name for p in self.backups()]
        self.assertIn(newer_name.name, names, "the second-newest by name is inside the keep floor")
        self.assertNotIn(older_name.name, names)

    def test_retention_keeps_recent_files_regardless_of_count(self):
        self.make_old_backups(5, 1)
        self.assertEqual(self.sh(GAME_BACKUP_RETAIN_DAYS=14, GAME_BACKUP_MIN_KEEP=1).returncode, 0)
        self.assertEqual(len(self.backups()), 6)

    def test_failed_offsite_step_fails_the_run_and_skips_retention(self):
        old = self.make_old_backups(5, 24 * 30)
        r = self.sh(GAME_BACKUP_OFFSITE_CMD="false", GAME_BACKUP_RETAIN_DAYS=14, GAME_BACKUP_MIN_KEEP=1)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((self.dest / ".last-local-ok").exists())
        self.assertFalse((self.dest / ".last-offsite-ok").exists())
        for f in old:
            self.assertTrue(f.exists(), "retention must not run after a failed off-site step")

    def test_successful_offsite_step_writes_its_own_marker(self):
        r = self.sh(GAME_BACKUP_OFFSITE_CMD="true")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.dest / ".last-offsite-ok").exists())

    # -- check --------------------------------------------------------------
    def test_check_passes_after_a_fresh_backup(self):
        self.assertEqual(self.sh().returncode, 0)
        self.assertEqual(self.sh("check").returncode, 0)

    def set_manifest(self, epoch, name):
        (self.dest / ".last-local-ok").write_text(f"{epoch} {name}\n")

    def test_check_fails_without_any_backup(self):
        self.dest.mkdir()
        r = self.sh("check")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no successful backup", r.stderr)

    def test_check_ignores_files_that_are_not_named_in_the_manifest(self):
        self.dest.mkdir()
        (self.dest / "game-20260101T000000Z.db.gz").write_bytes(gzip.compress(b"x"))
        self.assertNotEqual(self.sh("check").returncode, 0)

    def test_check_fails_when_the_last_success_is_stale(self):
        self.assertEqual(self.sh().returncode, 0)
        name = self.backups()[-1].name
        self.set_manifest(int(time.time()) - 48 * 3600, name)
        r = self.sh("check")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("older than", r.stderr)

    def test_touching_an_old_backup_does_not_make_it_look_fresh(self):
        self.assertEqual(self.sh().returncode, 0)
        gz = self.backups()[-1]
        self.set_manifest(int(time.time()) - 48 * 3600, gz.name)
        os.utime(gz, None)
        self.assertNotEqual(self.sh("check").returncode, 0)

    def test_check_fails_for_a_corrupt_empty_or_missing_published_file(self):
        self.assertEqual(self.sh().returncode, 0)
        gz = self.backups()[-1]
        gz.write_bytes(b"not gzip")
        self.assertNotEqual(self.sh("check").returncode, 0)
        gz.write_bytes(b"")
        self.assertNotEqual(self.sh("check").returncode, 0)
        gz.unlink()
        self.assertNotEqual(self.sh("check").returncode, 0)

    def test_check_fails_for_a_malformed_manifest(self):
        self.assertEqual(self.sh().returncode, 0)
        for content in ("", "garbage\n", "abc file.db.gz\n", "1700000000\n"):
            with self.subTest(content=content):
                (self.dest / ".last-local-ok").write_text(content)
                self.assertNotEqual(self.sh("check").returncode, 0)

    def test_check_requires_offsite_success_when_configured(self):
        self.assertEqual(self.sh().returncode, 0)
        self.assertNotEqual(self.sh("check", GAME_BACKUP_OFFSITE_CMD="true").returncode, 0)

    def test_unknown_mode_is_rejected(self):
        self.assertNotEqual(self.sh("bogus").returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
