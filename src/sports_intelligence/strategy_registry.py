"""Durable strategy account registry: never reset or silently rebase virtual funds.

The ledger is the transaction source of truth. The account summary is an integrity
checkpoint: conflicting balances block the run instead of being overwritten.
New strategies start at 1000 virtual EUR; existing strategies retain balances.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

from .custom_strategies import ORIGINAL_NAMES, CUSTOM_NAMES, REFERENCE_NAMES, FREQUENCY_NAMES, RULES

START_EUR = 1000.0
FIRST_LAB_UTC = "2026-10-18T12:34:17.387525+00:00"
REGISTRY_VERSION = 1


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf8")
    temp.replace(path)


def ensure_registry(root: Path, now: dt.datetime, names: tuple[str, ...]) -> dict:
    """Register each strategy exactly once; persist its creation timestamp.

    Old strategies are backfilled with their documented historical lab inception.
    New strategy start times use the actual first deployment time; NEVER the
    historical training data timestamp.
    """
    if now.tzinfo is None:
        raise ValueError("Registration time must have UTC offset")
    stamp = now.astimezone(dt.timezone.utc).isoformat()
    path = root / "data" / "strategy_registry_v3.json"
    if path.exists():
        value = json.loads(path.read_text(encoding="utf8"))
        registered = value["strategies"]
        if not isinstance(registered, dict):
            raise ValueError("Invalid strategy registry")
        removed = set(registered) - set(names)
        if removed:
            raise ValueError(f"Refusing to drop existing virtual accounts: {sorted(removed)}")
    else:
        registered = {}
        value = {"registry_version": REGISTRY_VERSION, "strategies": registered}
    amended = not path.exists()
    for name in names:
        if name in registered:
            if registered[name]["initial_bank_eur"] != START_EUR:
                raise ValueError("Unexpected starting bank for " + name)
            continue
        if name in ORIGINAL_NAMES:
            started = FIRST_LAB_UTC
            origin = "legacy_existing_account"
        elif name in CUSTOM_NAMES:
            started = stamp
            origin = "original_v3_strategy"
        elif name in REFERENCE_NAMES:
            started = stamp
            origin = "research_control_reference_no_edge"
        elif name in FREQUENCY_NAMES:
            started = stamp
            origin = "prospective_frequency_model_paper"
        else:
            raise ValueError("New strategy requires explicit registered model identity: " + name)
        registered[name] = {
            "created_utc": started,
            "initial_bank_eur": START_EUR,
            "origin": origin,
            "strategy_version": RULES.get(name, {}).get("version", "legacy-1.0.0"),
        }
        amended = True
    if amended:
        _write_atomic(path, value)
    return registered


def verify_prior_accounts(root: Path, registered: dict) -> None:
    """Fail closed before any updates if a persisted balance does not match ledger.

    Prevents accidental resets, deletion of older accounts or hand-edited P/L.
    No automatic migration is attempted where numbers conflict.
    """
    state_file = root / "data" / "strategy_accounts_v3.json"
    if not state_file.exists():
        return
    saved = json.loads(state_file.read_text(encoding="utf8"))
    accounts = saved.get("accounts", {})
    unknown = set(accounts) - set(registered)
    if unknown:
        raise ValueError("Unexpected accounts in previous state: " + repr(sorted(unknown)))
    for name, old in accounts.items():
        expected_init = registered[name]["initial_bank_eur"]
        if float(old["bankroll_initial_eur"]) != expected_init:
            raise ValueError("Starting bank was modified for " + name)
        ledger = root / "data" / "strategy_ledgers" / (name.lower() + ".jsonl")
        pnl = 0.0
        if ledger.exists():
            for line in ledger.read_text(encoding="utf8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if item.get("strategy", name) != name:
                    raise ValueError("Cross-account paper detected")
                if item.get("status") == "SETTLED":
                    pnl += float(item["pnl_eur"])
        expected = round(expected_init + pnl, 4)
        actual = float(old["bankroll_eur"])
        if not math.isfinite(actual) or not math.isclose(actual, expected, abs_tol=0.0001):
            raise ValueError(
                "Bankroll inconsistency: " + name +
                ". Refusing overwrite; reconcile ledger with original state."
            )
