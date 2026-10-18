"""Independent recomputation of paper ledger metrics; no editing of frozen rows."""
from __future__ import annotations

import csv
import math
from pathlib import Path


def audit_ledger(path: Path) -> dict:
    rows = list(csv.DictReader(path.open(encoding="utf8", newline="")))
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate ledger IDs")
    pnl, stakes, briers, losses, wins = 0., 0., [], [], 0
    for row in rows:
        p = float(row["probability"])
        odd = float(row["odds"])
        stake = float(row["stake"])
        if not (0 < p < 1 and odd > 1 and stake > 0):
            raise ValueError(f"Invalid row {row['id']}")
        success = row["result"] == "WIN"
        if not success and row["result"] != "LOSS":
            raise ValueError(f"Unknown settlement {row['id']}")
        profit = stake * (odd - 1) if success else -stake
        if not math.isclose(float(row["pnl"]), profit, abs_tol=0.0002):
            raise ValueError(f"P/L mismatch {row['id']}")
        pnl += profit
        stakes += stake
        wins += int(success)
        briers.append((p - int(success)) ** 2)
        losses.append(-math.log(p if success else 1 - p))
    return {
        "rows": len(rows), "wins": wins, "losses": len(rows) - wins,
        "stakes_eur": round(stakes, 4), "pnl_eur": round(pnl, 4),
        "bankroll_eur": round(1000 + pnl, 4),
        "yield_percent": round(100 * pnl / stakes, 4) if stakes else None,
        "brier": round(sum(briers) / len(briers), 6) if rows else None,
        "log_loss": round(sum(losses) / len(losses), 6) if rows else None,
        "status": "HISTORICAL_EXPERIMENTAL_NO_VALIDATED_EDGE",
    }
