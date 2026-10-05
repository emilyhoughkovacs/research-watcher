"""System card index parsers, on trimmed copies of the real page shapes."""

from __future__ import annotations

import html
import json
from datetime import date

import pytest

from research_watcher.fetch import (
    fetch_model_card_table,
    fetch_openai_safety_hub,
    parse_month,
)
from research_watcher.models import SYSTEM_CARDS

ANTHROPIC = """<table><tbody>
<tr><th>Model</th><th>Date</th><th>System card</th></tr>
<tr><td>Claude Opus 5.5</td><td>September 2026</td>
    <td><a href="https://www.anthropic.com/claude-opus-5-5-system-card">Read</a></td></tr>
<tr><td>Claude 2</td><td>July 2023</td>
    <td><a href="https://www-cdn.anthropic.com/bd2a/Model-Card-Claude-2.pdf">Read</a></td></tr>
</tbody></table>"""

DEEPMIND = """<table>
<tr><th>Model card</th><th>Published or updated date</th><th>Link to model card</th></tr>
<tr><th>Gemini 3.8 Audio (Live, Flash TTS)</th><td>Updated 24 September 2026</td>
    <td><a href="/models/model-cards/gemini-3-8-audio/">View</a></td></tr>
<tr><th>Gemma 4</th><td>Published 2 April 2026</td>
    <td><a href="https://ai.google.dev/gemma/docs/core/model_card_4?utm_source=x">View</a></td></tr>
</table>"""


def _island(entries: list[dict]) -> str:
    # Astro's encoding: arrays as [1, [...]], plain values as [0, value].
    enc = [[0, {k: [0, v] for k, v in e.items()}] for e in entries]
    props = html.escape(json.dumps({"items": [1, enc]}))
    return (f'<astro-island component-export="UpdatesList" props="{props}"></astro-island>'
            '<a href="/gpt-6-1-sol">only the first few render as links</a>')


class FakeResponse:
    def __init__(self, text: str):
        self.content = text.encode()
        self.headers = {"content-type": "text/html"}

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, text: str):
        self.text = text

    def get(self, url, timeout=None):
        return FakeResponse(self.text)


def _src(**kw) -> dict:
    return {"id": "cards", "display": "Lab", "area": "system-cards",
            "section": SYSTEM_CARDS, **kw}


def test_parse_month_keeps_a_label_for_month_only_dates():
    assert parse_month("September 2026") == (date(2026, 9, 1), "September 2026")
    assert parse_month("24 September 2026") == (date(2026, 9, 24), None)
    assert parse_month("Read system card") == (None, None)


def test_anthropic_table_month_dates_and_suffix():
    src = _src(url="https://www.anthropic.com/system-cards", title_suffix=" system card")
    items = fetch_model_card_table(src, {}, FakeSession(ANTHROPIC))
    assert [i.title for i in items] == ["Claude Opus 5.5 system card", "Claude 2 system card"]
    first = items[0]
    assert first.published == date(2026, 9, 1) and first.when == "September 2026"
    assert first.key == "cards:anthropic.com/claude-opus-5-5-system-card"
    assert first.is_card


def test_deepmind_table_strips_updated_and_query():
    src = _src(url="https://deepmind.google/models/model-cards/", title_suffix=" model card")
    items = fetch_model_card_table(src, {}, FakeSession(DEEPMIND))
    assert [(i.published, i.date_label) for i in items] == [
        (date(2026, 9, 24), None), (date(2026, 4, 2), None),
    ]
    assert items[0].url == "https://deepmind.google/models/model-cards/gemini-3-8-audio/"
    assert items[1].key == "cards:ai.google.dev/gemma/docs/core/model_card_4"


def test_openai_hub_reads_the_island_not_the_links():
    page = _island([
        {"title": "Addendum to GPT-6 Astra System Card: GPT-6.1 Sol", "date": "2026-09-29",
         "href": "/gpt-6-1-sol", "published": True, "archived": False},
        {"title": "GPT-6 Astra System Card", "date": "2026-09-03",
         "href": "/gpt-6-astra", "published": True, "archived": False},
        {"title": "Withdrawn", "date": "2026-01-01", "href": "/old",
         "published": True, "archived": True},
    ])
    src = _src(url="https://deploymentsafety.openai.com/")
    items = fetch_openai_safety_hub(src, {}, FakeSession(page))
    assert [(i.title, i.published) for i in items] == [
        ("Addendum to GPT-6 Astra System Card: GPT-6.1 Sol", date(2026, 9, 29)),
        ("GPT-6 Astra System Card", date(2026, 9, 3)),
    ]
    assert items[0].url == "https://deploymentsafety.openai.com/gpt-6-1-sol"


def test_openai_hub_without_island_fails_loudly():
    with pytest.raises(ValueError, match="UpdatesList"):
        fetch_openai_safety_hub(_src(url="https://x/"), {}, FakeSession("<html></html>"))
