"""Prospective-only events and optional market observation.

The scanner NEVER places bets and never invents odds. A frozen observation is
created only if the full market, model, event time and source are known.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path

from .feeds import SCOREBOARDS, fetch_espn
from .football import FootballElo, canonical
from .poisson import FootballPoisson
from .metrics import american_to_decimal, brier, log_loss, no_vig

UTC = dt.timezone.utc


def parse_iso(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp without timezone")
    return parsed.astimezone(UTC)


def extract_odds(competition: dict, sport: str) -> dict | None:
    """ESPN prices are unofficial/unversioned; strictly require one complete quote."""
    options = competition.get("odds") or []
    if not isinstance(options, list):
        return None
    for item in options:
        if not isinstance(item, dict):
            continue
        try:
            prices = [
                american_to_decimal(item["homeTeamOdds"]["moneyLine"]),
                american_to_decimal(item["awayTeamOdds"]["moneyLine"]),
            ]
            if sport in {"epl", "la_liga", "bundesliga", "serie_a", "ligue_1"}:
                draw = item.get("drawOdds", {})
                prices.insert(1, american_to_decimal(draw["moneyLine"]))
            normalized = no_vig(prices)
            return {"decimal": [round(x, 5) for x in prices],
                    "no_vig": [round(x, 7) for x in normalized],
                    "provider": str((item.get("provider") or {}).get("name", "ESPN score feed")),
                    "market": "1X2" if len(prices) == 3 else "ML",
                    "quote_timestamp_available": False}
        except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError):
            continue
    return None


def unpack_scoreboard(payload: dict, sport: str, observed_at: dt.datetime) -> list[dict]:
    result = []
    for ev in payload.get("events", []):
        try:
            kickoff = parse_iso(ev["date"])
            competition = ev["competitions"][0]
            competitors = competition["competitors"]
            home = next(c for c in competitors if c["homeAway"] == "home")
            away = next(c for c in competitors if c["homeAway"] == "away")
            home_name, away_name = home["team"]["displayName"], away["team"]["displayName"]
            event_id = f"{sport}:{ev['id']}"
            status = ev.get("status", {}).get("type", {})
            completed = bool(status.get("completed", False))
            match_state = str(status.get("state", "unknown"))
            final = None
            if completed:
                hs, as_ = int(home["score"]), int(away["score"])
                final = {"home": hs, "away": as_,
                         "outcome": 0 if hs > as_ else (1 if hs == as_ else 2)}
            # Odds are relevant only for scheduled events not yet started.
            quote = extract_odds(competition, sport) if (kickoff > observed_at and match_state == "pre") else None
            result.append({"id": event_id, "sport": sport, "home": home_name, "away": away_name,
                           "start_utc": kickoff.isoformat(), "status": match_state,
                           "completed": completed, "final": final, "quote": quote})
        except (KeyError, ValueError, TypeError, StopIteration, IndexError):
            continue
    return result


def capture_scoreboards(now: dt.datetime, fetcher=fetch_espn, sports: dict | None = None,
                        future_days: int = 2, need_yesterday: bool = True) -> tuple[list[dict], dict]:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    sports = SCOREBOARDS if sports is None else sports
    days = list(range(-1 if need_yesterday else 0, future_days + 1))
    all_events: dict[str, dict] = {}
    report = {"sources_checked": 0, "sources_ok": 0, "sources_failed": 0,
              "errors": [], "provenance": [], "by_sport": {}}
    for sport, route in sports.items():
        report["by_sport"][sport] = 0
        for offset in days:
            report["sources_checked"] += 1
            day = now.date() + dt.timedelta(days=offset)
            try:
                payload, metadata = fetcher(route, day)
                report["sources_ok"] += 1
                report["provenance"].append(metadata)
                for event in unpack_scoreboard(payload, sport, now):
                    # Later responses may have a more recent status.
                    previous = all_events.get(event["id"])
                    if previous is None or (not previous["completed"] and event["completed"]):
                        all_events[event["id"]] = event
            except Exception as exc:
                report["sources_failed"] += 1
                report["errors"].append({"sport": sport, "date": day.isoformat(),
                                         "error": type(exc).__name__ + ": " + str(exc)[:150]})
    for event in all_events.values():
        report["by_sport"][event["sport"]] += 1
    return list(all_events.values()), report


def classify(event: dict, model: FootballElo | FootballPoisson | None, now: dt.datetime) -> dict | None:
    kickoff = parse_iso(event["start_utc"])
    if not (now < kickoff <= now + dt.timedelta(hours=48)) or event["status"] != "pre":
        return None
    record = {k: event[k] for k in ("id", "sport", "home", "away", "start_utc")}
    record["model_version"] = None
    record["model_probabilities"] = None
    record["market"] = event["quote"]
    record["classification"] = "FIXTURE_ONLY"
    record["reason"] = "No calibrated model for sport"
    if event["sport"] == "epl" and model is not None:
        home, away = canonical(event["home"]), canonical(event["away"])
        if model.played.get(home, 0) >= 8 and model.played.get(away, 0) >= 8:
            record["model_probabilities"] = [round(p, 7) for p in model.predict(home, away)]
            record["model_version"] = "POISSON-SHRINK-0.3" if isinstance(model, FootballPoisson) else "ELO-FOOTBALL-0.3"
            record["classification"] = "EXPERIMENTAL"
            record["reason"] = "No complete fresh 1X2 odds" if not event["quote"] else "Paper criteria not validated"
        else:
            record["reason"] = "Insufficient historical matches for teams"
    if event["quote"] and record["model_probabilities"]:
        diffs = [a - b for a, b in zip(record["model_probabilities"], event["quote"]["no_vig"])]
        record["best_model_edge_pp"] = round(100 * max(diffs), 3)
        record["best_model_selection"] = ["HOME", "DRAW", "AWAY"][diffs.index(max(diffs))]
        # First-stage observation only: fixed heuristic risk band, NOT a confidence interval.
        record["heuristic_uncertainty_pp"] = 8.0
        if max(diffs) >= 0.02:
            record["classification"] = "OBSERVATION_CANDIDATE"
            record["reason"] = "Uncalibrated experimental Elo; no stake"
    return record


def freeze_observations(candidates: list[dict], file: Path, now: dt.datetime,
                        max_per_day: int = 10) -> tuple[list[dict], int]:
    """Append-only initially; settlements update separate fields later, never forecasts."""
    existing = []
    if file.exists():
        for line in file.read_text(encoding="utf8").splitlines():
            if line.strip():
                existing.append(json.loads(line))
    seen = {(x["event_id"], x["market"], x["model_version"]) for x in existing}
    count_today = sum(1 for x in existing if x["recorded_utc"][:10] == now.date().isoformat())
    new = []
    for c in sorted(candidates, key=lambda x: -x.get("best_model_edge_pp", 0)):
        if c["classification"] != "OBSERVATION_CANDIDATE" or count_today >= max_per_day:
            continue
        market = c["market"]["market"]
        key = (c["id"], market, c["model_version"])
        if key in seen:
            continue
        # Provider odds lack a quote timestamp; observation only, not a tradable recommendation.
        record = {"id": f"OBS3-{hashlib.sha256(('|'.join(key)).encode()).hexdigest()[:12]}",
                  "event_id": c["id"], "sport": c["sport"], "home": c["home"], "away": c["away"],
                  "start_utc": c["start_utc"], "recorded_utc": now.isoformat(),
                  "market": market, "model_version": c["model_version"],
                  "probabilities": c["model_probabilities"],
                  "market_no_vig": c["market"]["no_vig"],
                  "decimal_odds": c["market"]["decimal"],
                  "provider": c["market"]["provider"],
                  "bookmakers": c["market"].get("bookmakers"),
                  "book_quote_times_utc": c["market"].get("best_book_updates"),
                  "quote_timestamp_available": c["market"].get("quote_timestamp_available", False),
                  "uncertainty_method": "heuristic_8pp_not_statistical_interval",
                  "stake_eur": 0, "status": "OPEN"}
        existing.append(record)
        new.append(record)
        seen.add(key)
        count_today += 1
    file.parent.mkdir(parents=True, exist_ok=True)
    if new:
        file.write_text("\n".join(json.dumps(x, ensure_ascii=False, sort_keys=True) for x in existing) + "\n", encoding="utf8")
    return existing, len(new)


def settle_observations(records: list[dict], events: list[dict], file: Path) -> int:
    finals = {event["id"]: event["final"] for event in events if event["completed"] and event["final"]}
    modified = 0
    for r in records:
        if r.get("status") != "OPEN" or r["event_id"] not in finals:
            continue
        result = finals[r["event_id"]]
        # For NFL/NBA/MLB/NHL, model forecasts are two-way; draws cannot settle two-way ML.
        idx = result["outcome"]
        if len(r["probabilities"]) == 2:
            if idx == 1:
                continue
            idx = 0 if idx == 0 else 1
        r["status"], r["result_outcome"] = "SETTLED", idx
        r["brier"], r["log_loss"] = round(brier(r["probabilities"], idx), 7), round(log_loss(r["probabilities"], idx), 7)
        modified += 1
    if modified:
        file.write_text("\n".join(json.dumps(x, ensure_ascii=False, sort_keys=True) for x in records) + "\n", encoding="utf8")
    return modified
