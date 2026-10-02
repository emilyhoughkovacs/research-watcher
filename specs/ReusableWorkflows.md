# Spec: Reusable workflows — tool logic in one repo, data in the other

Status: **APPROVED — implementing** (GO! 2026-10-02)
Date: 2026-10-02

## Purpose

Workflow logic is duplicated today. `ehk-os/.github/workflows/research-watch-*.yml`
are copies of `research-watcher/workflows.example/` with paths swapped in.
Every fix to *how* a run works has to be made twice. On 2026-10-02 that
happened twice (the `git add` fix, the check/costs modes), and the copies
had already drifted (comments, paths).

The boundary stays where it is: **code public, data private**. What
moves is the workflow *logic*, into the tool repo, as a reusable workflow
(`on: workflow_call`). `ehk-os` keeps only its data (profile, state,
archive, guides) and two stubs that say *when* to run and *with what*.

## Scope

### 1. One reusable workflow in `research-watcher`

`research-watcher/.github/workflows/research-watch.yml`, `on: workflow_call`
only, so it never runs on its own in the public repo.

Inputs:

| Input | Type | Default | Purpose |
|---|---|---|---|
| `command` | string | `digest` | `digest` \| `pick` \| `baseline` \| `check` \| `costs` |
| `profile` | string | `profile.yaml` | Path in the caller repo |
| `base_dir` | string | `.` | Output paths resolve against this |
| `dry_run` | boolean | `false` | Print instead of send; saves nothing |
| `days` | number | `7` | `pick` window |
| `tool_ref` | string | `main` | Tool version to install. Stubs track `@main` |

Secrets: `ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, all
`required: false`. `check`, `costs` and `baseline` don't need them, and the
CLI already fails clearly when `digest`/`pick` run without them.

Steps, moved unchanged from today's workflows: check out the caller,
check out the tool at `tool_ref` into `.tool`, set up Python, install,
run, commit back.

One file, not one per command: the digest and pick workflows differ only
in their arguments. Job timeout 30 min (pick's guide generation is the
long one).

### 2. Commit paths come from the profile, not the workflow

New CLI command, `research-watch paths`, prints the archive, guides, and
state paths from the profile's `output:` block, resolved against
`--base-dir`. The commit step stages exactly those (existing paths only,
errors not suppressed — the 2026-10-02 lesson). This removes the
"Match these to the output: block in your profile.yaml" comment, which
was itself a duplication that could drift.

### 3. Stubs in `ehk-os`

`research-watch-digest.yml` and `research-watch-pick.yml` each shrink to
~25 lines: triggers (schedule + `workflow_dispatch` inputs), `permissions:
contents: write`, workflow-level `concurrency`, one job that `uses:` the
reusable workflow, `with:` the profile path, and explicit secret mapping.

- Explicit secrets, not `secrets: inherit`: GitHub documents `inherit`
  only for same-organization callers; these are personal-account repos.
- `inputs.*` is empty on scheduled runs, so stubs default with
  `${{ inputs.mode || 'digest' }}`.

### 4. `workflows.example/` becomes the two stubs

Generic paths (`profile.yaml`, base dir `.`). README "Schedule" section:
copy two short stubs and set three secrets.

### 5. Local runs from any directory

`--profile`, `--base-dir`, `--sources` and `--env` take defaults from
`RESEARCH_WATCH_PROFILE`, `RESEARCH_WATCH_BASE_DIR`, `RESEARCH_WATCH_SOURCES`,
`RESEARCH_WATCH_ENV` when set. Flags still win. With four lines in
`~/.zshrc`, `research-watch costs` works from anywhere against `ehk-os`.

## Rollout order (so a mistake can't break a scheduled run)

1. Push the reusable workflow and `paths` command to `research-watcher`.
   Today's `ehk-os` workflows still run their full copies and are unaffected.
2. Push the `ehk-os` stubs.
3. Verify (below), in this order: `check`, then `pick --dry-run`, then
   the live digest. The pick dry-run must come first: once the digest
   archives today's items, the pick's window isn't empty and the dry run
   would spend ~$0.50 grading them. If anything fails, revert the single
   `ehk-os` commit, and the old full workflows are back before the next
   scheduled run.

## Success criteria

1. `ehk-os` stubs contain no install/run/commit steps, and each is ~30 non-comment lines.
2. `check` dispatched through the stub passes for all 15 sources: the
   whole chain works (call → tool checkout at `tool_ref` → install →
   profile path).
3. `pick --dry-run` dispatched through the pick stub reaches its empty-window
   output: secrets arrive (it checks for them before reading the archive)
   at zero API cost.
4. `research-watch paths` in CI prints the three `ehk-os` paths.
5. A **live digest dispatched today** through the stub sends the email
   and commits its state and archive to `ehk-os`, as a
   `research-watch: digest 2026-10-02` commit. This is also the first of
   the five measured runs for the cost report.
6. A change to run logic needs no `ehk-os` edit, by construction.

## Out of scope

- Merging the repos, or moving data out of `ehk-os`
- Release tags / versioning
- A test-running CI workflow for `research-watcher` itself

## Decisions

- Stubs track `@main`: a push to `research-watcher` goes live on the next
  run, same as today.
- Local env-var defaults: yes.
- Verification: a live digest today through the stub (~$1, sends the
  email tonight). It also saves state, so tomorrow's scheduled run
  only picks up what's new after it. With `sweep_every_days: 2`, the next
  sweep is Oct 4.

## Implementation notes

- **Write guard (addition).** `digest`, `pick` and `baseline` refuse to use
  `RESEARCH_WATCH_BASE_DIR` unless `--dry-run` is set; an explicit
  `--base-dir` still works. CI owns `ehk-os` state, and a local write would
  fork its history. Read-only commands use the variable freely.
- **Dry runs commit nothing (behavior change).** The old workflows ran the
  commit step after a dry run too, which committed archive files for items
  the digest never marked seen, putting them in the pick's window.
- **Branch-agnostic push.** The commit step pulls and pushes the caller's
  `GITHUB_REF_NAME` rather than a hard-coded `main`.
- **`tool_repository` input** (default this repo) so a fork can install
  itself.
- **Static checks.** `actionlint` passes on the reusable workflow and all
  four stubs. Stub inputs and secrets were type-checked against the
  reusable workflow by pointing temporary copies at its local path;
  `actionlint` can't resolve a remote `uses:`. A deliberately wrong stub
  (misspelled input, wrong type) was caught.
- Stubs are 31 non-comment lines.
