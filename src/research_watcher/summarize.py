"""LLM stage: fetch full text, summarize, score, extract repro signals.

Runs only on items `fetch` flagged as new, so a quiet day costs zero tokens.

Two-stage scoring, deliberately split:
  - HERE (daily): `signal` and `artifact_value`. Both are properties of the paper
    and are cheap to judge while the text is already loaded.
  - FRIDAY: `feasibility`, which needs deep profile context, plus the
    composite and the repro tier.

Repro *signals* are extracted here but not graded. Extracting them costs
almost nothing while the paper is in context and would cost a full re-read
on Friday. Grading them daily would burn tokens on papers never opened.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

import requests
import yaml
from anthropic import Anthropic
from bs4 import BeautifulSoup

from .models import Item

log = logging.getLogger(__name__)

MODEL = "claude-opus-5"

# Daily summarization is bounded extraction, not open-ended reasoning.
# Friday guide generation runs at "high" — see friday.py.
DAILY_EFFORT = "medium"

# ~10k tokens of body. Enough for methods + results on a long paper;
# Friday re-reads the winner in full when it writes the guide.
MAX_BODY_CHARS = 40_000

_DATE_RE = re.compile(r"([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})")

_SCHEMA = {
    "type": "object",
    "properties": {
        "bullets": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-4 bullets: key claim, method, why it matters, "
            "a limitation the authors name. One sentence each.",
        },
        "paper_url": {"type": ["string", "null"]},
        "code_url": {"type": ["string", "null"]},
        "signal": {
            "type": "integer",
            "description": "0-10: how much this matters for AI safety, "
            "independent of reproducibility",
        },
        "artifact_value": {
            "type": "integer",
            "description": "0-10: is there something worth producing by working "
            "through this — a replication, a negative result, a reusable "
            "implementation, or an extension",
        },
        "repro_signals": {
            "type": "object",
            "properties": {
                "model_prereqs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Capabilities the phenomenon requires that a "
                    "base LM lacks: instruction-following, chain-of-thought, "
                    "RLHF artifacts, refusal behavior, tool use, long context. "
                    "Empty array means a base model suffices.",
                },
                "sae_dependency": {
                    "type": "string",
                    "enum": ["none", "existing-open-saes", "must-train-saes"],
                },
                "access_type": {
                    "type": "string",
                    "enum": ["read-only", "intervention", "training", "api-only"],
                },
                "compute_floor": {
                    "type": "string",
                    "enum": ["cpu", "mps", "colab-t4", "single-gpu", "a100", "multi-gpu"],
                },
                "artifacts": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["code", "notebook", "weights", "data"]},
                },
                "smallest_viable_model": {
                    "type": ["string", "null"],
                    "description": "Smallest open model the core claim could plausibly "
                    "be tested on, e.g. 'gpt2-small', 'gemma-2-2b'. Null if unclear.",
                },
            },
            "required": [
                "model_prereqs",
                "sae_dependency",
                "access_type",
                "compute_floor",
                "artifacts",
                "smallest_viable_model",
            ],
            "additionalProperties": False,
        },
    },
    "required": ["bullets", "paper_url", "code_url", "signal", "artifact_value", "repro_signals"],
    "additionalProperties": False,
}


def build_system_prompt(profile: dict) -> str:
    """Stable across every item in a run — this is the cached prefix.

    Must stay byte-identical between calls or prompt caching silently
    stops working. Nothing volatile (no timestamps, no per-item text).
    """
    goal = profile.get("goal", {})
    skill = profile.get("skill", {})
    threads = profile.get("threads", []) or []

    thread_lines = "\n".join(
        f"  - {t.get('id')}: {t.get('summary')}" for t in threads
    ) or "  (none recorded)"

    interests = ", ".join(goal.get("areas", [])) or "AI safety broadly"
    return f"""You are triaging new AI safety and interpretability publications \
for a working researcher. Your output feeds a daily digest and a weekly \
"which of these is worth running" pick.

## Reader

Research interests: {interests}

Technical context (used for feasibility, assessed separately):
  Python: {skill.get('python', 'unknown')}
  ML frameworks: {skill.get('pytorch', 'unknown')}
  Uses regularly: {', '.join(skill.get('familiar', []))}
  Has not used: {', '.join(skill.get('unfamiliar', []))}

Open threads (a paper bearing on one of these is worth more):
{thread_lines}

## Scoring

`signal` (0-10) — does this matter for AI safety, independent of whether \
it can be reproduced? High: advances a core question, or measures \
something previously unmeasurable. Low: capability work with a safety \
framing, position pieces, surveys, program announcements. Ignore citation \
count, author prominence, and venue — those track prestige, not signal.

`artifact_value` (0-10) — is there something worth producing by working \
through this? Replication that confirms or fails to confirm a result, a \
reusable implementation of a method, a negative result, or an obvious \
extension all count. The bar is whether the work would tell someone \
something they did not already know.

High requires the method be specified well enough that a run is \
verifiable — you can tell whether you got the same answer. A paper whose \
central claim rests on a proprietary model or unreleased data scores LOW \
even when the idea is excellent, because nobody outside the lab can check \
it. That is a property of the artifact, not a judgement of the work.

Do NOT score feasibility. That is assessed separately with fuller context.

## Repro signals

Extract signals, do not grade them. Be concrete and conservative: if the \
paper needs chain-of-thought or refusal behavior, say so — that rules out \
base models like GPT-2 regardless of anything else. If you cannot tell \
from the text, prefer the more demanding answer.

## Bullets

3-4 bullets, one sentence each: the key claim, the method, why it matters, \
and a limitation the authors themselves name. Write for someone deciding \
in ten seconds whether to open the paper. No preamble, no hedging."""


def fetch_body(item: Item, session: requests.Session, timeout: int = 25) -> None:
    """Fetch the item's page. Backfills `published` when the index had no date.

    Some indexes (alignment.anthropic.com, safe.ai) carry no date on their
    cards. Since we fetch the page here anyway, recovering the date costs
    nothing extra.
    """
    try:
        resp = session.get(item.url, timeout=timeout)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log.warning("body fetch failed for %s: %s", item.url, exc)
        return

    soup = BeautifulSoup(resp.content, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    item.body = text[:MAX_BODY_CHARS]

    if item.published is None:
        m = _DATE_RE.search(text[:4000])
        if m:
            from .fetch import parse_date

            item.published = parse_date(m.group(1))

    # Links to the underlying paper / code, if the post points at them.
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if item.code_url is None and "github.com" in href:
            item.code_url = href
        if item.paper_url is None and (
            "arxiv.org" in href or "transformer-circuits.pub" in href or href.endswith(".pdf")
        ):
            if href != item.url:
                item.paper_url = href


def summarize_item(client: Anthropic, item: Item, system_prompt: str) -> None:
    """One API call per item. System prompt is cached across the run."""
    body = item.body or "(no body text could be fetched)"
    user = (
        f"Title: {item.title}\n"
        f"Source: {item.source_display}\n"
        f"Area: {item.area}\n"
        f"URL: {item.url}\n"
        f"Published: {item.published or 'unknown'}\n"
        f"Extract repro signals: {'yes' if item.grade_repro else 'no — set conservative defaults'}\n\n"
        f"--- CONTENT ---\n{body}"
    )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                # Stable prefix across every item in the run. Opus 5's minimum
                # cacheable prefix is 512 tokens; this prompt clears it.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        thinking={"type": "adaptive"},
        output_config={
            "effort": DAILY_EFFORT,
            "format": {"type": "json_schema", "schema": _SCHEMA},
        },
        messages=[{"role": "user", "content": user}],
    )

    if resp.stop_reason == "refusal":
        log.warning("refused: %s", item.key)
        item.bullets = ["(model declined to summarize this item)"]
        return

    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        log.warning("empty response for %s", item.key)
        return

    data = json.loads(text)
    item.bullets = data.get("bullets", [])
    item.paper_url = data.get("paper_url") or item.paper_url
    item.code_url = data.get("code_url") or item.code_url

    # For sources that ARE the paper (transformer-circuits, arXiv mirrors),
    # blog and paper resolve to the same URL. Showing it twice under two
    # labels reads as a rendering bug.
    if item.paper_url and item.paper_url.rstrip("/") == item.url.rstrip("/"):
        item.paper_url = None
    item.scores["signal"] = data.get("signal")
    item.scores["artifact_value"] = data.get("artifact_value")
    item.repro_signals = data.get("repro_signals", {})

    usage = resp.usage
    log.info(
        "%s  in=%d cached=%d out=%d",
        item.key,
        usage.input_tokens,
        getattr(usage, "cache_read_input_tokens", 0) or 0,
        usage.output_tokens,
    )


def write_archive(item: Item, archive_dir) -> str:
    """Write the /paper-summary-format archive file. Returns the path."""
    from pathlib import Path

    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / item.archive_name

    front = {
        "title": item.title,
        "source": item.source_id,
        "source_display": item.source_display,
        "area": item.area,
        "published": item.published.isoformat() if item.published else None,
        "links": {
            k: v
            for k, v in (("blog", item.url), ("paper", item.paper_url), ("code", item.code_url))
            if v
        },
        "scores": item.scores,
        "repro_signals": item.repro_signals,
        "repro_tier": item.repro_tier,
        "picked": item.picked,
        "key": item.key,
    }

    body = "\n".join(f"- {b}" for b in item.bullets) or "- (no summary generated)"
    content = (
        "---\n"
        + yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
        + "---\n\n"
        + f"# {item.title}\n\n"
        + f"**{item.source_display}** · {item.published or 'date unknown'}\n\n"
        + "## Key learnings\n\n"
        + body
        + "\n"
    )
    path.write_text(content, encoding="utf-8")
    return str(path)


def rank(items: list[Item], top_n: int) -> tuple[list[Item], list[Item]]:
    """Split into (expanded, headline-only) by daily score.

    Daily rank uses signal + artifact_value; feasibility is a weekly concern.
    """

    def score(i: Item) -> float:
        s = i.scores.get("signal") or 0
        f = i.scores.get("artifact_value") or 0
        return (s + f) / 2

    ordered = sorted(items, key=score, reverse=True)
    return ordered[:top_n], ordered[top_n:]


def load_profile(path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def today() -> date:
    return date.today()
