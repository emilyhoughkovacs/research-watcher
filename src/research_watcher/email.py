"""SMTP delivery and email rendering.

The digest goes out as plain text plus an HTML alternative built from the
same blocks (see `_Doc`), so the two can't say different things. The HTML
only adds type: real headings, bold labels, text that reflows to the
window. The plain text is the fallback, and what the tests read.

Neither is hard-wrapped: a mail client reflows a long line to fit, but it
can't un-break a line wrapped at 72 characters.

Gmail app password over STARTTLS — no OAuth, so this works headless in CI.
"""

from __future__ import annotations

import html
import logging
import re
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage

from .dedupe import arxiv_id
from .models import Item, SourceResult

log = logging.getLogger(__name__)

# "Gemini 3.8 Audio (Live, Live Extended Thinking, Flash TTS, ...)" — a
# variant list belongs in the body, not the subject line.
_PARENS = re.compile(r"\s*\([^)]*\)")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
RULE = "─" * 62


def send(
    subject: str, body: str, address: str, app_password: str, html_body: str | None = None
) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = address
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(address, app_password)
        smtp.send_message(msg)
    log.info("sent: %s", subject)


# ── shared bits ─────────────────────────────────────────────────────

# Inline styles: Gmail drops <style> blocks in some views, never inline ones.
_FONT = ("font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,"
         "sans-serif;font-size:14px;line-height:1.5;color:#1f1f1f")
_STYLE = {
    "section": "font-size:22px;font-weight:700;margin:28px 0 12px;padding-bottom:6px;"
               "border-bottom:2px solid #1f1f1f",
    "title": "font-size:19px;font-weight:700;margin:22px 0 2px",
    "label": "font-size:16px;font-weight:700;margin:16px 0 4px",
    "item": "font-weight:600;margin:12px 0 0",
    "meta": "color:#666;margin:0",
    "p": "margin:0 0 8px",
    "ul": "margin:0 0 8px;padding-left:22px",
    "li": "margin:0 0 6px",
    "foot": "color:#777;font-size:12px;margin:0",
}
_URL = re.compile(r"https?://[^\s<>\"]+")


def _inline(text: str) -> str:
    """Escape, then turn bare URLs into links."""
    return _URL.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', html.escape(text))


class _Doc:
    """One email built block by block, rendered as plain text and as HTML.

    Every block appends to both, so the HTML can only differ from the plain
    text in typography, never in content.
    """

    def __init__(self) -> None:
        self.text: list[str] = []
        self._html: list[str] = []
        self._bullets: list[str] = []

    def _add(self, text: str | None, html_part: str | None) -> None:
        if self._bullets:
            items = "".join(f'<li style="{_STYLE["li"]}">{b}</li>' for b in self._bullets)
            self._html.append(f'<ul style="{_STYLE["ul"]}">{items}</ul>')
            self._bullets = []
        if text is not None:
            self.text.append(text)
        if html_part is not None:
            self._html.append(html_part)

    def section(self, name: str) -> None:
        bar = f"━━ {name} "
        self._add(bar + "━" * (57 - len(bar)), f'<h2 style="{_STYLE["section"]}">'
                  f"{html.escape(name.title())}</h2>")
        self.blank()

    def heading(self, text: str, style: str = "title") -> None:
        """A card title ("title") or a block label like "Risk level" ("label")."""
        tag = "h3" if style == "title" else "h4"
        self._add(text, f'<{tag} style="{_STYLE[style]}">{html.escape(text)}</{tag}>')

    def line(self, text: str, style: str = "meta", indent: str = "") -> None:
        self._add(indent + text, f'<div style="{_STYLE[style]}">{_inline(text)}</div>')

    def para(self, text: str, indent: str = "") -> None:
        text = " ".join(text.split())
        self._add(indent + text, f'<p style="{_STYLE["p"]}">{_inline(text)}</p>')

    def bullet(self, text: str, indent: str = "") -> None:
        text = " ".join(text.split())
        self._add(f"{indent}• {text}", None)
        self._bullets.append(_inline(text))

    def blank(self) -> None:
        self.text.append("")

    def rule(self) -> None:
        self._add(RULE, '<hr style="border:0;border-top:1px solid #ddd;margin:24px 0 10px">')

    def render(self) -> tuple[str, str]:
        self._add(None, None)  # close a trailing list
        body = "".join(self._html)
        return "\n".join(self.text), (
            f'<!doctype html><html><head><meta charset="utf-8"></head>'
            f'<body style="margin:0;padding:16px">'
            f'<div style="{_FONT};max-width:720px">{body}</div></body></html>'
        )


def _links(item: Item, doc: _Doc) -> None:
    # Sweep hits and arXiv items link to the paper itself, not a blog post.
    label = "Paper" if item.source_id == "news-sweep" or arxiv_id(item.url) else "Blog"
    doc.line(f"→ {label + ':':<7}{item.url}", indent="   ")
    if item.paper_url:
        doc.line(f"→ Paper: {item.paper_url}", indent="   ")
    if item.code_url:
        doc.line(f"→ Code:  {item.code_url}", indent="   ")


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
    doc: _Doc,
    results: list[SourceResult],
    archive_dir: str,
    failing: list[tuple[str, int]],
    notes: DigestNotes | None = None,
) -> None:
    notes = notes or DigestNotes()
    lines: list[str] = []
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
    doc.blank()
    doc.rule()
    for line in lines:
        doc.line(line, style="foot")


def _cards_section(cards: list[Item], doc: _Doc) -> None:
    doc.section("SYSTEM CARDS")
    for card in cards:
        doc.heading(card.title)
        doc.line(f"{card.source_display} · {card.when or 'date unknown'}")
        doc.line(f"→ Card:  {card.url}")
        doc.blank()
        for label, text in (
            ("Summary", card.abstract),
            ("Risk level", card.card.get("risk_level")),
        ):
            if text:
                doc.heading(label, style="label")
                doc.para(text)
                doc.blank()
        if card.bullets:
            doc.heading("Notable evaluations", style="label")
            for finding in card.bullets:
                doc.bullet(finding)
            doc.blank()
        if card.card.get("changes"):
            doc.heading("What changed", style="label")
            doc.para(card.card["changes"])
            doc.blank()


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
    cards: list[Item] | None = None,
) -> tuple[str, str, str]:
    """Returns (subject, plain-text body, HTML body).

    `waves` are already-sent items now getting coverage: dicts with title,
    url, first_seen, outlets. `cards` are new system cards, shown above
    the research in their own section and never ranked against it.
    """
    cards = cards or []
    n = len(top) + len(rest)
    areas = sorted({i.area for i in top + rest})
    area_str = ", ".join(a.replace("-", " ").title() for a in areas[:3])
    # Daily is the default and goes unlabelled — a subject that reads the same
    # every day is easier to filter on. Anything rarer says so, because a
    # monthly digest arriving unannounced looks like a backlog.
    label = "" if cadence == "daily" else f"{cadence.capitalize()} · "
    parts = []
    if cards:
        # Model names, not a count: which model shipped is the news.
        models = ", ".join(
            _PARENS.sub("", c.card.get("model") or c.title) for c in cards
        )
        parts.append(f"System card{'s' if len(cards) > 1 else ''}: {models}")
    if n:
        parts.append(f"{n} new · {area_str}")
    subject = f"[Research Watch] {label}{' · '.join(parts)}"
    if failing or (notes and notes.stale_since):
        subject = f"⚠ {subject}"

    doc = _Doc()

    if cards:
        _cards_section(cards, doc)

    if top:
        doc.section(f"TOP {len(top)}")
        for idx, item in enumerate(top, 1):
            doc.line(f"{idx}. {item.title}", style="item")
            via = f" · found via {', '.join(item.found_via[:3])}" if item.found_via else ""
            doc.line(f"{item.source_display} · {item.published or 'date unknown'}{via}",
                     indent="   ")
            _links(item, doc)
            doc.blank()
            if item.abstract:
                doc.para(item.abstract, indent="   ")
            else:
                # Summary failed or was declined; bullets are the fallback.
                for b in item.bullets:
                    doc.bullet(b, indent="   ")
            if item.reach:
                doc.blank()
                doc.para(f"Reach: {item.reach}", indent="   ")
            doc.blank()

    also = [i for i in rest if i.section != "alignment_blog"]
    blog = [i for i in top + rest if i.section == "alignment_blog"]
    # An alignment-blog item promoted into TOP is shown there, not twice.
    blog = [i for i in blog if i not in top]

    if also:
        doc.section("ALSO NEW")
        for item in also:
            doc.line(f"• {item.title}", style="item")
            doc.line(f"{item.source_display}, {item.published or '—'} · {item.url}", indent="  ")
        doc.blank()

    if blog:
        doc.section("ALIGNMENT SCIENCE BLOG")
        for item in blog:
            doc.line(f"• {item.title}", style="item")
            doc.line(f"{item.published or '—'} · {item.url}", indent="  ")
        doc.blank()

    if waves:
        doc.section("MAKING WAVES")
        for w in waves:
            doc.line(f"• {w['title']}", style="item")
            doc.line(
                f"First seen {w.get('first_seen') or '—'} · now covered by "
                f"{', '.join(w['outlets'][:4]) or 'the press'}",
                indent="  ",
            )
            if w.get("url"):
                doc.line(w["url"], indent="  ")
        doc.blank()

    _footer(doc, results, archive_dir, failing, notes)
    body, html_body = doc.render()
    return subject, body, html_body


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
