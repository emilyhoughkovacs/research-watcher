"""Dedupe state, source health, Making-waves tracking and the cost ledger.

State is committed to the repo so the runner is stateless. Keyed on
`source_id:stable_id`, never on title — but each entry also carries enough
(title, url) to fingerprint it, which is how the same work arriving from a
second source is recognized (see dedupe.py).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# v2: seen entries gain `url` and `reported`; adds waves_* and costs.
SCHEMA_VERSION = 2

# How long a Making-waves item waits for a digest to ride along with.
# Emails only go out on days with new items, so a breakout detected on a
# quiet day is held — but not forever, or it stops being news.
WAVES_PENDING_DAYS = 7

CADENCE_DAYS = {"daily": 1, "weekly": 7, "monthly": 31}


def _iso(d) -> str | None:
    if d is None:
        return None
    return d.isoformat() if isinstance(d, date) else str(d)


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data = self._load()
        self._fp_index: dict[str, str] | None = None

    def _load(self) -> dict:
        if not self.path.exists():
            data = {"version": SCHEMA_VERSION, "last_run": None}
        else:
            with self.path.open() as f:
                data = json.load(f)
        data.setdefault("seen", {})
        data.setdefault("source_health", {})
        data.setdefault("picks", [])
        data.setdefault("waves_pending", {})
        data.setdefault("waves_reported", {})
        data.setdefault("costs", [])
        data["version"] = SCHEMA_VERSION
        return data

    # ── dedupe ──────────────────────────────────────────────────────
    def is_new(self, key: str) -> bool:
        return key not in self.data["seen"]

    def mark_seen(
        self,
        key: str,
        title: str,
        published,
        url: str | None = None,
        reported: bool = False,
    ) -> None:
        """Record a key. `reported` means it went out in a digest."""
        self.data["seen"][key] = {
            "first_seen": datetime.now(UTC).date().isoformat(),
            "published": _iso(published),
            "title": title,
            "url": url,
            "reported": reported,
        }
        if self._fp_index is not None:
            from .dedupe import fingerprints

            for fp in fingerprints(title, url):
                self._fp_index.setdefault(fp, key)

    def seen_count(self, source_id: str) -> int:
        prefix = f"{source_id}:"
        return sum(1 for k in self.data["seen"] if k.startswith(prefix))

    def match_fingerprint(self, fps: set[str]) -> str | None:
        """Key of a seen entry sharing any fingerprint, else None.

        Built lazily from every seen entry's stored title and url, so
        entries written before v2 (title only) still participate.
        """
        if self._fp_index is None:
            from .dedupe import fingerprints

            self._fp_index = {}
            for key, entry in self.data["seen"].items():
                for fp in fingerprints(entry.get("title"), entry.get("url")):
                    self._fp_index.setdefault(fp, key)
        return next((self._fp_index[fp] for fp in fps if fp in self._fp_index), None)

    def seen_entry(self, key: str) -> dict | None:
        return self.data["seen"].get(key)

    # ── freshness ───────────────────────────────────────────────────
    def stale_since(self, cadence: str, now: datetime | None = None) -> str | None:
        """`last_run` date if it's older than 2x the cadence period, else None.

        A digest that ran but whose state never got committed leaves
        `last_run` frozen. Seeing it frozen is how the next run finds out.
        """
        last = self.data.get("last_run")
        if not last:
            return None
        now = now or datetime.now(UTC)
        last_dt = datetime.fromisoformat(last)
        if now - last_dt > timedelta(days=2 * CADENCE_DAYS.get(cadence, 1)):
            return last_dt.date().isoformat()
        return None

    # ── source health ───────────────────────────────────────────────
    def record_source(self, source_id: str, ok: bool, error: str | None = None) -> int:
        """Record a fetch outcome. Returns the consecutive failure count."""
        h = self.data["source_health"].setdefault(
            source_id, {"last_ok": None, "consecutive_failures": 0, "last_error": None}
        )
        if ok:
            h["last_ok"] = datetime.now(UTC).date().isoformat()
            h["consecutive_failures"] = 0
            h["last_error"] = None
        else:
            h["consecutive_failures"] += 1
            h["last_error"] = (error or "")[:300]
        return h["consecutive_failures"]

    def failing_sources(self, threshold: int = 5) -> list[tuple[str, int]]:
        return [
            (sid, h["consecutive_failures"])
            for sid, h in self.data["source_health"].items()
            if h.get("consecutive_failures", 0) >= threshold
        ]

    # ── making waves ────────────────────────────────────────────────
    def add_wave(self, key: str, outlets: list[str], title: str, url: str | None) -> bool:
        """Queue a breakout for an already-seen item. False if already handled."""
        if key in self.data["waves_reported"]:
            return False
        entry = self.data["waves_pending"].setdefault(
            key,
            {"detected": datetime.now(UTC).date().isoformat(), "outlets": [],
             "title": title, "url": url},
        )
        entry["outlets"] = sorted(set(entry["outlets"]) | set(outlets))
        return True

    def pending_waves(self, today: date | None = None) -> list[tuple[str, dict]]:
        today = today or datetime.now(UTC).date()
        cutoff = (today - timedelta(days=WAVES_PENDING_DAYS)).isoformat()
        for key in [k for k, v in self.data["waves_pending"].items() if v["detected"] < cutoff]:
            del self.data["waves_pending"][key]
        return sorted(self.data["waves_pending"].items())

    def mark_waves_reported(self, keys: list[str]) -> None:
        stamp = datetime.now(UTC).date().isoformat()
        for key in keys:
            self.data["waves_pending"].pop(key, None)
            self.data["waves_reported"][key] = stamp

    # ── costs ───────────────────────────────────────────────────────
    def record_cost(self, entry: dict) -> None:
        self.data["costs"].append(entry)

    @property
    def costs(self) -> list[dict]:
        return self.data["costs"]

    # ── repro picks ────────────────────────────────────────────────
    def already_picked(self, key: str) -> bool:
        return any(p["key"] == key for p in self.data["picks"])

    def record_pick(self, key: str, guide_path: str) -> None:
        self.data["picks"].append(
            {
                "date": datetime.now(UTC).date().isoformat(),
                "key": key,
                "guide": guide_path,
            }
        )

    # ── persistence ─────────────────────────────────────────────────
    def save(self) -> None:
        self.data["last_run"] = datetime.now(UTC).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w") as f:
            json.dump(self.data, f, indent=2, sort_keys=True)
            f.write("\n")
        tmp.replace(self.path)
