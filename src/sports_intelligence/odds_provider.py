"""Optional verified odds adapter with a strict free-tier call budget.

The Odds API is an OPTIONAL provider. No key => no paid calls, no fabricated odds.
Featured markets plus targeted BTTS, one region (eu). Budget counts CREDITS, not HTTP calls.
No API credentials are ever written to disk.
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
    "epl": "soccer_epl",
    "la_liga": "soccer_spain_la_liga",
    "bundesliga": "soccer_germany_bundesliga",
    "serie_a": "soccer_italy_serie_a",
    "ligue_1": "soccer_france_ligue_one",
    "nfl": "americanfootball_nfl",
    "nba": "basketball_nba",
    "nhl": "icehockey_nhl",
    "mlb": "baseball_mlb",
}
# Ten daily requests split across four time windows, nearer to kick-off.
# Preserve the same maximum API quota; no extra paid requests.
SPORTS_PER_POLL = {
    2: (),  # Legacy slot retained for idempotent cache migration; no new calls.
    8: ("epl",),
    14: ("nfl", "la_liga", "bundesliga"),
    20: ("nba", "nhl", "mlb"),
}
POLL_HOURS_UTC = tuple(SPORTS_PER_POLL)
MAX_CALLS_PER_DAY = 16  # backwards compatible name: HTTP requests
MAX_CREDITS_PER_DAY = 16
FOOTBALL = {"epl", "la_liga", "bundesliga", "serie_a", "ligue_1"}
MAX_SNAPSHOT_AGE_HOURS = 13
MAX_BOOK_QUOTE_AGE_HOURS = 12


def key_for(name: str) -> str:
    return canonical(name)


class OddsRows(list):
    """List-compatible response with non-secret quota headers."""
    credits_used = None
    credits_remaining = None


def _request_odds(sport: str, secret: str, markets: str = "h2h",
                  event_id: str | None = None, discover: bool = False, events_only: bool = False) -> list[dict]:
    parameters = urllib.parse.urlencode({
        "apiKey": secret, "regions": "eu", "markets": markets,
        "oddsFormat": "decimal", "dateFormat": "iso"
    })
    if events_only:
        parameters = urllib.parse.urlencode({"apiKey":secret,"dateFormat":"iso"})
    suffix = f"events/{urllib.parse.quote(event_id, safe='')}/{'markets' if discover else 'odds'}" if event_id else "events" if events_only else "odds"
    url = f"https://api.the-odds-api.com/v4/sports/{sport}/{suffix}?{parameters}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            data = response.read(2_000_001)
            last = response.headers.get("x-requests-last")
            remaining = response.headers.get("x-requests-remaining")
        if len(data) > 2_000_000:
            raise ValueError("Odds response too large")
        parsed = json.loads(data)
        if event_id and isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            raise ValueError("Unexpected odds response schema")
        result = OddsRows(parsed)
        result.credits_used = int(last) if last is not None else None
        result.credits_remaining = int(remaining) if remaining is not None else None
        return result
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
    is_three_way = event["sport"] in {"epl", "la_liga", "bundesliga", "serie_a", "ligue_1"}
    matched = []
    for row in rows:
        try:
            if key_for(row["home_team"]) != key_for(event["home"]) or key_for(row["away_team"]) != key_for(event["away"]):
                continue
            if abs((parse_utc(row["commence_time"]) - parse_utc(event["start_utc"])).total_seconds()) > 1800:
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
    # Use each bookmaker only once; duplicated rows or stale reruns cannot
    # artificially inflate consensus confidence or the source count.
    deduplicated = {}
    for quote in matched:
        book = quote["bookmaker"]
        if book not in deduplicated or quote["updated"] > deduplicated[book]["updated"]:
            deduplicated[book] = quote
    matched = list(deduplicated.values())
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
        "bookmaker_count_is_unique": True,
        "complete_book_quotes": [{"bookmaker": q["bookmaker"], "decimal": q["decimal"],
                                 "no_vig": q["no_vig"], "updated_utc": q["updated"]} for q in matched],
        "start_time_tolerance_seconds": 1800,
        "quote_timestamp_available": True,
        "scope": "EU bookmaker quotes; consensus no-vig and best prices across books"
    }


def market_quotes_for_event(event: dict, rows: list[dict], at: dt.datetime) -> dict:
    """Return separately priced complete binary goal markets at exact lines."""
    if event["sport"] not in FOOTBALL or event["status"] != "pre" or parse_utc(event["start_utc"]) <= at:
        return {}
    grouped = {}
    for row in rows:
        try:
            if (key_for(row["home_team"]) != key_for(event["home"]) or
                key_for(row["away_team"]) != key_for(event["away"]) or
                abs((parse_utc(row["commence_time"])-parse_utc(event["start_utc"])).total_seconds()) > 1800):
                continue
            for book in row["bookmakers"]:
                for market in book.get("markets", []):
                    try:
                        stamp = parse_utc(market.get("last_update") or book["last_update"])
                        if not 0 <= (at-stamp).total_seconds() <= MAX_BOOK_QUOTE_AGE_HOURS*3600:
                            continue
                        outcomes = market["outcomes"]
                        key = market["key"]
                        if key == "totals":
                            pair = {key_for(o["name"]):o for o in outcomes if float(o.get("point", -999)) == 2.5}
                            values = [pair["over"]["price"], pair["under"]["price"]]
                            name = "OVER_UNDER_2_5"
                        elif key == "btts":
                            pair = {key_for(o["name"]):o for o in outcomes}
                            values = [pair["yes"]["price"], pair["no"]["price"]]
                            name = "BTTS"
                        elif key == "spreads":
                            pair = {key_for(o["name"]):o for o in outcomes}
                            h, a = pair[key_for(row["home_team"])], pair[key_for(row["away_team"])]
                            if float(h["point"]) != -.5 or float(a["point"]) != .5:
                                continue
                            values = [h["price"], a["price"]]
                            name = "ASIAN_HOME_MINUS_0_5"
                        else:
                            continue
                        prices = [float(v) for v in values]
                        normalized = no_vig(prices)
                        quote = {"decimal": prices, "no_vig": normalized, "bookmaker": book["key"],
                                 "updated": stamp.isoformat(), "provider_event_id": row.get("id")}
                        previous = grouped.setdefault(name, {}).get(book["key"])
                        if previous is None or quote["updated"] > previous["updated"]:
                            grouped[name][book["key"]] = quote
                    except (ValueError, KeyError, TypeError, OverflowError):
                        continue
        except (ValueError, KeyError, TypeError):
            continue
    result = {}
    for name, books in grouped.items():
        quotes = list(books.values())
        best = [max(quotes, key=lambda q:q["decimal"][i]) for i in range(2)]
        result[name] = {"market": name, "decimal": [best[i]["decimal"][i] for i in range(2)],
                        "no_vig": [sum(q["no_vig"][i] for q in quotes)/len(quotes) for i in range(2)],
                        "bookmakers": [q["bookmaker"] for q in best],
                        "best_book_updates": [q["updated"] for q in best],
                        "bookmaker_count": len(books), "bookmaker_count_is_unique": True,
                        "complete_book_quotes": [{"bookmaker":q["bookmaker"], "decimal":q["decimal"],
                                                  "no_vig":q["no_vig"], "updated_utc":q["updated"]} for q in quotes],
                        "quote_timestamp_available": True, "provider": "The Odds API v4",
                        "rules": "REGULATION_90_MINUTES", "captured_at_utc": at.isoformat()}
    return result


def _provider_id(event, rows):
    for row in rows:
        try:
            if (key_for(row["home_team"]) == key_for(event["home"]) and
                key_for(row["away_team"]) == key_for(event["away"]) and
                abs((parse_utc(row["commence_time"])-parse_utc(event["start_utc"])).total_seconds()) <= 1800):
                return row.get("id")
        except (KeyError, ValueError, TypeError):
            pass
    return None


def annotate_odds(events: list[dict], root: Path, now: dt.datetime,
                  secret: str | None = None, requester=_request_odds) -> dict:
    """Updates odds cache only during designated hours and within daily budget."""
    now = now.astimezone(UTC)
    secret = os.getenv("ODDS_API_KEY", "") if secret is None else secret
    cache_path = root / "data" / "odds_cache_v3.json"
    record = {"day": now.date().isoformat(), "calls_today": 0,
              "snapshots": {}, "polled_windows": [], "credits_today": 0, "btts_attempted": [], "event_markets_done": [], "market_inventory": {}}
    if cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text(encoding="utf8"))
            if isinstance(loaded.get("snapshots"), dict):
                record["snapshots"] = loaded["snapshots"]
            if loaded.get("day") == record["day"]:
                record["calls_today"] = max(0, int(loaded.get("calls_today", 0)))
                record["polled_windows"] = loaded.get("polled_windows", [])
                record["credits_today"] = max(record["calls_today"], int(loaded.get("credits_today", record["calls_today"])))
                record["btts_attempted"] = loaded.get("btts_attempted", [])
                record["event_markets_done"] = loaded.get("event_markets_done", [])
                record["market_inventory"] = loaded.get("market_inventory", {})
            # Probe quota again on a new day: a previous billing period may have reset.
            record["credits_remaining"] = loaded.get("credits_remaining") if loaded.get("day") == record["day"] else None
        except (ValueError, KeyError, TypeError):
            pass
    diagnostics = {"configured": bool(secret), "calls_this_run": 0,
                   "cached_sports": [], "quotes_matched": 0, "errors": []}
    # When GitHub skips or delays cron jobs, catch up EACH missed daily slot
    # within the same daily budget. Never substitute later quotes for earlier ones
    # in historical decisions: only future unstarted events enter the cache.
    pending_slots = [
        hour for hour in POLL_HOURS_UTC
        if hour <= now.hour and hour not in record["polled_windows"]
    ]
    for slot in pending_slots if secret else ():
        complete = True
        sports = SPORTS_PER_POLL[slot]
        if slot == 14:
            rotation = ("la_liga", "bundesliga", "serie_a", "ligue_1")
            offset = 2*(now.date().toordinal() % 2)
            sports = ("nfl",) + rotation[offset:offset+2]
        for sport in sports:
            if record["calls_today"] >= MAX_CALLS_PER_DAY:
                complete = False
                break
            markets = "h2h,totals,spreads" if sport == "epl" else "h2h"
            cost = len(markets.split(","))
            if (record["credits_today"] + cost > MAX_CREDITS_PER_DAY or
                record.get("credits_remaining") is not None and record["credits_remaining"] < cost):
                diagnostics["errors"].append({"sport": sport, "reason": "CREDIT_BUDGET_EXHAUSTED"})
                complete = False
                break
            sport_key = ODDS_KEY_MAP[sport]
            snap = record["snapshots"].get(sport)
            if (snap and snap.get("fetched_at", "")[:10] == record["day"] and
                    parse_utc(snap["fetched_at"]).hour >= slot):
                continue
            record["calls_today"] += 1
            diagnostics["calls_this_run"] += 1
            record["credits_today"] += cost
            try:
                rows = requester(sport_key, secret, markets) if requester is _request_odds else requester(sport_key, secret)
                if getattr(rows, "credits_remaining", None) is not None:
                    record["credits_remaining"] = rows.credits_remaining
                limited = {event["id"]: quote for event in events if event["sport"] == sport
                           if (quote := quotes_for_event(event, rows, now)) is not None}
                extras = {event["id"]: market_quotes_for_event(event, rows, now)
                          for event in events if event["sport"] == sport}
                from .market_catalog import catalog_quotes
                for event in events:
                    if event["sport"]==sport:
                        extended,_ = catalog_quotes(event,rows,now)
                        extras.setdefault(event["id"],{}).update(extended)
                ids = {event["id"]: _provider_id(event, rows) for event in events if event["sport"] == sport}
                record["snapshots"][sport] = {"fetched_at": now.isoformat(), "quotes": limited,
                                               "market_quotes": extras, "provider_ids": ids}
            except Exception as exc:
                diagnostics["errors"].append({
                    "sport": sport,
                    "reason": type(exc).__name__ + ": " + str(exc)[:100]
                })
        if complete:
            record["polled_windows"].append(slot)
        if not complete:
            break
    # Extra markets are discovered/priced by EVENT, not fabricated from 1X2.
    # Reserve room for any unpolled featured slots; spend at most 7 extra credits.
    featured_reserve = sum((3 if sport=="epl" else 1) for hour,sports in SPORTS_PER_POLL.items()
                           if hour>now.hour and hour not in record["polled_windows"] for sport in sports)
    extra_budget = min(7, MAX_CREDITS_PER_DAY-record["credits_today"]-featured_reserve)
    if secret and extra_budget>=2:
        from .market_catalog import EXTRA_MARKET_PRIORITY,catalog_quotes
        candidates=sorted((e for e in events if e["sport"] in FOOTBALL and e["status"]=="pre"
                          and now<parse_utc(e["start_utc"])<=now+dt.timedelta(hours=48)),key=lambda e:e["start_utc"])
        for event in candidates:
            snap=record["snapshots"].get(event["sport"],{})
            provider_id=snap.get("provider_ids",{}).get(event["id"])
            if not provider_id and requester is _request_odds:
                try:
                    identity=(_request_odds(ODDS_KEY_MAP[event["sport"]],secret,events_only=True)
                              if requester is _request_odds else requester(ODDS_KEY_MAP[event["sport"]],secret))
                    diagnostics["free_event_identity_calls"]=diagnostics.get("free_event_identity_calls",0)+1
                    provider_id=_provider_id(event,identity)
                    if provider_id:
                        snap=record["snapshots"].setdefault(event["sport"],{"fetched_at":now.isoformat(),"quotes":{}})
                        snap.setdefault("provider_ids",{})[event["id"]]=provider_id
                except Exception as exc:
                    diagnostics["errors"].append({"sport":event["sport"],"reason":"FREE_EVENT_ID_"+type(exc).__name__})
            if not provider_id or provider_id in record["event_markets_done"]:continue
            remaining=record.get("credits_remaining")
            if remaining is not None:extra_budget=min(extra_budget,remaining)
            if extra_budget<2:break
            # Discovery costs 1 credit. It also records unsupported market types.
            record["calls_today"]+=1;record["credits_today"]+=1;diagnostics["calls_this_run"]+=1
            record["event_markets_done"].append(provider_id)
            try:
                available=(_request_odds(ODDS_KEY_MAP[event["sport"]],secret,"h2h",provider_id,True)
                           if requester is _request_odds else requester(ODDS_KEY_MAP[event["sport"]],secret))
                keys=sorted({m["key"] for row in available for b in row.get("bookmakers",[]) for m in b.get("markets",[])})
                record["market_inventory"][event["id"]]={"keys":keys,"captured_at_utc":now.isoformat(),
                    "unpriced_reason":"Provider coverage/model/rules and remaining credit budget determine admission."}
                if getattr(available,"credits_remaining",None) is not None:record["credits_remaining"]=available.credits_remaining
                selected=[k for k in EXTRA_MARKET_PRIORITY if k in keys][:max(0,extra_budget-1)]
                if not selected:continue
                cost=len(selected)
                if record.get("credits_remaining") is not None and record["credits_remaining"]<cost:continue
                record["calls_today"]+=1;record["credits_today"]+=cost;diagnostics["calls_this_run"]+=1
                rows=(_request_odds(ODDS_KEY_MAP[event["sport"]],secret,",".join(selected),provider_id)
                      if requester is _request_odds else requester(ODDS_KEY_MAP[event["sport"]],secret))
                if getattr(rows,"credits_remaining",None) is not None:record["credits_remaining"]=rows.credits_remaining
                quotes=market_quotes_for_event(event,rows,now);extended,_=catalog_quotes(event,rows,now);quotes.update(extended)
                snap.setdefault("market_quotes",{}).setdefault(event["id"],{}).update(quotes)
                snap["fetched_at"]=now.isoformat()
                extra_budget-=1+cost
            except Exception as exc:
                diagnostics["errors"].append({"sport":event["sport"],"reason":"EVENT_MARKETS_"+type(exc).__name__})
            break  # One broad event catalog per day; multi-line markets yield many paired prices.
    if secret:
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
            if event["status"] != "pre" or parse_utc(event["start_utc"]) <= now:
                continue
            event["market_quotes"] = {name:q for name,q in snap.get("market_quotes", {}).get(event["id"], {}).items()
                if all(0 <= (now-parse_utc(t)).total_seconds() <= MAX_BOOK_QUOTE_AGE_HOURS*3600
                       for t in q.get("best_book_updates", [])) and len(q.get("best_book_updates", [])) == 2}
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
    diagnostics["credits_today"] = record["credits_today"]
    diagnostics["max_credits_per_day"] = MAX_CREDITS_PER_DAY
    diagnostics["credits_remaining"] = record.get("credits_remaining")
    diagnostics["market_inventory"] = record["market_inventory"]
    diagnostics["unsupported_requested_markets"] = {"offsides":"NOT_ADVERTISED_BY_CURRENT_PROVIDER", "throw_ins":"NOT_ADVERTISED_BY_CURRENT_PROVIDER", "fouls":"NOT_ADVERTISED_BY_CURRENT_PROVIDER"}
    diagnostics["goal_market_quotes"] = sum(len(e.get("market_quotes", {})) for e in events)
    diagnostics["status"] = "OK" if secret else "MISSING_KEY_NO_ODDS_API_CALLS"
    return diagnostics
