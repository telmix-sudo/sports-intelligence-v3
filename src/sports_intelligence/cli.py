"""CLI for scheduled scans and historical walk-forward research."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

from .feeds import fetch_text
from .football import backtest_football
from .scanner import capture_scoreboards, classify, freeze_observations, parse_iso, settle_observations
from .odds_provider import annotate_odds
from .poisson import backtest_poisson
from .audit import audit_ledger
from .strategy_lab import collect_openfootball
from .tennis import backtest_tennis, parse_matches as tennis_matches

UTC = dt.timezone.utc


def historical_football(now: dt.datetime) -> tuple[object | None, dict]:
    # OpenFootball (CC0) replaces Football-Data.co.uk, whose current site terms
    # prohibit automated bot/AI scraping. Do not reintroduce that source here.
    grouped, provenance = collect_openfootball(now.date(), leagues={"epl": "en.1"})
    matches = grouped["epl"]
    sources = provenance["sources"]
    if len(matches) < 120:
        return None, {"status": "UNAVAILABLE", "matched_rows": len(matches), "sources": sources,
                      "reason": "Insufficient verifiable historical matches; no model probabilities."}
    elo, elo_metrics = backtest_football(matches)
    poisson, poisson_metrics = backtest_poisson(matches)
    # This is model comparison, not independent OOS validation of a selected winner.
    choose_poisson = poisson_metrics["log_loss"] < elo_metrics["log_loss"]
    model = poisson if choose_poisson else elo
    metrics = poisson_metrics if choose_poisson else elo_metrics
    return model, {"status": "EXPERIMENTAL", "sources": sources,
                   "selected_model": "POISSON-SHRINK-0.3" if choose_poisson else "ELO-FOOTBALL-0.3",
                   "selection_caveat": "Selection compared on the historical evaluation set; NOT independently validated.",
                   "metrics": metrics,
                   "alternative_metrics": elo_metrics if choose_poisson else poisson_metrics}


def historical_tennis(now: dt.datetime) -> dict:
    if os.getenv("ENABLE_ATP_RESEARCH") != "1":
        return {"status": "DISABLED", "reason": "CC BY-NC-SA licensing: opt-in research only"}
    rows = []
    sources = []
    for year in range(2023, now.year + 1):
        url = f"https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_matches_{year}.csv"
        try:
            text, source = fetch_text(url)
            parsed = tennis_matches(text, cutoff=now.date())
            rows.extend(parsed)
            sources.append(dict(source, rows=len(parsed)))
        except Exception as exc:
            sources.append({"url": url, "error": str(exc)[:170]})
    if len(rows) < 300:
        return {"status": "UNAVAILABLE", "matched_rows": len(rows), "sources": sources}
    _, metrics = backtest_tennis(rows)
    return {"status": "EXPERIMENTAL", "metrics": metrics, "sources": sources}


def run(root: Path, now: dt.datetime, fetcher=None) -> dict:
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    now = now.astimezone(UTC)
    football_model, football = historical_football(now)
    tennis = historical_tennis(now)
    kwargs = {"fetcher": fetcher} if fetcher else {}
    events, coverage = capture_scoreboards(now, **kwargs)
    odds_coverage = annotate_odds(events, root, now)
    candidates = [x for x in (classify(ev, football_model, now) for ev in events) if x]
    path = root / "data" / "forecasts_v3.jsonl"
    frozen, added = freeze_observations(candidates, path, now)
    settled = settle_observations(frozen, events, path)
    complete = [x for x in frozen if x.get("status") == "SETTLED"]
    v1_audit = audit_ledger(root / "data" / "ledger_v1.csv") if (root / "data" / "ledger_v1.csv").exists() else None
    metrics = football.get("metrics") or {}
    # Public-domain scores have no point-in-time bookmaker odds; None is not proof of value.
    if not metrics:
        gate = "NO_MODEL"
    elif metrics.get("benchmark_n", 0) == 0:
        gate = "NO_TIMED_MARKET_BENCHMARK"
    elif metrics.get("improvement_brier") is None or metrics.get("improvement_log_loss") is None:
        gate = "INCOMPLETE_MARKET_BENCHMARK"
    elif metrics["improvement_brier"] < 0 or metrics["improvement_log_loss"] < 0:
        gate = "UNVALIDATED_AGAINST_MARKET"
    else:
        gate = "EXPERIMENTAL_NEEDS_PROSPECTIVE_CONFIRMATION"
    # Read-only cross-reference: the independent strategy lab can have open papers
    # even when the main V3 scanner deliberately has no stake-enabled strategy.
    # Never add these balances together: the portfolios may share the same fixtures.
    lab_snapshot = {"status": "UNAVAILABLE", "open_papers_by_strategy": {}}
    lab_file = root / "reports" / "strategy_lab_latest.json"
    if lab_file.exists():
        try:
            lab = json.loads(lab_file.read_text(encoding="utf8"))
            accounts = lab.get("accounts", {})
            if not isinstance(accounts, dict):
                raise ValueError("Invalid lab accounts")
            lab_asof = parse_iso(lab["asof_utc"])
            lab_snapshot = {
                "status": "AVAILABLE" if dt.timedelta(0) <= now - lab_asof <= dt.timedelta(hours=24) else "STALE",
                "asof_utc": lab_asof.isoformat(),
                "open_papers_by_strategy": {name: int(a.get("open", 0)) for name, a in accounts.items()},
                "settled_papers_by_strategy": {name: int(a.get("settled", 0)) for name, a in accounts.items()},
                "note": "Independent portfolios: overlapping events, do not sum as unique bets or combine bankrolls.",
            }
        except (ValueError, KeyError, TypeError, OverflowError):
            lab_snapshot = {"status": "INVALID_REPORT", "open_papers_by_strategy": {}}
    report = {
        "generated_at_utc": now.isoformat(),
        "v1_audit": v1_audit,
        "version": "0.3.0",
        "mode": "PAPER_RESEARCH_ONLY",
        "live_bets_allowed": False,
        "v3_papers_with_stake": 0,  # Main scanner only; see independent strategy_lab_snapshot.\n        "strategy_lab_snapshot": lab_snapshot,
        "observations_open": sum(1 for x in frozen if x["status"] == "OPEN"),
        "observations_settled": len(complete),
        "observations_new_this_run": added,
        "observations_settled_this_run": settled,
        "prospective_event_count": len(candidates),
        "market_coverage": {"with_complete_odds": sum(bool(c.get("market")) for c in candidates),
                             "with_model": sum(bool(c.get("model_probabilities")) for c in candidates)},
        "football_elo": football,
        "tennis_elo": tennis,
        "event_sources": coverage,
        "odds_sources": odds_coverage,
        "candidates": sorted(candidates, key=lambda c: -c.get("best_model_edge_pp", -999))[:50],
        "model_gate": gate,
        "integrity": ["No automatic paper stake until model risk bounds and quote timestamps validated",
                      "Free ESPN odds may be unavailable or lack quote timestamps",
                      "Only events with start time strictly after snapshot are eligible",
                      "No reconstruction of earlier inactive periods"],
    }
    if complete:
        report["observations_brier"] = round(sum(x["brier"] for x in complete) / len(complete), 6)
        report["observations_log_loss"] = round(sum(x["log_loss"] for x in complete) / len(complete), 6)
    reports = root / "reports"
    reports.mkdir(exist_ok=True, parents=True)
    output = reports / "latest.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf8")
    archive = reports / "runs" / (now.date().isoformat() + ".jsonl")
    archive.parent.mkdir(parents=True, exist_ok=True)
    archived_row = {"time": now.isoformat(), "events": len(candidates),
                    "sources_ok": coverage["sources_ok"], "sources_failed": coverage["sources_failed"],
                    "odds_quotes": odds_coverage["quotes_matched"], "odds_status": odds_coverage["status"],
                    "observations_new": added, "observations_settled": settled,
                    "football_model": football.get("selected_model"),
                    "football_brier": football.get("metrics", {}).get("brier"),
                    "model_gate": report["model_gate"]}
    with archive.open("a", encoding="utf8") as history:
        history.write(json.dumps(archived_row, ensure_ascii=False) + "\n")
    state_path = root / "data" / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf8"))
        state["v3"] = {
            "last_successful_snapshot_utc": now.isoformat(),
            "fixtures_next_48h": len(candidates),
            "market_quotes_available": odds_coverage["quotes_matched"],
            "model_gate": report["model_gate"],
            "observations_open": report["observations_open"],
            "observations_settled": report["observations_settled"],
            "papers_with_stake": 0,
            "open_exposure_eur": 0
        }
        state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf8")
    return report


def main() -> None:
    cli = argparse.ArgumentParser()
    cli.add_argument("--root", type=Path, default=Path("."))
    cli.add_argument("--mode", choices=["scan", "backtest"], default="scan")
    cli.add_argument("--now", default=None, help="UTC ISO datetime override; research/testing only")
    args = cli.parse_args()
    now = parse_iso(args.now) if args.now else dt.datetime.now(UTC)
    if args.mode == "backtest":
        _, football = historical_football(now)
        tennis = historical_tennis(now)
        print(json.dumps({"football": football, "tennis": tennis}, indent=2, ensure_ascii=False))
    else:
        report = run(args.root, now)
        print(json.dumps({"snapshot": report["generated_at_utc"],
                          "prospective_events": report["prospective_event_count"],
                          "observations_added": report["observations_new_this_run"],
                          "source_success": report["event_sources"]["sources_ok"],
                          "source_failures": report["event_sources"]["sources_failed"],
                          "football_status": report["football_elo"]["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
