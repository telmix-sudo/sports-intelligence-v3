import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sports_intelligence.cli import run


class PipelineTests(unittest.TestCase):
    def test_report_archives_and_state_with_no_data(self):
        now = dt.datetime(2026, 10, 18, 10, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "data" / "state.json").write_text('{"mode":"paper_trading","v1":{"papers":5}}')
            coverage = {"sources_checked": 1, "sources_ok": 0, "sources_failed": 1,
                        "errors": [{"sport":"nfl","date":"2026-10-18","error":"offline"}],
                        "provenance": [], "by_sport": {"nfl":0}}
            odds = {"status": "MISSING_KEY_NO_ODDS_API_CALLS", "configured": False,
                    "calls_today": 0, "calls_this_run": 0, "quotes_matched": 0}
            with (patch("sports_intelligence.cli.historical_football",
                        return_value=(None, {"status":"UNAVAILABLE"})),
                  patch("sports_intelligence.cli.historical_tennis",
                        return_value={"status":"DISABLED"}),
                  patch("sports_intelligence.cli.capture_scoreboards",
                        return_value=([], coverage)),
                  patch("sports_intelligence.cli.annotate_odds", return_value=odds)):
                report = run(root, now)
            self.assertEqual(report["model_gate"], "NO_MODEL")
            self.assertEqual(report["prospective_event_count"], 0)
            self.assertEqual(report["v3_papers_with_stake"], 0)
            self.assertTrue((root / "reports" / "latest.json").is_file())
            self.assertTrue((root / "reports" / "runs" / "2026-10-18.jsonl").is_file())
            state = json.loads((root / "data" / "state.json").read_text())
            self.assertEqual(state["v3"]["open_exposure_eur"], 0)
            self.assertEqual(state["v1"]["papers"], 5)


if __name__ == "__main__":
    unittest.main()
