# research-watcher

A daily digest of AI safety and interpretability research, and one weekly
reproduction guide sized to a single day of work.

Reading papers doesn't build a portfolio. Building does. So this doesn't
just tell you what came out — every Friday it picks one paper you can
actually reproduce at your skill level, and writes the step-by-step guide
to get from "interesting" to a public repo and a blog post.

```
Daily   → 11 sources, deduped, summarized. Silent when nothing is new.
Friday  → one pick + a repro guide: env setup, prerequisites, numbered
          steps with checkpoints, hazards, repo scaffold, blog skeleton.
```

## What makes it useful

**It scores feasibility against *you*.** Not "is this reproducible" but
"can this person do it in a day." A paper needing a framework you've never
used *and* a trained SAE isn't a 6 just because each part looks small. A
hard feasibility gate means a paper below the bar cannot win, no matter
how good it is — a guide you can't finish is a guide you won't start.

**A RED verdict must name the nearest tractable version.** "Not
reproducible" is useless. "Needs refusal behavior GPT-2 lacks, but
Gemma-2-2B with Gemma Scope gets you most of it" is a project.

**The guide is written from the methods section, not the abstract.** "They
found models have a global workspace" doesn't help you build. "Hook the
residual stream at layer N, project through the SAE decoder, measure
cosine similarity against the reference direction" does.

**A source that silently returns nothing is treated as broken.** The way a
watcher actually dies is a site redesign that turns the scraper into a
permanent, cheerful "no news today."

## Setup (about 15 minutes)

### 1. Install

```bash
git clone https://github.com/emilyhoughkovacs/research-watcher
cd research-watcher
python3 -m venv .venv && .venv/bin/pip install -e .
```

### 2. Credentials

```bash
cp .env.example .env && chmod 600 .env
```

| Variable | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) → Create Key. **Shown once** — if you lost an old one, make a new one; you can't retrieve it. |
| `GMAIL_ADDRESS` | Sender and recipient. The same address is fine. |
| `GMAIL_APP_PASSWORD` | Google Account → Security → 2-Step Verification → App passwords. Not your account password. Requires 2FA already on. |

To keep the secret out of your shell history:

```bash
printf 'ANTHROPIC_API_KEY=%s\n' "$(pbpaste | tr -d '[:space:]')" >> .env
```

### 3. Profile

```bash
cp profile.example.yaml profile.yaml
```

This is the file that makes the tool yours: your goal, your honest skill
level, your open research threads, your available compute. **Be honest in
`skill`** — overstating it produces guides you don't finish. Gitignored.

### 4. Check the sources parse

```bash
research-watch check
```

Expect a table of 11 sources and a few hundred items. Any `FAIL` row is a
parser to fix before going further.

### 5. Baseline — once, before anything else

```bash
research-watch baseline
```

The first run finds every item every source has ever published (~240).
Summarizing all of it would cost roughly $50 and bury the signal.
`baseline` marks it as seen, spends zero tokens, and sets the waterline.
Everything after is genuinely new.

### 6. Try it

```bash
research-watch daily --dry-run     # prints the email instead of sending
research-watch weekly --dry-run
```

## Scheduling

Copy the workflows from `workflows.example/` into whichever repo you want
the output to live in, and set the same three secrets there:

```bash
gh secret set ANTHROPIC_API_KEY  -R <owner>/<repo>
gh secret set GMAIL_ADDRESS      -R <owner>/<repo>
gh secret set GMAIL_APP_PASSWORD -R <owner>/<repo>
```

GitHub cron is UTC-only, so the local fire time shifts by an hour across
DST. Harmless for a digest. Cron can also drift 10 to 30 minutes under
load — a late email isn't a failure.

Running in the cloud rather than via `launchd` or `cron` is deliberate:
neither fires while a laptop is asleep. `launchd` at least catches up on
wake, which plain `cron` does not, but neither is reliable enough for a
daily job.

## Commands

| Command | Cost | What it does |
|---|---|---|
| `check` | free | Parse every source, print a table. No LLM, no email. |
| `baseline` | free | Mark everything currently published as seen. |
| `daily` | ~$0.05/item | Summarize new items, archive, send the digest. |
| `weekly` | ~$0.50 | Grade the week, pick one, write the guide, send. |

`daily` refuses to run on more than 25 new items unless you pass
`--force`. That many overnight means a parser changed or state was reset,
not that 25 papers dropped — the cap exists so a bug can't quietly spend
your balance.

## Sources

Tier 1 is Anthropic: the Interpretability, Alignment, Societal Impacts and
Frontier Red Team pages, plus the Alignment Science blog and Transformer
Circuits. Tier 2 adds Redwood Research, DeepMind Safety Research,
EleutherAI, CAIS, and a karma-filtered Alignment Forum.

Disable tier 2 in `sources.yaml` if the volume feels like homework. CAIS
and Alignment Forum are the widest and least targeted — drop those first.

**On "verified":** an HTTP 200 does not mean a feed exists. Both
`alignment.anthropic.com/feed.xml` and `safe.ai/blog?format=rss` return
200 with an HTML body — the first is a SPA catch-all route, the second is
Webflow ignoring a Squarespace convention. Neither site has RSS anywhere;
both are scraped. When you add a source, confirm it **parses**, not that
it responds.

Known gaps, each needing a bespoke scraper: METR, Apollo Research,
Transluce, Goodfire, UK AISI. Apollo and METR are the painful ones.

## Architecture

```
fetch.py       deterministic, no LLM. No new items → exit, zero tokens.
summarize.py   per-item summary + signal/fellows scores + repro SIGNALS
               (extracted, not graded — grading daily would burn tokens
               on papers you never open; re-reading on Friday would cost
               a whole second pass)
friday.py      feasibility scored across the shortlist in one call, so
               candidates are compared rather than judged in isolation;
               then the guide, at high effort, re-reading the paper in full
email.py       plain text — renders identically everywhere and can't break
               in a way that hides content
```

The system prompt is byte-identical across items within a run so prompt
caching applies. You'll see `cached=N` in the logs when it's working.

## Cost

Roughly $10 to $18/month at 10-20 items/week on Claude Opus 5 with prompt
caching on. Levers if that lands wrong: truncate paper input to abstract +
intro + results (about half), or drop tier 2.

## License

MIT.
