"""Optional verified odds adapter with a strict free-tier call budget.

The Odds API is an OPTIONAL provider. No key => no paid calls, no fabricated odds.
One market (h2h), one region (eu). No API credentials are ever written to disk.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .feeds import USER_AGENT
from .football import canonical
from .metrics import no_vig

UTC = dt.timezone.utc
ODDS_KEY_MAP = {
    "epl": "soccer_epl", "nfl": "americanfootball_nfl",
    "nba": "basketball_nba", "nhl": "icehockey_nhl", "mlb": "baseball_mlb"
}
# Five sports, twice daily: <=10 credits/day; ~300/month, below a 500-credit free plan.
POLL_HOURS_UTC = (8, 20)
MAX_CALLS_PER_DAY = 10
MAX_SNAPSHOT_AGE_HOURS = 13
MAX_BOOK_QUOTE_AGE_HOURS = 12


def key_for(name: str) -> str:
    return canonical(name)


def _request_odds(sport: str, secret: str) -> list[dict]:
    parameters = urllib.parse.urlencode({
        "apiKey": secret, "regions": "eu", "markets": "h2h",
        "oddsFormat": "decimal", "dateFormat": "iso"
    })
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/odds/?{parameters}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError("Odds response too large")
        parsed = json.loads(data)
        if not isinstance(parsed, list):
            raise ValueError("Unexpected odds response schema")
        return parsed
    except urllib.error.HTTPError as exc:
        # NEVER include the full URL or raw error; that would leak apiKey.
        raise ValueError(f"Odds API HTTP {exc.code}") from None
    except urllib.error.URLError:
        raise ConnectionError("Odds API unreachable") from None


def parse_utc(text: str) -> dt.datetime:
    value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("Missing UTC offset")
    return value.astimezone(UTC)


def quotes_for_event(event: dict, rows: list[dict], at: dt.datetime) -> dict | None:
    if event["status"] != "pre" or parse_utc(event["start_utc"]) <= at:
        return None
    is_three_way = event["sport"] == "epl"
    matched = []
    for row in rows:
        try:
            if key_for(row["home_team"]) != key_for(event["home"]) or key_for(row["away_team"]) != key_for(event["away"]):
                continue
            if abs((parse_utc(row["commence_time"]) - parse_utc(event["start_utc"])).total_seconds()) > 7200:
                continue
            for book in row["bookmakers"]:
                if not isinstance(book, dict):
                    continue
                for market in book.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    updated = parse_utc(market.get("last_update") or book["last_update"])
                    age = (at - updated).total_seconds()
                    if age < -120 or age > MAX_BOOK_QUOTE_AGE_HOURS * 3600:
                        continue
                    outcomes = {key_for(o["name"]): float(o["price"]) for o in market["outcomes"]}
                    prices = [
                        outcomes[key_for(row["home_team"])],
                        *([outcomes["draw"]] if is_three_way else []),
                        outcomes[key_for(row["away_team"])]
                    ]
                    p = no_vig(prices)
                    matched.append({"bookmaker": book["key"], "updated": updated.isoformat(),
                                    "decimal": prices, "no_vig": p})
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    if not matched:
        return None
    # Market consensus = average of bookmaker-specific vig-free probabilities.
    n = len(matched)
    consensus = [sum(book["no_vig"][i] for book in matched) / n for i in range(len(matched[0]["no_vig"]))]
    best = [max(matched, key=lambda q: q["decimal"][i]) for i in range(len(consensus))]
    return {
        "market": "1X2" if is_three_way else "ML",
        "decimal": [round(x["decimal"][i], 5) for i, x in enumerate(best)],
        "no_vig": [round(x, 7) for x in consensus],
        "bookmakers": [x["bookmaker"] for x in best],
        "best_book_updates": [x["updated"] for x in best],
        "provider": "The Odds API v4",
        "bookmaker_count": n,
        "quote_timestamp_available": True,
        "scope": "EU bookmaker quotes; consensus no-vig and best prices across books"
    }


def annotate_odds(events: list[dict], root: Path, now: dt.datetime,
                  secret: str | None = None, requester=_request_odds) -> dict:
    """Updates odds cache only during designated hours and within daily budget."""
    now = now.astimezone(UTC)
    secret = os.getenv("ODDS_API_KEY", "") if secret is None else secret
    cache_path = root / "data" / "odds_cache_v3.json"
    record = {"day": now.date().isoformat(), "calls_today": 0, "snapshots": {}}
    if cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text(encoding="utf8"))
            if isinstance(loaded.get("snapshots"), dict):
                record["snapshots"] = loaded["snapshots"]
            if loaded.get("day") == record["day"]:
                record["calls_today"] = max(0, int(loaded.get("calls_today", 0)))
        except (ValueError, KeyError, TypeError):
            pass
    diagnostics = {"configured": bool(secret), "calls_this_run": 0,
                   "cached_sports": [], "quotes_matched": 0, "errors": []}
    if secret and now.hour in POLL_HOURS_UTC:
        for sport, sport_key in ODDS_KEY_MAP.items():
            if record["calls_today"] >= MAX_CALLS_PER_DAY:
                break
            snap = record["snapshots"].get(sport)
            if snap and snap.get("fetched_at", "")[:13] == now.isoformat()[:13]:
                continue
            # Count even failed calls conservatively to protect free quota.
            record["calls_today"] += 1
            diagnostics["calls_this_run"] += 1
            try:
                rows = requester(sport_key, secret)
                limited = {event["id"]: q for event in events if event["sport"] == sport
                           if (q := quotes_for_event(event, rows, now)) is not None}
                # Never redistribute a provider's bulk feed in our public repository.
                record["snapshots"][sport] = {"fetched_at": now.isoformat(), "quotes": limited}
            except Exception as exc:
                diagnostics["errors"].append({"sport": sport, "reason": type(exc).__name__ + ": " + str(exc)[:100]})
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf8")
    for event in events:
        snap = record["snapshots"].get(event["sport"])
        if not snap:
            continue
        try:
            if (now - parse_utc(snap["fetched_at"])).total_seconds() > MAX_SNAPSHOT_AGE_HOURS * 3600:
                continue
            if now < parse_utc(snap["fetched_at"]):
                continue
            quote = snap["quotes"].get(event["id"])
            if quote and any((now - parse_utc(timestamp)).total_seconds() > MAX_BOOK_QUOTE_AGE_HOURS * 3600
                             for timestamp in quote["best_book_updates"]):
                quote = None
            if quote:
                event["quote"] = quote
                diagnostics["quotes_matched"] += 1
                if event["sport"] not in diagnostics["cached_sports"]:
                    diagnostics["cached_sports"].append(event["sport"])
        except (KeyError, ValueError, TypeError):
            continue
    diagnostics["calls_today"] = record["calls_today"]
    diagnostics["status"] = "OK" if secret else "MISSING_KEY_NO_ODDS_API_CALLS"
    return diagnostics
