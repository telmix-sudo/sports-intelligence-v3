import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from sports_intelligence.football import FootballElo
from sports_intelligence.scanner import (
    capture_scoreboards, classify, extract_odds, freeze_observations,
    settle_observations, unpack_scoreboard,
)

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 18, 10, tzinfo=UTC)


def fixture(kickoff="2026-10-18T18:00:00Z", state="pre", completed=False, draw=False):
    return {"events": [{
        "id": "123", "date": kickoff,
        "status": {"type": {"state": state, "completed": completed}},
        "competitions": [{"competitors": [
            {"homeAway": "home", "team": {"displayName": "Arsenal"}, "score": "3"},
            {"homeAway": "away", "team": {"displayName": "Chelsea"}, "score": "1"}
        ], "odds": [{
            "provider": {"name": "Example"},
            "homeTeamOdds": {"moneyLine": "-120"},
            "awayTeamOdds": {"moneyLine": "+130"},
            **({"drawOdds": {"moneyLine": "+300"}} if draw else {})
        }]}]
    }]}


class ScannerTests(unittest.TestCase):
    def test_only_complete_odds(self):
        competition = fixture(draw=True)["events"][0]["competitions"][0]
        quote = extract_odds(competition, "epl")
        self.assertEqual(quote["market"], "1X2")
        self.assertAlmostEqual(sum(quote["no_vig"]), 1, places=6)
        self.assertIsNone(extract_odds(fixture()["events"][0]["competitions"][0], "epl"))

    def test_past_no_odds(self):
        past = unpack_scoreboard(fixture("2026-10-18T08:00:00Z"), "epl", NOW)
        self.assertIsNone(past[0]["quote"])
        self.assertIsNone(classify(past[0], None, NOW))

    def test_candidate_and_no_stake(self):
        events = unpack_scoreboard(fixture(draw=True), "epl", NOW)
        elo = FootballElo()
        elo.played["arsenal"], elo.played["chelsea"] = 15, 15
        elo.ratings["arsenal"], elo.ratings["chelsea"] = 1650, 1470
        result = classify(events[0], elo, NOW)
        self.assertIsNotNone(result["model_probabilities"])
        self.assertGreater(result["best_model_edge_pp"], 2)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "forecasts_v3.jsonl"
            records, new = freeze_observations([result], path, NOW)
            self.assertEqual(new, 1)
            self.assertEqual(records[0]["stake_eur"], 0)
            records2, new2 = freeze_observations([result], path, NOW)
            self.assertEqual(new2, 0)
            self.assertEqual(len(records2), 1)
            final = unpack_scoreboard(fixture("2026-10-18T18:00:00Z", "post", True, True), "epl", NOW)
            settled = settle_observations(records2, final, path)
            self.assertEqual(settled, 1)
            self.assertEqual(records2[0]["result_outcome"], 0)
            self.assertGreaterEqual(records2[0]["brier"], 0)
            self.assertEqual(settle_observations(records2, final, path), 0)

    def test_data_unavailable_is_not_invented(self):
        def offline(route, day):
            raise ConnectionError("offline")
        events, coverage = capture_scoreboards(
            NOW, fetcher=offline, sports={"nfl": "football/nfl"}, future_days=0, need_yesterday=False)
        self.assertEqual(events, [])
        self.assertEqual(coverage["sources_failed"], 1)
        self.assertEqual(coverage["sources_ok"], 0)

    def test_future_only(self):
        def mocked(route, day):
            return fixture(draw=True), {"url": "test", "fetched_at_utc": NOW.isoformat()}
        events, coverage = capture_scoreboards(
            NOW, fetcher=mocked, sports={"epl": "soccer/eng.1"}, future_days=0, need_yesterday=False)
        self.assertEqual(len(events), 1)
        self.assertEqual(coverage["by_sport"]["epl"], 1)


if __name__ == "__main__":
    unittest.main()
