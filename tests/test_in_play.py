import datetime as dt
from http.client import IncompleteRead
import json
import unittest

from sports_intelligence import in_play
from sports_intelligence.odds_provider import parse_utc
from sports_intelligence.poisson import FootballPoisson
from sports_intelligence.scanner import capture_scoreboards


START = "2026-11-07T15:00:00+00:00"
NOW = parse_utc("2026-11-07T15:35:20+00:00")
BOARD_TIME = "2026-11-07T15:35:00+00:00"
STATUS = {"name": "STATUS_IN_PROGRESS", "state": "in", "completed": False}
TEAMS = [
    {"homeAway": "home", "score": "1", "team": {"id": "10", "displayName": "Arsenal"}},
    {"homeAway": "away", "score": "0", "team": {"id": "11", "displayName": "Chelsea"}},
]


def fixture():
    competition = {"id": "99", "date": START, "status": {"type": STATUS.copy(), "displayClock": "35'", "period": 1},
                   "competitors": json.loads(json.dumps(TEAMS))}
    board = {"events": [{"id": "99", "date": START, "status": dict(competition["status"], clock=2100, period=1),
                         "competitions": [competition]}]}
    events, _ = capture_scoreboards(NOW, fetcher=lambda *_: (board, {"fetched_at_utc": BOARD_TIME, "sha256": "board"}),
                                    sports={"epl": "soccer/eng.1"}, future_days=0, need_yesterday=False)
    summary = {
        "header": {"id": "99", "competitions": [competition]},
        "format": {"regulation": {"periods": 2, "clock": 2700}},
        "wallclockAvailable": True,
        "keyEvents": [{"wallclock": "2026-11-07T15:34:30Z", "period": {"number": 1}, "clock": {"value": 2070}}],
        "boxscore": {"teams": [
            {"team": {"id": ident}, "statistics": [{"name": "shotsOnTarget", "displayValue": shots},
                                                     {"name": "redCards", "displayValue": reds}]}
            for ident, shots, reds in [("10", "3", "0"), ("11", "1", "1")]
        ]},
    }
    return events[0], summary


class InPlayTests(unittest.TestCase):
    def test_model_updates_same_markets_and_drops_decided_total(self):
        event, summary = fixture()
        state = in_play.read_state(summary, event, NOW)
        forecasts = in_play.forecast((1.5, 1.2), state)
        self.assertGreater(forecasts["1X2"]["context"][0], forecasts["1X2"]["baseline"][0])
        for comparison in forecasts.values():
            for p in comparison.values():
                self.assertAlmostEqual(sum(p), 1)
        state.update(score=[3, 0], shots_on_target=[3, 3], red_cards=[0, 0])
        forecasts = in_play.forecast((1.5, 1.2), state)
        self.assertNotIn("OVER_UNDER_2_5", forecasts)
        self.assertEqual(forecasts["1X2"]["baseline"], forecasts["1X2"]["context"])

    def test_fails_closed_for_stale_future_and_final_payloads(self):
        event, summary = fixture()
        with self.assertRaisesRegex(ValueError, "STALE_SCOREBOARD"):
            in_play.read_state(summary, event, NOW + dt.timedelta(minutes=3))
        summary["keyEvents"][0]["wallclock"] = "2026-11-07T15:36:00Z"
        with self.assertRaisesRegex(ValueError, "STALE_OR_INCONSISTENT_HEARTBEAT"):
            in_play.read_state(summary, event, NOW)
        event, summary = fixture()
        summary["header"]["competitions"][0]["status"]["type"]["completed"] = True
        with self.assertRaisesRegex(ValueError, "SUMMARY_NOT_IN_PLAY"):
            in_play.read_state(summary, event, NOW)

    def test_state_requires_matching_score_teams_and_explicit_stats(self):
        event, summary = fixture()
        summary["header"]["competitions"][0]["competitors"][0]["score"] = "2"
        with self.assertRaisesRegex(ValueError, "SCORE_OR_ID_CHANGED"):
            in_play.read_state(summary, event, NOW)
        event, summary = fixture()
        summary["boxscore"]["teams"][1]["statistics"].pop()
        with self.assertRaises(KeyError):
            in_play.read_state(summary, event, NOW)
        summary["boxscore"]["teams"][1]["team"]["id"] = "12"
        with self.assertRaisesRegex(ValueError, "BOX_SCORE_TEAM_MISMATCH"):
            in_play.read_state(summary, event, NOW)

    def test_event_in_the_displayed_minute_can_be_newer_than_minute_floor(self):
        event, summary = fixture()
        summary["keyEvents"][0]["wallclock"] = "2026-11-07T15:35:10Z"
        summary["keyEvents"][0]["clock"]["value"] = 2110
        self.assertEqual(in_play.read_state(summary, event, NOW)["elapsed_seconds"], 2100)
        summary["keyEvents"][0]["clock"]["value"] = 2160
        with self.assertRaisesRegex(ValueError, "STALE_OR_INCONSISTENT_HEARTBEAT"):
            in_play.read_state(summary, event, NOW)

    def test_skips_ineligible_matches_before_budget_and_contains_transport_errors(self):
        event, _ = fixture()
        old = dict(event, id="epl:0", live_state=dict(event["live_state"], elapsed_seconds=5200))
        weak = dict(event, id="epl:1", away="Unknown team")
        model = FootballPoisson(played={"arsenal": 8, "chelsea": 8})
        models = {"epl": {"POISSON_SHRINK": model}}
        requested = []

        def fetcher(url):
            requested.append(url.rsplit("=", 1)[-1])
            raise IncompleteRead(b"")

        rows, diag = in_play.collect([old, weak] * 12 + [event], models, set(), NOW.date(), str.lower, fetcher)
        self.assertEqual(rows, [])
        self.assertEqual(requested, ["99"])
        self.assertEqual(diag["skipped"], {"OUTSIDE_CAPTURE_WINDOW": 12,
                                          "INSUFFICIENT_TEAM_HISTORY": 12, "SUMMARY_FETCH_FAILED": 1})

    def test_one_zero_stake_capture_and_normal_full_time_only(self):
        event, summary = fixture()
        model = FootballPoisson(played={"arsenal": 8, "chelsea": 8})
        models = {"epl": {"POISSON_SHRINK": model}}
        fetcher = lambda url: (json.dumps(summary), {"fetched_at_utc": NOW.isoformat(), "sha256": "summary"})
        rows, diag = in_play.collect([event], models, set(), NOW.date(), str.lower, fetcher)
        self.assertEqual(diag["live_candidates"], 1)
        self.assertEqual(rows[0]["stake_eur"], 0)
        self.assertEqual(in_play.pending(rows), {("epl", NOW.date())})
        duplicate, _ = in_play.collect([event], models, {event["id"]}, NOW.date(), str.lower, fetcher)
        self.assertEqual(duplicate, [])
        final = dict(event, completed=True, provider_status="STATUS_ABANDONED", final={"home": 2, "away": 0, "outcome": 0})
        end = NOW + dt.timedelta(hours=2)
        self.assertEqual(in_play.finish(rows, {event["id"]: final}, end), [])
        final["provider_status"] = "STATUS_FULL_TIME"
        self.assertEqual(in_play.finish(rows, {event["id"]: final}, NOW), [])
        result = in_play.finish(rows, {event["id"]: final}, end)
        self.assertEqual(result[0]["outcomes"], {"1X2": 0, "OVER_UNDER_2_5": 1})
        self.assertEqual(in_play.finish(rows + result, {event["id"]: final}, end), [])
        report = in_play.report(rows + result, diag, 1)
        self.assertEqual(report["settled"], 1)
        self.assertEqual(report["streams"][0]["metrics"]["n"], 1)
        self.assertIsNone(report["streams"][0]["paired_improvement_95pct"])
        self.assertIsNone(report["roi"])
        self.assertFalse(report["wallet_enabled"])

    def test_paired_intervals_group_observations_by_date(self):
        dates = {day: [(0.01, 0.02)] * 8 for day in range(14)}
        self.assertIsNone(in_play.paired_intervals(dict(list(dates.items())[:13])))
        interval = in_play.paired_intervals(dates)
        self.assertAlmostEqual(interval["brier"][0], .01)
        self.assertAlmostEqual(interval["log_loss"][1], .02)


if __name__ == "__main__":
    unittest.main()
