import unittest
from sports_intelligence.poisson import FootballPoisson
from sports_intelligence.experimental_markets import football_goal_markets, settle_goal_market

class GoalMarketsTests(unittest.TestCase):
    def test_normalized_distributions(self):
        m=FootballPoisson()
        for market,p in football_goal_markets(m,"Home","Away").items():
            if isinstance(p,tuple):
                self.assertAlmostEqual(sum(p),1,places=9)
                self.assertTrue(all(0<=x<=1 for x in p))
    def test_outcomes(self):
        self.assertTrue(settle_goal_market("OVER_UNDER_2_5",0,2,1))
        self.assertTrue(settle_goal_market("OVER_UNDER_2_5",1,1,1))
        self.assertTrue(settle_goal_market("BTTS",0,1,1))
        self.assertTrue(settle_goal_market("BTTS",1,1,0))
        self.assertTrue(settle_goal_market("ASIAN_HOME_MINUS_0_5",0,1,0))
        self.assertTrue(settle_goal_market("ASIAN_HOME_MINUS_0_5",1,0,0))
    def test_reject_missing_final(self):
        with self.assertRaises(ValueError):
            settle_goal_market("BTTS",0,None,1)

if __name__=="__main__": unittest.main()
