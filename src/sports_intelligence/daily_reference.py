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
MAX_QUOTE_AGE_HOURS = 8
MAX_GAME_AHEAD_HOURS = 48
DAILY_TARGET_MIN = 1
DAILY_TARGET_MAX = 3
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
    """Up to three distinct, genuine pre-game paper references per Lisbon day.

    This portfolio is a market BASELINE, not a prediction with demonstrated edge.
    The one-per-day minimum is an operational target, never a fabricated bet.
    """
    if now.tzinfo is None:
        raise ValueError("Naive clock cannot create paper forecast")
    now = now.astimezone(dt.timezone.utc)
    day = local_day(now)
    from .strategy_lab import read_jsonl, save_jsonl
    ledger = root / "data" / "strategy_ledgers" / (NAME.lower()+".jsonl")
    records = read_jsonl(ledger)
    if not ledger.exists():
        save_jsonl(ledger, [])
    started = parse_iso(registry[NAME]["created_utc"])
    daily = [r for r in records if local_day(parse_iso(r["forecast_utc"])) == day]
    previous_ids = {r["event_id"] for r in records}
    diagnostics = {
        "local_date": day, "target": DAILY_TARGET_MIN,
        "max_per_day": DAILY_TARGET_MAX,
        "opened_today": len(daily), "new_entries": 0,
        "reference": True, "not_a_value_signal": True,
        "status": "NO_ELIGIBLE_QUOTE", "reason": None,
        "eligible_events": 0,
    }
    if now < started:
        diagnostics.update(status="NOT_REGISTERED_YET", reason="Strategy not yet registered")
        return diagnostics
    if len(daily) >= DAILY_TARGET_MAX:
        diagnostics.update(status="DAILY_CAP", reason="Daily cap of 3 separate events reached")
        return diagnostics
    bankroll = 1000 + sum(float(r["pnl_eur"]) for r in records if r.get("status") == "SETTLED")
    exposure = sum(float(r["stake_eur"]) for r in records if r.get("status") == "OPEN")
    candidates = []
    for event in events:
        if event["id"] in previous_ids:
            continue
        details = quote_for_reference(event, now)
        if details:
            start, odds, probs, timestamps, books, quote = details
            idx = max(range(len(probs)), key=lambda i: probs[i])
            # Sport diversity first, then nearer kickoffs; no arbitrary model edge.
            candidates.append((start, -probs[idx], event["id"], event,
                               idx, odds, probs, timestamps, books, quote))
    diagnostics["eligible_events"] = len(candidates)
    if not candidates:
        diagnostics.update(status="COMPLETED" if daily else "NO_ELIGIBLE_QUOTE",
                           reason=("Existing reference(s) already registered today" if daily else
                                   "No upcoming event with recent, genuine odds from 2+ bookmakers"))
        return diagnostics
    # Prefer different sports within the day where possible. Re-rank after
    # EACH opening: static sorting could choose three games from the same league.
    used_sports = {r.get("sport") for r in daily}
    while candidates and len(daily) < DAILY_TARGET_MAX:
        candidates.sort(key=lambda x: (x[3]["sport"] in used_sports, x[0], x[1], x[2]))
        _, _, _, event, pick, odds, p, timestamps, books, quote = candidates.pop(0)
        stake = round(min(bankroll * FRACTION,
                          max(0., bankroll * MAX_EXPOSURE - exposure)), 2)
        if stake <= 0:
            diagnostics["reason"] = "Maximum exposure (2% of virtual bank) reached"
            break
        record = {
            "id": "PREF-"+hashlib.sha256((NAME+"|"+event["id"]).encode()).hexdigest()[:14],
            "strategy": NAME, "event_id": event["id"], "sport": event["sport"],
            "home": event["home"], "away": event["away"], "start_utc": event["start_utc"],
            "forecast_utc": now.isoformat(), "market": quote["market"],
            "probabilities": list(p), "no_vig": list(p),
            "odds": list(odds), "selection_index": pick,
            "stake_eur": stake, "status": "OPEN",
            "price_source": quote["provider"], "bookmakers": list(books),
            "book_quote_updates_utc": list(timestamps),
            "strategy_version": "2.0.0", "strategy_registered_utc": registry[NAME]["created_utc"],
            "model_status": "MARKET_REFERENCE_NO_ESTIMATED_EDGE",
            "classification": "EXPLORATORY_DAILY_BASELINE_NOT_VALUE_BET",
            "edge_pp": 0, "selection_expected_value": round(p[pick]*odds[pick]-1, 7),
            "sizing_note": "0.5% independent virtual bank, max 2% exposure; price execution unconfirmed.",
        }
        records.append(record)
        daily.append(record)
        previous_ids.add(event["id"])
        used_sports.add(event["sport"])
        exposure += stake
        diagnostics["new_entries"] += 1
    if diagnostics["new_entries"]:
        save_jsonl(ledger, records)
    diagnostics["opened_today"] = len(daily)
    diagnostics["status"] = ("DAILY_CAP" if len(daily) >= DAILY_TARGET_MAX else
                             "PARTIAL" if daily else
                             "NO_ELIGIBLE_QUOTE")
    if diagnostics["reason"] is None:
        diagnostics["reason"] = (
            "Genuine timestamped paper selections; reference only, no demonstrated edge"
            if daily else "No valid quote or remaining exposure")
    diagnostics["distinct_event_ids_today"] = sorted({r["event_id"] for r in daily})
    return diagnostics
