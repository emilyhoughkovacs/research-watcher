from datetime import timedelta

from conftest import make_item, results_for

from research_watcher.dedupe import (
    canonical_url,
    fingerprints,
    normalize_title,
    select_new,
)
from research_watcher.state import State

CROSSPOST = "Continual learning might make your blocking monitors nearly useless"


def test_normalize_title_folds_quotes_case_and_punctuation():
    assert normalize_title("SOTA alignment assessments don’t strongly update us") == \
        normalize_title("SOTA Alignment Assessments Don't Strongly Update Us!")


def test_canonical_url_arxiv_variants_collapse():
    ids = {
        canonical_url("https://arxiv.org/abs/2609.16247"),
        canonical_url("http://arxiv.org/pdf/2609.16247v2"),
        canonical_url("https://arxiv.org/html/2609.16247v1"),
    }
    assert ids == {"arxiv:2609.16247"}


def test_canonical_url_ignores_scheme_www_query_and_trailing_slash():
    assert canonical_url("https://www.example.org/blog/post/?utm=x") == \
        canonical_url("http://example.org/blog/post")


def test_short_titles_are_not_fingerprinted():
    # Too generic to merge two items on: "Request for proposals"
    assert not any(fp.startswith("t:") for fp in fingerprints("Request for proposals", None))


def test_seen_key_is_excluded(tmp_path, today):
    state = State(tmp_path / "s.json")
    a = make_item("redwood", "a", CROSSPOST, today)
    state.mark_seen(a.key, a.title, a.published, url=a.url, reported=True)
    assert select_new(results_for(a), state, today, 4).new == []


def test_crosspost_on_a_later_day_is_a_duplicate(tmp_path, today):
    """Spec success criterion 3: one digest, not two."""
    state = State(tmp_path / "s.json")
    original = make_item("redwood", "continual", CROSSPOST, today - timedelta(days=1))
    state.mark_seen(original.key, original.title, original.published, url=original.url,
                    reported=True)
    crosspost = make_item("alignment-forum", "QnDq", CROSSPOST, today, aggregator=True)
    sel = select_new(results_for(crosspost), state, today, 4)
    assert sel.new == []
    assert sel.duplicates == [(crosspost, original.key)]
    # Decided once: marked seen (but not reported) so it isn't re-examined.
    assert not state.is_new(crosspost.key)
    assert state.seen_entry(crosspost.key)["reported"] is False


def test_same_run_duplicate_keeps_the_original_publisher(tmp_path, today):
    state = State(tmp_path / "s.json")
    crosspost = make_item("alignment-forum", "QnDq", CROSSPOST, today, aggregator=True)
    original = make_item("redwood", "continual", CROSSPOST, today)
    # Aggregator listed first, as in sources.yaml order it might be.
    sel = select_new(results_for(crosspost, original), state, today, 4)
    assert [i.key for i in sel.new] == [original.key]


def test_pre_v2_entries_without_url_still_match_on_title(tmp_path, today):
    state = State(tmp_path / "s.json")
    state.data["seen"]["redwood-research:p/continual"] = {
        "first_seen": "2026-09-25", "published": "2026-09-25", "title": CROSSPOST,
    }
    moved = make_item("redwood-research", "continual-learning", CROSSPOST, today)
    assert select_new(results_for(moved), state, today, 4).new == []


def test_date_floor_excludes_old_and_keeps_undated(tmp_path, today):
    state = State(tmp_path / "s.json")
    old = make_item("cais", "old", "An older post that a key change resurfaced",
                    today - timedelta(days=5))
    edge = make_item("cais", "edge", "A post right at the edge of the window",
                     today - timedelta(days=4))
    undated = make_item("cais", "nodate", "An index card with no date on it", None)
    sel = select_new(results_for(old, edge, undated), state, today, 4)
    assert [i.key for i in sel.new] == [edge.key, undated.key]
    assert sel.too_old == [old]
    assert not state.is_new(old.key)
