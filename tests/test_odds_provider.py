import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from sports_intelligence.odds_provider import annotate_odds, quotes_for_event, SPORTS_PER_POLL, MAX_CALLS_PER_DAY


UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 18, 8, 45, tzinfo=UTC)
EVENT = {
    "id": "epl:123", "sport": "epl", "home": "Arsenal", "away": "Chelsea",
    "status": "pre", "start_utc": "2026-10-18T18:00:00+00:00", "quote": None
}
ROWS = [{
    "home_team": "Arsenal", "away_team": "Chelsea",
    "commence_time": "2026-10-18T18:00:00Z",
    "bookmakers": [{
        "key": "trusted_eu",
        "last_update": "2026-10-18T08:35:00Z",
        "markets": [{"key": "h2h", "last_update": "2026-10-18T08:35:00Z",
                    "outcomes": [
                        {"name": "Arsenal", "price": 1.8},
                        {"name": "Draw", "price": 3.5},
                        {"name": "Chelsea", "price": 4.9}
                    ]}]
    }]
}]


class OddsProviderTests(unittest.TestCase):
    def test_three_way_and_book_timestamp(self):
        value = quotes_for_event(EVENT, ROWS, NOW)
        self.assertEqual(value["market"], "1X2")
        self.assertEqual(value["decimal"], [1.8, 3.5, 4.9])
        self.assertAlmostEqual(sum(value["no_vig"]), 1.0, places=6)
        self.assertTrue(value["quote_timestamp_available"])

    def test_stale_market_fails_closed(self):
        stale = json.loads(json.dumps(ROWS))
        stale[0]["bookmakers"][0]["last_update"] = "2026-10-16T08:35:00Z"
        stale[0]["bookmakers"][0]["markets"][0]["last_update"] = "2026-10-16T08:35:00Z"
        self.assertIsNone(quotes_for_event(EVENT, stale, NOW))

    def test_api_key_never_persisted_and_budgeted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            called = []
            def fake(sport, key):
                called.append((sport, key))
                return ROWS
            events = [dict(EVENT)]
            summary = annotate_odds(events, path, NOW, secret="DO_NOT_PERSIST", requester=fake)
            self.assertEqual(len(called), len(SPORTS_PER_POLL[NOW.hour]))
            self.assertEqual(summary["quotes_matched"], 1)
            self.assertTrue(events[0]["quote"])
            disk = (path / "data" / "odds_cache_v3.json").read_text()
            self.assertNotIn("DO_NOT_PERSIST", disk)
            self.assertNotIn("commence_time", disk, "Bulk API response cannot be redistributed")
            again = annotate_odds([dict(EVENT)], path, NOW, secret="DO_NOT_PERSIST", requester=fake)
            self.assertEqual(len(called), len(SPORTS_PER_POLL[NOW.hour]), "No repeated calls inside same hour")
            self.assertEqual(again["calls_this_run"], 0)
            self.assertLessEqual(summary["calls_today"], MAX_CALLS_PER_DAY)

    def test_no_key_no_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = annotate_odds([dict(EVENT)], Path(tmp), NOW, secret="", requester=lambda x, y: self.fail("unexpected request"))
            self.assertEqual(result["status"], "MISSING_KEY_NO_ODDS_API_CALLS")


if __name__ == "__main__":
    unittest.main()
