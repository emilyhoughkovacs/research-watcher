# Spec: Impact ranking, breakout discovery, and source repairs

Status: **IMPLEMENTED 2026-10-02** — CI source check and 5-run cost measurement pending
Date: 2026-10-02

## Purpose

The daily digest should be something you can read in two minutes and trust
to (a) contain only what's new since the last email, and (b) put the work
the AI safety community will actually be talking about at the top. Today it
fails both: it re-sent a 2-month backlog daily (fixed by the hotfix below),
and its top 3 is ranked for *reproducibility*, which is the weekly pick's
job, not the digest's.

The motivating miss: **The Pain Axis** (Tagliabue, Dung, Berg; arXiv
2609.16247, submitted 2026-09-14, Euronews 2026-09-22). It was heard about
in the news first. That miss has two causes, and fixing only one won't
catch the next one:

1. **Coverage.** An independent-author arXiv paper. None of the 11
   configured sources would ever have seen it (only AF ≥ 30 karma could).
2. **Lag.** Mainstream reach showed up ~8 days after submission. A reach
   check at digest time on a same-day paper will almost always say "none".

## Already done (hotfix, 2026-10-02 — not part of this spec)

- `ehk-os` workflows: `git add` was passed a nonexistent pathspec
  (`research/repro-guides`), which aborts the whole add; `2>/dev/null ||
  true` hid it. State and archive never persisted after 2026-07-28. Fixed
  to stage only existing paths, errors no longer suppressed.
- Same pattern removed from `workflows.example/`.
- Re-baselined: 35 backlog items marked seen.

This also fixes "0 item(s) reviewed" in the weekly pick: it reads the
archive the digest writes, which was never committed.

**Measured true volume.** Diffing consecutive CI runs (each re-summarized
the whole backlog, so day-over-day differences are exactly what was new)
shows **6 genuinely new items in 14 days** from the current sources. Oct 2
had 0. With the fix in place a day earlier, Oct 2 would have sent no email.

## Scope

### A. "Only new since the last email" — three layers

How "new" is decided, in order. Each layer can only *exclude* an item;
none can make an item eligible that an earlier layer rejected.

1. **Seen state = the record of what's been reported.** An item's key is
   added to `seen` when it goes into a digest, and the state is saved only
   after the email sends (dry runs don't save). Day 1's items are in `seen`
   on day 2, so they can't recur, regardless of any date window. This is
   the primary check, and it's what the hotfix restored.
2. **Cross-source dedupe (new).** Today `seen` is keyed by
   `source:id`, so the *same* research arriving via a second source on a
   later day is re-reported. Found 8 such pairs in current state:
   - Redwood posts crossposted to Alignment Forum (5), e.g. "Continual
     learning might make your blocking monitors nearly useless". This
     produced a real repeat on Sep 25.
   - `anthropic-alignment` ↔ `alignment-science` ("Teaching Claude why")
   - `anthropic-interpretability` ↔ `transformer-circuits` ("Emotion
     concepts…")

   Fix: every item also gets a **content fingerprint**, the normalized
   title (casefold, NFKC, punctuation stripped) plus the normalized
   canonical URL (arXiv ID when present). An item whose fingerprint matches
   anything already in `seen` is marked seen silently. Within one run, two
   copies collapse to one, preferring the original publisher: lab
   site > aggregator (AF/LW crossposts).
3. **Date floor (backstop).** Even if layers 1-2 fail, as when state
   doesn't persist or source keys change, an item counts as new only if
   its published date is within the cadence window plus slack (daily → last
   4 days, weekly → 10, monthly → 35), or is unknown. Older unseen items are
   marked seen silently and counted in the footer ("3 older items
   skipped"). The floor is computed from *today*, because a non-persisting
   state has a stale `last_run`.
   - Trade-off: an AF post crossing the karma threshold >4 days after
     posting is skipped. Accepted; slack is configurable.

Plus a **stale-state alarm**: if `state.last_run` is older than 2× the
cadence period at the start of a digest, the subject gets ⚠ and the footer
says "state last saved <date> — previous runs aren't persisting". This
would have fired on day 3 of the July→October failure.

### B. Source repairs and additions

Each verified to *parse*, not just respond. Redwood must additionally be
verified **from CI**, since the 403 is environment-specific.

| Source | Tier | Method | Status found 2026-10-02 |
|---|---|---|---|
| OpenAI Alignment | 1 | RSS `alignment.openai.com/rss.xml` | parses, 19 entries |
| METR | 2 | RSS `metr.org/feed.xml` | parses, 103 entries (blog + notes) |
| UK AISI | 2 | `link_prefix` on `/blog` | parses, 98 dated items |
| Transluce | 2 | new scraper on `/news` (dated cards) | sitemap + server-rendered cards |
| Redwood (fix) | 2 | `link_prefix` on `redwoodresearch.org/blog` | parses, 133 dated; Substack 403s from GH IPs |
| EleutherAI (fix) | 2 | `link_prefix` on `blog.eleuther.ai/` | parses; needs "Read post →" title fix |

- Tier 1 is renamed from "Anthropic" to "Frontier labs" (README +
  `sources.yaml` comments).
- UK AISI: `/blog` rather than `/research` — `/blog` includes incident
  reports and announces most `/research` papers; using both double-lists.
- New source keys (Redwood moves off Substack) would re-surface old posts;
  layers 2-3 in A absorb this with no manual re-baseline.

### C. Impact score and top-N ranking

- New score **`impact`** (0-10): how much this will change what the AI
  safety community believes or does. Judged on novelty and credibility of
  the claim, plus reach evidence when there is any: news coverage, lab or
  researcher responses, discussion on AF/LW/HN, follow-on artifacts (repos,
  replications).
- Two passes, so search spend goes where it matters:
  1. Every new item gets a no-search `impact` estimate in the existing
     summarize call (free — the paper is already in context).
  2. The top K (default 6) candidates get a **search-assisted** pass using
     the server-side web search tool (`web_search_20260209`), `max_uses` ≤ 3
     each, which may revise `impact` and returns a one-line reach note with
     outlet names. At ~1 new item/day, K rarely binds.
- **All top-N slots are ordered by `impact`.** Reproducibility already
  has its own email. `signal` and `artifact_value` are still computed and
  archived; the weekly pick keeps using them.
- **Top-N format changes** from 3-4 technical bullets to an
  abstract-style summary: what the work set out to show, how, what it
  found, in 3-4 plain sentences. Then an optional `Reach:` line, e.g.
  `Reach: Euronews, ThePrint (Sep 22); ai-torture-chamber repo`.
- Bullets are still generated and written to the archive, because the
  pick's guide generation reads them.

### D. Breakout sweep (catches the Pain Axis case)

A new pseudo-source, `news-sweep`. One Claude call per run with web
search, asking for AI safety / alignment / interpretability **research**
(papers and technical reports, not policy news or op-eds) that got
mainstream or broad technical attention **in the last 3 days**.

**News is only how a paper is discovered. It is never the content.** Each
sweep hit must be traced to the exact paper and where it was published.
The summary is written from that primary source, never from news articles
or search snippets.

1. **Discover.** The sweep returns structured candidates: research title,
   authors, the outlets covering it, and the claimed primary URL.
2. **Resolve.** The primary URL must be the paper itself: arXiv, a lab's
   own blog or research page, OpenReview, or a journal. Never a news
   article. Verification is built to tolerate the ways titles legitimately
   differ, because a plain `<title>` equality check would often wrongly
   reject the right paper:
   - **Why exact match fails.** Site suffixes (`[2609.16247] The Pain
     Axis…`, `… | Anthropic`); subtitles dropped or added; the sweep
     paraphrasing a title it learned from a headline; Unicode quotes and
     dashes, LaTeX; an arXiv v2 retitle; JS-rendered pages whose `<title>`
     is just the site name; PDFs with no HTML title; journals that return
     403 to bots.
   - **Metadata first.** For arXiv, query the arXiv API
     (`export.arxiv.org/api/query?id_list=<id>`) for the canonical title,
     authors, and submission date, rather than scraping a page. Elsewhere,
     check in order `citation_title` → `og:title` → first `<h1>` →
     `<title>` with site suffixes stripped.
   - **Fuzzy, two-signal match.** Accept when the normalized titles have
     token-set similarity ≥ 0.85 or one contains the other, **or** when the
     title similarity is ≥ 0.6 *and* the author surnames overlap.
   - **Ambiguous → one cheap LLM check.** Given the page's first ~2k chars
     and the candidate's title and authors: "is this the same work?" Yes,
     no, or unsure; unsure counts as no.
   - **Bias.** A false positive (summarizing the wrong paper) is worse than
     a false negative (skipping the right one). So the bar leans strict,
     and every rejection is visible (step 3), so misses can't be silent.
   - **Fixture test.** Known cases with expected outcomes: Pain Axis
     (arXiv), an Anthropic research page, a transformer-circuits paper, an
     AISI page, a Transluce page, a journal DOI, plus a deliberately wrong
     arXiv ID that must be rejected.
   - For arXiv, the body comes from `arxiv.org/html/<id>` (full text), with
     `arxiv.org/abs/<id>` as fallback; `published` is the submission date.
3. **Unresolvable → skipped, not summarized.** Footer line: "1 news item
   skipped — couldn't confirm the primary paper: <title> (<reason>)".
4. **Dedupe** the resolved paper against everything seen, using the same
   content fingerprint as A.2. An Anthropic paper in the news must match
   its `anthropic-*` key.
5. **Unseen** → a normal new item: `fetch_body` on the primary URL, then
   the same summarize/impact path as every other item. In the email, the
   item shows where it was published, not "news sweep", e.g.
   `arXiv · Sep 14 · found via Euronews, ThePrint`.
6. **Already seen** → **"Making waves"** section: "sent to you Sep 14, now
   covered by Euronews". At most once per paper, tracked in state
   (`waves_reported`). This is the fix for the lag, and it costs nothing
   extra because the sweep already ran.

Sweep hits get their own date floor: **30 days** from the paper's date,
not A's 4. *(Corrected during implementation: the 4-day floor would have
rejected Pain Axis itself — arXiv Sep 14, covered Sep 22. Coverage lags
the paper, so the window has to cover that lag.)* A paper older than 30
days that trends again goes to Making waves if it was previously sent;
otherwise it is skipped as old.

### E. Cost

**Measured baseline** (14 CI runs, Sep 19 – Oct 2, `claude-opus-5` list
prices: $5 in / $25 out / $0.50 cache-read per MTok):

| | Value |
|---|---|
| Per summarized item | $0.057–0.061 (≈70% input tokens: up to 40k chars of body) |
| Per day while bugged (23-25 items) | $1.34–1.45, mean **$1.41** → ~$42/month |
| Per day at true volume (~0.4 items/day today) | ~$0.03 → ~$1/month |

All of the $42/month came from the bug. The README's "$5-10/month"
assumed 10-20 items/week; the measured volume is ~3/week.

**Estimate for this spec, before measurement.** Assumes ~1 item/day once
B's sources are added:

| Stage | Per day | Per month |
|---|---|---|
| Summarize (existing) | ~$0.06 | ~$2 |
| Impact search pass (C.2) | $0.10–0.20 | $3–6 |
| News sweep (D) | $0.15–0.40 | $5–12 |
| Resolve checks + sweep-hit summaries | ~$0.02 | ~$1 |
| **Total** | | **~$11–21** |

The sweep is both the largest and the least certain line: how many tokens
the search results add is the unknown. Web search is $10 per 1,000
searches, so the per-search fee is the small part.

**Measurement.** The first **5 live runs** after merge are the measurement
window. Live rather than `--dry-run`, because dry runs don't save state:
five dry runs would re-see the same items each day and inflate the number,
which is the bug pattern again. Each run appends per-stage token counts,
search counts, and $ to a `costs` ledger in state. After run 5, report
measured spend and the 30-day projection **even if it exceeds $20**, with
the levers that would bring it under (K, `max_uses`, effort, model).

**Model.** Switch `claude-opus-5` → `claude-opus-5-5`: $4 / $20 per MTok,
20% cheaper, and a drop-in for this code. Adaptive thinking and explicit
effort are already set; no forced tool choice; single-turn calls, so
preserved thinking doesn't apply. *(Pending your OK.)*

## Success criteria

1. Two consecutive digest runs with no new publications in between: the
   second sends no email (`suppress_empty: true`).
2. Every item in a digest has a first-seen date of that run. Verified by
   reading `.watch-state.json` from the commit the run made.
3. A Redwood post and its AF crosspost, arriving on different days, appear
   in exactly one digest.
4. Simulated non-persisting state (old `last_run`): the stale-state alarm
   fires, and the date floor keeps the email to at most the window + slack.
5. `research-watch check` lists all 15 sources ok, run **in CI** via
   `workflow_dispatch`, not only locally.
6. Top N is ordered by `impact`; each top item shows an abstract-style
   summary, plus a reach line when there is evidence.
7. Sweep dry-run today returns at least one relevant research item from
   outside the configured sources. Its summary is written from the
   fetched paper; the archive file's `links.paper` is the primary source,
   and no news URL appears as the item's link.
8. Resolver fixture test passes: every known-good case accepted, the
   wrong-ID case rejected. A rejected live candidate is named in the
   footer, never summarized.
9. After 5 live runs: measured spend reported with a 30-day projection,
   whatever it is. Target ≤ $20/month.

## Technical considerations

- Confirm before building whether `output_config.format` (JSON schema)
  composes with the web search server tool in one request. If not, the
  search pass returns free text and a second cheap call structures it.
- Server-tool errors don't raise: a failed search comes back as HTTP 200
  with an error object in the `web_search_tool_result` content. Branch on
  it, don't index blindly.
- `summarize._SCHEMA` gains `impact` and `abstract`. The system prompt
  stays byte-identical across items so caching keeps working.
- `rank()` changes its sort key; `render_digest` changes the top-N block.
  The `also` and `alignment_blog` sections are unchanged.
- Content fingerprints (A.2) are stored in each `seen` entry. Existing
  entries are backfilled from their stored title on load, so pairs that
  already exist dedupe from day one.
- Transluce: anchors on `/news` are single-segment paths (`/agent-activity`)
  mixed with nav links. Keep only anchors whose card contains a parseable
  date, which drops nav. Title from the heading element, not flat text.
- New state fields: `waves_reported`, `costs`, per-entry `fingerprint`.
  Bump `SCHEMA_VERSION`; the loader `setdefault`s as it does today.
- Sanity cap stays. Layers 2-3 make it much harder to hit.

## Considered and rejected

- **X API, as a sweep alternative.** Pay-per-use since Feb 2026, about
  $0.005 per post read, no free tier, recent search limited to 7 days. A
  daily query pulling ~200 posts is ~$1/day (~$30/month) *before* any LLM
  triage of those posts, which is 3-6× the web-search sweep. It also
  answers a different question: X surfaces early researcher chatter, while
  the goal here is "reached the mainstream", which is what news search
  measures. Revisit only if the sweep proves too slow.
- **Hacker News (Algolia API).** Free, but Pain Axis peaked at 11 points
  across five submissions. Any threshold that filters HN noise would have
  missed it.
- **Bluesky.** Free API, but authenticated search needs an app password.
  It has the same early-chatter character as X. A possible free add-on
  later.
- **Altmetric.** Tracks news mentions by arXiv ID, so it measures reach
  directly. But free access is a researcher program: application, ~30
  business-day review, 6-month max. Not viable for a standing tool.

## Out of scope

- arXiv firehose or HF daily papers as sources
- Apollo Research, Goodfire
- Changes to the weekly pick's scoring or guide format
- HTML email

## Decisions

- Discovery: a news sweep over the last 3 days, checked against what's
  already been reported. Hits resolve to the exact paper and venue, and
  are summarized from the paper, never from search results.
- Top N: all slots by `impact`.
- Making waves: yes, at most once per paper.
- Cost: **$20/month cap**, enforced. When month-to-date spend plus the
  run's expected cost would exceed it, the optional search stages (C.2
  reach pass, D sweep) are skipped for that run and the footer says so.
  Summarization always runs. The 5-run measurement is reported regardless
  of outcome.
- Model: `claude-opus-5-5`.
- **An email goes out only when at least one new item exists.** No email
  for "nothing new", and no standalone "sources failing" email: failure
  warnings, the stale-state alarm, and pending Making waves items go out
  with the next digest that has new items. 6 new papers on 6 different
  days in 14 means 6 emails. *(Changes existing behavior: the
  standalone failing-sources email is removed.)*

## Status

Approved 2026-10-02 ("GO!"). Implemented the same day.

## Implementation notes (deviations and measurements)

- **Sweep date floor is 30 days, not 4** (D). Corrected during
  implementation, because the 4-day floor would have rejected Pain Axis.
- **Title match uses a prefix rule, not token containment** (D.2). The
  fixture tests showed containment was too loose: "LLMs Represent Harm" is
  contained in the Pain Axis title but is a different paper. A dropped
  subtitle is always a prefix ("The Pain Axis" → "The Pain Axis: …").
- **Sweep: 8 searches with an explicit search plan, not 5.** The first
  live sweep used all 5 on generic queries and one repeated query, and
  found nothing. The prompt now splits the budget between finding
  stories and finding each story's paper. Added `email.sweep_every_days`
  (default 1) as a cost lever; the window stretches so no days are
  skipped.
- **Reach pass is gated:** only for impact ≥ 5, up to `top_n` items, and
  never for sweep hits, whose outlets are already known.
- **Measured** (list prices, `claude-opus-5-5`): sweep $0.20-0.54;
  summarize ~$0.04/item; reach ~$0.13/item. Full dry run on 2026-10-02
  with 5 new items: $1.07. Projection: ~$21/month with a daily sweep,
  ~$14/month every 2 days. The 5-run live measurement (E) is still owed.
- **Success criteria verified locally:** 1, 2 (via tests), 3, 4, 6, 7
  (Pain Axis resolved from arXiv, summarized from full text), 8 (9/9
  network fixtures, including the wrong-ID and news-article rejections).
  Criterion 5 (sources parse from CI) runs after push. Criterion 9 runs
  after 5 live runs.
