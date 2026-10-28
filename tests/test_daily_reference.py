"""Daily target is met only by genuine pregame, timestamped bookmaker quotes."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from sports_intelligence.custom_strategies import ALL_NAMES
from sports_intelligence.daily_reference import NAME, quote_for_reference, select_daily_reference
from sports_intelligence.strategy_lab import build_accounts, read_jsonl
from sports_intelligence.strategy_registry import ensure_registry, verify_prior_accounts

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 27, 3, 0, tzinfo=UTC)


def event(sport="nba", market="ML", kickoff="2026-10-27T23:00:00+00:00"):
    p = [.62, .38] if market == "ML" else [.48, .28, .24]
    odds = [1.61, 2.58] if market == "ML" else [2.1, 3.55, 4.2]
    return {
        "id": sport + ":999",
        "sport": sport, "home": "Home Team", "away": "Away Team",
        "start_utc": kickoff, "status": "pre",
        "quote": {
            "provider": "The Odds API v4", "market": market,
            "decimal": odds, "no_vig": p, "bookmaker_count": 8,
            "bookmakers": ["book_a"]*len(p),
            "best_book_updates": ["2026-10-27T02:30:00+00:00"]*len(p),
            "quote_timestamp_available": True,
        },
    }


class DailyReferenceTests(unittest.TestCase):
    def test_one_real_market_paper_per_local_calendar_day(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry = ensure_registry(root, NOW, ALL_NAMES)
            baseline = event()
            self.assertEqual(registry[NAME]["initial_bank_eur"], 1000)
            first = select_daily_reference(root, [baseline], NOW, registry)
            self.assertEqual(first["new_entries"], 1)
            self.assertEqual(first["opened_today"], 1)
            second = select_daily_reference(root, [event("nhl")], NOW+dt.timedelta(minutes=15), registry)
            self.assertEqual(second["new_entries"], 1)
            self.assertEqual(second["status"], "PARTIAL")
            saved = read_jsonl(root/"data"/"strategy_ledgers"/(NAME.lower()+".jsonl"))
            self.assertEqual(len(saved), 2)
            self.assertEqual(saved[0]["stake_eur"], 5)
            self.assertEqual(saved[0]["market"], "ML")
            self.assertEqual(saved[0]["model_status"], "MARKET_REFERENCE_NO_ESTIMATED_EDGE")
            self.assertEqual(saved[0]["forecast_utc"], NOW.isoformat())
            verify_prior_accounts(root, registry)

    def test_three_distinct_daily_events_no_fourth_and_no_bank_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = ensure_registry(root, NOW, ALL_NAMES)
            four = [event(sport) for sport in ("nba", "nhl", "nfl", "mlb")]
            result = select_daily_reference(root, four, NOW, registry)
            self.assertEqual(result["new_entries"], 3)
            self.assertEqual(result["opened_today"], 3)
            self.assertEqual(len(result["distinct_event_ids_today"]), 3)
            self.assertEqual(result["status"], "DAILY_CAP")
            saved = read_jsonl(root / "data" / "strategy_ledgers" / (NAME.lower()+".jsonl"))
            self.assertEqual(len(saved), 3)
            self.assertEqual(len({r["event_id"] for r in saved}), 3)
            self.assertEqual(sum(r["stake_eur"] for r in saved), 15.0)
            retry = select_daily_reference(root, four, NOW + dt.timedelta(minutes=20), registry)
            self.assertEqual(retry["new_entries"], 0)
            self.assertEqual(len(read_jsonl(root / "data" / "strategy_ledgers" / (NAME.lower()+".jsonl"))), 3)
            accounts, _ = build_accounts(root, {}, NOW)
            self.assertEqual(accounts[NAME]["bankroll_eur"], 1000.0)
            self.assertEqual(accounts[NAME]["open"], 3)

    def test_missing_stale_and_nonprovable_prices_cannot_meet_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry = ensure_registry(root, NOW, ALL_NAMES)
            quoted = event()
            quoted["quote"]["quote_timestamp_available"] = False
            result = select_daily_reference(root, [quoted], NOW, registry)
            self.assertEqual(result["new_entries"], 0)
            self.assertEqual(result["status"], "NO_ELIGIBLE_QUOTE")
            quoted["quote"]["quote_timestamp_available"] = True
            quoted["quote"]["best_book_updates"] = ["2026-10-26T10:00:00+00:00"]*2
            self.assertIsNone(quote_for_reference(quoted, NOW))
            quoted["quote"]["best_book_updates"] = ["2026-10-27T02:30:00+00:00"]*2
            quoted["quote"]["bookmaker_count"] = 1
            self.assertIsNone(quote_for_reference(quoted, NOW))
            quoted["quote"]["bookmaker_count"] = 8
            quoted["start_utc"] = "2026-10-27T02:00:00+00:00"
            self.assertIsNone(quote_for_reference(quoted, NOW))
            self.assertEqual(read_jsonl(root/"data"/"strategy_ledgers"/(NAME.lower()+".jsonl")), [])

    def test_settle_ml_away_without_phantom_draw_loss(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            registry=ensure_registry(root,NOW,ALL_NAMES)
            quoted=event()
            baseline=select_daily_reference(root,[quoted],NOW,registry)
            self.assertEqual(baseline["new_entries"],1)
            account,_=build_accounts(root,{},NOW)
            self.assertEqual(account[NAME]["bankroll_eur"],1000)
            self.assertEqual(account[NAME]["open"],1)
            at=dt.datetime(2026,10,28,1,tzinfo=UTC)
            settled,diagnostics=build_accounts(root,{quoted["id"]:{"outcome":2}},at)
            self.assertEqual(diagnostics["settled_this_run"],1)
            self.assertEqual(settled[NAME]["bankroll_eur"],995)
            self.assertEqual(settled[NAME]["open"],0)
            again,details=build_accounts(root,{quoted["id"]:{"outcome":2}},at)
            self.assertEqual(details["settled_this_run"],0)

    def test_ml_draw_is_void_and_never_counted_as_loss(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            registry=ensure_registry(root,NOW,ALL_NAMES)
            quoted=event("nfl")
            select_daily_reference(root,[quoted],NOW,registry)
            at=dt.datetime(2026,10,28,1,tzinfo=UTC)
            accounts,changes=build_accounts(root,{quoted["id"]:{"outcome":1}},at)
            self.assertEqual(changes["settled_this_run"],1)
            self.assertEqual(accounts[NAME]["bankroll_eur"],1000)
            self.assertEqual(accounts[NAME]["paper_losses"],0)
            self.assertEqual(accounts[NAME]["open"],0)
            rows=read_jsonl(root/"data"/"strategy_ledgers"/(NAME.lower()+".jsonl"))
            self.assertEqual(rows[0]["status"],"VOID")
            self.assertEqual(rows[0]["pnl_eur"],0.0)


if __name__ == "__main__":
    unittest.main()
