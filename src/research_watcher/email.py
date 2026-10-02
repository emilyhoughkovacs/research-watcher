"""SMTP delivery and plain-text email rendering.

Plain text on purpose: it renders identically everywhere, is readable on a
phone lock screen, and can't break in a way that hides content.

Gmail app password over STARTTLS — no OAuth, so this works headless in CI.
"""

from __future__ import annotations

import logging
import smtplib
import textwrap
from dataclasses import dataclass, field
from email.message import EmailMessage

from .dedupe import arxiv_id
from .models import Item, SourceResult

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
RULE = "─" * 62


def send(subject: str, body: str, address: str, app_password: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = address
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(address, app_password)
        smtp.send_message(msg)
    log.info("sent: %s", subject)


# ── shared bits ─────────────────────────────────────────────────────


def _links(item: Item) -> list[str]:
    # Sweep hits and arXiv items link to the paper itself, not a blog post.
    label = "Paper" if item.source_id == "news-sweep" or arxiv_id(item.url) else "Blog"
    out = [f"   → {label + ':':<7}{item.url}"]
    if item.paper_url:
        out.append(f"   → Paper: {item.paper_url}")
    if item.code_url:
        out.append(f"   → Code:  {item.code_url}")
    return out


@dataclass
class DigestNotes:
    """Everything the footer reports that isn't an item."""

    too_old: int = 0
    duplicates: int = 0
    unresolved: list[tuple[str, str]] = field(default_factory=list)  # (title, reason)
    stale_since: str | None = None
    cost_run: float | None = None
    cost_month: float | None = None
    cap: float | None = None
    budget_skipped: list[str] = field(default_factory=list)


def _footer(
    results: list[SourceResult],
    archive_dir: str,
    failing: list[tuple[str, int]],
    notes: DigestNotes | None = None,
) -> str:
    notes = notes or DigestNotes()
    lines = ["", RULE]
    if notes.stale_since:
        lines.append(
            f"⚠ State was last saved {notes.stale_since}. Earlier runs aren't persisting, "
            "so items may repeat — check the workflow's commit step."
        )
    errored = [r for r in results if not r.ok]
    lines.append(f"Archived to {archive_dir}/")
    skipped = []
    if notes.too_old:
        skipped.append(f"{notes.too_old} older item(s) skipped")
    if notes.duplicates:
        skipped.append(f"{notes.duplicates} duplicate(s) of earlier items skipped")
    if skipped:
        lines.append(" · ".join(skipped))
    for title, reason in notes.unresolved:
        lines.append(f"News item skipped, couldn't confirm the paper: {title[:60]} ({reason})")
    if errored:
        detail = ", ".join(f"{r.source_id} ({(r.error or '')[:40]})" for r in errored)
        lines.append(f"{len(errored)} source(s) errored: {detail}")
    if failing:
        for sid, n in failing:
            lines.append(f"⚠ {sid} has failed {n} consecutive runs — check the parser")
    if notes.cost_run is not None:
        month = (
            f" · month to date ${notes.cost_month:.2f} of ${notes.cap:.0f} cap"
            if notes.cost_month is not None and notes.cap is not None
            else ""
        )
        lines.append(f"Cost: this run ${notes.cost_run:.2f}{month}")
    if notes.budget_skipped:
        lines.append(
            f"Skipped to stay under the monthly cap: {', '.join(notes.budget_skipped)}"
        )
    return "\n".join(lines)


def _wrap(text: str, indent: str = "   ") -> list[str]:
    return textwrap.wrap(" ".join(text.split()), width=72, initial_indent=indent,
                         subsequent_indent=indent)


# ── digest ──────────────────────────────────────────────────────────


def render_digest(
    top: list[Item],
    rest: list[Item],
    results: list[SourceResult],
    archive_dir: str,
    failing: list[tuple[str, int]],
    cadence: str = "daily",
    *,
    waves: list[dict] | None = None,
    notes: DigestNotes | None = None,
) -> tuple[str, str]:
    """Returns (subject, body).

    `waves` are already-sent items now getting coverage: dicts with title,
    url, first_seen, outlets.
    """
    n = len(top) + len(rest)
    areas = sorted({i.area for i in top + rest})
    area_str = ", ".join(a.replace("-", " ").title() for a in areas[:3])
    # Daily is the default and goes unlabelled — a subject that reads the same
    # every day is easier to filter on. Anything rarer says so, because a
    # monthly digest arriving unannounced looks like a backlog.
    label = "" if cadence == "daily" else f"{cadence.capitalize()} · "
    subject = f"[Research Watch] {label}{n} new · {area_str}"
    if failing or (notes and notes.stale_since):
        subject = f"⚠ {subject}"

    lines: list[str] = []

    if top:
        lines += ["━━ TOP " + str(len(top)) + " " + "━" * 48, ""]
        for idx, item in enumerate(top, 1):
            lines.append(f"{idx}. {item.title}")
            via = f" · found via {', '.join(item.found_via[:3])}" if item.found_via else ""
            lines.append(f"   {item.source_display} · {item.published or 'date unknown'}{via}")
            lines += _links(item)
            lines.append("")
            if item.abstract:
                lines += _wrap(item.abstract)
            else:
                # Summary failed or was declined; bullets are the fallback.
                lines += [f"   • {b}" for b in item.bullets]
            if item.reach:
                lines.append("")
                lines += _wrap(f"Reach: {item.reach}")
            lines.append("")

    also = [i for i in rest if i.section != "alignment_blog"]
    blog = [i for i in top + rest if i.section == "alignment_blog"]
    # An alignment-blog item promoted into TOP is shown there, not twice.
    blog = [i for i in blog if i not in top]

    if also:
        lines += ["━━ ALSO NEW " + "━" * 45, ""]
        for item in also:
            lines.append(f"• {item.title}")
            lines.append(f"  {item.source_display}, {item.published or '—'} · {item.url}")
        lines.append("")

    if blog:
        lines += ["━━ ALIGNMENT SCIENCE BLOG " + "━" * 31, ""]
        for item in blog:
            lines.append(f"• {item.title}")
            lines.append(f"  {item.published or '—'} · {item.url}")
        lines.append("")

    if waves:
        lines += ["━━ MAKING WAVES " + "━" * 41, ""]
        for w in waves:
            lines.append(f"• {w['title']}")
            lines.append(
                f"  First seen {w.get('first_seen') or '—'} · now covered by "
                f"{', '.join(w['outlets'][:4]) or 'the press'}"
            )
            if w.get("url"):
                lines.append(f"  {w['url']}")
        lines.append("")

    lines.append(_footer(results, archive_dir, failing, notes))
    return subject, "\n".join(lines)


# ── repro pick ─────────────────────────────────────────────────────


def render_pick(
    pick: Item | None,
    guide_path: str | None,
    escalation: Item | None,
    runners_up: list[Item],
    guides_dir: str,
    reviewed: int,
    estimate: str | None = None,
    days: int = 7,
) -> tuple[str, str]:
    if pick is None:
        subject = "[Research Watch] No pick — nothing cleared the bar"
        body = (
            f"Nothing cleared the bar in the last {days} days "
            f"({reviewed} item(s) reviewed).\n\n"
            "Either nothing was reproducible inside your budget, or nothing scored\n"
            "high enough on signal to be worth the time. This is a normal outcome —\n"
            "no action needed.\n"
        )
        if runners_up:
            body += "\nClosest calls:\n"
            for i in runners_up[:3]:
                body += f"  • {i.title} — {_score_str(i)}\n"
        return subject, body

    subject = f"[Research Watch] Paper to reproduce — {pick.title[:60]}"
    tier_icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(pick.repro_tier or "", "")
    sig = pick.repro_signals or {}

    lines = [
        "━━ THE PICK " + "━" * 45,
        "",
        pick.title,
        f"{pick.source_display} · {pick.published or 'date unknown'}",
    ]
    lines += _links(pick)
    lines += [
        "",
        f"SCORES   signal {pick.scores.get('signal')}  ·  "
        f"artifact {pick.scores.get('artifact_value')}  ·  "
        f"feasibility {pick.scores.get('feasibility')}   →  "
        f"{pick.scores.get('composite')}",
        "",
        "WHY THIS ONE",
    ]
    lines += [f"  {line}" for line in (pick.repro_signals.get("why", "") or "").split("\n") if line]
    lines += [
        "",
        f"REPRO TIER: {tier_icon} {pick.repro_tier}",
        f"  Model prereqs : {', '.join(sig.get('model_prereqs') or []) or 'none beyond a base LM'}",
        f"  SAE dependency: {sig.get('sae_dependency', '?')}",
        f"  Access type   : {sig.get('access_type', '?')}",
        f"  Compute floor : {sig.get('compute_floor', '?')}",
        f"  Artifacts     : {', '.join(sig.get('artifacts') or []) or 'none released'}",
        f"  Smallest model: {sig.get('smallest_viable_model') or 'unclear'}",
        "",
        f"📋 FULL GUIDE → {guide_path}",
        "   Env setup · prerequisites · numbered steps · blog skeleton",
    ]
    if estimate:
        lines.append(f"   Estimated: {estimate}")
    lines.append("")

    if escalation:
        esig = escalation.repro_signals or {}
        lines += [
            "━━ ⚡ WORTH THE COMPUTE " + "━" * 33,
            "",
            f"{escalation.title}",
            f"  feasibility {escalation.scores.get('feasibility')} — "
            f"needs {esig.get('smallest_viable_model') or 'more than local'} "
            f"({esig.get('compute_floor', '?')})",
            f"  signal {escalation.scores.get('signal')} · "
            f"artifact {escalation.scores.get('artifact_value')}",
            f"  {escalation.url}",
            "",
            "  High signal, low effort, but fails the local-compute bar.",
            "  Want to scope renting a GPU for this?",
            "",
        ]

    if runners_up:
        lines += ["━━ ALSO REVIEWED " + "━" * 40, ""]
        for i in runners_up:
            icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(i.repro_tier or "", "  ")
            tier = i.repro_tier or "not graded"
            lines.append(f"• {i.title[:56]}")
            lines.append(f"  {icon} {tier} · {_score_str(i)}")
        lines.append("")

    lines += [RULE, f"Guides in {guides_dir}/"]
    return subject, "\n".join(lines)


def _score_str(i: Item) -> str:
    s = i.scores
    return (
        f"signal {s.get('signal', '-')} · artifact {s.get('artifact_value', '-')} · "
        f"feas {s.get('feasibility', '-')}"
        + (f" → {s['composite']}" if s.get("composite") is not None else "")
    )
