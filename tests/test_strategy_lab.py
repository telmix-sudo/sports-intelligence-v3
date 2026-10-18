"""Tests for point-in-time integrity and independent virtual portfolios."""
import datetime as dt
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sports_intelligence.strategy_lab import (
    STRATEGIES, collect_openfootball, openfootball_matches, historical_comparison,
    build_accounts, open_accounts, make_models, predictions, run_strategy_lab,
    INITIAL_BANKROLL_EUR, dc_adjusted,
)

UTC = dt.timezone.utc
DAY = dt.datetime(2026, 10, 18, 10, tzinfo=UTC)
PAYLOAD = {
    "matches": [
        {"date": "2024-01-01", "team1": "Arsenal FC", "team2": "Chelsea FC", "score": {"ft": [2, 1]}},
        {"date": "2024-01-01", "team1": "Liverpool FC", "team2": "Fulham FC", "score": [0, 0]},
        {"date": "2026-10-18", "team1": "Arsenal FC", "team2": "Chelsea FC", "score": {"ft": [4, 1]}},
        {"date": "2024-02-01", "team1": "Chelsea FC", "team2": "Liverpool FC", "score": None},
    ]
}


class StrategyLabTests(unittest.TestCase):
    def test_cc0_parser_excludes_today_and_missing_scores(self):
        result = openfootball_matches(PAYLOAD, "epl", DAY.date())
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["home"], "arsenal")
        self.assertEqual(result[0]["away"], "chelsea")
        self.assertEqual(result[1]["goals_home"], 0)

    def test_public_domain_loader_reports_sources_and_failures(self):
        def fetcher(url):
            if "/2024-25/" in url:
                raise ConnectionError()
            return json.dumps(PAYLOAD), {"sha256": "synthetic"}
        grouped, provenance = collect_openfootball(
            DAY.date(), fetcher=fetcher, seasons=("2024-25", "2025-26"),
            leagues={"epl": "en.1"})
        self.assertEqual(len(grouped["epl"]), 2)
        self.assertEqual(len(provenance["sources"]), 1)
        self.assertEqual(len(provenance["failures"]), 1)

    def test_five_models_proper_distributions(self):
        models = make_models()
        probabilities = predictions(models, "Arsenal", "Chelsea")
        self.assertEqual(set(probabilities), set(STRATEGIES))
        for values in probabilities.values():
            self.assertAlmostEqual(sum(values), 1, places=9)
            self.assertTrue(all(0 <= value <= 1 for value in values))

    def test_separate_research_scoreboard_without_odds_does_not_fake_roi(self):
        game = {"date": dt.date(2025, 9, 1), "league": "epl",
                "home": "arsenal", "away": "chelsea",
                "goals_home": 2, "goals_away": 1, "odds": None}
        models, scores = historical_comparison({"epl": [game]})
        self.assertEqual(set(scores), set(STRATEGIES))
        for name in STRATEGIES:
            self.assertIsNone(scores[name]["historical_roi"])
            self.assertEqual(scores[name]["n"], 0)

    def test_each_account_has_own_balance_and_leaves_frozen_forecast(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            name = STRATEGIES[0]
            path = root / "data" / "strategy_ledgers" / (name.lower() + ".jsonl")
            path.parent.mkdir(parents=True)
            row = {"id": "xyz", "event_id": "epl:123", "sport": "epl",
                   "probabilities": [0.6, 0.2, 0.2], "odds": [2.0, 3.0, 5.0],
                   "selection_index": 0, "stake_eur": 5.0, "status": "OPEN",
                   "forecast_utc": "2026-10-17T10:00:00+00:00",
                   "start_utc": "2026-10-17T16:00:00+00:00"}
            path.write_text(json.dumps(row) + "\n")
            accounts, counts = build_accounts(root, {"epl:123": {"outcome": 0}}, DAY)
            self.assertEqual(counts["settled_this_run"], 1)
            self.assertEqual(accounts[name]["bankroll_eur"], 1005.0)
            for other in STRATEGIES[1:]:
                self.assertEqual(accounts[other]["bankroll_eur"], INITIAL_BANKROLL_EUR)
            new = json.loads(path.read_text().splitlines()[0])
            self.assertEqual(new["forecast_utc"], row["forecast_utc"])
            self.assertEqual(new["probabilities"], row["probabilities"])
            self.assertEqual(new["odds"], row["odds"])
            _, repeated = build_accounts(root, {"epl:123": {"outcome": 0}}, DAY)
            self.assertEqual(repeated["settled_this_run"], 0)

    def test_without_stamped_quotes_no_virtual_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = {"epl": make_models()}
            for m in models["epl"].values():
                if m is not None:
                    m.played.update({"arsenal": 20, "chelsea": 20})
            event = {"id": "epl:1", "sport": "epl", "home": "Arsenal", "away": "Chelsea",
                     "start_utc": "2026-10-18T18:00:00+00:00", "status": "pre",
                     "quote": None}
            counts, added = open_accounts(root, models, [event], DAY)
            self.assertFalse(added)
            self.assertEqual(sum(x["new_entries"] for x in counts.values()), 0)

    def test_frozen_odds_open_isolated_paper_and_settle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = {"epl": make_models()}
            for m in models["epl"].values():
                if m is not None:
                    m.played.update({"arsenal": 20, "chelsea": 20})
            models["epl"]["ELO_STANDARD"].ratings.update({"arsenal": 1700, "chelsea": 1450})
            models["epl"]["ELO_DYNAMIC"].ratings.update({"arsenal": 1700, "chelsea": 1450})
            prices = [2.0, 3.7, 4.5]
            from sports_intelligence.metrics import no_vig
            event = {"id": "epl:101", "sport": "epl", "home": "Arsenal", "away": "Chelsea",
                     "start_utc": "2026-10-18T18:00:00+00:00", "status": "pre",
                     "quote": {"market": "1X2", "decimal": prices,
                               "no_vig": list(no_vig(prices)),
                               "quote_timestamp_available": True,
                               "provider": "synthetic bookmaker",
                               "bookmakers": ["A", "A", "A"],
                               "best_book_updates": ["2026-10-18T09:30:00+00:00"] * 3}}
            _, entries = open_accounts(root, models, [event], DAY)
            self.assertEqual(entries.get("ELO_STANDARD"), 1)
            ledger = root / "data" / "strategy_ledgers" / "elo_standard.jsonl"
            row = json.loads(ledger.read_text().splitlines()[0])
            self.assertEqual(row["stake_eur"], 5.0)
            self.assertEqual(row["forecast_utc"], DAY.isoformat())
            self.assertEqual(row["selection_index"], 0)
            _, repeated = open_accounts(root, models, [event], DAY)
            self.assertEqual(repeated, {})
            accounts, settled = build_accounts(
                root, {"epl:101": {"outcome": 0}},
                dt.datetime(2026, 10, 19, tzinfo=UTC))
            self.assertEqual(accounts["ELO_STANDARD"]["bankroll_eur"], 1005.0)
            self.assertEqual(settled["settled_this_run"] >= 1, True)

    def test_end_to_end_offline_reproduces_honest_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def fake_history(url):
                return json.dumps(PAYLOAD), {"sha256": "synthetic"}
            coverage = {"sources_ok": 1, "sources_failed": 0}
            def discover(now, sports, future_days, need_yesterday):
                return [], coverage
            with patch("sports_intelligence.strategy_lab.collect_openfootball") as loader:
                loader.return_value = ({"epl": openfootball_matches(PAYLOAD, "epl", DAY.date())},
                                       {"matches_per_league": {"epl": 2}, "sources": [], "failures": []})
                report = run_strategy_lab(root, DAY, discover=discover)
            self.assertEqual(len(report["accounts"]), 5)
            self.assertTrue((root / "reports" / "strategy_lab_latest.json").is_file())
            self.assertTrue((root / "data" / "strategy_accounts_v3.json").is_file())
            self.assertEqual(report["new_paper_entries"], {})
            for val in report["accounts"].values():
                self.assertEqual(val["bankroll_eur"], 1000.0)


if __name__ == "__main__":
    unittest.main()
