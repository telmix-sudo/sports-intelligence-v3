"""Daily market-consensus REFERENCE account, deliberately NOT a value strategy.

The control makes a modest, transparent virtual selection from a real bookmaker
snapshot to measure how a basic market favourite performs relative to our models.
Never invent events, results or prices to meet the desired one-per-day cadence.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from .scanner import parse_iso

NAME = "MARKET_DAILY_REFERENCE"
LOCAL = ZoneInfo("Europe/Lisbon")
MAX_QUOTE_AGE_HOURS = 6
MAX_GAME_AHEAD_HOURS = 48
FRACTION = .005
MAX_EXPOSURE = .02


def local_day(timestamp: dt.datetime) -> str:
    return timestamp.astimezone(LOCAL).date().isoformat()


def quote_for_reference(e: dict, now: dt.datetime) -> tuple | None:
    """The Odds API verified prices only. No ESPN non-timestamped odds."""
    try:
        kickoff = parse_iso(e["start_utc"])
        if (e.get("status") != "pre" or
            not (now < kickoff <= now + dt.timedelta(hours=MAX_GAME_AHEAD_HOURS))):
            return None
        quote = e.get("quote") or {}
        if (quote.get("provider") != "The Odds API v4" or
            not quote.get("quote_timestamp_available") or
            int(quote.get("bookmaker_count", 0)) < 2):
            return None
        market = quote["market"]
        required = 3 if e["sport"] in ("epl", "la_liga", "bundesliga", "serie_a", "ligue_1") else 2
        if market != ("1X2" if required == 3 else "ML"):
            return None
        prices = [float(x) for x in quote["decimal"]]
        p = [float(x) for x in quote["no_vig"]]
        updates = quote["best_book_updates"]
        books = quote["bookmakers"]
        if (len(prices) != required or len(p) != required or
            len(updates) != required or len(books) != required or
            abs(sum(p)-1) > 0.001 or
            any(not math.isfinite(x) or x <= 1 for x in prices) or
            any(not math.isfinite(x) or x <= 0 or x >= 1 for x in p)):
            return None
        for updated in updates:
            age = (now - parse_iso(updated)).total_seconds()
            if not (-120 <= age <= MAX_QUOTE_AGE_HOURS*3600):
                return None
        return kickoff, prices, p, updates, books, quote
    except (TypeError, ValueError, KeyError, OverflowError):
        return None


def select_daily_reference(root: Path, events: list[dict], now: dt.datetime,
                           registry: dict) -> dict:
    if now.tzinfo is None:
        raise ValueError("Naive clock cannot create paper forecast")
    now = now.astimezone(dt.timezone.utc)
    day = local_day(now)
    ledger = root / "data" / "strategy_ledgers" / (NAME.lower()+".jsonl")
    records = read_jsonl(ledger)
    started = parse_iso(registry[NAME]["created_utc"])
    daily = [r for r in records if local_day(parse_iso(r["forecast_utc"])) == day]
    diagnostics = {"local_date": day, "target": 1, "opened_today": len(daily),
                   "new_entries": 0, "reference": True,
                   "not_a_value_signal": True,
                   "status": "COMPLETED" if daily else "NO_ELIGIBLE_QUOTE",
                   "reason": None}
    if now < started or daily:
        diagnostics["reason"] = "already_recorded" if daily else "not_registered_yet"
        return diagnostics
    bankroll = 1000 + sum(float(r["pnl_eur"]) for r in records if r.get("status") == "SETTLED")
    exposure = sum(float(r["stake_eur"]) for r in records if r.get("status") == "OPEN")
    stake = round(min(bankroll * FRACTION, max(0, bankroll * MAX_EXPOSURE - exposure)), 2)
    if stake <= 0:
        diagnostics["status"] = "MAX_EXPOSURE"
        diagnostics["reason"] = "No available virtual exposure"
        return diagnostics
    seen = {r["event_id"] for r in records}
    candidates = []
    for event in events:
        if event["id"] in seen:
            continue
        details = quote_for_reference(event, now)
        if details:
            start, prices, probs, timestamps, books, quote = details
            idx = max(range(len(probs)), key=lambda i: probs[i])
            candidates.append((start, -probs[idx], event["id"], event, idx, prices, probs, timestamps, books, quote))
    diagnostics["eligible_events"] = len(candidates)
    if not candidates:
        diagnostics["reason"] = "No upcoming event with authentic, timely 1X2/ML odds from at least two bookmakers"
        return diagnostics
    _, _, _, event, pick, odds, p, timestamps, books, quote = min(candidates)
    key = NAME+"|"+event["id"]+"|"+day
    record = {
        "id": "PREF-"+hashlib.sha256(key.encode()).hexdigest()[:14],
        "strategy": NAME, "event_id": event["id"], "sport": event["sport"],
        "home": event["home"], "away": event["away"], "start_utc":event["start_utc"],
        "forecast_utc": now.isoformat(), "market":quote["market"],
        "probabilities": p, "no_vig":p,
        "odds": odds, "selection_index":pick,
        "stake_eur":stake, "status":"OPEN", "pnl_eur":None,
        "price_source":quote["provider"], "bookmakers":books,
        "book_quote_updates_utc":timestamps,
        "strategy_version":"1.0.0",
        "strategy_registered_utc":registry[NAME]["created_utc"],
        "model_status":"MARKET_REFERENCE_NO_ESTIMATED_EDGE",
        "classification":"EXPLORATORY_DAILY_BASELINE_NOT_VALUE_BET",
        "edge_pp":0, "selection_expected_value":round(p[pick]*odds[pick]-1,7),
        "sizing_note":"0.5% of independent virtual bankroll; market favourite reference only; no demonstrated edge and no guarantee of execution.",
    }
    records.append(record)
    save_jsonl(ledger, records)
    diagnostics.update({"opened_today": 1, "new_entries": 1,
                        "status":"COMPLETED", "reason":"Genuine pre-game odds reference",
                        "event_id": event["id"],"sport":event["sport"],"kickoff_utc":event["start_utc"]})
    return diagnostics
