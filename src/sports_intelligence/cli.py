"""CLI for scheduled scans and historical walk-forward research."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

from .feeds import fetch_text, football_csv_urls
from .football import backtest_football, parse_matches as football_matches
from .scanner import capture_scoreboards, classify, freeze_observations, parse_iso, settle_observations
from .tennis import backtest_tennis, parse_matches as tennis_matches

UTC = dt.timezone.utc


def historical_football(now: dt.datetime) -> tuple[object | None, dict]:
    matches = []
    sources = []
    for url in football_csv_urls():
        try:
            text, source = fetch_text(url)
            data = football_matches(text, cutoff=now.date())
            sources.append(dict(source, matches=len(data), ok=bool(data)))
            matches.extend(data)
        except Exception as exc:
            sources.append({"url": url, "ok": False, "error": str(exc)[:170]})
    if len(matches) < 120:
        return None, {"status": "UNAVAILABLE", "matched_rows": len(matches), "sources": sources,
                      "reason": "Insufficient verifiable historical matches; no model probabilities."}
    model, metrics = backtest_football(matches)
    return model, {"status": "EXPERIMENTAL", "sources": sources, "metrics": metrics}


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
    candidates = [x for x in (classify(ev, football_model, now) for ev in events) if x]
    path = root / "data" / "forecasts_v3.jsonl"
    frozen, added = freeze_observations(candidates, path, now)
    settled = settle_observations(frozen, events, path)
    complete = [x for x in frozen if x.get("status") == "SETTLED"]
    report = {
        "generated_at_utc": now.isoformat(),
        "version": "0.3.0",
        "mode": "PAPER_RESEARCH_ONLY",
        "live_bets_allowed": False,
        "v3_papers_with_stake": 0,
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
        "candidates": sorted(candidates, key=lambda c: -c.get("best_model_edge_pp", -999))[:50],
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
