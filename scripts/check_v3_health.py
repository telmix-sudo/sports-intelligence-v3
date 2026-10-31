"""Read-only V3 health audit; never mutate or settle ledger records."""
from __future__ import annotations
import datetime as dt
import json
import os
from pathlib import Path


def check(root: Path, now: dt.datetime) -> tuple[bool, list[str], list[str]]:
    errors, warnings = [], []
    try:
        report=json.loads((root/"reports/strategy_lab_latest.json").read_text(encoding="utf8"))
        accounts=json.loads((root/"data/strategy_accounts_v3.json").read_text(encoding="utf8"))
        latest=dt.datetime.fromisoformat(report["asof_utc"])
        age=(now-latest).total_seconds()/3600
        if age > 4:
            errors.append(f"Strategy lab last report is {age:.1f} hours old (limit 4h)")
        if age < -0.2:
            errors.append("Lab report timestamp is in the future")
        if accounts["accounts"] != report["accounts"]:
            errors.append("Account summary does not match last strategy lab")
        if len(accounts["accounts"]) != 12:
            errors.append("Not all 12 V3 paper wallets are present")
        audit=report.get("ledger_audit") or {}
        if not audit:
            warnings.append("New ledger audit metrics not yet published")
        if audit.get("overdue_count",0):
            warnings.append(f"{audit['overdue_count']} positions pending beyond 5h after kickoff; investigate official final")
        risk=report.get("aggregate_risk") or {}
        if not risk:
            warnings.append("New aggregate paper risk metrics not yet published")
        if report.get("missing_final_event_ids"):
            warnings.append(f"{len(report['missing_final_event_ids'])} started events awaiting official result")
    except (ValueError,KeyError,OSError,TypeError) as exc:
        errors.append("Health check cannot read published lab: "+type(exc).__name__)
    return not errors,errors,warnings


if __name__=="__main__":
    root=Path(__file__).resolve().parents[1]
    ok,errors,warnings=check(root,dt.datetime.now(dt.timezone.utc))
    for item in warnings:print("::warning::"+item)
    for item in errors:print("::error::"+item)
    summary=os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary,"a",encoding="utf8") as fp:
            fp.write("## V3 paper health check\n\n")
            fp.write("Status: **"+("PASS" if ok else "FAIL")+"**\n\n")
            for line in warnings+errors:
                fp.write("- "+line+"\n")
    raise SystemExit(0 if ok else 1)
