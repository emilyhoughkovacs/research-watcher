"""Per-run cost accounting and the monthly cap.

Every Claude call goes through `llm.create`, which hands its response to a
`Ledger`. The ledger prices the call from `response.usage` at list price,
by stage, and the digest persists one ledger entry per run in state. That
history is what the monthly cap reads, and what `research-watch costs`
reports.

List prices, not billed prices: negotiated discounts would make the real
bill lower. The cap is therefore conservative, which is the safe direction.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

log = logging.getLogger(__name__)

# $ per million tokens: (input, output, cache read, 5-minute cache write).
# Cache write is 1.25x input. A server-side fallback can serve a request on
# another model, so price by `response.model`, not by what was requested.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-opus-5": (5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-8": (5.00, 25.00, 0.50, 6.25),
}
DEFAULT_MODEL = "claude-opus-5-5"
WEB_SEARCH_USD = 10.00 / 1000

DEFAULT_MONTHLY_CAP = 20.0

# Pre-call estimates for the budget guard. Measured 2026-10-02: one search
# call with 2 searches read ~34k tokens of results (~$0.17). The guard
# needs an upper-ish bound, not a mean.
ESTIMATE = {
    "summarize": 0.08,  # per item
    "reach": 0.25,      # per item, max_uses=2
    "sweep": 0.70,      # per run, max_uses=8 (measured $0.54)
    "resolve": 0.02,    # per ambiguous candidate
}

RUNS_PER_MONTH = {"daily": 30, "weekly": 30 / 7, "monthly": 1}


def usage_cost(model: str, usage) -> float:
    """List-price cost of one response's usage."""
    p_in, p_out, p_read, p_write = PRICES.get(model, PRICES[DEFAULT_MODEL])
    searches = 0
    stu = getattr(usage, "server_tool_use", None)
    if stu is not None:
        searches = getattr(stu, "web_search_requests", 0) or 0
    return (
        (usage.input_tokens or 0) * p_in
        + (usage.output_tokens or 0) * p_out
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * p_read
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * p_write
    ) / 1_000_000 + searches * WEB_SEARCH_USD


class Ledger:
    """Spend for one run, by stage."""

    def __init__(self) -> None:
        self.usd: dict[str, float] = defaultdict(float)
        self.calls: dict[str, int] = defaultdict(int)
        self.searches = 0

    def add(self, stage: str, response) -> float:
        usage = response.usage
        cost = usage_cost(response.model, usage)
        self.usd[stage] += cost
        self.calls[stage] += 1
        stu = getattr(usage, "server_tool_use", None)
        if stu is not None:
            self.searches += getattr(stu, "web_search_requests", 0) or 0
        return cost

    @property
    def total(self) -> float:
        return sum(self.usd.values())

    def to_entry(self, command: str) -> dict:
        return {
            "date": date.today().isoformat(),
            "command": command,
            "total": round(self.total, 4),
            "stages": {k: round(v, 4) for k, v in sorted(self.usd.items())},
            "calls": dict(sorted(self.calls.items())),
            "searches": self.searches,
        }


def month_to_date(entries: list[dict], today: date | None = None) -> float:
    today = today or date.today()
    prefix = today.strftime("%Y-%m")
    return sum(e.get("total", 0) for e in entries if e.get("date", "").startswith(prefix))


def projection(entries: list[dict], cadence: str, last_n: int = 5) -> float | None:
    """30-day projection from the mean of the last N digest runs."""
    runs = [e for e in entries if e.get("command") == "digest"][-last_n:]
    if not runs:
        return None
    mean = sum(e["total"] for e in runs) / len(runs)
    weekly_pick = [e for e in entries if e.get("command") == "pick"][-4:]
    pick_month = (sum(e["total"] for e in weekly_pick) / len(weekly_pick) * 30 / 7
                  if weekly_pick else 0.0)
    return mean * RUNS_PER_MONTH.get(cadence, 30) + pick_month


class Budget:
    """The monthly cap. Optional stages ask before they spend.

    Summarization is never gated: it's the core of the digest and costs
    cents. The search stages (reach pass, sweep) are what can run a month
    over, so they're the ones that get skipped.
    """

    def __init__(self, cap: float, spent_this_month: float, ledger: Ledger):
        self.cap = cap
        self.spent = spent_this_month
        self.ledger = ledger
        self.skipped: list[str] = []

    def allows(self, stage: str, estimate: float) -> bool:
        projected = self.spent + self.ledger.total + estimate
        if projected <= self.cap:
            return True
        if stage not in self.skipped:
            self.skipped.append(stage)
        log.warning(
            "budget: skipping %s (month-to-date $%.2f + this run $%.2f + est $%.2f > cap $%.2f)",
            stage, self.spent, self.ledger.total, estimate, self.cap,
        )
        return False
