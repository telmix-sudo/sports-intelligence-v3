import datetime as dt
import unittest

from sports_intelligence.football import FootballElo, backtest_football, canonical, parse_matches
from sports_intelligence.tennis import TennisElo, backtest_tennis, parse_matches as parse_tennis


FOOTBALL_CSV = """Date,HomeTeam,AwayTeam,FTHG,FTAG,AvgH,AvgD,AvgA
15/08/2025,Man United,Arsenal,2,1,2.30,3.10,3.20
15/08/2025,Chelsea,Liverpool,1,1,2.90,3.00,2.40
16/08/2025,Arsenal,Chelsea,0,1,1.90,3.60,4.50
20/10/2026,Arsenal,Liverpool,4,1,1.55,4.50,6.00
"""

TENNIS_CSV = """tourney_id,tourney_date,surface,winner_name,loser_name
2025-001,20250101,Hard,Zoey,Bob
2025-001,20250101,Hard,Bob,Amy
2025-002,20250201,Clay,Amy,Zoey
"""


class FootballTests(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(canonical("Man United"), canonical("Manchester United"))
        self.assertEqual(canonical("Wolves"), canonical("Wolverhampton Wanderers"))

    def test_cutoff_excludes_today_and_future(self):
        matches = parse_matches(FOOTBALL_CSV, dt.date(2026, 10, 20))
        self.assertEqual(len(matches), 3)
        self.assertEqual(matches[0]["home"], "manchester united")

    def test_date_grouped_prequential(self):
        matches = parse_matches(FOOTBALL_CSV)
        model, report = backtest_football(matches, warmup=2)
        self.assertEqual(report["n"], 1)
        self.assertEqual(report["benchmark_n"], 1)
        self.assertEqual(report["training_matches"], 4)
        self.assertAlmostEqual(sum(model.predict("Arsenal", "Liverpool")), 1)
        self.assertEqual(model.played["arsenal"], 3)

    def test_day_never_uses_same_day_result(self):
        one = {"date": dt.date(2024, 1, 1), "home": "a", "away": "b",
               "goals_home": 6, "goals_away": 0, "odds": None}
        two = {"date": dt.date(2024, 1, 1), "home": "a", "away": "c",
               "goals_home": 0, "goals_away": 1, "odds": None}
        _, result = backtest_football([one, two], warmup=1)
        self.assertEqual(result["n"], 0, "Same-day results must never count as warmup")


class TennisTests(unittest.TestCase):
    def test_prediction_symmetric(self):
        elo = TennisElo()
        self.assertAlmostEqual(elo.predict("Alice", "Bob", "Hard")[0], 0.5)
        elo.update("Alice", "Bob", "Hard")
        self.assertGreater(elo.predict("Alice", "Bob", "Hard")[0], 0.5)

    def test_tournament_grouped(self):
        parsed = parse_tennis(TENNIS_CSV)
        model, report = backtest_tennis(parsed, warmup=1)
        self.assertEqual(report["n"], 1, "Same event date predictions must not use same-day results")
        self.assertEqual(report["benchmark_n"], 1)
        self.assertEqual(report["model_status"], "EXPERIMENTAL")


if __name__ == "__main__":
    unittest.main()
