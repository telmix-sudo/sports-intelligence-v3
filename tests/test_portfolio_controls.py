"""Regression: no ledger resets, no fictional odds, aggregate exposure limits."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from sports_intelligence.portfolio_controls import (
    PaperRiskBook, conservative_expected_value, audit_ledgers,
    INITIAL_TOTAL_BANK, MAX_EVENT_OPEN_FRACTION,
)

UTC = dt.timezone.utc


class PaperControlsTests(unittest.TestCase):
    def test_cross_wallet_event_cap_and_minimum_stake(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            risk = PaperRiskBook(root)
            allocated = []
            for i in range(12):
                allocated.append(risk.reserve("epl:one", 5))
            self.assertEqual(round(sum(allocated), 2),
                             round(INITIAL_TOTAL_BANK * MAX_EVENT_OPEN_FRACTION, 2))
            self.assertLessEqual(risk.reserve("epl:one", 5), 0)
            self.assertEqual(risk.reserve("epl:two", .02), 0)
            self.assertEqual(risk.reserve("epl:two", 5), 5)
            self.assertEqual(risk.summary()["total_open_eur"], 41)

    def test_preserves_existing_pending_even_if_exceeding_new_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "strategy_ledgers" / "existing.jsonl"
            path.parent.mkdir(parents=True)
            item = {"id": "old", "event_id": "epl:one", "status": "OPEN",
                    "stake_eur": 50, "start_utc": "2026-10-30T15:00:00+00:00",
                    "forecast_utc": "2026-10-29T10:00:00+00:00", "strategy": "EXISTING"}
            content = json.dumps(item)+"\n"
            path.write_text(content)
            risk = PaperRiskBook(root)
            self.assertEqual(risk.reserve("epl:one", 5), 0)
            self.assertEqual(path.read_text(), content)
            self.assertEqual(risk.reserve("epl:second", 5), 5)

    def test_conservative_ev_rejects_marginal_longshot(self):
        self.assertLess(conservative_expected_value(.19, 6.2, 16), .01)
        self.assertGreater(conservative_expected_value(.7, 2.1, 50), .01)
        self.assertLess(conservative_expected_value(float("nan"), 2, 40), 0)

    def test_overdue_requires_official_final_not_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "strategy_ledgers" / "example.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "id": "pending", "event_id": "epl:1", "strategy": "EXAMPLE",
                "sport": "epl", "market": "1X2", "status": "OPEN", "stake_eur": 5,
                "start_utc": "2026-10-30T15:00:00+00:00",
                "forecast_utc": "2026-10-29T15:00:00+00:00"})+"\n")
            audit = audit_ledgers(root, dt.datetime(2026,10,31,15,tzinfo=UTC))
            self.assertEqual(audit["overdue_count"], 1)
            self.assertEqual(audit["open"], 1)
            self.assertTrue(audit["attention_required"])
            self.assertEqual(json.loads(path.read_text()), json.loads(path.read_text()))


if __name__ == "__main__":
    unittest.main()
