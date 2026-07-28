"""Dedupe state and source health tracking.

State is committed to the repo so the runner is stateless. Keyed on
`source_id:stable_id`, never on title.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {
                "version": SCHEMA_VERSION,
                "last_run": None,
                "seen": {},
                "source_health": {},
                "picks": [],
            }
        with self.path.open() as f:
            data = json.load(f)
        data.setdefault("seen", {})
        data.setdefault("source_health", {})
        data.setdefault("picks", [])
        return data

    # ── dedupe ──────────────────────────────────────────────────────
    def is_new(self, key: str) -> bool:
        return key not in self.data["seen"]

    def mark_seen(self, key: str, title: str, published: str | None) -> None:
        self.data["seen"][key] = {
            "first_seen": datetime.now(UTC).date().isoformat(),
            "published": published,
            "title": title,
        }

    def seen_count(self, source_id: str) -> int:
        prefix = f"{source_id}:"
        return sum(1 for k in self.data["seen"] if k.startswith(prefix))

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
