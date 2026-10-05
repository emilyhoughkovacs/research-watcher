from __future__ import annotations

from datetime import date

import pytest

from research_watcher.models import SYSTEM_CARDS, Item, SourceResult


def make_item(
    source_id: str,
    slug: str,
    title: str,
    published: date | None = None,
    url: str | None = None,
    aggregator: bool = False,
    tier: int = 2,
) -> Item:
    return Item(
        key=f"{source_id}:{slug}",
        source_id=source_id,
        source_display=source_id,
        area="alignment",
        section="also",
        grade_repro=True,
        title=title,
        url=url or f"https://{source_id}.example/{slug}",
        published=published,
        tier=tier,
        aggregator=aggregator,
    )


def make_card(
    source_id: str, slug: str, title: str, published: date | None = None,
    date_label: str | None = None,
) -> Item:
    return Item(
        key=f"{source_id}:{slug}",
        source_id=source_id,
        source_display=source_id,
        area="system-cards",
        section=SYSTEM_CARDS,
        grade_repro=False,
        title=title,
        url=f"https://{source_id}.example/{slug}",
        published=published,
        date_label=date_label,
    )


def results_for(*items: Item, ok_sources: tuple[str, ...] = ()) -> list[SourceResult]:
    by_source: dict[str, list[Item]] = {s: [] for s in ok_sources}
    for i in items:
        by_source.setdefault(i.source_id, []).append(i)
    return [SourceResult(sid, items=its) for sid, its in by_source.items()]


@pytest.fixture
def today() -> date:
    return date(2026, 10, 2)
