# 0005. One feedback analyzer for both channels

**Status:** accepted · **Decided:** November 2026 (planning voice feedback)

## Context

Feedback arrives from two channels. VK messages reach the host through a Cloudflare Worker
and two queues; Telegram messages are stored by the product bot directly. Both land in the
`feedback` table, and `src/feedback/insights.py` (Claude Haiku) turns them into
observations with verbatim quotes, updates the child's profile and replies to the parent.
Personalization runs on that.

VK feedback also took a second path: the host pushed each message to an enrichment queue,
where the Worker's semantic agent (deterministic segmentation, then a classifier on Workers
AI) analysed it and sent the result back for storage in `analysis_json`. The agent came
from the first version of the feedback loop, before `insights.py` existed. By November its
output was stored but read by nothing except the retired Google Sheets sync; 2 of 8
feedback messages in production carried it. The roadmap still said Telegram feedback should
"join the semantic agent".

## Options

1. **Telegram joins the semantic agent** (the old roadmap item): two analyzers for every
   message, with nothing reading the second one's output.
2. **Keep the agent for VK only**: the channels stay different and the extra queue hop,
   model call and failure mode remain.
3. **One analyzer for both channels**: retire the agent and the enrichment queue.

## Decision

Option 3. The Worker validates the VK callback and hands the message to the host; it makes
no model call. The consumer stores the message and acks it; `insights.py` analyses VK and
Telegram the same way. Analysis envelopes still in flight at the cutover are accepted and
stored. Existing `analysis_json` values stay in the database. The agent, its segmentation
and LLM evals and the manual LLM-evals workflow are removed; they remain in git history.

## Consequences

- One place where feedback is understood, one prompt to improve, one set of caps.
- One queue and one model call fewer on the VK path; the Worker needs no Workers AI binding.
- Features that act on feedback (voice transcription next) are built once, on the host,
  for both channels.
- The deterministic segmentation of the agent is gone; `insights.py` relies on the verbatim
  quote check instead.

## Revisit when

Feedback volume makes per-message analysis on the host too slow or too expensive, or a
channel needs analysis the host cannot do.
