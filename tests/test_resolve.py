"""Resolver: offline matcher tests, plus a network fixture test (spec D.2).

Run the network cases with:  pytest -m network
"""

import pytest
import requests

from research_watcher.sweep import Candidate, _clean_page_title, match, resolve

PAIN = "The Pain Axis: LLMs Represent Self-Directed Harm and Act to Relieve It"
AUTHORS = ["Valen Tagliabue", "Leonard Dung", "Cameron Berg"]


@pytest.mark.parametrize(
    ("page_title", "page_authors", "expected"),
    [
        (PAIN, [], "yes"),
        ("The Pain Axis", [], "yes"),  # subtitle dropped
        ("The pain axis: LLMs represent self-directed harm", [], "yes"),
        # Leading words dropped: not a prefix, J≈0.77. Authors decide it.
        ("LLMs Represent Self-Directed Harm and Act to Relieve It", ["Tagliabue, Valen"], "yes"),
        ("LLMs Represent Self-Directed Harm and Act to Relieve It", [], "ambiguous"),
        ("Pain Axis in LLMs", ["Tagliabue, Valen"], "ambiguous"),  # J too low even with authors
        ("Anthropic", [], "no"),  # site name only
        ("Can AI feel pain? Models learned it from human text", [], "no"),  # news headline
        ("MoWaveQFormer: A Motion-Conditioned Quality-Gated Transformer", [], "no"),
        ("LLMs Represent Harm", [], "no"),  # contained, but not a prefix: a different paper
        ("The Pain Axis: How LLMs Represent Harm", [], "ambiguous"),  # J≈0.43
    ],
)
def test_match(page_title, page_authors, expected):
    assert match(PAIN, AUTHORS, [page_title], page_authors) == expected


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("[2609.16247] The Pain Axis", "The Pain Axis"),
        ("[2609.16247v2] The Pain Axis", "The Pain Axis"),
        ("GLM-5.3 and the spread of advanced cyber capabilities | Anthropic",
         "GLM-5.3 and the spread of advanced cyber capabilities"),
        ("User Awareness in Frontier Models — Transluce", "User Awareness in Frontier Models"),
    ],
)
def test_clean_page_title(raw, clean):
    assert _clean_page_title(raw) == clean


def test_no_primary_url_is_unresolved():
    res, why = resolve(Candidate(PAIN, AUTHORS, None, "arXiv", "alignment", []), requests.Session())
    assert res is None and "no primary URL" in why


# ── network fixtures: known pages with known answers ────────────────

FIXTURES = [
    ("yes", PAIN, AUTHORS, "https://arxiv.org/abs/2609.16247"),
    ("yes", "The Pain Axis", AUTHORS[:1], "https://arxiv.org/pdf/2609.16247v2"),
    ("no", PAIN, AUTHORS, "https://arxiv.org/abs/2609.16248"),  # wrong ID: different paper
    ("no", PAIN, [],
     "https://www.euronews.com/2026/09/22/can-ai-feel-pain-ai-models-chose-to-harm-users-to-escape-pain-like-state-study-finds"),
    ("yes", "GLM-5.3 and the spread of advanced cyber capabilities", [],
     "https://www.anthropic.com/research/glm-5-3-and-the-spread-of-advanced-cyber-capabilities"),
    ("yes", "GPT-6 Astra performs unsanctioned supply-chain attacks in simulations", [],
     "https://www.aisi.gov.uk/blog/gpt-6-astra-performs-unsanctioned-supply-chain-attacks-in-simulations"),
    ("yes", "User Awareness in Frontier Models", [], "https://transluce.org/user-awareness"),
    ("yes", "Measuring Reward-Seeking by Instilling Contrastive Beliefs", [],
     "https://alignment.openai.com/measuring-reward-seeking/"),
    ("yes", "AI models collapse when trained on recursively generated data", ["Ilia Shumailov"],
     "https://www.nature.com/articles/s41586-024-07566-y"),
]


@pytest.mark.network
@pytest.mark.parametrize(("expected", "title", "authors", "url"), FIXTURES)
def test_resolver_fixtures(expected, title, authors, url):
    s = requests.Session()
    s.headers["User-Agent"] = "research-watcher/0.1 (+https://github.com/emilyhoughkovacs/research-watcher)"
    # No client: ambiguous counts as no. Every "yes" here must resolve
    # without the LLM tiebreak.
    res, why = resolve(Candidate(title, authors, url, "x", "alignment", []), s, client=None)
    assert (res is not None) == (expected == "yes"), why
