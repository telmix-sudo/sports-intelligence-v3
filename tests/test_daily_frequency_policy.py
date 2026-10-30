import unittest
from sports_intelligence.custom_strategies import FREQUENCY_NAMES, ALL_NAMES
from sports_intelligence.frequency_paper import POLICIES

class DailyFrequencyPolicyTests(unittest.TestCase):
    def test_daily_strategy_registered(self):
        self.assertIn("FREQ_DAILY_5_10", FREQUENCY_NAMES)
        self.assertIn("FREQ_DAILY_5_10", ALL_NAMES)
    def test_daily_limits_and_risk(self):
        low, cap, edge, uncertainty, exposure = POLICIES["FREQ_DAILY_5_10"]
        self.assertEqual((low, cap), (5, 10))
        self.assertGreater(edge, 0)
        self.assertGreater(uncertainty, edge)
        self.assertLessEqual(exposure, .02)

if __name__ == "__main__":
    unittest.main()
