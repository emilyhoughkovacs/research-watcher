# research-watcher

Scheduled email digest of new AI alignment and safety research.

Alignment work is scattered across lab blogs, personal sites, and forums,
and most of it has no RSS feed. Keeping current means remembering to check
a dozen places. This checks them for you.

Each run polls 11 sources, compares what it finds against what it has
already seen, and summarizes only the genuinely new items with Claude. Each
gets three or four bullets: the claim, the method, why it matters, and a
limitation the authors name. Items are ranked, the top few get the full
treatment, the rest get a line each. You get one email. When nothing was
published, you get nothing.

Runs daily by default; weekly and monthly are one config line away. Cadence
changes how much a single run picks up, not how it works.

Every summarized paper is also written to disk as markdown with YAML front
matter, so the digest doubles as a searchable archive.

Runs on GitHub Actions, or any scheduler, or by hand. Roughly $5-10/month
in API cost at a daily cadence.

An optional second mode looks at the papers from the last N days and picks
the one most worth actually running, scored against your hardware and the
libraries you use, then writes an implementation guide for it. Off with one
line of config if you only want the digest.

## Sources

**Tier 1: Anthropic**

| Source | Method |
|---|---|
| Research: Interpretability | scrape |
| Research: Alignment | scrape |
| Research: Societal Impacts | scrape |
| Research: Frontier Red Team | scrape |
| Alignment Science blog | scrape |
| Transformer Circuits | scrape |

**Tier 2: safety orgs**

| Source | Method |
|---|---|
| Redwood Research | RSS |
| DeepMind Safety Research | RSS |
| EleutherAI | RSS |
| Center for AI Safety | scrape |
| Alignment Forum | RSS, karma ≥ 30 |

~10-20 items/week combined. Disable tier 2 in `sources.yaml` to cut volume.
Configured per-source: `enabled`, `tier`, `section`, and the karma threshold
on Alignment Forum.

Not covered, each needs a custom scraper: METR, Apollo Research, Transluce,
Goodfire, UK AISI. PRs welcome.

**Note on feeds:** `alignment.anthropic.com/feed.xml` and
`safe.ai/blog?format=rss` both return HTTP 200 with an HTML body. The first
is a SPA catch-all route, the second is Webflow ignoring a Squarespace
convention. Neither site has RSS. Verify a new source *parses*, not that it
responds.

## Output

```
Subject: [Research Watch] 6 new · Alignment, Red Team

━━ TOP 3 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Verbalizable Representations Form a Global Workspace in Language Models
   Transformer Circuits · 2026-07-06
   → Blog:  https://transformer-circuits.pub/2026/workspace/index.html
   → Code:  https://github.com/anthropics/jacobian-lens
   • [claim]
   • [method]
   • [why it matters]
   • [limitation the authors name]

━━ ALSO NEW ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• [title] — Redwood Research, 2026-07-27 · [link]
```

Top N ranked by relevance get bullets; the rest get one line. Every item is
also written to `out/digest/` as markdown with YAML front matter.

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

**Write both to `.env`:**

```bash
cp .env.example .env
chmod 600 .env
```

```
ANTHROPIC_API_KEY=sk-ant-api03-...
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=abcdefghijklmnop
```

`.env` is gitignored.

<details>
<summary>Optional: pipe from clipboard to keep secrets out of shell history</summary>

```bash
printf 'ANTHROPIC_API_KEY=%s\n' "$(pbpaste | tr -d '[:space:]')" >> .env
```

**Type this, don't copy it.** `pbpaste` reads the clipboard. Copying the
command replaces your key with the command text, which then gets written to
`.env`.

</details>

## Configure

```bash
cp profile.example.yaml profile.yaml
```

`profile.yaml` is gitignored. For the digest:

| Block | Purpose |
|---|---|
| `identity` | Name, email |
| `goal.areas` | Topics you care about. Drives which items get bullets vs one line |
| `schedule.cadence` | `daily` (default), `weekly`, or `monthly` |
| `output` | Paths for digest, guides, state. Defaults to `out/`, all gitignored |
| `email.top_n` | How many items get the expanded treatment. Defaults by cadence |

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
```

**Run `baseline` once before anything else.** A first run finds every item
every source has ever published (~240). Summarizing all of it costs about
$50. `baseline` marks it seen at zero token cost and sets the waterline.

Add `--dry-run` to `digest` or `pick` to print the email instead of
sending. A dry run doesn't save state, so the items it previews stay new
for the real run.

## Commands

| Command | Cost | Description |
|---|---|---|
| `check` | free | Parse all sources, print a table |
| `baseline` | free | Mark current items as seen |
| `digest` | ~$0.04/item | Summarize new items, archive, send digest |
| `pick` | ~$0.50 | Pick one paper, write repro guide, send |

`daily` and `weekly` still work as aliases for `digest` and `pick`.

Flags: `--sources`, `--profile`, `--base-dir`, `--env`, `--dry-run`,
`--force`, `--cadence`, `--days`, `-v`.

`digest` aborts above the sanity cap unless `--force` is passed. That volume
means a parser changed or state was reset, not that 25 papers dropped
overnight.

## Schedule

Copy from `workflows.example/` into the repo you want output committed to,
then set the same three credentials as secrets:

```bash
gh secret set ANTHROPIC_API_KEY  -R <owner>/<repo>
gh secret set GMAIL_ADDRESS      -R <owner>/<repo>
gh secret set GMAIL_APP_PASSWORD -R <owner>/<repo>
```

`gh secret set` reads stdin when `--body` is omitted. Pass `-R` explicitly
if the repo has multiple remotes.

Defaults: digest at 15:47 UTC daily, pick on Fridays at 15:17 UTC. For a
different cadence, change the cron and set `schedule.cadence` to match:

```yaml
- cron: "47 15 * * *"    # daily
- cron: "47 15 * * 1"    # weekly, Mondays
- cron: "47 15 1 * *"    # monthly, the 1st
```

GitHub cron is UTC-only, so local time shifts with DST, and can drift 10-30
minutes under load.

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
fetch.py       source adapters, dedupe against state. No LLM.
summarize.py   per-item summary + scoring. Runs only on new items.
pick.py        pick scoring and guide generation. Optional.
email.py       plain-text rendering, SMTP over STARTTLS.
```

Model is `claude-opus-5`. The system prompt is byte-identical across items
in a run so prompt caching applies; logs show `cached=N` when it hits.

Failures are isolated per source and reported in the email footer. A source
with history that returns zero items is treated as an error, not as a quiet
run, since a site redesign otherwise turns a scraper into a permanent silent
success.

## Cost

$5-10/month at 10-20 items/week. Measured: 6 papers, $0.25.

To reduce: truncate paper input to abstract/intro/results, or disable tier 2.

## License

MIT
