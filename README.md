# research-watcher

Scheduled email digest of new AI alignment and safety research. Polls 11
sources daily, summarizes anything new with Claude, emails you the result.
Sends nothing on days with no new publications.

Optional weekly mode picks one paper and writes a reproduction guide.

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

`profile.yaml` is gitignored. For the daily digest:

| Block | Purpose |
|---|---|
| `identity` | Name, email |
| `goal.areas` | Topics you care about. Drives which items get bullets vs one line |
| `output` | Paths for digest, guides, state. Defaults to `out/`, all gitignored |
| `email.top_n` | How many items get the expanded treatment. Default 3 |

For the weekly reproduction pick, also set:

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
research-watch daily       # summarize new items, archive, send
```

**Run `baseline` once before anything else.** A first run finds every item
every source has ever published (~240). Summarizing all of it costs about
$50. `baseline` marks it seen at zero token cost and sets the waterline.

Add `--dry-run` to `daily` or `weekly` to print the email instead of
sending.

## Commands

| Command | Cost | Description |
|---|---|---|
| `check` | free | Parse all sources, print a table |
| `baseline` | free | Mark current items as seen |
| `daily` | ~$0.04/item | Summarize new items, archive, send digest |
| `weekly` | ~$0.50 | Pick one paper, write repro guide, send |

Flags: `--sources`, `--profile`, `--base-dir`, `--env`, `--dry-run`,
`--force`, `--days`, `-v`.

`daily` aborts above 25 new items unless `--force` is passed. That volume
means a parser changed or state was reset, not that 25 papers dropped.

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

Defaults: daily at 15:47 UTC, weekly Fridays at 15:17 UTC. GitHub cron is
UTC-only, so local time shifts with DST, and can drift 10-30 minutes under
load.

Cloud scheduling rather than cron/launchd because neither fires while a
laptop is asleep. launchd catches up on wake; plain cron drops the run.

## Weekly reproduction pick

Disabled with:

```yaml
email:
  weekly_enabled: false
```

When on, it scores each of the week's papers on three axes, 0-10:

- **signal**: does this matter for AI safety
- **fellows**: would replicating it produce a public artifact worth having
- **feasibility**: can *you* build it in `repro_budget_days`, given `skill`

Feasibility is a hard gate, default 6. Below it a paper can't be picked
regardless of the other scores. If nothing clears the gate, no pick is made
that week.

Output is a markdown guide in `out/guides/`: environment setup, prerequisite
checklist, numbered steps each with a verification checkpoint, likely
failure points, a repo scaffold, and a blog post outline. Written from the
paper's methods section, not its abstract.

Papers scoring high on signal but failing only on compute get flagged
separately rather than dropped.

## How it works

```
fetch.py       source adapters, dedupe against state. No LLM.
summarize.py   per-item summary + scoring. Runs only on new items.
friday.py      weekly scoring and guide generation. Optional.
email.py       plain-text rendering, SMTP over STARTTLS.
```

Model is `claude-opus-5`. The system prompt is byte-identical across items
in a run so prompt caching applies; logs show `cached=N` when it hits.

Failures are isolated per source and reported in the email footer. A source
with history that returns zero items is treated as an error, not as an empty
day, since a site redesign otherwise turns a scraper into a permanent silent
success.

## Cost

$5-10/month at 10-20 items/week. Measured: 6 papers, $0.25.

To reduce: truncate paper input to abstract/intro/results, or disable tier 2.

## License

MIT
