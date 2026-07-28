"""Command-line entry point.

  research-watch check      parse all sources, print a table. No LLM, no email.
  research-watch baseline   mark everything currently published as seen.
                            No LLM, no email, no cost. Run this ONCE first.
  research-watch digest     summarize new items, archive, send the digest.
  research-watch pick       grade the window, pick one, write the guide, send.

`digest` runs at whatever cadence you schedule it — daily, weekly, or
monthly. The mechanics are identical either way (poll, diff against state,
summarize what's new); cadence only changes how much a single run picks up,
which is why it adjusts the sanity cap and how many items get expanded.
`daily` and `weekly` remain as aliases for `digest` and `pick`.

`baseline` exists because the first run finds every item every source has
ever published (242 at time of writing). Summarizing that would cost ~$50
and bury the signal. Baseline establishes the waterline; after it, only
genuinely new work flows through.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import requests
from anthropic import Anthropic
from dotenv import load_dotenv

from . import email as mailer
from . import pick as pick_mod
from . import summarize
from .fetch import fetch_all, new_items
from .state import State

log = logging.getLogger("research_watcher")

# A run that sees more new items than this has almost certainly broken
# (a parser change, a reset state file) — refuse rather than spend. The
# threshold has to scale with the window: 25 new items in a day means
# something is wrong; 25 in a month is a normal month.
SANITY_CAP = {"daily": 25, "weekly": 60, "monthly": 150}

# How many items get the expanded treatment when the profile doesn't say.
# A monthly digest with 3 expanded items and 40 headlines isn't a digest.
DEFAULT_TOP_N = {"daily": 3, "weekly": 5, "monthly": 8}

CADENCES = ("daily", "weekly", "monthly")
WINDOW = {"daily": "day", "weekly": "week", "monthly": "month"}

# Self-contained defaults: a fresh clone writes only inside itself, and
# out/ is gitignored. Override in profile.yaml → output: to write into a
# notes repo instead.
DEFAULT_ARCHIVE = "out/digest"
DEFAULT_GUIDES = "out/guides"
DEFAULT_STATE = "out/state.json"


def _setup(args) -> tuple[dict, State, Path]:
    # Explicit path: find_dotenv() walks the call stack and fails when the
    # entry point isn't a real file (stdin, some CI shims).
    env_path = Path(args.env).expanduser()
    if env_path.exists():
        load_dotenv(env_path)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )

    profile = summarize.load_profile(args.profile)
    out = profile.get("output", {})
    base = Path(args.base_dir).expanduser()
    if not base.is_dir():
        sys.exit(
            f"error: --base-dir {base} does not exist.\n"
            "  Output paths are resolved against it; pointing at a missing\n"
            "  directory would scatter files somewhere unexpected."
        )
    # Defaults keep everything inside the repo (out/ is gitignored) so a
    # fresh clone is self-contained. Override in profile.yaml to write into
    # a notes repo instead.
    state = State(base / out.get("state_file", DEFAULT_STATE))
    return profile, state, base


def _cadence(args, profile: dict) -> str:
    """CLI flag wins over profile, profile over the daily default."""
    value = (
        getattr(args, "cadence", None)
        or profile.get("schedule", {}).get("cadence")
        or "daily"
    )
    value = str(value).lower()
    if value not in CADENCES:
        sys.exit(
            f"error: unknown cadence {value!r}.\n"
            f"  Expected one of: {', '.join(CADENCES)}\n"
            "  Set it in profile.yaml under schedule.cadence, or pass --cadence."
        )
    return value


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(
            f"error: {name} is not set.\n"
            f"  local: add it to your .env\n"
            f"  CI:    gh secret set {name} -R <owner>/<repo>"
        )
    return val


# ── commands ────────────────────────────────────────────────────────


def cmd_check(args) -> int:
    """Parse every source and report. The step-3 checkpoint, rerunnable."""
    logging.basicConfig(level=logging.ERROR)
    results = fetch_all(args.sources, state=None)

    print(f"{'SOURCE':<30} {'N':>4} {'DATED':>6}  STATUS")
    print("-" * 76)
    total = failed = 0
    for r in results:
        total += len(r.items)
        dated = sum(1 for i in r.items if i.published)
        if not r.ok:
            failed += 1
        status = "ok" if r.ok else f"FAIL: {(r.error or '')[:34]}"
        print(f"{r.source_id:<30} {len(r.items):>4} {dated:>6}  {status}")
    print("-" * 76)
    print(f"{'TOTAL':<30} {total:>4}   ({failed} source(s) failing)")
    return 1 if failed else 0


def cmd_baseline(args) -> int:
    """Mark everything currently published as seen. No LLM, no email."""
    profile, state, _ = _setup(args)
    results = fetch_all(args.sources, state=state)

    marked = 0
    for r in results:
        state.record_source(r.source_id, r.ok, r.error)
        if not r.ok:
            log.warning("%s failed, not baselined: %s", r.source_id, r.error)
            continue
        for item in r.items:
            if state.is_new(item.key):
                state.mark_seen(
                    item.key, item.title, item.published.isoformat() if item.published else None
                )
                marked += 1

    state.save()
    print(f"\nBaselined {marked} item(s) across {len(results)} source(s). No tokens spent.")
    print("Future runs will only process genuinely new work.")
    return 0


def cmd_digest(args) -> int:
    profile, state, base = _setup(args)
    cadence = _cadence(args, profile)
    api_key = _require("ANTHROPIC_API_KEY")
    address = _require("GMAIL_ADDRESS")
    app_pw = _require("GMAIL_APP_PASSWORD")

    out = profile.get("output", {})
    archive_dir = base / out.get("archive_dir", DEFAULT_ARCHIVE)
    email_cfg = profile.get("email", {})
    # suppress_empty_daily was the pre-cadence name; still honoured.
    suppress_empty = email_cfg.get(
        "suppress_empty", email_cfg.get("suppress_empty_daily", True)
    )

    results = fetch_all(args.sources, state=state)
    for r in results:
        state.record_source(r.source_id, r.ok, r.error)

    items = new_items(results, state)
    failing = state.failing_sources()

    if not items:
        log.info("no new items")
        state.save()
        if failing:
            # Nothing new, but something is broken — that still warrants mail,
            # because "quiet" and "the scraper died" look identical from here.
            subject = f"⚠ [Research Watch] {len(failing)} source(s) failing"
            body = "No new items, but these sources have been failing:\n\n" + "\n".join(
                f"  {sid}: {n} consecutive failures" for sid, n in failing
            )
            mailer.send(subject, body, address, app_pw)
        elif not suppress_empty:
            subject = f"[Research Watch] {cadence.capitalize()} digest — nothing new"
            body = f"No new items across {len(results)} source(s).\n"
            mailer.send(subject, body, address, app_pw)
        return 0

    cap = profile.get("schedule", {}).get("sanity_cap") or SANITY_CAP[cadence]
    if len(items) > cap and not args.force:
        state.save()
        sys.exit(
            f"error: {len(items)} new items exceeds the {cadence} sanity cap of {cap}.\n"
            "This usually means a parser changed or the state file was reset —\n"
            f"not that {len(items)} papers were published in one {WINDOW[cadence]}.\n\n"
            "  Establish a new waterline:  research-watch baseline\n"
            "  Raise the bar permanently:  schedule.sanity_cap in profile.yaml\n"
            "  Or override just this run:  research-watch digest --force"
        )

    client = Anthropic(api_key=api_key)
    session = requests.Session()
    session.headers.update({"User-Agent": "research-watcher/0.1"})
    system_prompt = summarize.build_system_prompt(profile)

    for item in items:
        summarize.fetch_body(item, session)
        try:
            summarize.summarize_item(client, item, system_prompt)
        except Exception as exc:  # noqa: BLE001
            log.warning("summarize failed for %s: %s", item.key, exc)
        summarize.write_archive(item, archive_dir)
        state.mark_seen(
            item.key, item.title, item.published.isoformat() if item.published else None
        )

    top_n = email_cfg.get("top_n") or DEFAULT_TOP_N[cadence]
    top, rest = summarize.rank(items, top_n)
    subject, body = mailer.render_digest(
        top, rest, results, str(archive_dir.relative_to(base)), failing, cadence
    )

    if args.dry_run:
        print(f"SUBJECT: {subject}\n\n{body}")
        # Deliberately no state.save(). A dry run that marked these seen would
        # make the next real run find nothing and send no email — the preview
        # would have eaten the digest it was previewing.
        log.info("dry run — state not saved, these %d item(s) stay new", len(items))
        return 0

    mailer.send(subject, body, address, app_pw)
    state.save()
    return 0


def cmd_pick(args) -> int:
    profile, state, base = _setup(args)

    email_cfg = profile.get("email", {})
    # The repro pick is optional. Checked before any credential lookup or
    # network call so that disabling it costs nothing and can't fail.
    # weekly_enabled was the pre-cadence name; still honoured.
    if not email_cfg.get("pick_enabled", email_cfg.get("weekly_enabled", True)):
        log.info("repro pick disabled in profile (email.pick_enabled: false)")
        return 0

    api_key = _require("ANTHROPIC_API_KEY")
    address = _require("GMAIL_ADDRESS")
    app_pw = _require("GMAIL_APP_PASSWORD")

    out = profile.get("output", {})
    archive_dir = base / out.get("archive_dir", DEFAULT_ARCHIVE)
    guides_dir = base / out.get("guides_dir", DEFAULT_GUIDES)

    items = _load_window(archive_dir, args.days)
    items = [i for i in items if not state.already_picked(i.key)]

    if not items:
        subject, body = mailer.render_pick(
            None, None, None, [], str(guides_dir), 0, days=args.days
        )
        if args.dry_run:
            print(f"SUBJECT: {subject}\n\n{body}")
        elif email_cfg.get("send_empty_pick", email_cfg.get("send_empty_weekly", True)):
            mailer.send(subject, body, address, app_pw)
        return 0

    client = Anthropic(api_key=api_key)
    session = requests.Session()
    session.headers.update({"User-Agent": "research-watcher/0.1"})

    pick, escalation, _why, estimates = pick_mod.score_shortlist(client, items, profile)

    guide_path = None
    estimate_str = None
    if pick is not None:
        try:
            guide = pick_mod.generate_guide(client, pick, profile, session)
            guide_path = pick_mod.write_guide(pick, guide, profile, guides_dir)
            estimate_str = (
                f"{guide['est_build_hours']}h build + {guide['est_writeup_hours']}h writeup"
            )
            # The guide file is written either way — it's the thing you want to
            # read in a dry run. Recording the pick is what must not happen,
            # since that permanently excludes the paper from future runs.
            if not args.dry_run:
                state.record_pick(pick.key, guide_path)
        except Exception as exc:  # noqa: BLE001
            log.error("guide generation failed: %s", exc)
            guide_path = "(guide generation failed — see logs)"

    runners = [i for i in items if i is not pick and i is not escalation]
    runners.sort(key=lambda i: i.scores.get("composite") or 0, reverse=True)

    subject, body = mailer.render_pick(
        pick,
        guide_path,
        escalation,
        runners[:5],
        str(guides_dir),
        len(items),
        estimate_str,
        days=args.days,
    )

    if args.dry_run:
        print(f"SUBJECT: {subject}\n\n{body}")
        log.info("dry run — state not saved, this pick stays eligible")
        return 0

    mailer.send(subject, body, address, app_pw)
    state.save()
    return 0


def _load_window(archive_dir: Path, days: int):
    """Rehydrate Items from the archive files written inside the window."""
    from datetime import date, timedelta

    import yaml

    from .models import Item

    if not archive_dir.exists():
        return []
    cutoff = date.today() - timedelta(days=days)
    items = []
    for path in sorted(archive_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        _, front, rest = text.split("---", 2)
        meta = yaml.safe_load(front) or {}
        try:
            file_date = date.fromisoformat(path.name[:10])
        except ValueError:
            continue
        if file_date < cutoff:
            continue
        links = meta.get("links", {}) or {}
        item = Item(
            key=meta.get("key", f"unknown:{path.stem}"),
            source_id=meta.get("source", "unknown"),
            source_display=meta.get("source_display", meta.get("source", "unknown")),
            area=meta.get("area", "unknown"),
            section="top",
            grade_repro=True,
            title=meta.get("title", path.stem),
            url=links.get("blog", ""),
            published=date.fromisoformat(meta["published"]) if meta.get("published") else None,
            paper_url=links.get("paper"),
            code_url=links.get("code"),
        )
        item.scores = meta.get("scores") or item.scores
        item.repro_signals = meta.get("repro_signals") or {}
        item.bullets = [
            ln[2:] for ln in rest.splitlines() if ln.startswith("- ")
        ]
        items.append(item)
    return items


# ── argument parsing ────────────────────────────────────────────────


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="research-watch", description=__doc__)
    p.add_argument("--sources", default="sources.yaml")
    p.add_argument("--profile", default="profile.yaml")
    p.add_argument("--base-dir", default=".", help="root for archive/guides/state paths")
    p.add_argument("--env", default=".env")
    p.add_argument("-v", "--verbose", action="store_true")

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="parse all sources; no LLM, no email").set_defaults(
        func=cmd_check
    )
    sub.add_parser(
        "baseline", help="mark everything currently published as seen; no cost"
    ).set_defaults(func=cmd_baseline)

    # `daily` and `weekly` are the pre-cadence command names, kept as aliases
    # so existing schedules and muscle memory keep working.
    d = sub.add_parser(
        "digest", aliases=["daily"], help="summarize new items and send the digest"
    )
    d.add_argument("--dry-run", action="store_true", help="print the email instead of sending")
    d.add_argument("--force", action="store_true", help="bypass the sanity cap")
    d.add_argument(
        "--cadence",
        choices=CADENCES,
        help="overrides schedule.cadence in the profile (default: daily)",
    )
    d.set_defaults(func=cmd_digest)

    w = sub.add_parser(
        "pick", aliases=["weekly"], help="grade the window, pick one paper, write the guide"
    )
    w.add_argument("--dry-run", action="store_true", help="print the email instead of sending")
    w.add_argument("--days", type=int, default=7, help="archive window to grade")
    w.set_defaults(func=cmd_pick)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
