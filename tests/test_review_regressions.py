"""Synthetic regressions for live quote allocation and shared-market settlement."""
import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from sports_intelligence.broad_odds import annotate_discovered, prune_expired_prices
from sports_intelligence.odds_provider import attach_cached_odds, quotes_for_event, annotate_odds
from sports_intelligence.portfolio_controls import PaperRiskBook
from sports_intelligence.strategy_lab import enrich_pending_statistics, save_jsonl

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 1, 8, tzinfo=UTC)


def event(sport="nfl", key="americanfootball_nfl", family="american_football", suffix="1"):
    return {"id": sport + ":" + suffix, "sport": sport, "family": family,
            "home": "Synthetic Home", "away": "Synthetic Away", "status": "pre",
            "start_utc": (NOW + dt.timedelta(hours=10)).isoformat(),
            "quote": None, "event_type": "MATCHUP", "completed": False,
            "paper_execution_enabled": sport in ("nfl", "epl"),
            "odds_sport_key": key, "odds_event_id": "provider-" + suffix}


def prices(e, updated=NOW):
    outcomes = [{"name": e["home"], "price": 2.1}, {"name": e["away"], "price": 2.1}]
    if e["family"] == "soccer":
        outcomes.append({"name": "Draw", "price": 3.5})
    return [{"id": e["odds_event_id"], "home_team": e["home"], "away_team": e["away"],
             "commence_time": e["start_utc"], "bookmakers": [
                 {"key": book, "last_update": updated.isoformat(),
                  "markets": [{"key": "h2h", "outcomes": copy.deepcopy(outcomes)}]}
                 for book in ("SYNTHETIC_A", "SYNTHETIC_B")]}]


class LiveCacheTests(unittest.TestCase):
    def test_quote_timestamp_survives_partial_snapshot_refresh(self):
        e = event()
        quote = quotes_for_event(e, prices(e), NOW)
        stale = copy.deepcopy(quote)
        stale["best_book_updates"] = [(NOW - dt.timedelta(hours=13)).isoformat()] * 2
        future = copy.deepcopy(quote)
        future["best_book_updates"] = [(NOW + dt.timedelta(minutes=5)).isoformat()] * 2
        missing = copy.deepcopy(quote)
        missing["best_book_updates"] = []
        snapshot = {"nfl": {"fetched_at": NOW.isoformat(), "quotes": {e["id"]: quote},
                            "market_quotes": {e["id"]: {"stale": stale, "future": future,
                                                         "missing": missing, "valid": quote}}}}
        frozen = copy.deepcopy(snapshot)
        self.assertEqual(attach_cached_odds([e], snapshot, NOW), 1)
        self.assertEqual(set(e["market_quotes"]), {"valid"})
        self.assertEqual(snapshot, frozen, "Reusing a quote must never refresh its original timestamps")
        del snapshot["nfl"]["fetched_at"]
        self.assertEqual(attach_cached_odds([event()], snapshot, NOW), 1)

    def test_rejects_stale_missing_and_future_winner_timestamps(self):
        e = event()
        quote = quotes_for_event(e, prices(e), NOW)
        for stamps in ([], [(NOW - dt.timedelta(hours=13)).isoformat()] * 2,
                       [(NOW + dt.timedelta(minutes=1)).isoformat()] * 2, [NOW.isoformat()]):
            with self.subTest(stamps=stamps):
                q = dict(quote, best_book_updates=stamps)
                fresh_event = event()
                self.assertEqual(attach_cached_odds([fresh_event], {"nfl": {
                    "fetched_at": NOW.isoformat(), "quotes": {e["id"]: q}}}, NOW), 0)
                self.assertIsNone(fresh_event["quote"])

    def test_started_events_do_not_receive_cached_prices(self):
        e = event()
        q = quotes_for_event(e, prices(e), NOW)
        e["status"] = "in"
        self.assertEqual(attach_cached_odds([e], {"nfl": {"quotes": {e["id"]: q}}}, NOW), 0)
        self.assertIsNone(e["quote"])

    def test_existing_models_receive_prices_before_discovery_only_leagues(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            (root / "data/odds_cache_v3.json").write_text(json.dumps({
                "day": str(NOW.date()), "credits_today": 5, "calls_today": 5,
                "snapshots": {}, "polled_windows": [2], "btts_attempted": ["legacy"]}))
            supported = event("epl", "soccer_epl", "soccer")
            unsupported = event("handball_new", "handball_new", "handball")
            unsupported["start_utc"] = (NOW + dt.timedelta(hours=1)).isoformat()
            calls = []
            def request(key, *_):
                calls.append(key)
                return prices(supported) if key == "soccer_epl" else []
            result = annotate_discovered([unsupported, supported], root, NOW, "SYNTHETIC", request)
            self.assertEqual(calls, ["soccer_epl", "soccer_epl"])
            self.assertEqual(result["credits_today"], 11)
            self.assertEqual(result["event_market_credits_today"], 3)
            self.assertIsNotNone(supported["quote"])
            cache = json.loads((root / "data/odds_cache_v3.json").read_text())
            self.assertEqual(cache["polled_windows"], [2])
            self.assertEqual(cache["btts_attempted"], ["legacy"])
            self.assertNotIn("SYNTHETIC", json.dumps({k: v for k, v in cache.items() if k != "snapshots"}))

    def test_model_prices_can_refresh_in_later_slot_without_repeating_same_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []
            e = event()
            def request(key, *_):
                calls.append(key)
                return prices(e)
            first = annotate_discovered([dict(e)], root, NOW, "SYNTHETIC", request)
            same = annotate_discovered([dict(e)], root, NOW + dt.timedelta(minutes=20), "SYNTHETIC", request)
            later = annotate_discovered([dict(e)], root, NOW + dt.timedelta(hours=6), "SYNTHETIC", request)
            self.assertEqual((first["calls_this_run"], same["calls_this_run"], later["calls_this_run"]), (1, 0, 1))
            self.assertEqual(later["credits_today"], 2)
            self.assertEqual(calls, ["americanfootball_nfl"] * 2)

    def test_partial_refresh_does_not_erase_another_event_quote(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            old_event = event(suffix="old")
            quote = quotes_for_event(old_event, prices(old_event), NOW)
            quote = json.loads(json.dumps(quote))
            path = root / "data/odds_cache_v3.json"
            path.write_text(json.dumps({"day": str(NOW.date()), "snapshots": {"nfl": {
                "fetched_at": NOW.isoformat(), "quotes": {old_event["id"]: quote},
                "market_quotes": {old_event["id"]: {"original": quote}}}}}))
            annotate_discovered([event()], root, NOW, "SYNTHETIC", lambda *_: [])
            snap = json.loads(path.read_text())["snapshots"]["nfl"]
            self.assertEqual(snap["quotes"][old_event["id"]], quote)
            self.assertEqual(snap["market_quotes"][old_event["id"]]["original"], quote)

    def test_provider_failure_is_once_per_slot_and_preserves_quota_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []
            def unavailable(*_):
                calls.append(1)
                raise ConnectionError("PRIVATE_PROVIDER_DETAIL")
            first = annotate_discovered([event()], root, NOW, "SYNTHETIC", unavailable)
            repeat = annotate_discovered([event()], root, NOW, "SYNTHETIC", unavailable)
            self.assertEqual(len(calls), 1)
            self.assertEqual(repeat["credits_today"], 1)
            self.assertEqual(first["errors"][0]["reason"], "ConnectionError")
            self.assertNotIn("PRIVATE_PROVIDER_DETAIL", json.dumps(first))

    def test_legacy_path_uses_same_quote_validation_and_keeps_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data").mkdir()
            e = event()
            quote = quotes_for_event(e, prices(e), NOW)
            path = root / "data/odds_cache_v3.json"
            for stamps in ([], [(NOW+dt.timedelta(minutes=1)).isoformat()]*2,
                           [NOW.isoformat()], [NOW.isoformat()]*2):
                with self.subTest(stamps=stamps):
                    q = dict(quote, best_book_updates=stamps)
                    future = dict(quote, best_book_updates=[(NOW+dt.timedelta(minutes=1)).isoformat()]*2)
                    path.write_text(json.dumps({"day": str(NOW.date()), "snapshots": {"nfl": {
                        "fetched_at": NOW.isoformat(), "quotes": {e["id"]: q},
                        "market_quotes": {e["id"]: {"valid": quote, "future": future}}}}}))
                    fresh_event = event()
                    result = annotate_odds([fresh_event], root, NOW, secret="")
                    self.assertEqual(result["quotes_matched"], int(len(stamps)==2 and stamps[0]==NOW.isoformat()))
                    self.assertEqual(set(fresh_event["market_quotes"]), {"valid"})
                    self.assertEqual(result["cached_sports"], ["nfl"] if fresh_event["quote"] else [])

    def test_cache_expiry_keeps_fresh_prices_and_opaque_legacy_metadata(self):
        e = event()
        quote = quotes_for_event(e, prices(e), NOW)
        old = copy.deepcopy(quote)
        old["best_book_updates"] = [(NOW-dt.timedelta(hours=13)).isoformat()]*2
        snapshots = {"nfl": {"quotes": {"fresh": copy.deepcopy(quote), "old": old, "legacy": {"x": 1}},
                             "market_quotes": {"fresh": {"valid": copy.deepcopy(quote), "old": old},
                                               "expired": {"old": old}},
                             "provider_ids": {"fresh": "A", "old": "B", "expired": "C", "no_prices": "D"}}}
        self.assertEqual(prune_expired_prices(snapshots, NOW), 3)
        snap = snapshots["nfl"]
        self.assertEqual(set(snap["quotes"]), {"fresh", "legacy"})
        self.assertEqual(snap["quotes"]["fresh"], quote)
        self.assertEqual(set(snap["market_quotes"]["fresh"]), {"valid"})
        self.assertEqual(snap["provider_ids"], {"fresh": "A", "no_prices": "D"})


class SharedSettlementTests(unittest.TestCase):
    def test_incomplete_legacy_row_is_diagnosed_without_interrupting_settlement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data/strategy_ledgers/elo_standard.jsonl"
            path.parent.mkdir(parents=True)
            save_jsonl(path, [{"id": "SYNTHETIC", "event_id": "epl:1", "status": "OPEN",
                              "start_utc": (NOW-dt.timedelta(hours=3)).isoformat(),
                              "market_spec": {"metric": "corners"}}])
            finals = {"epl:1": {"home": 1, "away": 0, "outcome": 0}}
            frozen = copy.deepcopy(finals)
            result = enrich_pending_statistics(root, finals, NOW, lambda _: self.fail("Missing identity"))
            self.assertEqual(result, {"lookups": 1, "errors": [{"event_id": "epl:1", "reason": "KeyError"}]})
            self.assertEqual(finals, frozen)

    def test_nullable_specs_do_not_crash_and_boxscore_is_shared_across_wallets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            e = dict(event("epl", "soccer_epl", "soccer"), start_utc=(NOW-dt.timedelta(hours=3)).isoformat())
            base = {"event_id": e["id"], "sport": "epl", "home": e["home"], "away": e["away"],
                    "start_utc": e["start_utc"], "status": "OPEN"}
            corner = dict(base, id="SYNTHETIC_CORNER", market_spec={"metric": "corners"})
            winner = dict(base, id="SYNTHETIC_WINNER", market_spec=None)
            one = root / "data/strategy_ledgers/elo_standard.jsonl"
            two = root / "data/strategy_ledgers/elo_dynamic.jsonl"
            one.parent.mkdir(parents=True)
            save_jsonl(one, [winner, corner])
            save_jsonl(two, [dict(corner, id="SYNTHETIC_OTHER_WALLET")])
            frozen = (one.read_bytes(), two.read_bytes())
            finals = {e["id"]: {"home": 1, "away": 0, "outcome": 0}}
            calls = []
            def summary(item):
                calls.append(item["id"])
                return dict(item, final=dict(item["final"], team_statistics={
                    "home": {"corners": 4}, "away": {"corners": 3}}))
            diagnostics = enrich_pending_statistics(root, finals, NOW, summary)
            self.assertEqual(calls, [e["id"]])
            self.assertEqual(diagnostics, {"lookups": 1, "errors": []})
            self.assertIn("team_statistics", finals[e["id"]])
            self.assertEqual((one.read_bytes(), two.read_bytes()), frozen)

    def test_boxscore_failure_keeps_official_final_and_does_not_leak_error_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data/strategy_ledgers/elo_standard.jsonl"
            path.parent.mkdir(parents=True)
            row = {"id": "SYNTHETIC", "event_id": "epl:1", "sport": "epl", "home": "A", "away": "B",
                   "status": "OPEN", "start_utc": (NOW-dt.timedelta(hours=3)).isoformat(),
                   "market_spec": {"metric": "corners"}}
            save_jsonl(path, [row])
            finals = {"epl:1": {"home": 1, "away": 0, "outcome": 0}}
            frozen = copy.deepcopy(finals)
            def fail(_):
                raise ConnectionError("PRIVATE_ERROR_BODY")
            result = enrich_pending_statistics(root, finals, NOW, fail)
            self.assertEqual(finals, frozen)
            self.assertEqual(result["errors"], [{"event_id": "epl:1", "reason": "ConnectionError"}])


class RiskRoundingTests(unittest.TestCase):
    def test_fractional_cent_cannot_exceed_request_or_remaining_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            risk = PaperRiskBook(Path(tmp))
            requested = 2.345
            self.assertEqual(risk.reserve("epl:one", requested), 2.34)
            risk.event_exposure["epl:two"] = 34.995
            risk.total_exposure += 34.995
            self.assertEqual(risk.reserve("epl:two", 5), 1.0)
            self.assertLessEqual(risk.event_exposure["epl:two"], 36)


if __name__ == "__main__":
    unittest.main()
