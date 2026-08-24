"""Deterministic relevance scoring with contradiction and staleness penalties."""

from __future__ import annotations

from typing import Any


def rank_document(row: Any, *, task_id: str | None, subject: str | None) -> float:
    score = -float(row["fts_score"] or 0.0)
    if task_id and row["task_id"] == task_id:
        score += 8.0
    if subject and row["subject"] == subject:
        score += 6.0
    score += float(row["confidence"] or 0.0) * 2.0
    score += 3.0 if row["exact"] else 0.0
    score += 4.0 if row["pinned"] else 0.0
    score -= 5.0 if row["stale"] else 0.0
    score -= 6.0 if row["contradicted"] else 0.0
    return score
