"""Sports score baselines are strictly research only, with no bookmaker fitting."""
import unittest
from sports_intelligence.north_america_models import TeamScoreBaseline


class ResearchBaselineTests(unittest.TestCase):
    def test_no_untrained_paper_probability(self):
        for sport in ("nba", "nhl", "nfl", "mlb"):
            model=TeamScoreBaseline(sport)
            self.assertEqual(model.forecast("A","B")["status"],"INSUFFICIENT_PRIOR_GAMES")
    def test_separate_sport_models_and_distribution(self):
        for sport,score in (("nba",110),("nhl",3),("nfl",25),("mlb",5)):
            model=TeamScoreBaseline(sport)
            minimum={"nba":12,"nhl":16,"nfl":8,"mlb":20}[sport]
            for i in range(minimum):
                model.update("A","B",score+1,score)
            output=model.forecast("A","B")
            self.assertEqual(output["status"],"RESEARCH_ONLY_UNCALIBRATED")
            p=output["home_probability"];a=output["away_probability"]
            self.assertTrue(0<=p<=1 and 0<=a<=1)
            if sport in ("nhl","mlb"):
                self.assertAlmostEqual(p+a+output["draw_regulation_probability"],1,places=5)
            else:
                self.assertAlmostEqual(p+a,1,places=5)

if __name__=="__main__":
    unittest.main()
