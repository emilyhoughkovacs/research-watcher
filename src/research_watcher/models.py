"""Core data structures shared across fetch / summarize / friday stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Item:
    """One publication, from first sighting through to weekly grading.

    Fields are populated in stages: `fetch` fills identity and links,
    `summarize` fills bullets and repro_signals, `friday` fills scores
    and repro_tier. Anything not yet assigned stays None so a half-filled
    item is never mistaken for a fully graded one.
    """

    # ── identity (fetch) ────────────────────────────────────────────
    key: str  # "source_id:stable_id" — never keyed on title, titles get edited
    source_id: str
    source_display: str
    area: str
    section: str  # top | also | alignment_blog
    grade_repro: bool
    title: str
    url: str
    published: date | None = None

    # ── enrichment (summarize) ──────────────────────────────────────
    paper_url: str | None = None
    code_url: str | None = None
    body: str | None = None  # fetched full text, not persisted
    bullets: list[str] = field(default_factory=list)
    repro_signals: dict = field(default_factory=dict)

    # ── grading (friday) ────────────────────────────────────────────
    scores: dict = field(default_factory=lambda: {
        "signal": None,
        "artifact_value": None,
        "feasibility": None,
        "composite": None,
    })
    repro_tier: str | None = None  # GREEN | YELLOW | RED
    picked: bool = False

    @property
    def slug(self) -> str:
        """Filesystem-safe slug derived from the stable id, not the title."""
        tail = self.key.split(":", 1)[1]
        cleaned = tail.strip("/").replace("/", "-").replace("index.html", "").strip("-")
        return cleaned or "untitled"

    @property
    def archive_name(self) -> str:
        d = (self.published or date.today()).isoformat()
        return f"{d}-{self.slug}.md"


@dataclass
class SourceResult:
    """Outcome of fetching one source. Failures never abort the run."""

    source_id: str
    items: list[Item] = field(default_factory=list)
    ok: bool = True
    error: str | None = None
    # A source that previously returned many items and now returns zero is
    # treated as a failure, not as "no news" — see fetch.check_suspicious_zero.
    suspicious_zero: bool = False
