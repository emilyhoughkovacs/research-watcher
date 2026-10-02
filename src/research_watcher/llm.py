"""The one place Claude is called from.

Centralizes three things every call needs and none should repeat:

  - the model, `claude-opus-5-5`
  - server-side refusal fallback. Sources here include cyber-capability
    evals (AISI, Frontier Red Team), and Opus 5.x's classifiers can decline
    benign summaries of them. `fallbacks: "default"` re-runs a declined
    request on Anthropic's recommended substitute (cyber → Opus 4.8) in the
    same round trip, instead of the item arriving as "(declined)".
  - cost accounting into the run's `Ledger`

Calls with the web search server tool can stop with `pause_turn` when the
server-side loop hits its iteration limit; `create` resumes those by
re-sending the turn, never by adding a "continue" message.
"""

from __future__ import annotations

import json
import logging

from anthropic import Anthropic

from .costs import Ledger

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_TOOL = "web_search_20260209"

MAX_CONTINUATIONS = 3


def create(
    client: Anthropic,
    *,
    stage: str,
    ledger: Ledger | None = None,
    messages: list[dict],
    **params,
):
    """`messages.create` with the model, fallback, pause_turn and costing applied."""
    resp = None
    for _ in range(MAX_CONTINUATIONS + 1):
        resp = client.beta.messages.create(
            model=MODEL,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            messages=messages,
            **params,
        )
        if ledger is not None:
            ledger.add(stage, resp)
        if resp.model != MODEL:
            log.info("%s: served by fallback model %s", stage, resp.model)
        if resp.stop_reason != "pause_turn":
            break
        # Append-only: the paused assistant turn goes back as-is and the
        # server resumes its search loop where it stopped.
        messages = messages + [{"role": "assistant", "content": resp.content}]
    return resp


def web_search(max_uses: int) -> dict:
    return {"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_uses}


def json_output(resp) -> dict | None:
    """The structured-output JSON from a response, or None.

    None on a refusal (the whole fallback chain declined) or an empty
    response — callers decide what a missing answer means for them.
    """
    if resp is None or resp.stop_reason == "refusal":
        return None
    text = next((b.text for b in reversed(resp.content) if b.type == "text"), None)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("unparseable structured output (stop_reason=%s)", resp.stop_reason)
        return None
