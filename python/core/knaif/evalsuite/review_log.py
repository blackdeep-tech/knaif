"""Persistent JSON review log for marking eval entries as reviewed/rejected/pending."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ReviewLog:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, int], dict[str, Any]] = {}

    def mark(
        self,
        row_id: str,
        utterance_idx: int,
        status: str,
        notes: str = "",
    ) -> None:
        self._entries[(row_id, utterance_idx)] = {
            "id": row_id,
            "utterance_idx": utterance_idx,
            "status": status,
            "notes": notes,
        }

    def get(self, row_id: str, utterance_idx: int) -> dict[str, Any] | None:
        return self._entries.get((row_id, utterance_idx))

    def all_entries(self) -> list[dict[str, Any]]:
        return list(self._entries.values())


def save_review_log(log: ReviewLog, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"entries": log.all_entries()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_review_log(path: Path) -> ReviewLog:
    log = ReviewLog()
    if not path.exists():
        return log
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return log
    for entry in data.get("entries") or []:
        log.mark(
            row_id=entry["id"],
            utterance_idx=entry.get("utterance_idx", 0),
            status=entry.get("status", "pending"),
            notes=entry.get("notes", ""),
        )
    return log
