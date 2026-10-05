"""cmd_digest end to end, with fetch, Claude and SMTP stubbed.

Covers the behavioral guarantees: one email per day that has new items
and none otherwise, crossposts reported once, and the stale-state
alarm plus date floor when state stops persisting.
"""

from __future__ import annotations

import json
from argparse import Namespace
from datetime import UTC, datetime, timedelta

import pytest
from conftest import make_card, make_item, results_for

from research_watcher import cli, summarize
from research_watcher import email as mailer
from research_watcher import sweep as sweep_mod

CROSSPOST = "Continual learning might make your blocking monitors nearly useless"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A base dir with a profile, and every external call stubbed."""
    (tmp_path / "profile.yaml").write_text(
        "schedule: {cadence: daily}\n"
        "email: {top_n: 3}\n"
        "output: {archive_dir: out/digest, state_file: out/state.json}\n"
    )
    for var in ("ANTHROPIC_API_KEY", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"):
        monkeypatch.setenv(var, "x")

    sent: list[tuple[str, str]] = []
    feed: dict = {"results": []}

    monkeypatch.setattr(cli, "fetch_all", lambda *a, **k: feed["results"])
    monkeypatch.setattr(cli, "Anthropic", lambda **k: object())
    monkeypatch.setattr(summarize, "fetch_body", lambda item, session, **k: None)

    def fake_summarize(client, item, system_prompt, ledger=None):
        item.abstract = f"What {item.title} set out to show, how, and what it found."
        item.scores["impact"] = 7

    monkeypatch.setattr(summarize, "summarize_item", fake_summarize)

    def fake_summarize_card(client, item, ledger=None):
        item.abstract = f"What {item.title} covers and the headline safety conclusion."
        item.bullets = ["A dangerous-capability finding with its number."]
        item.card = {"model": item.title.removesuffix(" system card"),
                     "risk_level": "ASL-3", "changes": None}

    monkeypatch.setattr(summarize, "summarize_card", fake_summarize_card)
    monkeypatch.setattr(summarize, "assess_reach", lambda *a, **k: None)
    monkeypatch.setattr(sweep_mod, "run", lambda *a, **k: sweep_mod.SweepResult())
    # Every run in a test happens on the same date; a real same-day re-run
    # would (correctly) skip the sweep as not due.
    monkeypatch.setattr(sweep_mod, "window", lambda *a: 3)
    monkeypatch.setattr(mailer, "send", lambda subject, body, *a: sent.append((subject, body)))

    def run(*items, dry_run=False):
        feed["results"] = results_for(*items)
        args = Namespace(
            env=str(tmp_path / "nope.env"), verbose=False, profile=str(tmp_path / "profile.yaml"),
            base_dir=str(tmp_path), sources="unused", cadence=None, dry_run=dry_run, force=False,
        )
        return cli.cmd_digest(args)

    def backfill(*items, dry_run=False):
        feed["results"] = results_for(*items)
        args = Namespace(
            env=str(tmp_path / "nope.env"), verbose=False, profile=str(tmp_path / "profile.yaml"),
            base_dir=str(tmp_path), sources="unused", dry_run=dry_run,
        )
        return cli.cmd_cards_backfill(args)

    return Namespace(run=run, backfill=backfill, sent=sent, base=tmp_path,
                     state_path=tmp_path / "out" / "state.json")


def today():
    return datetime.now(UTC).date()


def test_one_email_per_day_with_new_items_none_otherwise(env):
    """One email per day with new items; nothing on a day without."""
    a = make_item("redwood", "a", "A genuinely new post about monitoring", today())
    env.run(a)
    assert len(env.sent) == 1
    env.run(a)  # nothing new since
    assert len(env.sent) == 1
    b = make_item("metr", "b", "A second genuinely new evaluation writeup", today())
    env.run(a, b)
    assert len(env.sent) == 2
    assert "A second genuinely new" in env.sent[1][1]
    assert "A genuinely new post" not in env.sent[1][1]


def test_failing_source_alone_sends_nothing(env, monkeypatch):
    state = {"source_health": {"eleuther": {"consecutive_failures": 9, "last_ok": None,
                                            "last_error": "404"}}}
    env.state_path.parent.mkdir(parents=True)
    env.state_path.write_text(json.dumps(state))
    env.run()
    assert env.sent == []


def test_crosspost_reported_once(env):
    """Redwood today, its AF crosspost tomorrow: one digest, not two."""
    env.run(make_item("redwood-research", "continual", CROSSPOST, today()))
    env.run(make_item("alignment-forum", "QnDq", CROSSPOST, today(), aggregator=True))
    assert len(env.sent) == 1


def test_stale_state_alarm_and_floor(env):
    """Frozen last_run → ⚠ in the email, and the date floor still holds."""
    env.state_path.parent.mkdir(parents=True)
    env.state_path.write_text(json.dumps({
        "last_run": (datetime.now(UTC) - timedelta(days=60)).isoformat(),
    }))
    old = make_item("cais", "old", "A post from weeks ago that was never seen",
                    today() - timedelta(days=20))
    new = make_item("cais", "new", "A post from today that is actually new", today())
    env.run(old, new)
    [(subject, body)] = env.sent
    assert subject.startswith("⚠")
    assert "State was last saved" in body
    assert "actually new" in body and "weeks ago" not in body
    assert "1 older item(s) skipped" in body


def test_top_item_shows_summary_not_bullets(env):
    env.run(make_item("redwood", "a", "A genuinely new post about monitoring", today()))
    [(_, body)] = env.sent
    assert "set out to show, how, and what it found" in " ".join(body.split())  # wrapped
    assert "   • " not in body


def test_making_waves_rides_along_only_with_new_items(env, monkeypatch):
    seen = make_item("redwood", "pain", "An earlier paper that is now in the news", today())
    env.run(seen)

    def sweep_with_wave(client, session, state, items, day, *a, **k):
        state.add_wave(seen.key, ["Euronews"], seen.title, seen.url)
        return sweep_mod.SweepResult(waves=[seen.key])

    monkeypatch.setattr(sweep_mod, "run", sweep_with_wave)
    env.run(seen)  # quiet day: the wave waits
    assert len(env.sent) == 1
    env.run(seen, make_item("metr", "n", "Something new arrives the next day", today()))
    body = env.sent[-1][1]
    assert "MAKING WAVES" in body and "Euronews" in body
    env.run(seen, make_item("metr", "n2", "And another new item after that", today()))
    assert "MAKING WAVES" not in env.sent[-1][1]  # once per paper


def test_dry_run_saves_nothing(env):
    env.run(make_item("redwood", "a", "A genuinely new post about monitoring", today()),
            dry_run=True)
    assert env.sent == [] and not env.state_path.exists()


# ── system cards ────────────────────────────────────────────────────


def test_card_alone_sends_email_in_its_own_section(env):
    env.run(make_card("anthropic-system-cards", "opus", "Claude Opus 9 system card", today()))
    [(subject, body)] = env.sent
    assert subject == "[Research Watch] System card: Claude Opus 9"
    assert "SYSTEM CARDS" in body and "Risk level: ASL-3" in body
    assert "TOP " not in body  # not ranked as research


def test_cards_sit_above_research_and_are_never_ranked(env):
    research = [make_item("metr", f"r{i}", f"Research item number {i} about evals", today())
                for i in range(4)]
    env.run(*research, make_card("openai-system-cards", "gpt", "GPT-9 system card", today()))
    [(subject, body)] = env.sent
    assert subject.startswith("[Research Watch] System card: GPT-9 · 4 new")
    assert body.index("SYSTEM CARDS") < body.index("TOP 3")
    top = body[body.index("TOP 3"):body.index("ALSO NEW")]
    assert "GPT-9" not in top


def test_month_dated_card_survives_the_daily_floor(env):
    """'September 2026' parses to the 1st; listed weeks later it's still new."""
    card = make_card("anthropic-system-cards", "m", "Claude Month system card",
                     today() - timedelta(days=20), date_label="September 2026")
    env.run(card)
    [(_, body)] = env.sent
    assert "September 2026" in body
    assert (today() - timedelta(days=20)).isoformat() not in body


def test_card_archive_is_marked_and_skipped_by_the_pick(env):
    env.run(make_card("anthropic-system-cards", "opus", "Claude Opus 9 system card", today()))
    [path] = (env.base / "out" / "digest").glob("*.md")
    assert "type: system_card" in path.read_text()
    assert cli._load_window(env.base / "out" / "digest", days=7) == []


def test_backfill_emails_newest_per_lab_and_archives_the_rest(env):
    a_new = make_card("anthropic-system-cards", "a2", "Claude Two system card",
                      today() - timedelta(days=5), date_label="September 2026")
    # Same month, listed second on the page: page order breaks the tie.
    a_tie = make_card("anthropic-system-cards", "a1b", "Claude Tie system card",
                      today() - timedelta(days=5), date_label="September 2026")
    a_old = make_card("anthropic-system-cards", "a1", "Claude One system card",
                      today() - timedelta(days=400))
    o_new = make_card("openai-system-cards", "o2", "GPT-Two System Card",
                      today() - timedelta(days=6))
    o_old = make_card("openai-system-cards", "o1", "GPT-One System Card",
                      today() - timedelta(days=90))
    env.backfill(a_new, a_tie, a_old, o_old, o_new)

    [(subject, body)] = env.sent
    assert "Claude Two" in subject and "GPT-Two" in subject
    assert "Claude Tie" not in body and "GPT-One" not in body

    files = {p.name: p.read_text() for p in (env.base / "out" / "digest").glob("*.md")}
    assert len(files) == 5
    backfilled = [t for t in files.values() if "backfill: true" in t]
    assert len(backfilled) == 3
    assert all("## Findings" not in t for t in backfilled)

    seen = json.loads(env.state_path.read_text())["seen"]
    assert {k for k, v in seen.items() if v["reported"]} == {a_new.key, o_new.key}
    assert len(seen) == 5

    # Nothing re-surfaces: not in a digest, not in a second backfill.
    env.run(a_new, a_tie, a_old, o_old, o_new)
    env.backfill(a_new, a_tie, a_old, o_old, o_new)
    assert len(env.sent) == 1


def test_backfill_dry_run_writes_nothing(env, capsys):
    env.backfill(make_card("openai-system-cards", "o", "GPT-Two System Card", today()),
                 dry_run=True)
    assert env.sent == [] and not env.state_path.exists()
    assert not (env.base / "out" / "digest").exists()
    assert "SYSTEM CARDS" in capsys.readouterr().out
