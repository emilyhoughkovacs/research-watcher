"""SMTP delivery and plain-text email rendering.

Plain text on purpose: it renders identically everywhere, is readable on a
phone lock screen, and can't break in a way that hides content.

Gmail app password over STARTTLS — no OAuth, so this works headless in CI.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

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
    out = [f"   → Blog:  {item.url}"]
    if item.paper_url:
        out.append(f"   → Paper: {item.paper_url}")
    if item.code_url:
        out.append(f"   → Code:  {item.code_url}")
    return out


def _footer(results: list[SourceResult], archive_dir: str, failing: list[tuple[str, int]]) -> str:
    lines = ["", RULE]
    errored = [r for r in results if not r.ok]
    lines.append(f"Archived to {archive_dir}/")
    if errored:
        detail = ", ".join(f"{r.source_id} ({(r.error or '')[:40]})" for r in errored)
        lines.append(f"{len(errored)} source(s) errored: {detail}")
    if failing:
        for sid, n in failing:
            lines.append(f"⚠ {sid} has failed {n} consecutive runs — check the parser")
    return "\n".join(lines)


# ── daily digest ────────────────────────────────────────────────────


def render_daily(
    top: list[Item],
    rest: list[Item],
    results: list[SourceResult],
    archive_dir: str,
    failing: list[tuple[str, int]],
) -> tuple[str, str]:
    """Returns (subject, body)."""
    n = len(top) + len(rest)
    areas = sorted({i.area for i in top + rest})
    area_str = ", ".join(a.replace("-", " ").title() for a in areas[:3])
    subject = f"[Research Watch] {n} new · {area_str}"
    if failing:
        subject = f"⚠ {subject}"

    lines: list[str] = []

    if top:
        lines += ["━━ TOP " + str(len(top)) + " " + "━" * 48, ""]
        for idx, item in enumerate(top, 1):
            lines.append(f"{idx}. {item.title}")
            lines.append(f"   {item.source_display} · {item.published or 'date unknown'}")
            lines += _links(item)
            for b in item.bullets:
                lines.append(f"   • {b}")
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

    lines.append(_footer(results, archive_dir, failing))
    return subject, "\n".join(lines)


# ── friday pick ─────────────────────────────────────────────────────


def render_friday(
    pick: Item | None,
    guide_path: str | None,
    escalation: Item | None,
    runners_up: list[Item],
    guides_dir: str,
    week_count: int,
    estimate: str | None = None,
) -> tuple[str, str]:
    if pick is None:
        subject = "[Research Watch] No pick this week"
        body = (
            f"Nothing cleared the bar this week ({week_count} item(s) reviewed).\n\n"
            "Either nothing was reproducible inside your budget, or nothing scored\n"
            "high enough on signal to be worth the day. This is a normal outcome —\n"
            "no action needed.\n"
        )
        if runners_up:
            body += "\nClosest calls:\n"
            for i in runners_up[:3]:
                body += f"  • {i.title} — {_score_str(i)}\n"
        return subject, body

    subject = f"[Research Watch] Paper of the week — {pick.title[:60]}"
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
        f"fellows {pick.scores.get('fellows')}  ·  "
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
            f"fellows {escalation.scores.get('fellows')}",
            f"  {escalation.url}",
            "",
            "  High signal, low effort, but fails the local-compute bar.",
            "  Want to scope renting a GPU for this?",
            "",
        ]

    if runners_up:
        lines += ["━━ ALSO THIS WEEK " + "━" * 39, ""]
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
        f"signal {s.get('signal', '-')} · fellows {s.get('fellows', '-')} · "
        f"feas {s.get('feasibility', '-')}"
        + (f" → {s['composite']}" if s.get("composite") is not None else "")
    )
