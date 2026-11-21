"""Read-only comparisons across virtual accounts and pre-registered forward cohorts."""
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

START = dt.datetime(2026, 11, 21, 9, tzinfo=dt.timezone.utc)
ODDS_LIMIT = 3.0
MODEL_DIGEST = "73074570a00a61dae1dc6affa0774e637a5ce78bba0967aae4cf54c391e4a688"
GROUPS = ("daily", "validated", "reference", "other")


def read_rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def category(row):
    if row.get("strategy") == "MARKET_DAILY_REFERENCE":
        return "reference"
    if row.get("classification") == "DAILY_EXPLORATORY_PAPER":
        return "daily"
    evidence = row.get("admission_evidence") or {}
    if evidence.get("eligible_for_paper") or (evidence.get("market_validation_passed") and evidence.get("eligible_for_small_paper")):
        return "validated"
    return "other"


def decision_key(row):
    spec = row.get("market_spec") or {}
    terms = tuple(spec.get(k) for k in ("kind", "metric", "line", "team", "period"))
    from .daily_paper_minimum import market_rules
    rules = row.get("market_rules") or market_rules(row, spec)
    return row["event_id"], row["market"], terms, rules, row["selection_index"]


def summarize(rows):
    closed = [r for r in rows if r["status"] == "SETTLED"]
    staked = sum(r["stake_eur"] for r in closed)
    pnl = sum(r["pnl_eur"] for r in closed)
    return dict(positions=len(rows), settled=len(closed), open=sum(r["status"] == "OPEN" for r in rows),
                events=len({r["event_id"] for r in rows}), pnl_eur=round(pnl, 4),
                roi_pct=round(100*pnl/staked, 3) if staked else None,
                unit_pnl=round(sum(r["pnl_eur"]/r["stake_eur"] for r in closed), 4))


def chronological(row):
    return dt.datetime.fromisoformat(row["forecast_utc"].replace("Z", "+00:00")), row["id"]


def first_decisions(rows, key):
    seen = {}
    for row in sorted(rows, key=chronological):
        seen.setdefault(key(row), row)
    return list(seen.values())


def forward_review(journal):
    outcomes = {r["forecast_id"]: r for r in journal if r["kind"] == "SETTLEMENT"}
    groups = defaultdict(list)
    for r in journal:
        if r["kind"] != "FORECAST" or r.get("model_code_sha256") != MODEL_DIGEST or chronological(r)[0] < START:
            continue
        if chronological(r)[0] >= dt.datetime.fromisoformat(r["start_utc"]):
            continue
        if r["sport"] == "nhl" and r["market"] == "ML" and r["family"] == "MULTIMARKET-1.0.0-nhl":
            groups["nhl"].append(r)
        elif r["market"] == "1X2" and r["family"] == "ELO_STANDARD":
            groups["football"].append(r)
    result = {}
    for group in ("nhl", "football"):
        unique = first_decisions(groups[group], lambda r: r["event_id"])
        settled = decisions = passed = 0
        original = variant = benchmark = 0.
        for row in unique:
            index = row["arms"]["VALUE_GAP"]
            use = group == "nhl" or row["odds"][index] < ODDS_LIMIT
            decisions += use
            settlement = outcomes.get(row["id"])
            if not settlement or settlement["status"] != "SETTLED":
                continue
            settled += 1
            passed += use
            y = settlement["outcome"]
            delta = row["odds"][index] - 1 if y == index else -1
            original += delta
            if use:
                variant += delta
            favourite = row["arms"]["MARKET_FAVOURITE"]
            benchmark += row["odds"][favourite] - 1 if y == favourite else -1
        result[group] = dict(events=len(unique), settled=settled, observed_only=len(unique)-decisions,
                             variant_settled=passed, original_units=round(original, 4),
                             filtered_units=round(variant, 4), market_units=round(benchmark, 4))
    return result


def write_review(root, now):
    rows = []
    for path in sorted((root/"data/strategy_ledgers").glob("*.jsonl")):
        rows.extend(read_rows(path))
    research = read_rows(root/"data/market_research_v3.jsonl")
    settlements = {r["forecast_id"]: r for r in research if r["kind"] == "SETTLEMENT"}
    for r in research:
        if r["kind"] == "FORECAST" and r.get("stake_eur", 0) > 0:
            settlement = settlements.get(r["id"], {})
            rows.append(dict(r, strategy="EXPERIMENTAL_MULTI_MARKET",
                             status=settlement.get("status", "OPEN"), pnl_eur=settlement.get("pnl_eur")))
    unique = first_decisions(rows, decision_key)
    report = dict(asof_utc=now.isoformat(), forward_start_utc=START.isoformat(), forward_model_sha256=MODEL_DIGEST,
                  all_positions=summarize(rows), first_decisions=summarize(unique),
                  by_purpose={k:summarize([r for r in rows if category(r)==k]) for k in GROUPS},
                  forward=forward_review(read_rows(root/"data/selection_experiment_v3.jsonl")))
    path = root/"reports/decision_review_latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    return report


if __name__ == "__main__":
    write_review(Path.cwd(), dt.datetime.now(dt.timezone.utc))
