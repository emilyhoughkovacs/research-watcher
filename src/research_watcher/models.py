"""Core data structures shared across fetch / summarize / pick stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Item:
    """One publication, from first sighting through to repro grading.

    Fields are populated in stages: `fetch` fills identity and links,
    `summarize` fills bullets, abstract, impact and repro_signals, the
    reach pass fills reach, `pick` fills feasibility and repro_tier.
    Anything not yet assigned stays None so a half-filled item is never
    mistaken for a fully graded one.
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
    # Which copy wins when the same work arrives from two sources in one
    # run: originals beat aggregators (AF crossposts), then lower tier.
    tier: int = 2
    aggregator: bool = False

    # ── enrichment (summarize) ──────────────────────────────────────
    paper_url: str | None = None
    code_url: str | None = None
    body: str | None = None  # fetched full text, not persisted
    bullets: list[str] = field(default_factory=list)
    abstract: str | None = None  # plain-language what/how/found, for top-N
    reach: str | None = None  # one line on coverage, from the search pass
    found_via: list[str] = field(default_factory=list)  # outlets, sweep items only
    repro_signals: dict = field(default_factory=dict)

    # ── grading (pick) ────────────────────────────────────────────
    scores: dict = field(default_factory=lambda: {
        "impact": None,  # digest rank: importance to the safety community
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
        # Keys are "source:path", but an archive file edited by hand may not
        # be — fall back to the whole key rather than crashing on it.
        tail = self.key.split(":", 1)[-1]
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
