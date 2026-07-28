"""Evaluation metric helpers for retrieval experiments."""

from __future__ import annotations

from math import ceil
from typing import Iterable


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, ceil((p / 100.0) * len(ordered)) - 1))
    return ordered[idx]


def compute_ranking_metrics(cases: Iterable[dict], *, k: int) -> dict[str, float]:
    total = 0
    hits = 0
    recall_sum = 0.0
    rr_sum = 0.0

    for case in cases:
        total += 1
        gold = set(case.get("gold") or [])
        retrieved = list(case.get("retrieved") or [])[:k]
        if not gold:
            continue
        matched = gold.intersection(retrieved)
        if matched:
            hits += 1
            first_rank = min(retrieved.index(item) + 1 for item in matched)
            rr_sum += 1.0 / first_rank
        recall_sum += len(matched) / len(gold)

    if total == 0:
        return {f"hit@{k}": 0.0, f"recall@{k}": 0.0, f"mrr@{k}": 0.0}
    return {
        f"hit@{k}": hits / total,
        f"recall@{k}": recall_sum / total,
        f"mrr@{k}": rr_sum / total,
    }
