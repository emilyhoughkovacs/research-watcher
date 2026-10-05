"""Source adapters. Deterministic, no LLM, no cost beyond HTTP.

Design rules:
  1. One source failing must never abort the run.
  2. A source that used to return items and now returns zero is a FAILURE,
     not "no news". Silent permanent success is how a watcher dies.
  3. Items are keyed on a stable id (slug / GUID), never on title.
  4. An HTTP 200 is not proof a feed is a feed. Several sites serve their
     SPA shell at .xml paths — parse_ok is what counts, not status_code.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

from .models import Item, SourceResult
from .state import State

log = logging.getLogger(__name__)

SUSPICIOUS_ZERO_THRESHOLD = 3


# ── config ──────────────────────────────────────────────────────────


def load_sources(path, section: str | None = None) -> tuple[dict, list[dict]]:
    with open(Path(path).expanduser()) as f:
        cfg = yaml.safe_load(f)
    defaults = cfg.get("defaults", {}) or {}
    sources = [
        s for s in cfg.get("sources", [])
        if s.get("enabled", True) and (section is None or s.get("section") == section)
    ]
    return defaults, sources


def _session(defaults: dict) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": defaults.get("user_agent", "research-watcher/0.1")})
    return s


def _soup(resp: requests.Response) -> BeautifulSoup:
    # Pass bytes, not text — lets bs4/lxml honour the document's own charset
    # declaration instead of trusting a possibly-wrong Content-Type header.
    return BeautifulSoup(resp.content, "lxml")


# ── date parsing ────────────────────────────────────────────────────

_DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y")
_DATE_RE = re.compile(r"([A-Z][a-z]{2,8}\.?\s+\d{1,2},\s+\d{4})")


def parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    raw = raw.strip().replace(".", "")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        try:
            return date(*map(int, m.groups()))
        except ValueError:
            pass
    return None


_MONTH_YEAR_RE = re.compile(r"^([A-Z][a-z]{2,8})\.?\s+(\d{4})$")


def parse_month(raw: str | None) -> tuple[date | None, str | None]:
    """A day-precise date, or a month-only one as (the 1st, "September 2026").

    The label is what gets printed for a month-only date, so a card
    listed as "September 2026" never shows up as "2026-09-01".
    """
    d = parse_date(raw)
    if d is not None or not raw:
        return d, None
    m = _MONTH_YEAR_RE.match(" ".join(raw.split()))
    if not m:
        return None, None
    for fmt in ("%B %Y", "%b %Y"):
        try:
            d = datetime.strptime(f"{m.group(1)} {m.group(2)}", fmt).date()
            return d, d.strftime("%B %Y")
        except ValueError:
            continue
    return None, None


def _struct_to_date(st) -> date | None:
    if not st:
        return None
    try:
        return date(st.tm_year, st.tm_mon, st.tm_mday)
    except (TypeError, ValueError):
        return None


def _clean_title(text: str, category: str | None) -> tuple[str, date | None]:
    """Strip a leading 'Jun 18, 2026 Frontier Red Team ' prefix off a title.

    Some Anthropic cards expose no heading element, only a flat anchor whose
    text concatenates date + category + title. Returns the cleaned title and
    any date recovered from the prefix.
    """
    text = " ".join(text.split())
    found_date = None
    m = _DATE_RE.match(text)
    if m:
        found_date = parse_date(m.group(1))
        text = text[m.end() :].strip()
    if category and text.lower().startswith(category.lower()):
        text = text[len(category) :].strip()
    return text, found_date


# Anchor text that is navigation, not a title. Trailing arrows are common
# ("Read post →" on blog.eleuther.ai) and must not make it look like a title.
_GENERIC_ANCHOR = re.compile(
    r"^(read more|read paper|read post|learn more|read the (paper|post)|view|more|details)"
    r"\s*[→›»>]*$",
    re.I,
)


# ── fetchers ────────────────────────────────────────────────────────


def _base(src: dict, grade_repro: bool) -> dict:
    """Item fields that come from source config rather than the page."""
    return {
        "source_id": src["id"],
        "source_display": src["display"],
        "area": src["area"],
        "section": src["section"],
        "grade_repro": src.get("grade_repro", grade_repro),
        "tier": src.get("tier", 2),
        "aggregator": src.get("aggregator", False),
    }


def fetch_rss(src: dict, defaults: dict, session: requests.Session) -> list[Item]:
    resp = session.get(src["url"], timeout=defaults.get("timeout_seconds", 20))
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)

    # An HTML page served at a feed URL parses to zero entries with bozo set.
    # Surface that as an error rather than letting it look like a quiet day.
    if not feed.entries and feed.bozo:
        raise ValueError(
            f"not a valid feed (content-type={resp.headers.get('content-type')}, "
            f"bozo={feed.bozo_exception})"
        )

    items = []
    for entry in feed.entries:
        stable = entry.get("id") or entry.get("guid") or entry.get("link", "")
        if not stable:
            continue
        stable = urlparse(stable).path.strip("/") or stable

        summary = entry.get("summary") or entry.get("description") or ""
        items.append(
            Item(
                key=f"{src['id']}:{stable}",
                **_base(src, grade_repro=False),
                title=" ".join((entry.get("title") or "untitled").split()),
                url=entry.get("link", src["url"]),
                published=_struct_to_date(
                    entry.get("published_parsed") or entry.get("updated_parsed")
                ),
                body=BeautifulSoup(summary, "html.parser").get_text(" ", strip=True)[:2000]
                or None,
            )
        )
    return items


def fetch_anthropic_team(src: dict, defaults: dict, session: requests.Session) -> list[Item]:
    """Scrape anthropic.com/research/team/<name>.

    Each card renders 2-3 anchors to the same slug (image, title, "Read more").
    We group by slug and pick the best title across them, because no single
    anchor is reliably the good one: some cards have an <h2>, others expose
    only flat text of the form "Jun 18, 2026 Frontier Red Team <title>".

    Research area comes from config — every item on a team page is that team's.
    """
    resp = session.get(src["url"], timeout=defaults.get("timeout_seconds", 20))
    resp.raise_for_status()
    soup = _soup(resp)
    category = src.get("category_label")

    by_slug: dict[str, dict] = {}

    for a in soup.select('a[href^="/research/"]'):
        slug = a.get("href", "").removeprefix("/research/").strip("/")
        if not slug or slug.startswith("team/"):
            continue

        rec = by_slug.setdefault(slug, {"title": "", "date": None})

        heading = a.find(["h1", "h2", "h3", "h4"])
        if heading:
            cand, _ = _clean_title(heading.get_text(" ", strip=True), category)
            if cand and len(cand) > len(rec["title"]):
                rec["title"] = cand
        else:
            raw = a.get_text(" ", strip=True)
            if raw and not _GENERIC_ANCHOR.match(raw):
                cand, found = _clean_title(raw, category)
                if cand and len(cand) > len(rec["title"]):
                    rec["title"] = cand
                rec["date"] = rec["date"] or found

        if rec["date"] is None:
            t = a.find("time")
            if t is None:
                card = a.find_parent(["article", "li", "div"])
                for _ in range(4):
                    if card is None or t is not None:
                        break
                    t = card.find("time")
                    card = card.parent
            if t is not None:
                rec["date"] = parse_date(t.get_text(strip=True))

    return [
        Item(
            key=f"{src['id']}:{slug}",
            **_base(src, grade_repro=False),
            title=rec["title"],
            url=urljoin("https://www.anthropic.com", f"/research/{slug}"),
            published=rec["date"],
        )
        for slug, rec in by_slug.items()
        if rec["title"]
    ]


def fetch_card_index(src: dict, defaults: dict, session: requests.Session) -> list[Item]:
    """Scrape a card-list index where each entry is `<a href="YYYY/slug/">`.

    Covers transformer-circuits.pub and alignment.anthropic.com, which share
    a layout: an anchor wrapping an <h3> title, a `.byline`, and a
    `.description`. transformer-circuits also carries `data-date` on the
    anchor, which is the authoritative date when present.
    """
    resp = session.get(src["url"], timeout=defaults.get("timeout_seconds", 20))
    resp.raise_for_status()
    soup = _soup(resp)

    items: list[Item] = []
    seen: set[str] = set()
    pattern = re.compile(r"^(?:\./)?(\d{4})/([^/]+)/")

    for a in soup.find_all("a", href=True):
        m = pattern.match(a["href"])
        if not m:
            continue
        stable = f"{m.group(1)}/{m.group(2)}"
        if stable in seen:
            continue

        heading = a.find(["h1", "h2", "h3", "h4"])
        if not heading:
            continue  # nav/image link, not a paper card
        title = " ".join(heading.get_text(" ", strip=True).split())
        if not title:
            continue

        # Authoritative when present; otherwise look for a date in the card.
        published = parse_date(a.get("data-date"))
        if published is None:
            dm = _DATE_RE.search(a.get_text(" ", strip=True))
            if dm:
                published = parse_date(dm.group(1))
        # Deliberately no year-only fallback: a wrong date is worse than none.

        desc = a.select_one(".description")
        byline = a.select_one(".byline")
        body = " ".join(
            x.get_text(" ", strip=True) for x in (byline, desc) if x is not None
        ).strip()

        seen.add(stable)
        items.append(
            Item(
                key=f"{src['id']}:{stable}",
                **_base(src, grade_repro=True),
                title=title,
                url=urljoin(src["url"], a["href"]),
                published=published,
                body=body or None,
            )
        )
    return items


def fetch_link_prefix(src: dict, defaults: dict, session: requests.Session) -> list[Item]:
    """Scrape an index whose posts are all `<a href="/<prefix>/<slug>">`.

    Used for sites with no feed and no year in the URL (e.g. safe.ai/blog).

    Options (all default off):
      nested        accept slugs containing "/" (transluce.org lists its
                    Docent blog at /docent/blog/<slug> next to /<slug>)
      require_date  drop anchors with no parseable date — the cheapest way
                    to tell post cards from nav links when the prefix is "/"
    """
    resp = session.get(src["url"], timeout=defaults.get("timeout_seconds", 20))
    resp.raise_for_status()
    soup = _soup(resp)
    prefix = src.get("link_prefix", "/blog/")
    nested = src.get("nested", False)
    require_date = src.get("require_date", False)

    items: list[Item] = []
    seen: set[str] = set()

    for a in soup.select(f'a[href^="{prefix}"]'):
        slug = a["href"].split("#")[0].removeprefix(prefix).strip("/")
        if not slug or slug in seen or ("/" in slug and not nested):
            continue

        heading = a.find(["h1", "h2", "h3", "h4"])
        title = " ".join(
            (heading or a).get_text(" ", strip=True).split()
        )
        if not title or _GENERIC_ANCHOR.match(title):
            # A "Read post →" link whose title is a sibling heading in the
            # same card (blog.eleuther.ai). Only trusted when the card has a
            # single heading — otherwise the "card" is a list container.
            card = a.find_parent(["article", "li", "div"])
            headings = card.find_all(["h1", "h2", "h3", "h4"]) if card else []
            if len(headings) != 1:
                continue
            title = " ".join(headings[0].get_text(" ", strip=True).split())
            if not title:
                continue

        # The anchor's own text first: when every card shares one parent
        # container (transluce.org), the parent's first date is the newest
        # card's, and would be stamped on all of them.
        published = None
        dm = _DATE_RE.search(a.get_text(" ", strip=True))
        if dm is None:
            card = a.find_parent(["article", "li", "div"])
            if card:
                dm = _DATE_RE.search(card.get_text(" ", strip=True))
        if dm:
            published = parse_date(dm.group(1))
        if require_date and published is None:
            continue

        seen.add(slug)
        items.append(
            Item(
                key=f"{src['id']}:{slug}",
                **_base(src, grade_repro=False),
                title=title,
                url=urljoin(src["url"], a["href"]),
                published=published,
            )
        )
    return items


def _card_key(src: dict, url: str) -> str:
    # The link, not the title: a card's title is the model name, and an
    # addendum for the same model is a separate card at a separate URL.
    p = urlparse(url)
    stable = f"{p.netloc.removeprefix('www.')}{p.path}".rstrip("/")
    return f"{src['id']}:{stable}"


_DATE_PREFIX = re.compile(r"^(updated|published)\s+", re.I)


def fetch_model_card_table(src: dict, defaults: dict, session: requests.Session) -> list[Item]:
    """Scrape a system/model card index laid out as table rows.

    anthropic.com/system-cards and deepmind.google/models/model-cards share
    a shape: one <tr> per card holding the model name, a date cell, and a
    link to the card. Anthropic dates to the month ("September 2026"),
    DeepMind to the day ("Updated 24 September 2026").

    Rows come back in page order, which is the lab's own newest-first
    ordering — the tiebreak when two cards share a month.

    Options:
      title_suffix  appended to the model name ("Claude Opus 5.5" →
                    "Claude Opus 5.5 system card")
    """
    resp = session.get(src["url"], timeout=defaults.get("timeout_seconds", 20))
    resp.raise_for_status()
    soup = _soup(resp)
    suffix = src.get("title_suffix", "")

    items: list[Item] = []
    seen: set[str] = set()
    for row in soup.find_all("tr"):
        a = row.find("a", href=True)
        cells = row.find_all(["th", "td"])
        if a is None or len(cells) < 2:
            continue  # header row
        url = urljoin(src["url"], a["href"]).split("#")[0]
        key = _card_key(src, url)
        if key in seen:
            continue
        name = " ".join(cells[0].get_text(" ", strip=True).split())
        if not name:
            continue
        published, label = None, None
        for cell in cells[1:]:
            raw = _DATE_PREFIX.sub("", " ".join(cell.get_text(" ", strip=True).split()))
            published, label = parse_month(raw)
            if published:
                break
        seen.add(key)
        items.append(
            Item(
                key=key,
                **_base(src, grade_repro=False),
                title=f"{name}{suffix}",
                url=url,
                published=published,
                date_label=label,
            )
        )
    return items


def _astro(value):
    """Decode Astro's island-prop serialization: [0, value] or [1, array]."""
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], int):
        kind, inner = value
        if kind == 1:
            return [_astro(v) for v in inner]
        if kind == 0 and isinstance(inner, dict):
            return {k: _astro(v) for k, v in inner.items()}
        return inner
    return value


def fetch_openai_safety_hub(src: dict, defaults: dict, session: requests.Session) -> list[Item]:
    """Scrape deploymentsafety.openai.com, OpenAI's system card index.

    Only the first few cards render as links; the full list is the props
    of the `UpdatesList` Astro island, as JSON. openai.com/system-cards
    would be simpler but answers scripts with 403.
    """
    resp = session.get(src["url"], timeout=defaults.get("timeout_seconds", 20))
    resp.raise_for_status()
    soup = _soup(resp)
    island = soup.find("astro-island", attrs={"component-export": "UpdatesList"})
    if island is None or not island.get("props"):
        raise ValueError("no UpdatesList island — the page layout changed")
    # The props object itself is plain JSON; its values are encoded.
    entries = _astro(json.loads(island["props"]).get("items")) or []

    items: list[Item] = []
    for e in entries:
        if not isinstance(e, dict) or not e.get("href") or not e.get("title"):
            continue
        if e.get("published") is False or e.get("archived"):
            continue
        url = urljoin(src["url"], e["href"])
        items.append(
            Item(
                key=_card_key(src, url),
                **_base(src, grade_repro=False),
                title=" ".join(e["title"].split()),
                url=url,
                published=parse_date(e.get("date")),
                body=e.get("excerpt") or None,
            )
        )
    return items


FETCHERS = {
    "rss": fetch_rss,
    "anthropic_team": fetch_anthropic_team,
    "card_index": fetch_card_index,
    "link_prefix": fetch_link_prefix,
    "model_card_table": fetch_model_card_table,
    "openai_safety_hub": fetch_openai_safety_hub,
}


# ── orchestration ───────────────────────────────────────────────────


def fetch_all(
    sources_path, state: State | None = None, section: str | None = None
) -> list[SourceResult]:
    """Fetch every enabled source (or one section's). Failures are captured, never raised."""
    defaults, sources = load_sources(sources_path, section)
    session = _session(defaults)
    results: list[SourceResult] = []

    for src in sources:
        fetcher = FETCHERS.get(src["kind"])
        if fetcher is None:
            results.append(
                SourceResult(src["id"], ok=False, error=f"unknown kind: {src['kind']}")
            )
            continue
        try:
            items = fetcher(src, defaults, session)
            result = SourceResult(src["id"], items=items, ok=True)

            # Rule 2: history + zero items now == broken parser, not a quiet day.
            if not items and state is not None:
                prior = state.seen_count(src["id"])
                if prior >= SUSPICIOUS_ZERO_THRESHOLD:
                    result.ok = False
                    result.suspicious_zero = True
                    result.error = (
                        f"returned 0 items but {prior} seen previously — parser likely broken"
                    )
            results.append(result)
            log.info("%s: %d items", src["id"], len(items))
        except Exception as exc:  # noqa: BLE001 — isolation is the whole point
            log.warning("%s FAILED: %s", src["id"], exc)
            results.append(
                SourceResult(src["id"], ok=False, error=f"{type(exc).__name__}: {exc}")
            )

    return results
