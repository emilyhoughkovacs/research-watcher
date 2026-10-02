from datetime import date
from types import SimpleNamespace

import pytest

from research_watcher.costs import Budget, Ledger, month_to_date, projection, usage_cost


def usage(i=0, o=0, cr=0, cw=0, searches=0):
    return SimpleNamespace(
        input_tokens=i, output_tokens=o, cache_read_input_tokens=cr,
        cache_creation_input_tokens=cw,
        server_tool_use=SimpleNamespace(web_search_requests=searches),
    )


def test_usage_cost_matches_the_measured_search_call():
    # The 2026-10-02 probe: 33,663 in, 944 out, 2 searches on Opus 5.5.
    assert usage_cost("claude-opus-5-5", usage(33663, 944, searches=2)) == pytest.approx(
        33663 * 4e-6 + 944 * 20e-6 + 0.02
    )


def test_fallback_model_is_priced_as_itself():
    assert usage_cost("claude-opus-4-8", usage(1_000_000)) == pytest.approx(5.0)


def test_budget_skips_optional_stages_over_cap():
    ledger = Ledger()
    b = Budget(cap=20.0, spent_this_month=19.70, ledger=ledger)
    assert b.allows("reach", 0.25)
    assert not b.allows("sweep", 0.60)
    assert b.skipped == ["sweep"]


def test_month_to_date_and_projection():
    entries = [
        {"date": "2026-09-30", "command": "digest", "total": 5.0},
        {"date": "2026-10-01", "command": "digest", "total": 0.40},
        {"date": "2026-10-02", "command": "digest", "total": 0.60},
        {"date": "2026-10-02", "command": "pick", "total": 0.70},
    ]
    assert month_to_date(entries, date(2026, 10, 2)) == pytest.approx(1.70)
    # mean of last 5 digests (5.0, 0.4, 0.6) * 30 + pick 0.70 * 30/7
    assert projection(entries, "daily") == pytest.approx(6.0 / 3 * 30 + 0.70 * 30 / 7)
