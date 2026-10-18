import datetime as dt
import math
import unittest

from sports_intelligence.poisson import FootballPoisson, backtest_poisson


class PoissonTests(unittest.TestCase):
    def test_probability_sum(self):
        model = FootballPoisson()
        p = model.predict("Arsenal", "Chelsea")
        self.assertEqual(len(p), 3)
        self.assertAlmostEqual(sum(p), 1, places=10)
        self.assertTrue(all(x > 0 for x in p))

    def test_updates_improve_attack_rating(self):
        model = FootballPoisson()
        initial = model.predict("Arsenal", "Chelsea")[0]
        for _ in range(15):
            model.update({"home": "arsenal", "away": "chelsea",
                          "goals_home": 4, "goals_away": 0})
        self.assertGreater(model.predict("Arsenal", "Chelsea")[0], initial)

    def test_same_date_outcome_unavailable_to_warmup(self):
        day = dt.date(2025, 1, 1)
        rows = [{"date": day, "home": "a", "away": "b",
                 "goals_home": 3, "goals_away": 2, "odds": None},
                {"date": day, "home": "c", "away": "d",
                 "goals_home": 2, "goals_away": 0, "odds": None}]
        _, score = backtest_poisson(rows, warmup=1)
        self.assertEqual(score["n"], 0)


if __name__ == "__main__":
    unittest.main()
