"""Prospective football comparison. Frozen, zero-stake and outside wallet admission."""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import math
import random
import re
from statistics import mean, quantiles
from urllib.parse import urlencode

from .experimental_markets import goal_rates
from .feeds import ESPN_BASE, SCOREBOARDS, fetch_text
from .metrics import aggregate, brier, log_loss
from .odds_provider import parse_utc
from .player_context import FOOTBALL, identity
from .poisson import FootballPoisson

VERSION = "FOOTBALL-IN-PLAY-1"
JOURNAL = "data/in_play_v3.jsonl"
MAX_SUMMARIES = 12
MAX_CAPTURE_GAP = dt.timedelta(seconds=120)
MAX_HEARTBEAT_AGE = dt.timedelta(minutes=8)
MIN_ELAPSED = 25 * 60
MAX_ELAPSED = 75 * 60
PLAYING = {"STATUS_IN_PROGRESS", "STATUS_FIRST_HALF", "STATUS_SECOND_HALF"}
MIN_EVALUATION_GAMES = 100
MIN_EVALUATION_DATES = 14


class InvalidSnapshot(ValueError):
    """The feed responded but its snapshot failed an eligibility check."""


def count(value):
    """A missing provider count is unknown, including missing red-card fields."""
    if not str(value).isascii() or not str(value).isdecimal():
        raise InvalidSnapshot("INVALID_COUNT")
    return int(value)


def read_state(payload, event, captured):
    """Reconcile the scoreboard with a freshly received match summary."""
    comp, teams, status = identity(payload, event, captured)
    if status.get("state") != "in" or status.get("completed") is not False or status.get("name") not in PLAYING:
        raise InvalidSnapshot("SUMMARY_NOT_IN_PLAY")
    regulation = payload["format"]["regulation"]
    if regulation["periods"] != 2 or regulation["clock"] != 2700:
        raise InvalidSnapshot("UNSUPPORTED_GAME_FORMAT")

    live = event["live_state"]
    if event.get("provider_status") not in PLAYING:
        raise InvalidSnapshot("SCOREBOARD_NOT_IN_PLAY")
    board_time = parse_utc(live["scoreboard_fetched_utc"])
    kickoff = parse_utc(event["start_utc"])
    if not kickoff <= board_time <= captured or captured - board_time > MAX_CAPTURE_GAP:
        raise InvalidSnapshot("STALE_SCOREBOARD")
    clock = comp["status"]
    minute = re.fullmatch(r"([0-9]{1,2})'", clock["displayClock"])
    if not minute:
        raise InvalidSnapshot("UNSUPPORTED_LIVE_CLOCK")
    elapsed = 60 * int(minute[1])
    period = clock["period"]
    if (not MIN_ELAPSED <= elapsed <= MAX_ELAPSED or period not in (1, 2)
        or (elapsed < 2700) != (period == 1)):
        raise InvalidSnapshot("OUTSIDE_CAPTURE_WINDOW")
    if (period != live["period"] or not -60 <= elapsed - float(live["elapsed_seconds"]) <= 180
        or elapsed > (captured - kickoff).total_seconds() + 60):
        raise InvalidSnapshot("LIVE_CLOCK_MISMATCH")

    competitors = {c["homeAway"]: c for c in comp["competitors"]}
    ids = [teams[side]["id"] for side in ("home", "away")]
    score = [count(competitors[side]["score"]) for side in ("home", "away")]
    if ids != [str(t) for t in live["team_ids"]] or score != [count(s) for s in live["score"]]:
        raise InvalidSnapshot("SCORE_OR_ID_CHANGED")

    # An HTTP receipt time alone does not prove that the provider's state is current.
    # Reject missing clocks, future plays and transitions to a different half.
    if payload.get("wallclockAvailable") is not True:
        raise InvalidSnapshot("NO_LIVE_HEARTBEAT")
    plays = [(parse_utc(p["wallclock"]), p) for p in payload["keyEvents"]]
    if not plays:
        raise InvalidSnapshot("NO_LIVE_HEARTBEAT")
    heartbeat, play = max(plays, key=lambda item: item[0])
    play_clock = float(play["clock"]["value"])
    if (not kickoff <= heartbeat <= captured or captured - heartbeat > MAX_HEARTBEAT_AGE
        or play["period"]["number"] != period
        or not -60 < elapsed - play_clock <= MAX_HEARTBEAT_AGE.total_seconds()):
        raise InvalidSnapshot("STALE_OR_INCONSISTENT_HEARTBEAT")

    groups = payload["boxscore"]["teams"]
    if len(groups) != 2 or {str(g["team"]["id"]) for g in groups} != set(ids):
        raise InvalidSnapshot("BOX_SCORE_TEAM_MISMATCH")
    stats = {str(g["team"]["id"]): {s["name"]: s["displayValue"] for s in g["statistics"]} for g in groups}
    shots = [count(stats[t]["shotsOnTarget"]) for t in ids]
    reds = [count(stats[t]["redCards"]) for t in ids]
    if max(shots) > 60 or max(reds) > 4:
        raise InvalidSnapshot("IMPLAUSIBLE_TEAM_STATS")
    return {
        "elapsed_seconds": elapsed, "period": period, "score": score, "team_ids": ids,
        "shots_on_target": shots, "red_cards": reds, "heartbeat_utc": heartbeat.isoformat(),
        "scoreboard_fetched_utc": board_time.isoformat(),
        "scoreboard_sha256": live["scoreboard_sha256"],
    }


def probabilities(rates, state):
    """Use the same remaining-goal distribution for 1X2 and the open goals line."""
    home, away = state["score"]
    fraction = 1 - state["elapsed_seconds"] / (90 * 60)
    # A large dismissal differential can put the remaining mean above ten.
    # Keep the discarded tail negligible even at that bound.
    ph, pa = (FootballPoisson.poisson_pmf(rate * fraction, 32) for rate in rates)
    win_draw_loss = [0., 0., 0.]
    over = 0.
    for i, h in enumerate(ph):
        for j, a in enumerate(pa):
            weight = h * a
            diff = home + i - away - j
            win_draw_loss[0 if diff > 0 else 1 if diff == 0 else 2] += weight
            if home + away + i + j >= 3:
                over += weight
    normalizer = math.fsum(win_draw_loss)
    result = {"1X2": [p / normalizer for p in win_draw_loss]}
    if home + away < 3:
        p_over = over / normalizer
        result["OVER_UNDER_2_5"] = [p_over, 1 - p_over]
    return result


def forecast(rates, state):
    """Pair the time-and-score baseline with the fixed context adjustment."""
    # Fixed research coefficients. A shot on target is a count, not an xG estimate.
    shots = state["shots_on_target"]
    reds = state["red_cards"]
    pressure = max(-.18, min(.18, .04 * (shots[0] - shots[1])))
    manpower = .25 * (reds[1] - reds[0])
    adjustment = pressure + manpower
    base = probabilities(rates, state)
    context = probabilities([rates[0] * math.exp(adjustment), rates[1] * math.exp(-adjustment)], state)
    return {market: {"baseline": p, "context": context[market]} for market, p in base.items()}


def collect(events, models, seen, cutoff, team_name, fetcher=fetch_text):
    """Capture one eligible forecast per match within a bounded request budget."""
    candidates, eligible, skipped = 0, [], Counter()
    for event in events:
        if event["sport"] not in FOOTBALL or not event.get("live_state") or event["id"] in seen:
            continue
        if parse_utc(event["start_utc"]).date() != cutoff:
            continue
        candidates += 1
        try:
            elapsed = float(event["live_state"]["elapsed_seconds"])
            if event.get("provider_status") not in PLAYING or not MIN_ELAPSED - 180 <= elapsed <= MAX_ELAPSED + 60:
                skipped["OUTSIDE_CAPTURE_WINDOW"] += 1
                continue
            model = models[event["sport"]]["POISSON_SHRINK"]
            home, away = team_name(event["home"]), team_name(event["away"])
            if min(model.played.get(home, 0), model.played.get(away, 0)) < 8:
                skipped["INSUFFICIENT_TEAM_HISTORY"] += 1
                continue
            event_id = event["id"].split(":", 1)[1]
            if not event_id.isascii() or not event_id.isdecimal():
                raise InvalidSnapshot("INVALID_PROVIDER_EVENT_ID")
            url = f"{ESPN_BASE}/{SCOREBOARDS[event['sport']]}/summary?{urlencode({'event': event_id})}"
            eligible.append((event, url, goal_rates(model, home, away)))
        except (KeyError, TypeError, ValueError, IndexError):
            skipped["INCOMPLETE_SCOREBOARD_OR_MODEL"] += 1

    def observe(candidate):
        """Keep provider transport failures inside the optional experiment."""
        event, url, rates = candidate
        try:
            raw, metadata = fetcher(url)
        except Exception:
            return None, "SUMMARY_FETCH_FAILED"
        try:
            captured = parse_utc(metadata["fetched_at_utc"])
            state = read_state(json.loads(raw), event, captured)
            return {
                "kind": "FORECAST", "version": VERSION, "event_id": event["id"],
                "sport": event["sport"], "home": event["home"], "away": event["away"],
                "start_utc": event["start_utc"], "forecast_utc": captured.isoformat(),
                "training_cutoff_exclusive_utc_date": str(cutoff),
                "live_state": state, "prior_goal_rates": rates, "markets": forecast(rates, state),
                "source": {"url": url, "sha256": metadata["sha256"]}, "stake_eur": 0,
            }, None
        except InvalidSnapshot as exc:
            return None, str(exc)
        except (OSError, AttributeError, KeyError, TypeError, IndexError, StopIteration, ValueError, OverflowError):
            return None, "SOURCE_UNAVAILABLE_OR_INCOMPLETE"

    selected = sorted(eligible, key=lambda item: (item[0]["start_utc"], item[0]["id"]))[:MAX_SUMMARIES]
    if len(eligible) > len(selected):
        skipped["REQUEST_LIMIT"] += len(eligible) - len(selected)
    with ThreadPoolExecutor(max_workers=4) as pool:
        captures = list(pool.map(observe, selected))
    skipped.update(error for _, error in captures if error)
    return [r for r, _ in captures if r], {
        "live_candidates": candidates, "candidates_checked": len(selected), "skipped": dict(skipped),
    }


def pending(rows):
    """Return match dates that still need an official regulation result."""
    finished = {r["event_id"] for r in rows if r["kind"] == "RESULT"}
    return {
        (r["sport"], parse_utc(r["start_utc"]).date())
        for r in rows if r["kind"] == "FORECAST" and r["event_id"] not in finished
    }


def finish(rows, final_events, observed_at):
    """Record new outcomes separately from the immutable forecasts."""
    finished = {r["event_id"] for r in rows if r["kind"] == "RESULT"}
    results = []
    for row in rows:
        if row["kind"] != "FORECAST" or row["event_id"] in finished:
            continue
        event = final_events.get(row["event_id"])
        if (not event or not event["completed"] or event.get("provider_status") != "STATUS_FULL_TIME"
            or not event["final"] or any(event[k] != row[k] for k in ("sport", "start_utc", "home", "away"))
            or observed_at <= parse_utc(row["forecast_utc"])):
            continue
        final = event["final"]
        score = [final["home"], final["away"]]
        if any(end < start for end, start in zip(score, row["live_state"]["score"])):
            continue
        results.append({
            "kind": "RESULT", "event_id": row["event_id"], "version": row["version"],
            "result_observed_utc": observed_at.isoformat(), "final_score": score,
            "outcomes": {"1X2": final["outcome"], "OVER_UNDER_2_5": int(sum(score) < 3)},
        })
    return results


def paired_intervals(by_day):
    """Resample whole match dates so simultaneous fixtures stay together."""
    days = list(by_day.values())
    if len(days) < MIN_EVALUATION_DATES or sum(len(d) for d in days) < MIN_EVALUATION_GAMES:
        return None
    daily = [(len(d), sum(x[0] for x in d), sum(x[1] for x in d)) for d in days]
    rng = random.Random(31)
    samples = [[], []]
    for _ in range(1000):
        n, brier_delta, loss_delta = map(sum, zip(*rng.choices(daily, k=len(daily))))
        samples[0].append(brier_delta / n)
        samples[1].append(loss_delta / n)
    intervals = {}
    for name, values in zip(("brier", "log_loss"), samples):
        limits = quantiles(values, n=40, method="inclusive")
        intervals[name] = [limits[0], limits[-1]]
    return intervals


def reliability(predictions):
    """Summarize the calibration of the most probable outcome in each forecast."""
    bins = defaultdict(list)
    for p, y in predictions:
        choice = max(range(len(p)), key=p.__getitem__)
        confidence = p[choice]
        bins[min(9, int(confidence * 10))].append((confidence, choice == y))
    return [{"lower": bucket / 10, "n": len(pairs), "mean_probability": mean(p for p, _ in pairs),
             "observed_frequency": mean(hit for _, hit in pairs)} for bucket, pairs in sorted(bins.items())]


def report(rows, diagnostics, new_count):
    """Compare both models on identical evaluated matches by league and market."""
    final = {r["event_id"]: r for r in rows if r["kind"] == "RESULT"}
    forecasts = [r for r in rows if r["kind"] == "FORECAST" and r["version"] == VERSION]
    groups = defaultdict(list)
    for row in forecasts:
        for market in row["markets"]:
            groups[row["sport"], market].append(row)
    streams = []
    for (sport, market), group in sorted(groups.items()):
        paired = []
        baseline_hits = context_hits = 0
        by_day = defaultdict(list)
        base_predictions, context_predictions = [], []
        for row in group:
            result = final.get(row["event_id"])
            if result is None:
                continue
            y = result["outcomes"][market]
            p = row["markets"][market]["baseline"]
            q = row["markets"][market]["context"]
            baseline_hits += max(range(len(p)), key=p.__getitem__) == y
            context_hits += max(range(len(q)), key=q.__getitem__) == y
            baseline = {"brier": brier(p, y), "log_loss": log_loss(p, y)}
            context = {"brier": brier(q, y), "log_loss": log_loss(q, y)}
            paired.append(dict(context, benchmark=baseline))
            by_day[parse_utc(row["start_utc"]).date()].append(
                (baseline["brier"] - context["brier"], baseline["log_loss"] - context["log_loss"])
            )
            base_predictions.append((p, y))
            context_predictions.append((q, y))
        metrics = aggregate(paired)
        n = metrics["n"]
        streams.append({
            "sport": sport, "market": market, "observed": len(group), "evaluated": n,
            "pending": len(group) - n, "dates": len(by_day), "metrics": metrics,
            "baseline_accuracy": baseline_hits / n if n else None,
            "context_accuracy": context_hits / n if n else None,
            "top_label_calibration": {"baseline": reliability(base_predictions), "context": reliability(context_predictions)},
            "paired_improvement_95pct": paired_intervals(by_day),
        })
    return {
        "version": VERSION, "status": "PAPER_RESEARCH", "stake_eur": 0,
        "wallet_enabled": False, "markets": ["1X2", "OVER_UNDER_2_5"],
        "odds_status": "NO_SYNCHRONIZED_LIVE_PRICES", "roi": None,
        "observed": len(forecasts), "settled": sum(r["event_id"] in final for r in forecasts),
        "new_forecasts": new_count, "streams": streams, **diagnostics,
        "minimum_for_intervals": {"matches": MIN_EVALUATION_GAMES, "dates": MIN_EVALUATION_DATES},
    }
