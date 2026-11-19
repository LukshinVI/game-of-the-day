"""The independent model: a text model on Cloudflare Workers AI, another provider than
Claude, for the days Claude cannot answer (quota, outage).

Chain (chain.obtain_candidate): primary model -> fallback model (same provider) ->
independent model -> library. The independent model has no web search, so it may cite
only a source an earlier game already used (known_sources), and the gate checks it. Its
game reaches a child only when a reviewer on the same provider checked it: a reviewer
outage sends the day to the library instead (review.ReviewUnavailable).

Enabled by PREPARATION_INDEPENDENT_MODEL (for example "@cf/openai/gpt-oss-120b") with
CF_AI_TOKEN and CF_ACCOUNT_ID (the voice token: Workers AI permission). The reviewer is
PREPARATION_INDEPENDENT_REVIEW_MODEL, the same model when unset.
"""

import json
import os
import time
import urllib.request

PREFIX = "@cf/"
TIMEOUT_SECONDS = 180
# Workers AI bills neurons: 0.011 USD per 1 000 beyond the free daily allocation.
USD_PER_NEURON = 0.011 / 1000


def model():
    return os.environ.get("PREPARATION_INDEPENDENT_MODEL", "").strip()


def review_model():
    return os.environ.get("PREPARATION_INDEPENDENT_REVIEW_MODEL", "").strip() or model()


def enabled():
    return (model().startswith(PREFIX) and bool(os.environ.get("CF_AI_TOKEN"))
            and bool(os.environ.get("CF_ACCOUNT_ID")))


def daily_calls():
    """Calls a day (selection and review): about 260 neurons each, so the default keeps
    within the free 10 000 neurons a day, which the voice transcription shares."""
    return int(os.environ.get("PREPARATION_INDEPENDENT_PER_DAY", "20"))


def calls_today(con, target_date):
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'model_runs'").fetchone() is None:
        return 0
    return con.execute(
        "SELECT COUNT(*) FROM model_runs WHERE dry_run = 0 AND target_date = ? "
        "AND (model LIKE '@cf/%' OR model LIKE 'review:@cf/%')", (str(target_date),)).fetchone()[0]


def is_independent(name):
    return bool(name) and str(name).startswith(PREFIX)


def _post(url, body, timeout):
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "Authorization": f"Bearer {os.environ['CF_AI_TOKEN']}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse(result):
    """The JSON object a Workers AI text model returned: `response` (an object or a
    string) or the OpenAI-style `choices`."""
    out = result.get("response")
    if out is None and result.get("choices"):
        out = (result["choices"][0].get("message") or {}).get("content")
    if isinstance(out, str):
        text = out.strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise RuntimeError(f"Workers AI answer is not JSON: {text[:200]}")
        out = json.loads(text[start:end + 1])
    if not isinstance(out, dict):
        raise RuntimeError("Workers AI returned no JSON object")
    return out


def run(prompt, name, schema, max_tokens=6000, post=None):
    """(object, meta) for one call to `name` with a JSON-schema response."""
    url = f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CF_ACCOUNT_ID']}/ai/run/{name}"
    body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens,
            "response_format": {"type": "json_schema", "json_schema": schema}}
    started = time.monotonic()
    data = (post or _post)(url, body, TIMEOUT_SECONDS)
    if not data.get("success", True):
        raise RuntimeError(f"Workers AI error: {json.dumps(data.get('errors'))[:200]}")
    result = data.get("result") or {}
    usage = result.get("usage") or {}
    neurons = usage.get("neurons")
    meta = {
        "backend": "workers_ai",
        "model": name,
        "api_ms": int((time.monotonic() - started) * 1000),
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "cost_usd": round(neurons * USD_PER_NEURON, 6) if neurons is not None else None,
    }
    return parse(result), meta


def known_sources(con, limit=60):
    """Sources earlier model games used (public pages, no family data), most used first."""
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'games'").fetchone() is None:
        return []
    columns = {r[1] for r in con.execute("PRAGMA table_info(games)")}
    origin = "AND COALESCE(origin, 'model') != 'library'" if "origin" in columns else ""
    rows = con.execute(
        f"""SELECT source_name, source_url, COUNT(*) AS n FROM games
            WHERE source_url LIKE 'https://%' AND TRIM(COALESCE(source_name, '')) != '' {origin}
            GROUP BY source_url ORDER BY n DESC, source_url LIMIT ?""", (limit,))
    return [{"source_name": r[0], "source_url": r[1]} for r in rows]


def sources_note(sources):
    return ("\n\nNO WEB SEARCH IS AVAILABLE IN THIS RUN. Choose source_name and source_url ONLY "
            "from this list of verified sources, copying the URL exactly: the one closest to the "
            "activity and the age.\n" + json.dumps(sources, ensure_ascii=False))


def source_error(candidate, sources):
    """A gate error when the candidate cites a source outside the list, else None."""
    if candidate.get("source_url") in {s["source_url"] for s in sources}:
        return None
    return ("source_url must be copied exactly from the list of verified sources "
            f"(no web search in this run), got {str(candidate.get('source_url'))[:120]!r}")


def review_runner(name):
    """A runner for review.review_tiered on Workers AI (the envelope shape it expects)."""
    def run_review(prompt, _name, schema=None):
        output, meta = run(prompt, name, schema, max_tokens=1500)
        return {"structured_output": output, "total_cost_usd": meta["cost_usd"]}
    return run_review
