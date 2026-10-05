"""Deciding what counts as new.

Three layers, each of which can only exclude:

  1. `seen` keys — the record of what's been reported. Exact `source:id`.
  2. Content fingerprints — the same work arriving from a second source
     (a Redwood post and its Alignment Forum crosspost; an Anthropic paper
     on both its team page and transformer-circuits). Keys differ, titles
     and URLs don't.
  3. Date floor — a backstop for when 1 and 2 fail (state not persisting,
     a source changing its key scheme). Computed from today, not from
     `state.last_run`, because a state file that isn't persisting has a
     stale `last_run` and a floor based on it would protect nothing.

Items rejected by 2 or 3 are marked seen silently (not reported), so
they're decided once rather than re-examined every run.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urlparse

from .models import Item, SourceResult
from .state import State

# Window + slack, in days. Slack absorbs index lag (a post dated yesterday
# that the site only listed today) and AF posts crossing the karma bar a
# day or two after posting.
DATE_FLOOR_DAYS = {"daily": 4, "weekly": 10, "monthly": 35}

# Anthropic dates system cards to the month, which reads as the 1st: a card
# listed on the 28th would look 27 days old. Cards get a window wide enough
# for that; the seen keys still catch any card already reported.
SYSTEM_CARD_FLOOR_DAYS = 35

# Titles shorter than this aren't distinctive enough to merge two items on
# title alone ("Introduction", "Request for proposals").
MIN_TITLE_WORDS = 4

_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})", re.I)
_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def normalize_title(title: str | None) -> str:
    t = unicodedata.normalize("NFKC", title or "").translate(_QUOTES).casefold()
    t = re.sub(r"\$[^$]*\$", " ", t)          # inline LaTeX
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


def arxiv_id(url: str | None) -> str | None:
    m = _ARXIV_RE.search(url or "")
    return m.group(1) if m else None


def canonical_url(url: str | None) -> str | None:
    """Scheme-, www-, query- and trailing-slash-insensitive; arXiv by ID."""
    if not url:
        return None
    aid = arxiv_id(url)
    if aid:
        return f"arxiv:{aid}"
    p = urlparse(url)
    host = (p.netloc or "").lower().removeprefix("www.")
    if not host:
        return None
    return f"{host}{p.path.rstrip('/')}"


def fingerprints(title: str | None, url: str | None) -> set[str]:
    fps = set()
    t = normalize_title(title)
    if len(t.split()) >= MIN_TITLE_WORDS:
        fps.add(f"t:{t}")
    u = canonical_url(url)
    if u:
        fps.add(f"u:{u}")
    return fps


def item_fingerprints(item: Item) -> set[str]:
    fps = fingerprints(item.title, item.url)
    if item.paper_url:
        fps |= {fp for fp in fingerprints(None, item.paper_url)}
    return fps


def too_old(item: Item, today: date, floor_days: int) -> bool:
    if item.is_card:
        floor_days = max(floor_days, SYSTEM_CARD_FLOOR_DAYS)
    return item.published is not None and item.published < today - timedelta(days=floor_days)


@dataclass
class Selection:
    new: list[Item] = field(default_factory=list)
    duplicates: list[tuple[Item, str]] = field(default_factory=list)  # (item, key it matched)
    too_old: list[Item] = field(default_factory=list)


def _priority(item: Item) -> tuple:
    # Originals before aggregators, then lower tier first. sorted() is
    # stable, so ties keep sources.yaml order.
    return (item.aggregator, item.tier)


def select_new(
    results: list[SourceResult], state: State, today: date, floor_days: int
) -> Selection:
    """Apply layers 1-3. Marks rejected items seen (unreported) in `state`."""
    sel = Selection()
    candidates = [
        item for r in results if r.ok for item in r.items if state.is_new(item.key)
    ]
    run_fps: dict[str, str] = {}

    for item in sorted(candidates, key=_priority):
        fps = item_fingerprints(item)
        match = state.match_fingerprint(fps) or next(
            (run_fps[fp] for fp in fps if fp in run_fps), None
        )
        if match:
            sel.duplicates.append((item, match))
            state.mark_seen(item.key, item.title, item.published, url=item.url)
            continue
        if too_old(item, today, floor_days):
            sel.too_old.append(item)
            state.mark_seen(item.key, item.title, item.published, url=item.url)
            continue
        sel.new.append(item)
        for fp in fps:
            run_fps[fp] = item.key
    return sel
