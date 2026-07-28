"""Weekly stage: grade the shortlist, pick one, write a repro guide.

Two LLM calls:
  1. Feasibility scoring across the whole shortlist in one call, so the
     model can compare candidates against each other rather than judging
     each in isolation.
  2. Guide generation for the winner only, re-reading the paper in full at
     high effort. The guide is the deliverable; it needs the methods
     section, not a summary of a summary.

The feasibility gate is the point of this module. A paper below the gate
cannot win regardless of signal or fellows value, because a guide you
can't execute in the budget is a guide you won't start.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import requests
from anthropic import Anthropic
from jinja2 import Environment, PackageLoader, select_autoescape

from .models import Item
from .summarize import MAX_BODY_CHARS, MODEL

log = logging.getLogger(__name__)

# The guide is the week's deliverable — worth the extra reasoning.
GUIDE_EFFORT = "high"
SCORING_EFFORT = "medium"

# Friday re-reads the winner in full; the daily cap doesn't apply.
GUIDE_BODY_CHARS = MAX_BODY_CHARS * 3

_SCORING_SCHEMA = {
    "type": "object",
    "properties": {
        "assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "feasibility": {"type": "integer"},
                    "repro_tier": {"type": "string", "enum": ["GREEN", "YELLOW", "RED"]},
                    "reasoning": {
                        "type": "string",
                        "description": "One or two sentences. Name the specific blocker "
                        "or the specific reason it's tractable.",
                    },
                    "nearest_analogue": {
                        "type": ["string", "null"],
                        "description": "REQUIRED for RED tier: the nearest tractable "
                        "version. 'Not reproducible' alone is useless — say what "
                        "smaller model or reduced scope would get most of the result.",
                    },
                    "est_build_hours": {"type": "number"},
                },
                "required": [
                    "key",
                    "feasibility",
                    "repro_tier",
                    "reasoning",
                    "nearest_analogue",
                    "est_build_hours",
                ],
                "additionalProperties": False,
            },
        },
        "pick_key": {"type": ["string", "null"]},
        "why_picked": {"type": "string"},
        "escalation_key": {
            "type": ["string", "null"],
            "description": "A paper with high signal and low effort that fails ONLY "
            "the local-compute bar. Null unless it genuinely qualifies — this "
            "section becomes noise if every RED paper triggers it.",
        },
    },
    "required": ["assessments", "pick_key", "why_picked", "escalation_key"],
    "additionalProperties": False,
}


def build_scoring_prompt(profile: dict) -> str:
    """Stable prefix — cached across the scoring call."""
    skill = profile.get("skill", {})
    compute = skill.get("compute", {})
    goal = profile.get("goal", {})
    cons = profile.get("constraints", {})
    gate = profile.get("scoring", {}).get("feasibility_gate", 6)
    budget = cons.get("repro_budget_days", 1)

    return f"""You are picking one paper for a researcher to reproduce this week.

## Who is doing the work

This matters more than anything else here. Feasibility means "can THIS
person do it in {budget} day(s)", not "is this theoretically reproducible".

  Python: {skill.get('python', 'unknown')}
  PyTorch: {skill.get('pytorch', 'unknown')}
  Comfortable with: {', '.join(skill.get('familiar', []))}
  Has NOT used: {', '.join(skill.get('unfamiliar', []))}
  Known gap: {skill.get('gap', 'unspecified')}

  Compute available:
    local: {compute.get('local', 'unknown')}
    free:  {compute.get('free', 'unknown')}
    willing to rent GPU: {compute.get('willing_to_rent', False)}

  Goal: {goal.get('target')} — {', '.join(goal.get('areas', []))}
  Deliverables per replication: {', '.join(cons.get('deliverables', []))}

## Feasibility scale (0-10)

  9-10  Authors shipped a Colab or runnable notebook. Inference only.
        Free tier or local. Uses frameworks they already know.
  7-8   One unfamiliar library with a good quickstart. Pre-trained SAEs
        exist. Local or free Colab.
  5-6   A few hours of rented GPU, OR two unfamiliar libraries at once,
        OR meaningful dataset assembly. Tight but possible.
  3-4   Requires training an SAE, fine-tuning, or building an eval
        harness from scratch. Multi-day.
  0-2   Frontier model access, multi-GPU, or proprietary data.

Penalise learning two new things at once — that is what actually eats the
day. A paper needing both TransformerLens AND a trained SAE is not a 6
even if each part looks small.

## The gate

Feasibility must be >= {gate} to be picked. Below that, the paper CANNOT be
the pick no matter how high its other scores. Set pick_key to null if
nothing clears the gate — an honest "nothing this week" beats a guide
that can't be finished.

## Escalation

Set escalation_key ONLY for a paper with high signal AND low estimated
effort that fails purely on compute. That is the case where renting a GPU
is the right conversation. If every RED paper triggers this, the section
is noise — leave it null when in doubt.

## RED tier

A RED verdict must name a nearest tractable analogue. "Needs refusal
behavior GPT-2 lacks, but Gemma-2-2B + Gemma Scope gets most of it" is
useful. "Not reproducible" is not."""


def score_shortlist(
    client: Anthropic, items: list[Item], profile: dict
) -> tuple[Item | None, Item | None, str, dict]:
    """Grade feasibility, apply the gate, choose a pick.

    Returns (pick, escalation, why_picked, estimates_by_key).
    """
    if not items:
        return None, None, "", {}

    candidates = []
    for i in items:
        candidates.append(
            {
                "key": i.key,
                "title": i.title,
                "source": i.source_display,
                "area": i.area,
                "signal": i.scores.get("signal"),
                "fellows": i.scores.get("fellows"),
                "repro_signals": i.repro_signals,
                "bullets": i.bullets,
                "links": {"blog": i.url, "paper": i.paper_url, "code": i.code_url},
            }
        )

    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=[
            {
                "type": "text",
                "text": build_scoring_prompt(profile),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        thinking={"type": "adaptive"},
        output_config={
            "effort": SCORING_EFFORT,
            "format": {"type": "json_schema", "schema": _SCORING_SCHEMA},
        },
        messages=[
            {
                "role": "user",
                "content": "Candidates from this week:\n\n"
                + json.dumps(candidates, indent=2, default=str),
            }
        ],
    )

    if resp.stop_reason == "refusal":
        log.warning("scoring call refused")
        return None, None, "(scoring declined)", {}

    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    data = json.loads(text)

    by_key = {i.key: i for i in items}
    estimates: dict[str, float] = {}
    weights = profile.get("scoring", {}).get("weights", {})
    w_sig = weights.get("signal", 0.35)
    w_fel = weights.get("fellows", 0.35)
    w_fea = weights.get("feasibility", 0.30)
    gate = profile.get("scoring", {}).get("feasibility_gate", 6)

    for a in data.get("assessments", []):
        item = by_key.get(a["key"])
        if item is None:
            continue
        item.scores["feasibility"] = a["feasibility"]
        item.repro_tier = a["repro_tier"]
        item.repro_signals = dict(item.repro_signals or {})
        item.repro_signals["reasoning"] = a["reasoning"]
        if a.get("nearest_analogue"):
            item.repro_signals["nearest_analogue"] = a["nearest_analogue"]
        estimates[item.key] = a.get("est_build_hours") or 0

        s = item.scores.get("signal") or 0
        f = item.scores.get("fellows") or 0
        item.scores["composite"] = round(
            w_sig * s + w_fel * f + w_fea * a["feasibility"], 1
        )

    pick = by_key.get(data.get("pick_key") or "")
    # Enforce the gate here too — never trust the model to honour it alone.
    if pick is not None and (pick.scores.get("feasibility") or 0) < gate:
        log.warning(
            "model picked %s with feasibility %s below gate %s — rejecting",
            pick.key,
            pick.scores.get("feasibility"),
            gate,
        )
        pick = None

    if pick is not None:
        pick.picked = True
        pick.repro_signals["why"] = data.get("why_picked", "")

    escalation = by_key.get(data.get("escalation_key") or "")
    if escalation is pick:
        escalation = None

    return pick, escalation, data.get("why_picked", ""), estimates


# ── guide generation ────────────────────────────────────────────────


def _fetch_full_text(item: Item, session: requests.Session) -> str:
    """Re-read the winner in full. Prefer the paper over the blog post."""
    from bs4 import BeautifulSoup

    url = item.paper_url or item.url
    try:
        resp = session.get(url, timeout=40)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(" ", strip=True)[:GUIDE_BODY_CHARS]
    except Exception as exc:  # noqa: BLE001
        log.warning("guide full-text fetch failed (%s): %s", url, exc)
        return item.body or ""


_GUIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "what_youre_building": {"type": "string"},
        "positive_result": {
            "type": "string",
            "description": "How they know they're done. A concrete, checkable outcome.",
        },
        "how_it_works": {
            "type": "string",
            "description": "Technical implementation from the METHODS section, not the "
            "abstract. What gets hooked, what gets measured, what the math is. "
            "Unicode math (Wᵀ, x₁, ∇f) — never LaTeX.",
        },
        "environment": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "enum": ["local", "colab", "cloud-gpu"]},
                "reasoning": {"type": "string"},
                "setup_commands": {"type": "array", "items": {"type": "string"}},
                "packages": {"type": "array", "items": {"type": "string"}},
                "cost_estimate": {"type": ["string", "null"]},
            },
            "required": ["location", "reasoning", "setup_commands", "packages", "cost_estimate"],
            "additionalProperties": False,
        },
        "prerequisites": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string"},
                    "where": {"type": "string"},
                    "needs_signup": {"type": "boolean"},
                    "notes": {"type": "string"},
                },
                "required": ["item", "where", "needs_signup", "notes"],
                "additionalProperties": False,
            },
        },
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "checkpoint": {
                        "type": "string",
                        "description": "How to verify this step worked before moving on. "
                        "A step you can't verify is a step you can silently get wrong.",
                    },
                    "est_minutes": {"type": "integer"},
                },
                "required": ["n", "title", "detail", "checkpoint", "est_minutes"],
                "additionalProperties": False,
            },
        },
        "hazards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hazard": {"type": "string"},
                    "mitigation": {"type": "string"},
                },
                "required": ["hazard", "mitigation"],
                "additionalProperties": False,
            },
        },
        "demo_repo": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "structure": {"type": "array", "items": {"type": "string"}},
                "readme_sections": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name", "structure", "readme_sections"],
            "additionalProperties": False,
        },
        "blog_skeleton": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "slug": {"type": "string"},
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string"},
                            "what_goes_here": {"type": "string"},
                        },
                        "required": ["heading", "what_goes_here"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "slug", "sections"],
            "additionalProperties": False,
        },
        "est_build_hours": {"type": "number"},
        "est_writeup_hours": {"type": "number"},
    },
    "required": [
        "what_youre_building",
        "positive_result",
        "how_it_works",
        "environment",
        "prerequisites",
        "steps",
        "hazards",
        "demo_repo",
        "blog_skeleton",
        "est_build_hours",
        "est_writeup_hours",
    ],
    "additionalProperties": False,
}


def build_guide_prompt(profile: dict) -> str:
    skill = profile.get("skill", {})
    compute = skill.get("compute", {})
    ident = profile.get("identity", {})
    cons = profile.get("constraints", {})

    return f"""Write a reproduction guide. The reader will open this and start
typing — it must be actionable without further research.

## The single most important rule

Write from the METHODS section, not the abstract. "They found that models
have a global workspace" is useless for building. "Hook the residual
stream at layer N, project through the SAE decoder, measure cosine
similarity against the reference direction across a prompt set" is what
they actually need. Outcome framing is a failure of this guide.

## Who is doing this

  Python: {skill.get('python')}
  PyTorch: {skill.get('pytorch')}
  Knows: {', '.join(skill.get('familiar', []))}
  Has NOT used: {', '.join(skill.get('unfamiliar', []))}
  Compute: local={compute.get('local')} / free={compute.get('free')}

For any library in the "has NOT used" list, do not assume familiarity.
Give the actual import and the actual call, not "load the SAE as usual".

## Budget

{cons.get('repro_budget_days', 1)} day, ending in:
{chr(10).join('  - ' + d for d in cons.get('deliverables', []))}

Scope `what_youre_building` to fit. The minimum viable demo that
demonstrates the core claim beats a faithful full replication that
doesn't finish. If your own time estimate exceeds the budget, say so in
the estimates — an honest overrun flag is more useful than a guide that
silently doesn't fit.

## Steps

Every step needs a CHECKPOINT — something concrete to verify before
moving on ("you should see ~N features with activation > 0.1"). A step
that can't be verified is a step that can be silently wrong, and silent
wrongness at hour 2 costs the whole day.

## Hazards

2-3 things most likely to eat time on THIS paper specifically. Version
pins, tokenizer mismatches, layer-indexing conventions (0- vs 1-based),
device placement. Not generic advice.

## Blog skeleton

Section headings plus one line each on what belongs there. Do NOT draft
prose — the reader writes it. Blog is {ident.get('blog_engine', 'jekyll')}.

## Formatting

Unicode math (Wᵀ, x₁, x², ∇f, ∈) — never LaTeX. No em-dashes."""


def generate_guide(
    client: Anthropic,
    item: Item,
    profile: dict,
    session: requests.Session,
) -> dict:
    full_text = _fetch_full_text(item, session)

    resp = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=[
            {
                "type": "text",
                "text": build_guide_prompt(profile),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        thinking={"type": "adaptive"},
        output_config={
            "effort": GUIDE_EFFORT,
            "format": {"type": "json_schema", "schema": _GUIDE_SCHEMA},
        },
        messages=[
            {
                "role": "user",
                "content": (
                    f"Paper: {item.title}\n"
                    f"Source: {item.source_display}\n"
                    f"Blog: {item.url}\n"
                    f"Paper: {item.paper_url or '(none)'}\n"
                    f"Code: {item.code_url or '(none released)'}\n"
                    f"Repro signals: {json.dumps(item.repro_signals, default=str)}\n\n"
                    f"--- FULL TEXT ---\n{full_text}"
                ),
            }
        ],
    )

    if resp.stop_reason == "refusal":
        raise RuntimeError("guide generation refused")

    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    return json.loads(text)


def write_guide(item: Item, guide: dict, profile: dict, guides_dir) -> str:
    env = Environment(
        loader=PackageLoader("research_watcher", "templates"),
        autoescape=select_autoescape(default=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("repro_guide.md.j2")

    today = date.today()
    rendered = template.render(
        item=item,
        g=guide,
        profile=profile,
        today=today.isoformat(),
        blog_filename=f"{today.isoformat()}-{guide['blog_skeleton']['slug']}.md",
    )

    guides_dir = Path(guides_dir)
    guides_dir.mkdir(parents=True, exist_ok=True)
    path = guides_dir / f"{today.isoformat()}-{item.slug}.md"
    path.write_text(rendered, encoding="utf-8")
    return str(path)
