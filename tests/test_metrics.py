import math
import unittest

from sports_intelligence.metrics import american_to_decimal, brier, log_loss, no_vig


class MetricTests(unittest.TestCase):
    def test_three_way(self):
        self.assertAlmostEqual(brier((0.5, 0.3, 0.2), 0), 0.38)
        self.assertAlmostEqual(log_loss((0.5, 0.3, 0.2), 0), math.log(2))

    def test_no_vig(self):
        probabilities = no_vig((2, 2))
        self.assertEqual(probabilities, (0.5, 0.5))
        self.assertAlmostEqual(sum(no_vig((1.8, 3.5, 5.0))), 1)

    def test_invalid(self):
        for bad in ((1, 2), (float("nan"), 2), (0.5,)):
            with self.assertRaises(ValueError):
                no_vig(bad)
        with self.assertRaises(ValueError):
            brier((0.9, 0.9), 0)

    def test_american(self):
        self.assertAlmostEqual(american_to_decimal(-200), 1.5)
        self.assertAlmostEqual(american_to_decimal("+150"), 2.5)
        with self.assertRaises(ValueError):
            american_to_decimal(80)


if __name__ == "__main__":
    unittest.main()
