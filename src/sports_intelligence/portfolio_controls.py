"""Prospective-only aggregate paper risk, diagnostics and conservative EV checks.

Controls never edit existing ledgers or rewrite historical predictions.
They affect only newly registered paper entries. Totals are across all 12 wallets.
"""
from __future__ import annotations

from collections import defaultdict
import datetime as dt
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

INITIAL_TOTAL_BANK = 12_000.0
MAX_EVENT_OPEN_FRACTION = .003  # 36 EUR aggregate per distinct game
MAX_PORTFOLIO_OPEN_FRACTION = .02  # 240 EUR aggregate
MIN_MEANINGFUL_STAKE_EUR = 1.0
LOCAL_TZ = ZoneInfo("Europe/Lisbon")


class PaperRiskBook:
    """Single mutable budget shared by all account openers in one lab execution."""

    def __init__(self, root: Path):
        self.event_exposure = defaultdict(float)
        self.total_exposure = 0.0
        self.event_legs = defaultdict(int)
        for path in (root / "data" / "strategy_ledgers").glob("*.jsonl"):
            for line in path.read_text(encoding="utf8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("status") != "OPEN":
                    continue
                stake = float(record.get("stake_eur") or 0)
                if not math.isfinite(stake) or stake < 0:
                    raise ValueError("Invalid virtual open exposure")
                self.event_exposure[record["event_id"]] += stake
                self.total_exposure += stake
                self.event_legs[record["event_id"]] += 1
        self.rejections = defaultdict(int)

    def reserve(self, event_id: str, requested: float) -> float:
        if not event_id or not math.isfinite(requested) or requested <= 0:
            self.rejections["invalid_stake"] += 1
            return 0.0
        available = min(
            requested,
            INITIAL_TOTAL_BANK * MAX_EVENT_OPEN_FRACTION - self.event_exposure[event_id],
            INITIAL_TOTAL_BANK * MAX_PORTFOLIO_OPEN_FRACTION - self.total_exposure,
        )
        size = round(max(0.0, available), 2)
        if size < MIN_MEANINGFUL_STAKE_EUR:
            self.rejections["aggregate_event_or_portfolio_limit"] += 1
            return 0.0
        self.event_exposure[event_id] += size
        self.total_exposure += size
        self.event_legs[event_id] += 1
        return size

    def summary(self) -> dict:
        return {
            "max_event_open_eur": round(INITIAL_TOTAL_BANK * MAX_EVENT_OPEN_FRACTION, 2),
            "max_total_open_eur": round(INITIAL_TOTAL_BANK * MAX_PORTFOLIO_OPEN_FRACTION, 2),
            "minimum_new_stake_eur": MIN_MEANINGFUL_STAKE_EUR,
            "total_open_eur": round(self.total_exposure, 2),
            "event_exposure_eur": {
                key: round(value, 2)
                for key, value in sorted(self.event_exposure.items()) if value > 0
            },
            "event_position_counts": dict(sorted(self.event_legs.items())),
            "limit_rejections": dict(self.rejections),
            "existing_positions_never_retroactively_changed": True,
        }


def conservative_expected_value(prob: float, price: float, sample_min: int,
                                baseline_uncertainty: float = .03) -> float:
    """Lower-bound sensitivity check, NOT a statistically calibrated CI.

    Conservative buffer grows with extreme odds and decreases slightly as
    training history accumulates. Prevents treating marginal model EV as certain.
    """
    if not (math.isfinite(prob) and math.isfinite(price)) or not 0 <= prob <= 1 or price <= 1:
        return -math.inf
    extra = max(0.0, .05 * min(1.0, (price - 2.0) / 6.0))
    history = .02 if sample_min < 20 else .01 if sample_min < 40 else 0
    return max(0.0, prob - baseline_uncertainty - extra - history) * price - 1


def audit_ledgers(root: Path, now: dt.datetime) -> dict:
    """Point-in-time accounting, unique events, stale pending and calibration.

    Never settle using scheduled kick-off time: official final is required.
    """
    from .metrics import brier, log_loss
    today = now.astimezone(LOCAL_TZ).date()
    trades = []
    for path in sorted((root / "data" / "strategy_ledgers").glob("*.jsonl")):
        for line in path.read_text(encoding="utf8").splitlines():
            if line.strip():
                trades.append(json.loads(line))
    opened_today = [r for r in trades if
                    dt.datetime.fromisoformat(r["forecast_utc"]).astimezone(LOCAL_TZ).date() == today]
    settled = [r for r in trades if r.get("status") == "SETTLED"]
    delayed = []
    for r in trades:
        if r.get("status") == "OPEN":
            kickoff = dt.datetime.fromisoformat(r["start_utc"])
            if (now - kickoff).total_seconds() > 5 * 3600:
                delayed.append({"event_id": r["event_id"], "strategy": r["strategy"],
                                "kickoff_utc": r["start_utc"], "delay_hours": round(
                                    (now-kickoff).total_seconds()/3600, 1)})
    indices = {r["event_id"] for r in settled}
    total_stake = sum(float(r["stake_eur"]) for r in settled)
    pnl = sum(float(r.get("pnl_eur") or 0) for r in settled)
    by_sport = {}
    for sport in sorted({r.get("sport", "unknown") for r in settled}):
        subset = [r for r in settled if r.get("sport") == sport]
        by_sport[sport] = {"n": len(subset),
                           "unique_events": len({r["event_id"] for r in subset}),
                           "pnl_eur": round(sum(float(r["pnl_eur"]) for r in subset), 2),
                           "wins": sum(float(r["pnl_eur"]) > 0 for r in subset)}
    return {
        "local_date": today.isoformat(),
        "new_entries": len(opened_today),
        "distinct_events_today": len({r["event_id"] for r in opened_today}),
        "total": len(trades), "open": sum(r.get("status") == "OPEN" for r in trades),
        "closed": len(settled), "voided": sum(r.get("status") == "VOID" for r in trades),
        "settled_unique_events": len(indices),
        "settled_wins": sum(float(r["pnl_eur"]) > 0 for r in settled),
        "settled_losses": sum(float(r["pnl_eur"]) < 0 for r in settled),
        "settled_stakes_eur": round(total_stake, 2),
        "settled_pnl_eur": round(pnl, 2),
        "roi_on_settled_stakes_pct": round(100*pnl/total_stake, 2) if total_stake else None,
        "by_sport": by_sport,
        "overdue_open_positions": delayed[:50],
        "overdue_count": len(delayed),
        "attention_required": bool(delayed),
        "independent_bets_caveat": "Correlated model selections on one event are NOT independent trials.",
        "paper_only": True,
    }
