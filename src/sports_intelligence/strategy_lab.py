"""Parallel, isolated zero-real-money strategy accounts with public-domain football data.

Research designs are fixed BEFORE observing the parallel run's results. Comparisons
on a shared historical window are EXPLORATORY, not a held-out strategy-selection test.
No historical ROI is calculated from nonexistent or post-match odds.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

from .feeds import fetch_text, SCOREBOARDS, fetch_espn
from .football import FootballElo, canonical
from .poisson import FootballPoisson
from .metrics import brier, log_loss, no_vig
from .scanner import capture_scoreboards, unpack_scoreboard, parse_iso
from .custom_strategies import (
    ORIGINAL_NAMES, CUSTOM_NAMES, ALL_NAMES, REFERENCE_NAMES, RULES,
    experimental_forecasts, accepts_candidate,
)
from .strategy_registry import ensure_registry, verify_prior_accounts
from .daily_reference import select_daily_reference

UTC = dt.timezone.utc
PORTUGAL = ZoneInfo("Europe/Lisbon")
SOURCE = "https://raw.githubusercontent.com/openfootball/football.json/master"
LEAGUE_CODES = {
    "epl": "en.1",
    "la_liga": "es.1",
    "bundesliga": "de.1",
    "serie_a": "it.1",
    "ligue_1": "fr.1",
}
SEASONS = tuple(f"{y}-{(y+1)%100:02d}" for y in range(2021, 2027))
STRATEGIES = ALL_NAMES
INITIAL_BANKROLL_EUR = 1000.0
EVALUATION_FROM = dt.date(2024, 8, 1)
WARMUP = 100
PAPER_STAKE_FRACTION = 0.005
MAX_PAPER_EXPOSURE_FRACTION = 0.02
EDGE_MINIMUM = 0.04
EV_MINIMUM = 0.02
MAX_DAILY_ENTRIES_PER_ACCOUNT = 4

# Exact-name aliases only. Unrecognized club names are NOT guessed/fuzzy-matched.
ALIASES = {
    "liverpool fc": "liverpool", "arsenal fc": "arsenal", "chelsea fc": "chelsea",
    "manchester city fc": "manchester city", "manchester united fc": "manchester united",
    "tottenham hotspur fc": "tottenham hotspur", "west ham united fc": "west ham united",
    "newcastle united fc": "newcastle united", "brighton hove albion fc": "brighton hove albion",
    "nottingham forest fc": "nottingham forest", "crystal palace fc": "crystal palace",
    "everton fc": "everton", "brentford fc": "brentford", "fulham fc": "fulham",
    "aston villa fc": "aston villa", "afc bournemouth": "afc bournemouth",
    "leicester city fc": "leicester city", "southampton fc": "southampton",
    "ipswich town fc": "ipswich town", "leeds united fc": "leeds united",
    "wolverhampton wanderers fc": "wolverhampton wanderers", "wolves": "wolverhampton wanderers",
    "burnley fc": "burnley", "sunderland afc": "sunderland",
    "coventry city fc": "coventry city", "hull city afc": "hull city",
}

def team_name(name: str) -> str:
    key = canonical(name)
    return ALIASES.get(key, key)


def openfootball_matches(payload: dict, league: str, cutoff: dt.date) -> list[dict]:
    """Read completed match scores only; reject any event on/after cutoff date."""
    if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
        raise ValueError("Unexpected OpenFootball JSON")
    out = []
    for row in payload["matches"]:
        try:
            date = dt.date.fromisoformat(str(row["date"])[:10])
            if date >= cutoff:
                continue
            score = row.get("score")
            if isinstance(score, dict):
                score = score.get("ft")
            if not isinstance(score, list) or len(score) != 2 or any(
                type(x) is not int or x < 0 for x in score
            ):
                continue
            home, away = team_name(row["team1"]), team_name(row["team2"])
            if not home or not away or home == away:
                continue
            out.append({"date": date, "league": league, "home": home, "away": away,
                        "goals_home": score[0], "goals_away": score[1], "odds": None})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def collect_openfootball(cutoff: dt.date, fetcher=fetch_text,
                         seasons=SEASONS, leagues=LEAGUE_CODES) -> tuple[dict, dict]:
    by_league = {}
    provenance = []
    failures = []
    for league, code in leagues.items():
        matches = {}
        for season in seasons:
            url = f"{SOURCE}/{season}/{code}.json"
            try:
                raw, metadata = fetcher(url)
                data = openfootball_matches(json.loads(raw), league, cutoff)
                for m in data:
                    key = (m["date"], m["home"], m["away"])
                    matches[key] = m
                provenance.append({"league": league, "season": season, "count": len(data),
                                   "url": url, "sha256": metadata.get("sha256")})
            except Exception as exc:
                failures.append({"league": league, "season": season, "error": type(exc).__name__})
        by_league[league] = sorted(matches.values(), key=lambda m: (m["date"], m["home"], m["away"]))
    return by_league, {"license": "OpenFootball CC0/public domain",
                       "attribution": "https://github.com/openfootball/football.json",
                       "sources": provenance, "failures": failures,
                       "matches_per_league": {league: len(x) for league, x in by_league.items()}}


def make_models() -> dict:
    return {
        "ELO_STANDARD": FootballElo(k=20, home_advantage=65, draw_probability=0.26),
        "ELO_DYNAMIC": FootballElo(k=36, home_advantage=65, draw_probability=0.26),
        "POISSON_SHRINK": FootballPoisson(prior_games=8),
        "DIXON_COLES_LOW": FootballPoisson(prior_games=8),
        "ELO_POISSON_BLEND": None,
        **{name: None for name in CUSTOM_NAMES + REFERENCE_NAMES},
    }


def dc_adjusted(poisson: FootballPoisson, home: str, away: str,
                rho: float = -0.07) -> tuple[float, float, float]:
    """Fixed low-score Dixon–Coles tau correction; rho not fitted to test window.

    This is a DC-inspired sensitivity strategy, NOT the full optimized DC MLE.
    """
    h, a = team_name(home), team_name(away)
    hs = poisson.home.get(h)
    as_ = poisson.away.get(a)
    from .poisson import TeamCounts
    hs = hs or TeamCounts()
    as_ = as_ or TeamCounts()
    lh = poisson.smooth(poisson.league_home_goals, poisson.league_games, poisson.home_prior, 20)
    la = poisson.smooth(poisson.league_away_goals, poisson.league_games, poisson.away_prior, 20)
    ah = poisson.smooth(hs.scored, hs.matches, lh, poisson.prior_games) / lh
    dh = poisson.smooth(hs.conceded, hs.matches, la, poisson.prior_games) / la
    aa = poisson.smooth(as_.scored, as_.matches, la, poisson.prior_games) / la
    da = poisson.smooth(as_.conceded, as_.matches, lh, poisson.prior_games) / lh
    mu_h = min(4.5, max(.25, lh * ah * da))
    mu_a = min(4.5, max(.25, la * aa * dh))
    p_h, p_a = poisson.poisson_pmf(mu_h), poisson.poisson_pmf(mu_a)
    # Fixed DC tau(x,y) for (0,0), (0,1), (1,0), (1,1).
    corrections = {(0, 0): 1 - mu_h * mu_a * rho,
                   (0, 1): 1 + mu_h * rho,
                   (1, 0): 1 + mu_a * rho,
                   (1, 1): 1 - rho}
    values = [0., 0., 0.]
    for i, pi in enumerate(p_h):
        for j, pj in enumerate(p_a):
            factor = corrections.get((i, j), 1.)
            if factor < 0:
                raise ValueError("Negative Dixon-Coles tau")
            values[0 if i > j else (1 if i == j else 2)] += pi * pj * factor
    total = sum(values)
    return tuple(x / total for x in values)


def predictions(models: dict, home: str, away: str,
                market_no_vig=None) -> dict:
    elo = models["ELO_STANDARD"].predict(home, away)
    quick = models["ELO_DYNAMIC"].predict(home, away)
    poisson = models["POISSON_SHRINK"].predict(home, away)
    dc = dc_adjusted(models["DIXON_COLES_LOW"], home, away)
    ensemble = tuple((a + b) / 2 for a, b in zip(elo, poisson))
    base = dict(zip(ORIGINAL_NAMES, (elo, quick, poisson, dc, ensemble)))
    base.update(experimental_forecasts(base, market_no_vig))
    return base


def update_all(models: dict, match: dict) -> None:
    for name in STRATEGIES:
        model = models[name]
        if model is not None:
            model.update(match)


def historical_comparison(matches_by_league: dict) -> tuple[dict, dict]:
    league_models, stats = {}, {}
    for league, matches in matches_by_league.items():
        models = make_models()
        daily = defaultdict(list)
        for m in matches:
            daily[m["date"]].append(m)
        tracked = {name: {"n": 0, "brier_sum": 0., "log_sum": 0.,
                          "bench_brier_sum": 0., "bench_log_sum": 0., "by_season": {}} for name in STRATEGIES}
        trained = 0
        for day in sorted(daily):
            games = daily[day]
            for m in games:
                if trained < WARMUP or day < EVALUATION_FROM:
                    continue
                o = 0 if m["goals_home"] > m["goals_away"] else (
                    1 if m["goals_home"] == m["goals_away"] else 2)
                row_preds = predictions(models, m["home"], m["away"])
                # 1/3 null benchmark is not a market benchmark. Keep separate.
                for name, p in row_preds.items():
                    if p is None:
                        continue  # market anchored has NO historical price, do not fabricate one
                    if name == "DISAGREEMENT_VETO":
                        votes = [row_preds[k] for k in
                                 ("ELO_STANDARD", "ELO_DYNAMIC", "POISSON_SHRINK", "DIXON_COLES_LOW")]
                        choice = max(range(3), key=lambda i: p[i])
                        if (min(models["ELO_STANDARD"].played.get(m["home"], 0),
                                models["ELO_STANDARD"].played.get(m["away"], 0)) < 16 or
                            sum(max(range(3), key=lambda i: v[i]) == choice for v in votes) < 3 or
                            max(v[choice] for v in votes) - min(v[choice] for v in votes) > .12):
                            continue
                    stat = tracked[name]
                    stat["n"] += 1
                    stat["brier_sum"] += brier(p, o)
                    stat["log_sum"] += log_loss(p, o)
                    stat["bench_brier_sum"] += 2 / 3
                    stat["bench_log_sum"] += math.log(3)
                    season = str(day.year if day.month >= 8 else day.year-1)
                    sub = stat["by_season"].setdefault(season, {"n": 0, "brier_sum": 0., "log_sum": 0.})
                    sub["n"] += 1
                    sub["brier_sum"] += brier(p, o)
                    sub["log_sum"] += log_loss(p, o)
            for m in games:
                update_all(models, m)
            trained += len(games)
        league_models[league] = models
        stats[league] = tracked
    aggregate = {}
    for name in STRATEGIES:
        rows = [s[name] for s in stats.values()]
        n = sum(x["n"] for x in rows)
        aggregate[name] = {
            "n": n,
            "brier": round(sum(x["brier_sum"] for x in rows) / n, 6) if n else None,
            "log_loss": round(sum(x["log_sum"] for x in rows) / n, 6) if n else None,
            "uniform_brier": round(2/3, 6),
            "uniform_log_loss": round(math.log(3), 6),
            "historical_roi": None,
            "historical_scope": ("UNAVAILABLE_NEEDS_POINT_IN_TIME_MARKET"
                                 if name == "MARKET_ANCHORED" else
                                 "CONDITIONAL_SUBSET_NOT_COMPARABLE_DIRECTLY"
                                 if name == "DISAGREEMENT_VETO" else
                                 "FULL_RESEARCH_SAMPLE"),
            "reason_roi_unavailable": "Public-domain historical scores contain no contemporaneously recorded odds",
            "by_league": {league: {"n": s[name]["n"],
                                "brier": round(s[name]["brier_sum"] / s[name]["n"], 6) if s[name]["n"] else None,
                                "log_loss": round(s[name]["log_sum"] / s[name]["n"], 6) if s[name]["n"] else None}
                          for league, s in stats.items()},
        }
    return league_models, aggregate


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf8").splitlines() if line.strip()]


def save_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Temp + replace avoids truncated ledgers if worker is interrupted.
    temp = path.with_name(path.name + ".tmp")
    temp.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf8")
    temp.replace(path)


def result_index(events: list[dict]) -> dict:
    return {e["id"]: e["final"] for e in events if e["completed"] and e["final"]}


def score_settlement(selection: int, outcome: int) -> bool:
    return selection == outcome


def build_accounts(root: Path, match_finals: dict, now: dt.datetime) -> tuple[dict, dict]:
    accounts = {}
    counts = {"settled_this_run": 0}
    for name in STRATEGIES:
        path = root / "data" / "strategy_ledgers" / (name.lower() + ".jsonl")
        rows = read_jsonl(path)
        seen = set()
        for record in rows:
            if record["id"] in seen:
                raise ValueError("Duplicate strategy ledger ID")
            seen.add(record["id"])
            if record.get("status") != "OPEN":
                continue
            outcome = match_finals.get(record["event_id"])
            if outcome is None or parse_iso(record["start_utc"]) > now:
                continue
            index = outcome["outcome"]
            selected = record["selection_index"]
            p = record["probabilities"]
            if (len(p) not in (2, 3) or len(p) != len(record["odds"])
                or selected not in range(len(p))):
                continue
            # Moneyline has no draw selection: a drawn NFL result voids the
            # paper bet rather than converting it into a phantom loss.
            if len(p) == 2 and index == 1:
                record["status"] = "VOID"
                record["settlement_utc"] = now.isoformat()
                record["observed_outcome"] = index
                record["pnl_eur"] = 0.0
                counts["settled_this_run"] += 1
                continue
            if len(p) == 2:
                index = 0 if index == 0 else 1
            success = score_settlement(selected, index)
            stake = record["stake_eur"]
            pnl = round((record["odds"][selected] - 1) * stake if success else -stake, 4)
            record["status"] = "SETTLED"
            record["settlement_utc"] = now.isoformat()
            record["observed_outcome"] = index
            record["pnl_eur"] = pnl
            record["brier"] = round(brier(p, index), 6)
            record["log_loss"] = round(log_loss(p, index), 6)
            market = record.get("no_vig")
            if market and len(market) == 3 and sum(market) > 0:
                normalized_market = [value / sum(market) for value in market]
                record["market_brier"] = round(brier(normalized_market, index), 6)
                record["market_log_loss"] = round(log_loss(normalized_market, index), 6)
            counts["settled_this_run"] += 1
        save_jsonl(path, rows)
        wins = sum(r.get("status") == "SETTLED" and r.get("pnl_eur", 0) > 0 for r in rows)
        losers = sum(r.get("status") == "SETTLED" and r.get("pnl_eur", 0) < 0 for r in rows)
        pnl = sum(r.get("pnl_eur", 0) for r in rows if r.get("status") == "SETTLED")
        exposure = sum(r["stake_eur"] for r in rows if r.get("status") == "OPEN")
        stake_total = sum(r["stake_eur"] for r in rows if r.get("status") == "SETTLED")
        settled = [r for r in rows if r.get("status") == "SETTLED"]
        # Realised-only drawdown; pending positions are excluded until settlement.
        settled_by_start = sorted(settled, key=lambda r: (r.get("start_utc", ""), r["id"]))
        wealth = peak = INITIAL_BANKROLL_EUR
        worst_drawdown = 0.
        for completed in settled_by_start:
            wealth += completed["pnl_eur"]
            peak = max(peak, wealth)
            worst_drawdown = max(worst_drawdown, (peak - wealth) / peak)
        comparable = [r for r in settled if r.get("market_brier") is not None]
        accounts[name] = {
            "bankroll_initial_eur": INITIAL_BANKROLL_EUR,
            "bankroll_eur": round(INITIAL_BANKROLL_EUR + pnl, 4),
            "open_exposure_eur": round(exposure, 4),
            "pnl_eur": round(pnl, 4), "total_staked_settled_eur": round(stake_total, 4),
            "paper_wins": wins, "paper_losses": losers,
            "settled": len(settled),
            "open": sum(r.get("status") == "OPEN" for r in rows),
            "yield_percent": round(100 * pnl / stake_total, 4) if stake_total else None,
            "return_on_initial_bank_percent": round(100 * pnl / INITIAL_BANKROLL_EUR, 4),
            "max_settled_drawdown_percent": round(worst_drawdown * 100, 4),
            "brier": round(sum(r["brier"] for r in settled) / len(settled), 6) if settled else None,
            "log_loss": round(sum(r["log_loss"] for r in settled) / len(settled), 6) if settled else None,
            "benchmark_n": len(comparable),
            "market_brier": round(sum(r["market_brier"] for r in comparable) / len(comparable), 6) if comparable else None,
            "market_log_loss": round(sum(r["market_log_loss"] for r in comparable) / len(comparable), 6) if comparable else None,
            "status": "EXPERIMENTAL_PAPER_ONLY",
        }
    return accounts, counts


def open_accounts(root: Path, models: dict, events: list[dict], now: dt.datetime,
                  registry: dict | None = None, diagnostics: dict | None = None) -> tuple[dict, dict]:
    """Optional experimental paper entries; each account independently limits exposure.

    Only model probabilities and pre-game timestamped quotes are eligible.
    Correlated portfolios are intentionally reported separately, not combined.
    """
    account_stats = {}
    added = defaultdict(int)
    rejected = defaultdict(int)
    for name in STRATEGIES:
        path = root / "data" / "strategy_ledgers" / (name.lower() + ".jsonl")
        rows = read_jsonl(path)
        if name in REFERENCE_NAMES:
            account_stats[name] = {"new_entries": 0, "total_records": len(rows)}
            continue
        if registry and now < parse_iso(registry[name]["created_utc"]):
            account_stats[name] = {"new_entries": 0, "total_records": len(rows)}
            continue
        bank = INITIAL_BANKROLL_EUR + sum(r.get("pnl_eur", 0) for r in rows if r.get("status") == "SETTLED")
        exposure = sum(r["stake_eur"] for r in rows if r.get("status") == "OPEN")
        today_count = sum(r["forecast_utc"][:10] == now.date().isoformat() for r in rows)
        seen_events = {r["event_id"] for r in rows}
        for e in events:
            if e["id"] in seen_events or today_count >= MAX_DAILY_ENTRIES_PER_ACCOUNT:
                continue
            if e["sport"] not in models or e["status"] != "pre" or not (now < parse_iso(e["start_utc"]) <= now + dt.timedelta(hours=48)):
                continue
            quote = e.get("quote")
            if not quote or not quote.get("quote_timestamp_available") or quote.get("market") != "1X2":
                rejected["missing_timestamped_odds"] += 1
                continue
            if len(quote.get("decimal", [])) != 3 or len(quote.get("no_vig", [])) != 3:
                continue
            # Never accept a feed merely claiming to have timestamps. Demand
            # all three actual bookmaker selection quote timestamps.
            timestamps = quote.get("best_book_updates")
            if not isinstance(timestamps, list) or len(timestamps) != 3:
                continue
            try:
                age_hours = [(now - parse_iso(t)).total_seconds()/3600 for t in timestamps]
                prices = [float(x) for x in quote["decimal"]]
                market = [float(x) for x in quote["no_vig"]]
                if (any(not math.isfinite(x) or x <= 1 for x in prices) or
                    any(not math.isfinite(x) or x <= 0 for x in market) or
                    abs(sum(market) - 1) > .001 or
                    any(age < 0 or age > 12 for age in age_hours)):
                    continue
            except (ValueError, TypeError):
                continue
            league_models = models[e["sport"]]
            home, away = team_name(e["home"]), team_name(e["away"])
            base = league_models["ELO_STANDARD"]
            if base.played.get(home, 0) < 8 or base.played.get(away, 0) < 8:
                rejected["insufficient_history"] += 1
                continue
            all_forecasts = predictions(league_models, home, away, market)
            p = all_forecasts[name]
            if p is None:
                continue
            diffs = [a-b for a,b in zip(p, market)]
            choice = max(range(3), key=lambda i: diffs[i])
            ev = p[choice] * prices[choice] - 1
            if name in RULES:
                if not accepts_candidate(
                    name, choice, all_forecasts,
                    min(base.played.get(home, 0), base.played.get(away, 0)),
                    diffs[choice], ev, max(age_hours),
                    int(quote.get("bookmaker_count") or 0)):
                    continue
            elif diffs[choice] < EDGE_MINIMUM or ev < EV_MINIMUM:
                rejected["insufficient_edge_or_ev"] += 1
                continue
            stake = round(min(bank * PAPER_STAKE_FRACTION,
                             max(0., bank * MAX_PAPER_EXPOSURE_FRACTION - exposure)), 2)
            if stake <= 0:
                break
            record_id = hashlib.sha256(f"{name}|{e['id']}".encode()).hexdigest()[:14]
            r = {
                "id": "PLAB-" + record_id,
                "strategy": name, "event_id": e["id"], "sport": e["sport"],
                "home": e["home"], "away": e["away"], "start_utc": e["start_utc"],
                "forecast_utc": now.isoformat(), "market": "1X2",
                "probabilities": list(p),
                "no_vig": quote["no_vig"], "odds": quote["decimal"],
                "selection_index": choice, "edge_pp": round(diffs[choice]*100, 4),
                "price_source": quote.get("provider"),
                "bookmakers": quote.get("bookmakers"),
                "book_quote_updates_utc": quote.get("best_book_updates"),
                "stake_eur": stake, "status": "OPEN",
                "model_status": "EXPERIMENTAL_UNCALIBRATED",
                "strategy_version": RULES.get(name, {}).get("version", "legacy-1.0.0"),
                "strategy_registered_utc": registry[name]["created_utc"] if registry else None,
                "selection_expected_value": round(ev, 7),
                "sizing_note": "0.5% virtual bank, max 2% exposure; hypothetical quote may not have been executable",
            }
            rows.append(r)
            seen_events.add(e["id"])
            added[name] += 1
            today_count += 1
            exposure += stake
        save_jsonl(path, rows)
        account_stats[name] = {"new_entries": added[name], "total_records": len(rows)}
    if diagnostics is not None:
        diagnostics.update(rejected)
    return account_stats, {name: count for name, count in added.items() if count > 0}


def run_strategy_lab(root: Path, now: dt.datetime, fetcher=fetch_text,
                     discover=capture_scoreboards) -> dict:
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    now = now.astimezone(UTC)
    registry = ensure_registry(root, now, STRATEGIES)
    verify_prior_accounts(root, registry)
    matches, provenance = collect_openfootball(now.date(), fetcher)
    models, historical = historical_comparison(matches)
    # Includes yesterday/today/future fixtures; only completed final statuses settle.
    events, coverage = discover(now, sports=SCOREBOARDS,
                                future_days=2, need_yesterday=True)
    archive_path = root / "data" / "odds_cache_v3.json"
    if archive_path.exists():
        cache = json.loads(archive_path.read_text(encoding="utf8"))
        for event in events:
            snap = cache.get("snapshots", {}).get(event["sport"], {})
            try:
                updated = parse_iso(snap["fetched_at"])
                if dt.timedelta(0) <= now - updated <= dt.timedelta(hours=13):
                    quote = snap["quotes"].get(event["id"])
                    if quote and all(now - parse_iso(t) <= dt.timedelta(hours=12)
                                     for t in quote.get("best_book_updates", [])):
                        event["quote"] = quote
            except (KeyError, ValueError, TypeError):
                continue
    # Retrieve confirmed results for ALL outstanding frozen rows, not only yesterday.
    pending = {}
    for strategy in STRATEGIES:
        for r in read_jsonl(root / "data" / "strategy_ledgers" / (strategy.lower() + ".jsonl")):
            if r.get("status") == "OPEN" and parse_iso(r["start_utc"]) <= now:
                pending[(r["sport"], parse_iso(r["start_utc"]).date())] = True
    finals = result_index(events)
    for (league, day) in sorted(pending):
        if not (dt.timedelta(0) <= now - dt.datetime.combine(day, dt.time(), UTC) <= dt.timedelta(days=30)):
            continue
        try:
            result, _ = fetch_espn(SCOREBOARDS[league], day)
            for record in unpack_scoreboard(result, league, now):
                if record["completed"] and record["final"]:
                    finals[record["id"]] = record["final"]
        except Exception as exc:
            print("Settlement lookup failed", league, day.isoformat(), type(exc).__name__, flush=True)
    missing_final_ids = sorted({row["event_id"] for name in STRATEGIES for row in read_jsonl(root / "data" / "strategy_ledgers" / (name.lower() + ".jsonl")) if row.get("status") == "OPEN" and parse_iso(row["start_utc"]) <= now and row["event_id"] not in finals})
    before, settle_counts = build_accounts(root, finals, now)
    rejected = {}
    additions, entry_counts = open_accounts(root, models, events, now, registry=registry, diagnostics=rejected)
    daily_reference = select_daily_reference(root, events, now, registry)
    if daily_reference["new_entries"]:
        entry_counts["MARKET_DAILY_REFERENCE"] = daily_reference["new_entries"]
    after, _ = build_accounts(root, {}, now)
    report = {
        "asof_utc": now.isoformat(),
        "experiment": "PARALLEL_INDEPENDENT_PAPER_ACCOUNTS_V3",
        "daily_reference": daily_reference,
        "daily_target_policy": "One genuine pre-game virtual reference if eligible; otherwise report missed target, never fabricate odds.",
        "registered_strategies": registry,
        "policy": "Existing balances and settled records persist; only brand-new strategies start with EUR 1000.",
        "sports_tested": sorted(matches),
        "strategy_names": list(STRATEGIES),
        "initial_bank_per_strategy_eur": INITIAL_BANKROLL_EUR,
        "historical": historical,
        "accounts": after,
        "provenance": provenance,
        "sources": {"fixtures": coverage["sources_ok"], "failures": coverage["sources_failed"]},
        "events_seen": len(events), "new_paper_entries": entry_counts,
        "entry_diagnostics": {"rejections": rejected, "new_entries": sum(entry_counts.values()), "unit": "strategy-event evaluations"},
        "newly_settled": settle_counts["settled_this_run"],
        "missing_final_event_ids": missing_final_ids,
        "restrictions": [
            "No real bets; outcomes do not imply profit guarantee.",
            "Historical comparison has NO historic book prices; historical ROI deliberately missing.",
            "Model ranking selected on same research data is NOT independently validated.",
            "Unverified external returns not imported.",
            "Money wagered is virtual and book execution/liquidity not confirmed.",
            "Current matchup name mismatches are rejected, not guessed.",
            "No odds: no paper wager.",
            "Market daily reference is NON-VALUE control, not an edge or investment recommendation.",
            "Strategy portfolios are correlated; never sum as independent diversification.",
        ],
    }
    path = root / "reports" / "strategy_lab_latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    archive = root / "reports" / "strategy_lab_runs" / (now.date().isoformat() + ".jsonl")
    archive.parent.mkdir(exist_ok=True, parents=True)
    with archive.open("a", encoding="utf8") as sink:
        sink.write(json.dumps({"utc": now.isoformat(), "events": len(events),
                               "entries": entry_counts, "settled": settle_counts["settled_this_run"],
                               "sources_ok": coverage["sources_ok"],
                               "historical_counts": {k:v["n"] for k,v in historical.items()}},
                              ensure_ascii=False) + "\n")
    accounts_path = root / "data" / "strategy_accounts_v3.json"
    accounts_path.write_text(json.dumps({"updated_utc": now.isoformat(), "accounts": after},
                                        ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    options = parser.parse_args()
    data = run_strategy_lab(options.root, dt.datetime.now(UTC))
    print(json.dumps({"time": data["asof_utc"],
                      "matches": data["provenance"]["matches_per_league"],
                      "model_scores": {k: {"n":s["n"], "brier":s["brier"], "log_loss":s["log_loss"]} for k,s in data["historical"].items()},
                      "accounts": {k: {"balance":a["bankroll_eur"], "open":a["open"]} for k,a in data["accounts"].items()},
                      "entries": data["new_paper_entries"]}, ensure_ascii=False))
