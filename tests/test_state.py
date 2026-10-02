import json
from datetime import UTC, date, datetime, timedelta

from research_watcher.state import SCHEMA_VERSION, State


def test_v1_state_loads_and_upgrades(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({
        "version": 1, "last_run": "2026-07-28T21:17:04+00:00",
        "seen": {"x:1": {"first_seen": "2026-07-28", "published": None, "title": "t"}},
        "source_health": {}, "picks": [],
    }))
    s = State(p)
    assert s.data["version"] == SCHEMA_VERSION
    assert s.data["waves_pending"] == {} and s.data["costs"] == []
    assert not s.is_new("x:1")


def test_stale_since_flags_a_frozen_last_run(tmp_path):
    s = State(tmp_path / "s.json")
    now = datetime(2026, 10, 2, 20, 0, tzinfo=UTC)
    s.data["last_run"] = "2026-07-28T21:17:04+00:00"
    assert s.stale_since("daily", now) == "2026-07-28"
    s.data["last_run"] = (now - timedelta(hours=23)).isoformat()
    assert s.stale_since("daily", now) is None
    s.data["last_run"] = (now - timedelta(days=10)).isoformat()
    assert s.stale_since("weekly", now) is None  # 2x a week is 14 days


def test_waves_once_per_paper(tmp_path):
    s = State(tmp_path / "s.json")
    assert s.add_wave("x:1", ["Euronews"], "Paper", "https://arxiv.org/abs/1")
    assert s.add_wave("x:1", ["ThePrint"], "Paper", "https://arxiv.org/abs/1")  # merges outlets
    [(key, w)] = s.pending_waves()
    assert key == "x:1" and w["outlets"] == ["Euronews", "ThePrint"]
    s.mark_waves_reported(["x:1"])
    assert s.pending_waves() == []
    assert not s.add_wave("x:1", ["Wired"], "Paper", None)


def test_pending_waves_expire(tmp_path):
    s = State(tmp_path / "s.json")
    s.add_wave("x:1", ["Euronews"], "Paper", None)
    s.data["waves_pending"]["x:1"]["detected"] = "2026-09-01"
    assert s.pending_waves(date(2026, 10, 2)) == []


def test_sweep_window_covers_gaps_without_overlap_blowup():
    from research_watcher.sweep import window

    today = date(2026, 10, 2)
    assert window(today, None, 1) == 3                    # first sweep
    assert window(today, "2026-10-01", 1) == 3            # daily: base window
    assert window(today, "2026-10-01", 2) is None         # every 2 days: not due
    assert window(today, "2026-09-30", 2) == 3            # due; 2 days + overlap
    assert window(today, "2026-09-27", 2) == 6            # missed runs: stretch
    assert window(today, "2026-08-01", 1) == 10           # capped
