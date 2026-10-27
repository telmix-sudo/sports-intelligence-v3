"""Regression tests: introducing new strategies must NOT reset existing ledgers."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from sports_intelligence.custom_strategies import (
    ORIGINAL_NAMES, CUSTOM_NAMES, REFERENCE_NAMES, ALL_NAMES, RULES, accepts_candidate
)
from sports_intelligence.strategy_lab import (
    build_accounts, make_models, open_accounts, predictions, STRATEGIES
)
from sports_intelligence.strategy_registry import ensure_registry, verify_prior_accounts

NOW = dt.datetime(2026, 10, 18, 14, 0, tzinfo=dt.timezone.utc)


class PersistentWalletTests(unittest.TestCase):
    def test_new_strategies_do_not_reset_old_portfolios(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            ledger_dir = data / "strategy_ledgers"
            ledger_dir.mkdir(parents=True)
            old = ORIGINAL_NAMES[0]
            row = {
                "id": "frozen-old-trade", "strategy": old, "event_id": "epl:older",
                "sport": "epl", "forecast_utc": "2026-10-18T12:40:00+00:00",
                "start_utc": "2026-10-18T12:50:00+00:00",
                "selection_index": 2, "probabilities": [.2, .25, .55],
                "odds": [2.6, 3.2, 2.1], "stake_eur": 5,
                "status": "SETTLED", "pnl_eur": -5,
                "brier": .0, "log_loss": 1,
            }
            (ledger_dir / (old.lower() + ".jsonl")).write_text(json.dumps(row) + "\n")
            previous = {
                "updated_utc": "2026-10-18T13:00:00+00:00",
                "accounts": {old: {"bankroll_initial_eur": 1000, "bankroll_eur": 995}}
            }
            (data / "strategy_accounts_v3.json").write_text(json.dumps(previous))
            registry = ensure_registry(root, NOW, ALL_NAMES)
            verify_prior_accounts(root, registry)
            self.assertEqual(len(registry), 9)
            self.assertEqual(registry["ELO_STANDARD"]["origin"], "legacy_existing_account")
            self.assertEqual(registry[CUSTOM_NAMES[0]]["created_utc"], NOW.isoformat())
            accounts, changes = build_accounts(root, {}, NOW)
            self.assertEqual(accounts[old]["bankroll_eur"], 995)
            for name in CUSTOM_NAMES + REFERENCE_NAMES:
                self.assertEqual(accounts[name]["bankroll_eur"], 1000)
                self.assertEqual(accounts[name]["settled"], 0)
            frozen_first = (ledger_dir / (old.lower() + ".jsonl")).read_text()
            later = NOW + dt.timedelta(days=7)
            reg_again = ensure_registry(root, later, ALL_NAMES)
            self.assertEqual(reg_again[CUSTOM_NAMES[0]]["created_utc"], NOW.isoformat())
            self.assertEqual((ledger_dir / (old.lower() + ".jsonl")).read_text(), frozen_first)

    def test_corrupted_previous_balance_blocks_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = ensure_registry(root, NOW, ALL_NAMES)
            data = root / "data"
            (data / "strategy_accounts_v3.json").write_text(json.dumps({
                "accounts": {"ELO_STANDARD": {
                    "bankroll_initial_eur": 1000, "bankroll_eur": 991
                }}
            }))
            with self.assertRaisesRegex(ValueError, "Bankroll inconsistency"):
                verify_prior_accounts(root, registry)

    def test_removed_account_cannot_be_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ensure_registry(root, NOW, ALL_NAMES)
            with self.assertRaisesRegex(ValueError, "Refusing to drop"):
                ensure_registry(root, NOW, ORIGINAL_NAMES)

    def test_strict_price_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = {"epl": make_models()}
            for x in models["epl"].values():
                if x is not None:
                    x.played.update({"arsenal": 40, "chelsea": 40})
                    if hasattr(x, "ratings"):
                        x.ratings.update({"arsenal": 1730, "chelsea": 1450})
            e = {
                "id": "epl:987", "sport": "epl",
                "home": "Arsenal", "away": "Chelsea", "status": "pre",
                "start_utc": "2026-10-18T20:00:00+00:00",
                "quote": {
                    "market": "1X2", "decimal": [2.15, 3.7, 4.4],
                    "no_vig": [.476, .294, .230], "provider": "test",
                    "quote_timestamp_available": True,
                    "bookmaker_count": 3
                }
            }
            _, added = open_accounts(root, models, [e], NOW)
            self.assertEqual(added, {}, "Flag alone is not a timestamp")
            e["quote"]["best_book_updates"] = ["2026-10-18T07:30:00+00:00"] * 3
            _, added = open_accounts(root, models, [e], NOW)
            self.assertNotIn("DISAGREEMENT_VETO", added, "Veto requires fresh odds <4h")
            self.assertNotIn("MARKET_ANCHORED", added, "Market anchored requires fresh quotes <6h")

    def test_veto_rejects_opposed_models(self):
        base = {
            "ELO_STANDARD": (.65,.18,.17),
            "ELO_DYNAMIC": (.58,.20,.22),
            "POISSON_SHRINK": (.25,.25,.50),
            "DIXON_COLES_LOW": (.20,.28,.52),
            "ELO_POISSON_BLEND": (.45,.23,.32),
        }
        self.assertFalse(accepts_candidate("DISAGREEMENT_VETO", 0, base, 50, .10, .10, 1, 4))
        self.assertFalse(accepts_candidate("MARKET_ANCHORED", 0, base, 50, .10, .10, 1, 1))


if __name__ == "__main__":
    unittest.main()
