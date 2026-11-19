"""Model backends for daily preparation: one call to Claude Code (headless) or Codex CLI
that proposes one candidate as structured output. The backend only proposes; the gate
(gate.py) and the chain (chain.py) decide."""

import json
import os
import subprocess
import sys
from pathlib import Path

from src.preparation import workers_ai


ROOT = Path(__file__).resolve().parents[2]

SCHEMA_PATH = ROOT / "src" / "preparation" / "schemas" / "game_candidate.json"

SHADOW_DIR = ROOT / "data" / "shadow"

# A selection with web research takes 15-30 s in production (November 2026); 3 minutes
# is six times that. A hung provider costs one timeout per run, not one per child: the
# chain stops calling it for the rest of the run (chain.ChildRun.down).
MODEL_TIMEOUT_SECONDS = int(os.environ.get("PREPARATION_MODEL_TIMEOUT", "180"))


def run_model(prompt, model=None, meta=None):
    """Ask the configured model backend for one candidate (a dict).

    The backend only proposes; validate_candidate() decides. PREPARATION_BACKEND
    selects `claude` (Claude Code headless, the default) or `codex` (Codex CLI).
    model overrides the configured Claude model (the fallback); a Workers AI model
    ("@cf/...", the independent model) goes to workers_ai instead; meta, when given, is
    filled with the attempt's backend, latency, tokens and cost (model_runs).
    """
    backend = os.environ.get("PREPARATION_BACKEND", "claude").strip().lower()
    meta = {} if meta is None else meta
    meta.clear()
    if workers_ai.is_independent(model):
        candidate, info = workers_ai.run(prompt, model, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
        meta.update(info)
        return candidate
    meta["backend"] = backend
    if backend == "claude":
        return run_claude(prompt, model=model, meta=meta)
    if backend == "codex":
        return run_codex(prompt)
    raise ValueError(f"unknown PREPARATION_BACKEND: {backend!r} (expected claude or codex)")


def claude_command(schema, model=None):
    model = model or os.environ.get("PREPARATION_CLAUDE_MODEL", "opus")
    return [
        os.environ.get("CLAUDE_BIN", "claude"),
        "--print",
        "--output-format", "json",
        "--json-schema", schema,
        "--model", model,
        # Web research only: no shell, no file access, no MCP servers, no plugins.
        "--tools", "WebSearch,WebFetch",
        "--allowedTools", "WebSearch,WebFetch",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
    ]


def run_claude(prompt, model=None, meta=None):
    schema = SCHEMA_PATH.read_text(encoding="utf-8")

    print("Starting Claude candidate selection...", file=sys.stderr)

    result = subprocess.run(
        claude_command(schema, model),
        input=prompt,
        text=True,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=MODEL_TIMEOUT_SECONDS,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Claude failed.\n"
            f"exit={result.returncode}\n"
            f"stdout={result.stdout[-4000:]}\n"
            f"stderr={result.stderr[-4000:]}"
        )

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Claude output is not valid JSON: {exc}\n{result.stdout[-4000:]}"
        ) from None

    if envelope.get("is_error") or envelope.get("subtype") != "success":
        raise RuntimeError(
            "Claude run did not succeed: "
            f"subtype={envelope.get('subtype')} result={str(envelope.get('result'))[:2000]}"
        )

    if meta is not None:
        meta.update(claude_meta(envelope, model))

    candidate = envelope.get("structured_output")
    if not isinstance(candidate, dict):
        raise RuntimeError("Claude returned no structured_output object")

    return candidate


def claude_meta(envelope, model=None):
    """Latency, tokens and cost from the Claude Code JSON envelope (missing fields -> None)."""
    usage = envelope.get("usage") or {}
    server = usage.get("server_tool_use") or {}
    models = list((envelope.get("modelUsage") or {}).keys())
    return {
        "model": ",".join(models) or model or os.environ.get("PREPARATION_CLAUDE_MODEL", "opus"),
        "api_ms": envelope.get("duration_api_ms") or envelope.get("duration_ms"),
        "num_turns": envelope.get("num_turns"),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "cache_write_tokens": usage.get("cache_creation_input_tokens"),
        "web_searches": server.get("web_search_requests"),
        "cost_usd": envelope.get("total_cost_usd"),
    }


def run_codex(prompt):
    SHADOW_DIR.mkdir(parents=True, exist_ok=True)

    output_path = SHADOW_DIR / "candidate-latest.json"

    if output_path.exists():
        output_path.unlink()

    cmd = [
        "codex",
        "--search",
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
        "exec",
        "--ephemeral",
        "--output-schema",
        str(SCHEMA_PATH),
        "--output-last-message",
        str(output_path),
        "-",
    ]

    print("Starting Codex candidate selection...", file=sys.stderr)

    result = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=MODEL_TIMEOUT_SECONDS,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Codex failed.\n"
            f"exit={result.returncode}\n"
            f"stdout={result.stdout[-4000:]}\n"
            f"stderr={result.stderr[-4000:]}"
        )

    if not output_path.exists():
        raise RuntimeError("Codex did not create output file")

    raw = output_path.read_text(encoding="utf-8").strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Codex output is not valid JSON: {exc}\n"
            f"Output:\n{raw[:4000]}"
        ) from exc
