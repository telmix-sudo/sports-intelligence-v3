"""Transparent proper scoring rules and summary statistics."""
from __future__ import annotations

import math


def brier(probabilities: tuple[float, ...] | list[float], outcome: int) -> float:
    if not 0 <= outcome < len(probabilities):
        raise ValueError("Outcome outside probability vector")
    validate_distribution(probabilities)
    return sum((p - (1.0 if i == outcome else 0.0)) ** 2 for i, p in enumerate(probabilities))


def log_loss(probabilities: tuple[float, ...] | list[float], outcome: int) -> float:
    if not 0 <= outcome < len(probabilities):
        raise ValueError("Outcome outside probability vector")
    validate_distribution(probabilities)
    return -math.log(max(probabilities[outcome], 1e-15))


def validate_distribution(probabilities: tuple[float, ...] | list[float]) -> None:
    if len(probabilities) < 2 or any(not math.isfinite(p) or p < 0 or p > 1 for p in probabilities):
        raise ValueError("Invalid probability distribution")
    if not math.isclose(sum(probabilities), 1.0, rel_tol=0, abs_tol=1e-7):
        raise ValueError("Probabilities must add to one")


def no_vig(decimal_odds: tuple[float, ...] | list[float]) -> tuple[float, ...]:
    if len(decimal_odds) < 2 or any(not math.isfinite(x) or x <= 1 for x in decimal_odds):
        raise ValueError("Invalid decimal odds")
    implied = [1.0 / x for x in decimal_odds]
    margin = sum(implied)
    return tuple(p / margin for p in implied)


def american_to_decimal(odds: str | int | float) -> float:
    raw = float(odds)
    if not math.isfinite(raw) or abs(raw) < 100:
        raise ValueError("Invalid American moneyline")
    return 1 + raw / 100 if raw > 0 else 1 + 100 / abs(raw)


def aggregate(rows: list[dict], benchmark: str = "benchmark") -> dict:
    count = len(rows)
    result = {"n": count, "brier": None, "log_loss": None,
              "benchmark_n": 0, "benchmark_brier": None, "benchmark_log_loss": None,
              "improvement_brier": None, "improvement_log_loss": None}
    if not count:
        return result
    result["brier"] = round(sum(r["brier"] for r in rows) / count, 6)
    result["log_loss"] = round(sum(r["log_loss"] for r in rows) / count, 6)
    b_rows = [r for r in rows if r.get(benchmark) is not None]
    result["benchmark_n"] = len(b_rows)
    if b_rows:
        result["benchmark_brier"] = round(sum(r["benchmark"]["brier"] for r in b_rows) / len(b_rows), 6)
        result["benchmark_log_loss"] = round(sum(r["benchmark"]["log_loss"] for r in b_rows) / len(b_rows), 6)
        result["improvement_brier"] = round(
            sum(r["benchmark"]["brier"] - r["brier"] for r in b_rows) / len(b_rows), 6)
        result["improvement_log_loss"] = round(
            sum(r["benchmark"]["log_loss"] - r["log_loss"] for r in b_rows) / len(b_rows), 6)
    return result
