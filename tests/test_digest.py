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
from conftest import make_item, results_for

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
    monkeypatch.setattr(summarize, "fetch_body", lambda item, session: None)

    def fake_summarize(client, item, system_prompt, ledger=None):
        item.abstract = f"What {item.title} set out to show, how, and what it found."
        item.scores["impact"] = 7

    monkeypatch.setattr(summarize, "summarize_item", fake_summarize)
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

    return Namespace(run=run, sent=sent, base=tmp_path,
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
