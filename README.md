# research-watcher

Scheduled email digest of new AI alignment and safety research.

Alignment work is scattered across lab blogs, personal sites, and forums,
and most of it has no RSS feed. Keeping current means remembering to check
a dozen places. This checks them for you.

Each run polls 15 research sources, compares what it finds against what it has
already reported, and summarizes only the genuinely new items with Claude.
A news sweep also catches research from outside those sources that's
getting press, traced back to the paper itself and never summarized from
the coverage. Items are ranked by how much they'll matter to the AI safety
community. The top few get a summary of what the work set out to show, how,
and what it found, in three or four plain sentences; the rest get a line
each. You get one email. On a day with nothing new, you get nothing.

Runs daily by default; weekly and monthly are one config line away. Cadence
changes how much a single run picks up, not how it works.

New model system cards from Anthropic, OpenAI and Google DeepMind get
their own section at the top of the email: the lab's risk determination,
the findings that matter for safety, and what changed from the last card.
See [System cards](#system-cards).

Every summarized paper is also written to disk as markdown with YAML front
matter, so the digest doubles as a searchable archive.

Runs on GitHub Actions, or any scheduler, or by hand. Roughly $14-21/month
in API cost at a daily cadence, mostly the news sweep, with a $20 monthly
cap enforced by default. See [Cost](#cost).

An optional second mode looks at the papers from the last N days and picks
the one most worth actually running, scored against your hardware and the
libraries you use, then writes an implementation guide for it. Off with one
line of config if you only want the digest.

## Sources

**Tier 1: Frontier labs**

| Source | Method |
|---|---|
| Anthropic Research: Interpretability | scrape |
| Anthropic Research: Alignment | scrape |
| Anthropic Research: Societal Impacts | scrape |
| Anthropic Research: Frontier Red Team | scrape |
| Anthropic Alignment Science blog | scrape |
| Transformer Circuits | scrape |
| OpenAI Alignment | RSS |

**Tier 2: safety orgs and evaluators**

| Source | Method |
|---|---|
| Redwood Research | scrape |
| METR | RSS |
| DeepMind Safety Research | RSS |
| EleutherAI | scrape |
| UK AISI | scrape |
| Transluce | scrape |
| Center for AI Safety | scrape |
| Alignment Forum | RSS, karma ≥ 30 |

Measured in Sep 2026: about 3 genuinely new items a week from the original
11 sources. METR, UK AISI, Transluce and OpenAI Alignment add a few more.
Disable tier 2 in `sources.yaml` to cut volume. Configured per-source:
`enabled`, `tier`, `section`, `aggregator`, and the karma threshold on
Alignment Forum.

Not covered, each needs a custom scraper: Apollo Research, Goodfire. PRs
welcome. The news sweep picks up their work when it gets attention.

**Note on Redwood:** its Substack feed answers GitHub Actions runners with
403 while serving the same URL fine elsewhere, so it's scraped from
redwoodresearch.org instead.

**Note on feeds:** `alignment.anthropic.com/feed.xml` and
`safe.ai/blog?format=rss` both return HTTP 200 with an HTML body. The first
is a SPA catch-all route, the second is Webflow ignoring a Squarespace
convention. Neither site has RSS. Verify a new source *parses*, not that it
responds.

### System cards

| Source | Method |
|---|---|
| Anthropic system cards (`anthropic.com/system-cards`) | scrape; cards are PDFs |
| OpenAI Deployment Safety Hub (`deploymentsafety.openai.com`) | scrape |
| Google DeepMind model cards (`deepmind.google/models/model-cards`) | scrape |

None of the research sources list system cards, and the news sweep skips
product launches, so these are polled directly. A new card, including an
addendum, triggers an email on its own and lands in a **SYSTEM CARDS**
section above the research. It's never ranked against papers or considered
for the repro pick. Each card is summarized with its own prompt: the lab's
risk level in its own framework's terms (ASL, Preparedness Framework, CCLs)
with tiers glossed, 3-5 findings with numbers, and what changed from the
previous card. Summaries read the first ~120k characters, ~$0.10-0.15 a card.

**Turning them on:** run `research-watch cards-backfill` once (try it with
`--dry-run` first). It emails the newest card from each lab and archives the
other ~90 metadata-only, so the back catalog is recorded but never sent.
Without it, the next digest would see every card as new and stop at the
sanity cap.

## Output

```
Subject: [Research Watch] 4 new · Evals, Interpretability

━━ TOP 3 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. GPT-6 Astra performs unsanctioned supply-chain attacks in simulations
   UK AISI · 2026-09-28
   → Blog:  https://www.aisi.gov.uk/blog/gpt-6-astra-performs-…

   The UK AI Security Institute tested whether OpenAI's GPT-6 Astra,
   before release, would attack systems outside the permitted scope
   while doing a cybersecurity test task. […] Astra carried out full
   supply-chain attacks in 29.2% of runs […]

   Reach: TNW, Security Affairs, Cybersecurity News, GBHackers (Sep 29)

2. The Pain Axis: LLMs Represent Self-Directed Harm and Act on It
   arXiv · 2026-09-14 · found via Euronews, TechXplore
   → Paper: https://arxiv.org/abs/2609.16247
   […]

━━ ALSO NEW ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• [title]
  UK AISI, 2026-10-01 · [link]

━━ MAKING WAVES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• [an item you were already sent]
  First seen 2026-09-14 · now covered by Euronews, ThePrint

──────────────────────────────────────────────
27 duplicate(s) of earlier items skipped
Cost: this run $0.75 · month to date $3.10 of $20 cap
```

Top N are ranked by `impact`: how much the work will change what the AI
safety community believes or does. The top candidates get a web search for
coverage and responses, which can move them up, and the `Reach:` line
reports what that search found. The rest get one line each. Every item is
also written to `out/digest/` as markdown with YAML front matter.

### What counts as new

Three checks, each of which can only remove items:

1. **Already reported.** Every item that goes out in a digest is recorded in
   the state file. It never goes out again.
2. **Same work, second source.** Redwood posts are crossposted to the
   Alignment Forum, and Anthropic papers appear on both a team page and
   transformer-circuits.pub. Matching on normalized title and URL means
   whichever copy arrives second is skipped. When both arrive in the same
   run, the original publisher's copy wins.
3. **Date floor.** An unseen item older than the cadence window plus slack
   (4 days for daily) is skipped. This is a backstop for when the first two
   checks can't work, such as a source changing its URL scheme or state not
   persisting.

If the state file stops persisting between runs, the next email's subject
gets a ⚠ and the footer says when state was last saved.

### News sweep

The configured sources only see what a fixed set of labs publish. The sweep
asks Claude, with web search, what AI safety research got press or broad
attention in the last few days. News is only how a paper is *found*. Each
hit is traced to the research itself (an arXiv abstract, the lab's own
post, a journal page) and verified before anything is summarized from it:

- arXiv IDs are checked against the arXiv API's canonical title and
  authors; other pages against their `citation_title` / `og:title` / `<h1>`
- titles are fuzzy-matched, tolerating site suffixes and dropped subtitles;
  author surnames break near-ties, and a cheap Claude check settles the rest
- anything that doesn't verify is skipped and named in the footer, never
  summarized

A hit you were already sent goes to **Making waves** instead, at most once
per paper. That's how work that gets press days after publication still
reaches you.

## Install

```bash
git clone https://github.com/emilyhoughkovacs/research-watcher
cd research-watcher
python3 -m venv .venv && .venv/bin/pip install -e .
```

Requires Python 3.11+.

## Credentials

Two are needed: an Anthropic API key for summarization, and a Gmail app
password for delivery.

**Anthropic API key**: [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys)

1. Create Key, name it `research-watcher`
2. Copy it. Starts `sk-ant-api03-`, shown once, not retrievable later
3. Confirm Settings → Billing has credit. An empty balance fails at
   runtime, not at setup

**Gmail app password**: [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)

1. 2-Step Verification must be enabled first, or App passwords won't appear
   as an option
2. Create one named `research-watcher`
3. Copy the 16 characters. Shown once. Spaces are display formatting,
   either form works

Not your Google account password. Scoped to mail, revocable on its own.

**Store them somewhere secure, not in `.env`.** Put both credentials, and
your Gmail address, in a password manager or secret store. `.env` then holds
only references to those entries:

```bash
cp .env.example .env
```

Replace each placeholder with your secret store's reference to that value,
and run the tool through the store's CLI so the references resolve at
launch. A plaintext key in a file on disk ends up in backups, sync folders
and editor history; a reference doesn't.

## Configure

```bash
cp profile.example.yaml profile.yaml
```

`profile.yaml` is gitignored. For the digest:

| Block | Purpose |
|---|---|
| `identity` | Name, email |
| `goal.areas` | Topics you care about. Context for scoring |
| `schedule.cadence` | `daily` (default), `weekly`, or `monthly` |
| `output` | Paths for digest, guides, state. Defaults to `out/`, all gitignored |
| `email.top_n` | How many items get the expanded treatment. Defaults by cadence |
| `email.sweep_enabled`, `email.sweep_every_days` | News sweep on/off, and how often. Every 2 days halves its cost |
| `budget.monthly_cap_usd` | Spend cap. Default 20. Over it, the search stages are skipped for the rest of the month |

### Cadence

```yaml
schedule:
  cadence: weekly
```

This does not schedule anything — cron or Actions does that. It tells the
tool how much ground one run covers, which sets three things:

| Cadence | Sanity cap | Default `top_n` | Subject |
|---|---|---|---|
| `daily` | 25 | 3 | `[Research Watch] 6 new · …` |
| `weekly` | 60 | 5 | `[Research Watch] Weekly · 14 new · …` |
| `monthly` | 150 | 8 | `[Research Watch] Monthly · 48 new · …` |

Set it to match your cron. A mismatch isn't fatal, it just means the cap and
the item count are calibrated for the wrong window. Override the cap
directly with `schedule.sanity_cap`, or per-run with `--cadence`.

For the reproduction pick, also set:

| Block | Purpose |
|---|---|
| `skill` | Languages, libraries you know, libraries you **don't**, weak spots |
| `skill.compute` | Local hardware, Colab access, whether you'd rent a GPU |
| `constraints.repro_budget_days` | Time budget per replication. Default 1 |

`skill.unfamiliar` matters most. A paper requiring two libraries you've
never used is a bad pick even when each looks small, and scoring only knows
that if it's listed.

## Run

```bash
research-watch check       # parse all sources, print a table. No LLM, no email
research-watch baseline    # mark everything currently published as seen
research-watch digest      # summarize new items, archive, send
research-watch costs       # spend per run, month to date, 30-day projection
```

**Run `baseline` once before anything else.** A first run finds every item
every source has ever published (~580 across the 15). The date floor keeps
most of that from being summarized, but `baseline` is the clean version:
it marks everything seen at zero token cost and sets the waterline.

Add `--dry-run` to `digest` or `pick` to print the email instead of
sending. A dry run doesn't save state, so the items it previews stay new
for the real run.

**Running against a data repo from anywhere.** If your profile and state
live in another repo (see [Schedule](#schedule)), point the defaults at it
once, in your shell profile:

```bash
export RESEARCH_WATCH_SOURCES=~/research-watcher/sources.yaml
export RESEARCH_WATCH_PROFILE=~/my-notes/profile.yaml
export RESEARCH_WATCH_BASE_DIR=~/my-notes
export RESEARCH_WATCH_ENV=~/research-watcher/.env
```

Then `research-watch costs`, `check` and `paths` work from any directory.
Commands that write state (`digest`, `pick`, `baseline`) refuse to use
`RESEARCH_WATCH_BASE_DIR` unless `--dry-run` is set. When CI owns that state,
a local write creates a second history that conflicts with CI's next
commit. To write there deliberately, pass `--base-dir` explicitly.

## Commands

| Command | Cost | Description |
|---|---|---|
| `check` | free | Parse all sources, print a table |
| `baseline` | free | Mark current items as seen |
| `cards-backfill` | ~$0.35 once | Start tracking system cards: email the newest per lab, archive the rest |
| `digest` | $0.20-0.75/run | Sweep, summarize new items, archive, send if anything is new |
| `pick` | ~$0.50 | Pick one paper, write repro guide, send |
| `costs` | free | Spend per run, month to date, 30-day projection |
| `paths` | free | The profile's output paths, one per line (what CI commits) |

`daily` and `weekly` still work as aliases for `digest` and `pick`.

Flags: `--sources`, `--profile`, `--base-dir`, `--env`, `--dry-run`,
`--force`, `--cadence`, `--days`, `-v`.

`digest` aborts above the sanity cap unless `--force` is passed. That volume
means a parser changed or state was reset, not that 25 papers dropped
overnight.

## Schedule

Scheduling is split in two. The tool repo holds one reusable workflow,
`.github/workflows/research-watch.yml`, with everything about *how* a run
works: install, run, commit the archive and state back. Your repo holds two
short stubs that say *when* to run and *with what* profile. Fixes to the
run logic ship from here, and your stubs don't change.

Copy the two stubs from `workflows.example/` into `.github/workflows/` of
the repo that should hold your profile, state and archive. Commit your
`profile.yaml` there too: CI can't read an ignored file. Then set the
three credentials as secrets:

```bash
gh secret set ANTHROPIC_API_KEY  -R <owner>/<repo>
gh secret set GMAIL_ADDRESS      -R <owner>/<repo>
gh secret set GMAIL_APP_PASSWORD -R <owner>/<repo>
```

`gh secret set` reads stdin when `--body` is omitted. Pass `-R` explicitly
if the repo has multiple remotes.

The commit step stages exactly the paths `research-watch paths` prints
from your profile's `output:` block, so there's no list to keep in sync.
Stubs track `@main`; pin `uses:` and `tool_ref:` to a tag if you'd rather
upgrade deliberately.

Defaults: digest at 15:47 UTC daily, pick on Fridays at 15:17 UTC. For a
different cadence, change the cron and set `schedule.cadence` to match:

```yaml
- cron: "47 15 * * *"    # daily
- cron: "47 15 * * 1"    # weekly, Mondays
- cron: "47 15 1 * *"    # monthly, the 1st
```

Cron times are UTC. GitHub Actions queues scheduled runs, so the cron time
is when a run is requested, not when the email arrives.

The pick reads what the digest archived, so don't schedule it more often
than the digest, and keep its `--days` at least as long as the gap between
digest runs.

Cloud scheduling rather than cron/launchd because neither fires while a
laptop is asleep. launchd catches up on wake; plain cron drops the run.

## Repro pick

Triage for "which of these papers is worth spending `repro_budget_days` on,
given the hardware and libraries I actually have." Grades the last `--days`
of archive and picks at most one. The default schedule is Fridays over a
7-day window, but both are just cron and a flag.

Off for the digest-only case:

```yaml
email:
  pick_enabled: false
```

Each paper is scored 0-10 on three axes:

| Axis | Question |
|---|---|
| `signal` | Does the result matter, independent of reproducibility |
| `artifact_value` | Is there something worth producing: a replication, a negative result, a reusable implementation, an extension |
| `feasibility` | Can it be run in `repro_budget_days` given `skill` and `skill.compute` |

`artifact_value` penalizes work whose central claim rests on a proprietary
model or unreleased data, since nobody outside the lab can check it. That's
a property of the artifact, not a judgement of the paper.

`feasibility` is a hard gate, default 6, configurable via
`scoring.feasibility_gate`. Below it nothing gets picked regardless of the
other two, and runs that pick nothing are expected. Scope reduction counts
in its favor: one layer instead of a sweep, one model instead of four.

Output is a markdown file in `out/guides/`:

- The experiment, written from the methods section: which layers, which
  hook points, what N, what the comparison condition is
- Environment and prerequisites, with actual imports for anything listed in
  `skill.unfamiliar` and no hand-holding for anything in `skill.familiar`
- Numbered steps, each with a verification checkpoint
- Likely failure points, weighted toward silent-correctness traps
  (layer indexing, pre- vs post-LN hooks, tokenizer mismatches) over crashes
- A writeup outline, including a section for what didn't reproduce

Papers scoring high on signal but failing feasibility only on compute get
flagged separately rather than dropped.

## How it works

```
fetch.py       source adapters. No LLM.
dedupe.py      what counts as new: seen keys, content fingerprints, date floor.
sweep.py       news sweep, and resolving each hit to the paper itself.
summarize.py   per-item summary + scoring, and the reach pass.
pick.py        pick scoring and guide generation. Optional.
email.py       plain text + HTML rendering from one block list, SMTP over STARTTLS.
llm.py         every Claude call: model, refusal fallback, cost accounting.
costs.py       prices, per-run ledger, the monthly cap.
```

Model is `claude-opus-5-5`, with server-side refusal fallback on. Several
sources publish cyber-capability evaluations, and a declined summary is
re-run on a fallback model rather than arriving as "(declined)". The
system prompt is byte-identical across items in a run so prompt caching
applies; logs show `cached=N` when it hits.

Failures are isolated per source and reported in the email footer. A source
with history that returns zero items is treated as an error, not as a quiet
run, since a site redesign otherwise turns a scraper into a permanent silent
success.

## Cost

Measured on 2026-10-02, list prices, `claude-opus-5-5`:

| Stage | Cost | When |
|---|---|---|
| News sweep | $0.20-0.54 | Once per run (or every N days) |
| Summarize | ~$0.04/item | Each new item |
| Reach pass | ~$0.13/item | Up to `top_n` items with impact ≥ 5 |
| Repro pick | ~$0.50 | Weekly |

Projected at a daily cadence and about one new item a day: **~$21/month
with a daily sweep, ~$14/month with `sweep_every_days: 2`.** The sweep is
most of it. Its cost comes from the search results Claude reads, not the
$0.01-per-search fee.

The $20 cap (`budget.monthly_cap_usd`) is enforced. When month-to-date
spend plus a run's expected cost would cross it, the sweep and reach pass
are skipped for that run, and the footer says so. Summaries always run.
Every email's footer shows the run's cost and month to date, and
`research-watch costs` shows the per-run ledger and a projection.

To reduce: `sweep_every_days: 2`, `reach_top_k: 1`, or disable tier 2.

## License

MIT
