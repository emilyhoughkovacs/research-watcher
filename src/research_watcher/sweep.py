"""News sweep: find research that broke out, then resolve it to the paper.

The configured sources only see what a fixed set of labs publish. The
Pain Axis (arXiv 2609.16247, independent authors) never appeared in any of
them and reached the user through the news first. This stage asks Claude,
with web search, what AI safety research got mainstream or broad attention
in the last few days.

News is only how a paper is *discovered*, never the content. Every
candidate is resolved to the research artifact itself — an arXiv abstract,
the lab's own post, a journal landing page — and verified before anything
is summarized from it. A candidate that can't be verified is skipped and
named in the footer: summarizing the wrong paper is worse than missing one,
but a miss should never be silent.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import feedparser
import requests
from anthropic import Anthropic
from bs4 import BeautifulSoup

from . import llm
from .costs import Ledger
from .dedupe import arxiv_id, canonical_url, item_fingerprints, normalize_title, too_old
from .fetch import parse_date
from .models import Item
from .state import State

log = logging.getLogger(__name__)

SWEEP_DAYS = 3
# Measured 2026-10-02: at 5, generic queries plus a repeated one used the
# whole allowance and found nothing. Searches are $0.01; results are the cost.
SWEEP_MAX_USES = 8
# Coverage lags the paper — Pain Axis: arXiv Sep 14, Euronews Sep 22. The
# digest's 4-day floor would reject exactly the case this stage exists for,
# so sweep hits get a window sized to press lag instead. Older work that
# trends again is old news unless it was already sent (→ Making waves).
SWEEP_FLOOR_DAYS = 30
SWEEP_MAX_CANDIDATES = 8
SWEEP_EFFORT = "medium"

# arXiv's API terms ask for no more than one request every 3 seconds.
ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_DELAY_S = 3.0

AREAS = ["interpretability", "alignment", "evals", "red-team", "societal-impacts", "other"]

_SWEEP_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The research's own title, not a headline",
                    },
                    "authors": {"type": "array", "items": {"type": "string"}},
                    "primary_url": {
                        "type": ["string", "null"],
                        "description": "The research artifact itself. Null if not found.",
                    },
                    "venue": {
                        "type": "string",
                        "description": "Where it was published: 'arXiv', 'Anthropic', "
                        "'Nature', ...",
                    },
                    "area": {"type": "string", "enum": AREAS},
                    "outlets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "News outlets or venues that covered it",
                    },
                },
                "required": ["title", "authors", "primary_url", "venue", "area", "outlets"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}

_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["yes", "no", "unsure"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}


def window(today: date, last_sweep: str | None, every_days: int) -> int | None:
    """Days to sweep back, or None if no sweep is due this run.

    Sweeping every N days (cost lever, `email.sweep_every_days`) must not
    leave gaps: the window stretches to cover everything since the last
    sweep, with a 1-day overlap, up to 10 days.
    """
    if last_sweep is None:
        return SWEEP_DAYS
    since = (today - date.fromisoformat(last_sweep)).days
    if since < every_days:
        return None
    return min(max(SWEEP_DAYS, since + 1), 10)


@dataclass
class Candidate:
    title: str
    authors: list[str]
    primary_url: str | None
    venue: str
    area: str
    outlets: list[str]


@dataclass
class Resolved:
    title: str
    url: str
    published: date | None
    venue: str


@dataclass
class SweepResult:
    new: list[Item] = field(default_factory=list)
    waves: list[str] = field(default_factory=list)  # seen keys queued for Making waves
    merged: list[Item] = field(default_factory=list)  # run items that got outlets attached
    unresolved: list[tuple[str, str]] = field(default_factory=list)  # (title, reason)
    too_old: list[Item] = field(default_factory=list)


# ── discover ────────────────────────────────────────────────────────


def discover(
    client: Anthropic, today: date, ledger: Ledger | None = None, days: int = SWEEP_DAYS
) -> list[Candidate]:
    start = today - timedelta(days=days)
    prompt = f"""Today is {today.isoformat()}. Find AI safety research that \
got mainstream news coverage or broad attention between {start.isoformat()} \
and {today.isoformat()}.

In scope: research artifacts — papers, technical reports, lab research \
posts, model evaluations — on AI safety, alignment, interpretability, \
dangerous-capability evaluations, model welfare, or AI risk, that news \
outlets covered or that was widely discussed.

Out of scope: policy and regulation news, funding and company news, \
product launches, op-eds, and AI news with no underlying research artifact.

Search like a news editor looking for this week's coverage, not a \
librarian: generic queries like "AI safety research" return evergreen \
pages. Use queries that surface recent reporting on findings, e.g. \
"AI models study researchers found", "AI deception study", "AI agents \
study", "AI Security Institute evaluation", "interpretability research \
finds", "arXiv study AI models", and findings from labs and evaluators \
(Anthropic, OpenAI, Google DeepMind, UK AISI, METR, Apollo Research, \
Redwood Research). Every search must be a different query — never repeat \
one. Research from those labs counts too; include it.

You have {SWEEP_MAX_USES} searches. Spend about half finding stories and \
the rest finding each story's primary research artifact (search its \
title with "arXiv" or the lab's name). A candidate without a primary URL \
is almost always wasted, so three resolved candidates beat eight \
unresolved ones.

For each item, find the research itself and give its primary URL: the arXiv \
abstract page (arxiv.org/abs/<id>), the authoring organization's own \
research page, OpenReview, or the journal's landing page. Never a news \
article, and not a PDF when a landing page exists. If you can't find the \
primary artifact, set primary_url to null rather than guessing — a wrong \
URL is worse than none. Use the research's own title, not a headline.

Up to {SWEEP_MAX_CANDIDATES} items. An empty list is a fine answer on a \
quiet day."""

    resp = llm.create(
        client,
        stage="sweep",
        ledger=ledger,
        max_tokens=12000,
        tools=[llm.web_search(SWEEP_MAX_USES)],
        thinking={"type": "adaptive"},
        output_config={
            "effort": SWEEP_EFFORT,
            "format": {"type": "json_schema", "schema": _SWEEP_SCHEMA},
        },
        messages=[{"role": "user", "content": prompt}],
    )
    data = llm.json_output(resp)
    if data is None:
        log.warning("sweep returned nothing (stop_reason=%s)", resp.stop_reason)
        return []
    cands = [Candidate(**c) for c in data.get("candidates", [])][:SWEEP_MAX_CANDIDATES]
    log.info("sweep: %d candidate(s)", len(cands))
    return cands


# ── resolve ─────────────────────────────────────────────────────────


def _tokens(title: str | None) -> set[str]:
    return set(normalize_title(title).split())


def title_similarity(a: str | None, b: str | None) -> float:
    """Jaccard similarity over normalized title tokens."""
    A, B = _tokens(a), _tokens(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def is_title_prefix(a: str | None, b: str | None, min_words: int = 3) -> bool:
    """One title is the other with a subtitle dropped.

    "The Pain Axis" vs "The Pain Axis: LLMs Represent…". A *prefix*, not
    mere token containment: three common words ("LLMs represent harm")
    appear inside plenty of long titles that are different papers.
    """
    na, nb = normalize_title(a), normalize_title(b)
    short, long_ = sorted((na, nb), key=len)
    return len(short.split()) >= min_words and (long_ + " ").startswith(short + " ")


def _surnames(names: list[str]) -> set[str]:
    out = set()
    for n in names:
        # "Tagliabue, Valen" and "Valen Tagliabue" both → "tagliabue"
        last = n.split(",")[0] if "," in n else (n.split() or [""])[-1]
        last = normalize_title(last)
        if len(last) >= 2:
            out.add(last)
    return out


def match(
    cand_title: str, cand_authors: list[str], page_titles: list[str], page_authors: list[str]
) -> str:
    """'yes' | 'no' | 'ambiguous'. Thresholds are spec D.2."""
    best_j = max((title_similarity(cand_title, t) for t in page_titles), default=0.0)
    prefix = any(is_title_prefix(cand_title, t) for t in page_titles)
    authors_overlap = bool(_surnames(cand_authors) & _surnames(page_authors))

    if best_j >= 0.85 or prefix:
        return "yes"
    if best_j >= 0.6 and authors_overlap:
        return "yes"
    if best_j >= 0.4 or authors_overlap:
        return "ambiguous"
    return "no"


_SITE_SUFFIX = re.compile(r"\s+[|–—-]\s+[^|–—-]{2,40}$")
_ARXIV_PREFIX = re.compile(r"^\[\d{4}\.\d{4,5}(v\d+)?\]\s*")


def _clean_page_title(t: str) -> str:
    t = " ".join(t.split())
    t = _ARXIV_PREFIX.sub("", t)
    return _SITE_SUFFIX.sub("", t)


def _arxiv_meta(aid: str, session: requests.Session) -> dict | None:
    resp = session.get(ARXIV_API, params={"id_list": aid}, timeout=20)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    if not feed.entries:
        return None
    e = feed.entries[0]
    title = " ".join((e.get("title") or "").split())
    if not title or title.lower() == "error":
        return None
    st = e.get("published_parsed")
    return {
        "title": title,
        "authors": [a.get("name", "") for a in e.get("authors", [])],
        "published": date(st.tm_year, st.tm_mon, st.tm_mday) if st else None,
        "text": " ".join((e.get("summary") or "").split()),
    }


def _page_meta(url: str, session: requests.Session) -> tuple[dict | None, str]:
    try:
        resp = session.get(url, timeout=20)
    except Exception as exc:  # noqa: BLE001
        return None, f"fetch failed ({type(exc).__name__})"
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"
    ctype = resp.headers.get("content-type", "")
    if "pdf" in ctype:
        return None, "PDF with no landing page"
    if "html" not in ctype:
        return None, f"unexpected content-type {ctype.split(';')[0]}"

    soup = BeautifulSoup(resp.content, "lxml")

    def metas(*names):
        return [
            m.get("content", "")
            for m in soup.find_all("meta")
            if (m.get("name") or m.get("property") or "").lower() in names and m.get("content")
        ]

    titles = metas("citation_title") + metas("og:title", "twitter:title")
    h1 = soup.find("h1")
    if h1:
        titles.append(h1.get_text(" ", strip=True))
    if soup.title and soup.title.string:
        titles.append(soup.title.string)
    titles = [_clean_page_title(t) for t in titles if t.strip()]

    published = None
    for raw in metas("citation_publication_date", "citation_date", "article:published_time"):
        published = parse_date(raw[:10]) or parse_date(raw)
        if published:
            break

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return {
        "titles": titles,
        "authors": metas("citation_author"),
        "published": published,
        "text": soup.get_text(" ", strip=True)[:2000],
        "final_url": resp.url,
    }, ""


def _llm_check(
    cand: Candidate, page_titles: list[str], text: str, client: Anthropic, ledger: Ledger | None
) -> tuple[bool, str]:
    prompt = (
        "A news sweep claims this page is the original research artifact for:\n"
        f"  Title: {cand.title}\n"
        f"  Authors: {', '.join(cand.authors) or 'unknown'}\n"
        f"  Venue: {cand.venue}\n\n"
        f"The page's own title(s): {' / '.join(page_titles[:3]) or '(none found)'}\n"
        f"Page text (start): {text}\n\n"
        "Is this page the research artifact itself — the paper, or the authoring "
        "organization's own post presenting it — for that same work? Not news "
        "coverage of it, and not a different paper. Answer unsure if you can't tell."
    )
    resp = llm.create(
        client,
        stage="resolve",
        ledger=ledger,
        max_tokens=2000,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": _CHECK_SCHEMA},
        },
        messages=[{"role": "user", "content": prompt}],
    )
    data = llm.json_output(resp) or {"verdict": "unsure", "reason": "no answer"}
    return data["verdict"] == "yes", data.get("reason", "")


def resolve(
    cand: Candidate,
    session: requests.Session,
    client: Anthropic | None = None,
    ledger: Ledger | None = None,
) -> tuple[Resolved | None, str]:
    """Verify the candidate's primary URL is the work. (Resolved, '') or (None, reason)."""
    if not cand.primary_url:
        return None, "no primary URL found"

    aid = arxiv_id(cand.primary_url)
    if aid:
        # Metadata from the API, not a scraped page: canonical title and
        # authors, and the v1 submission date.
        try:
            meta = _arxiv_meta(aid, session)
        except Exception as exc:  # noqa: BLE001
            return None, f"arXiv API failed ({type(exc).__name__})"
        finally:
            time.sleep(ARXIV_DELAY_S)
        if meta is None:
            return None, f"arXiv {aid} does not exist"
        page_titles, page_authors = [meta["title"]], meta["authors"]
        url, published, text = f"https://arxiv.org/abs/{aid}", meta["published"], meta["text"]
        venue = "arXiv"
    else:
        meta, reason = _page_meta(cand.primary_url, session)
        if meta is None:
            return None, reason
        page_titles, page_authors = meta["titles"], meta["authors"]
        url, published, text = meta["final_url"], meta["published"], meta["text"]
        venue = cand.venue

    verdict = match(cand.title, cand.authors, page_titles, page_authors)
    if verdict == "ambiguous" and client is not None:
        ok, why = _llm_check(cand, page_titles, text, client, ledger)
        verdict = "yes" if ok else "no"
        if not ok:
            return None, f"page didn't check out ({why[:80]})"
    if verdict != "yes":
        shown = page_titles[0][:60] if page_titles else "(no title)"
        return None, f"title mismatch — page says '{shown}'"

    # The page's own title is authoritative over the sweep's rendering of it.
    title = page_titles[0] if page_titles else cand.title
    return Resolved(title=title, url=url, published=published, venue=venue), ""


# ── run ─────────────────────────────────────────────────────────────


def run(
    client: Anthropic,
    session: requests.Session,
    state: State,
    run_items: list[Item],
    today: date,
    ledger: Ledger | None = None,
    days: int = SWEEP_DAYS,
) -> SweepResult:
    """Discover, resolve, then route each candidate.

    unseen + recent      → a new item for this digest
    already seen         → Making waves (queued in state, once per paper)
    also new this run    → outlets attached to that item, no duplicate
    unresolvable         → named in the footer
    """
    out = SweepResult()
    run_index: dict[str, Item] = {}
    for item in run_items:
        for fp in item_fingerprints(item):
            run_index[fp] = item

    for cand in discover(client, today, ledger, days):
        res, reason = resolve(cand, session, client, ledger)
        if res is None:
            log.info("sweep: unresolved %r: %s", cand.title, reason)
            out.unresolved.append((cand.title, reason))
            continue

        canon = canonical_url(res.url) or normalize_title(res.title).replace(" ", "-")
        item = Item(
            key=f"news-sweep:{canon.replace(':', '-')}",
            source_id="news-sweep",
            source_display=res.venue,
            area=cand.area if cand.area != "other" else "alignment",
            section="top",
            grade_repro=True,
            title=res.title,
            url=res.url,
            published=res.published,
            tier=1,
            found_via=cand.outlets,
        )
        fps = item_fingerprints(item)

        same_run = next((run_index[fp] for fp in fps if fp in run_index), None)
        if same_run is not None:
            same_run.found_via = sorted(set(same_run.found_via) | set(cand.outlets))
            out.merged.append(same_run)
            continue

        seen_key = state.match_fingerprint(fps) or (
            None if state.is_new(item.key) else item.key
        )
        if seen_key:
            if state.add_wave(seen_key, cand.outlets, item.title, item.url):
                out.waves.append(seen_key)
            continue

        if too_old(item, today, SWEEP_FLOOR_DAYS):
            out.too_old.append(item)
            state.mark_seen(item.key, item.title, item.published, url=item.url)
            continue

        out.new.append(item)
        for fp in fps:
            run_index[fp] = item
    return out
