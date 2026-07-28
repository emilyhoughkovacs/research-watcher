# research-watcher

**A scheduled email agent that keeps you current on AI alignment and safety
research.**

It watches 11 sources every day — Anthropic's four research teams,
Transformer Circuits, the Alignment Science blog, Redwood, DeepMind Safety,
EleutherAI, CAIS, and a karma-filtered Alignment Forum — and emails you a
digest of anything new. If nothing was published, no email arrives. You
stop refreshing sites and stop discovering good papers three months late.

That's the whole core. Everything below is optional.

```
Every morning  →  one email, everything new across 11 sources,
                  summarized. Silent on quiet days.
```

## The Friday extra (optional)

Once a week it can also pick a single paper from that week and write you a
guide for reproducing it in one day — environment setup, prerequisites,
numbered steps with checkpoints, and a blog post skeleton. It scores
feasibility against *your* stated skill level, not against a generic ML
engineer, and won't pick something you can't finish.

This is a bonus, not the point. Turn it off with one line in your profile:

```yaml
email:
  weekly_enabled: false
```

You'll still get the daily digest, and nothing else changes.

---

## Setup

Three things to do: install, get two credentials, write a profile. About
15 minutes, most of it waiting on Google.

### 1. Install

```bash
git clone https://github.com/emilyhoughkovacs/research-watcher
cd research-watcher
python3 -m venv .venv && .venv/bin/pip install -e .
```

### 2. Get your Anthropic API key

This is what pays for summarization. Roughly $5 to $10/month at normal
volume.

1. Go to **[console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys)**
2. Sign in (or create an account)
3. Click **Create Key**
4. Name it `research-watcher` — so it's obvious later what it's for, and
   you can revoke it without touching anything else
5. **Copy it now.** It starts `sk-ant-api03-` and is shown exactly once.
   There is no way to view it again later; if you lose it you make a new
   one. That's normal, not a mistake on your part.

Also check **Settings → Billing** has credit on it. A key with no balance
fails at runtime, not at setup, so it looks like a broken watcher rather
than an empty wallet.

### 3. Get a Gmail app password

This is how the agent sends you mail. It is **not** your Google account
password — it's a separate 16-character string scoped to mail only, which
you can revoke on its own.

1. Go to **[myaccount.google.com/security](https://myaccount.google.com/security)**
2. **2-Step Verification must already be on.** App passwords don't exist
   as an option until it is. Turn it on first if needed.
3. Under 2-Step Verification, scroll to **App passwords**
   (direct link: [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords))
4. Enter a name — `research-watcher` — and click **Create**
5. Google shows you 16 characters in four groups, like `abcd efgh ijkl mnop`.
   **Copy it.** Also shown only once.

The spaces are display formatting. Keep them or strip them, Gmail accepts
either.

### 4. Put both credentials in `.env`

```bash
cp .env.example .env
chmod 600 .env
open -e .env          # macOS TextEdit; or use whatever editor you like
```

Replace the three placeholder values:

```
ANTHROPIC_API_KEY=sk-ant-api03-your-actual-key-here
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=abcdefghijklmnop
```

Save and close. `.env` is gitignored — it will never be committed.

<details>
<summary><b>Alternative: keep secrets out of your shell history</b> (read the warning first)</summary>

On macOS you can pipe straight from the clipboard so the value never
appears in your terminal or `~/.zsh_history`:

```bash
printf 'ANTHROPIC_API_KEY=%s\n' "$(pbpaste | tr -d '[:space:]')" >> .env
```

> ### ⚠️ Type this by hand. Do not copy-paste it.
>
> `pbpaste` reads your clipboard. If you *copy* the command above, your
> clipboard now holds the command instead of your API key — and running it
> writes the command text into `.env`. You'd end up with a file containing
> `ANTHROPIC_API_KEY=printf 'ANTHROPIC_API_KEY=%s\n'...` and a confusing
> auth error.
>
> Copy your **key**, then type the command.

Check what's actually on your clipboard first if you're unsure:

```bash
pbpaste | wc -c     # an Anthropic key is ~108 chars; an app password 17 or 20
```

</details>

### 5. Write your profile

```bash
cp profile.example.yaml profile.yaml
```

Open it and edit. Every field has a comment explaining what it drives. It
is gitignored, so your goals and self-assessment stay yours.

If you turned the Friday pick off, only the top section matters — identity,
goal, and areas. The `skill` and `compute` blocks exist to calibrate
feasibility scoring, which you're not using.

If you left the Friday pick on: **be honest in `skill`.** It's the input to
"can this person do it in a day." Overstating it produces guides you don't
finish, which is the exact failure this is built to avoid.

### 6. Verify, then set the waterline

```bash
research-watch check
```

Expect a table of 11 sources and a few hundred items. Any `FAIL` row is a
broken parser — fix before continuing.

```bash
research-watch baseline
```

**Run this once, before anything else.** The first run finds every item
every source has ever published (~240). Summarizing all of it would cost
around $50 and bury the signal. `baseline` marks it all as seen, spends
zero tokens, and sets the waterline. Everything after is genuinely new.

### 7. Try it

```bash
research-watch daily --dry-run      # prints the email instead of sending
research-watch daily                # actually sends
```

---

## Scheduling

Copy the workflows from `workflows.example/` into whichever repo you want
output to live in, then set the same three credentials as GitHub secrets:

```bash
gh secret set ANTHROPIC_API_KEY  -R <owner>/<repo>
gh secret set GMAIL_ADDRESS      -R <owner>/<repo>
gh secret set GMAIL_APP_PASSWORD -R <owner>/<repo>
```

`gh secret set` with no `--body` reads from stdin, so nothing lands in your
shell history. Paste the value, then Ctrl-D.

> If your repo has more than one git remote, `gh` won't guess which one you
> mean — that's what `-R owner/repo` is for. Getting it wrong could write
> your credentials to someone else's repo, so `gh` errors rather than
> assuming.

Cloud scheduling rather than `cron` or `launchd` is deliberate: neither
fires while a laptop is asleep. `launchd` at least catches up on wake,
which plain `cron` does not, but neither is reliable for a daily job.

GitHub cron is UTC-only, so local fire time shifts an hour across DST, and
can drift 10 to 30 minutes under load. A late email is not a failure.

---

## Commands

| Command | Cost | What it does |
|---|---|---|
| `check` | free | Parse every source, print a table. No LLM, no email. |
| `baseline` | free | Mark everything currently published as seen. |
| `daily` | ~$0.04/item | Summarize new items, archive them, send the digest. |
| `weekly` | ~$0.50 | Pick one paper, write the repro guide, send. No-op if disabled. |

Add `--dry-run` to `daily` or `weekly` to print the email instead of
sending it.

`daily` refuses to process more than 25 new items without `--force`. That
many overnight means a parser changed or state was reset, not that 25
papers dropped — the cap exists so a bug can't quietly drain your balance.

---

## Sources

**Tier 1 (Anthropic):** Interpretability, Alignment, Societal Impacts, and
Frontier Red Team research pages; the Alignment Science blog; Transformer
Circuits.

**Tier 2:** Redwood Research, DeepMind Safety Research, EleutherAI, CAIS,
Alignment Forum (karma-filtered).

Roughly 10 to 20 items a week combined. If that's too much, disable tier 2
in `sources.yaml` — start with CAIS and Alignment Forum, the widest and
least targeted.

**On "verified":** an HTTP 200 does not mean a feed exists. Both
`alignment.anthropic.com/feed.xml` and `safe.ai/blog?format=rss` return 200
with an HTML body — the first is a SPA catch-all route, the second is
Webflow ignoring a Squarespace convention. Neither site has RSS anywhere;
both are scraped instead. When you add a source, confirm it **parses**, not
that it responds.

Known gaps, each needing a bespoke scraper: METR, Apollo Research,
Transluce, Goodfire, UK AISI. Apollo and METR are the painful ones — PRs
very welcome.

---

## How it works

```
fetch.py       deterministic, no LLM. No new items → exit, zero tokens.
summarize.py   per-item summary. Runs only on genuinely new work.
friday.py      optional. Scores the week's shortlist in one call so
               candidates are compared rather than judged in isolation,
               then writes the guide from the paper's methods section.
email.py       plain text — renders identically everywhere and can't
               break in a way that hides content.
```

Two design choices worth knowing:

**A source that goes quiet is treated as broken.** If a source has history
and suddenly returns zero items, that's reported as a failure, not a slow
news day. A site redesign turning a scraper into a permanent cheerful
"nothing today" is how a watcher actually dies.

**The system prompt is byte-identical across items in a run**, so prompt
caching applies. You'll see `cached=N` in the logs when it's working.

---

## Cost

About **$5 to $10/month** at 10-20 items/week on Claude Opus 5 with caching
on. Measured: six papers cost $0.25.

If that's still too much: truncate paper input to abstract + intro +
results (roughly halves it), or disable tier 2 sources.

---

## License

MIT.
