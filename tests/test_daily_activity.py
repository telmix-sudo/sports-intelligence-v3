"""Synthetic daily-throughput regressions; no live quotes or network requests."""
import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from sports_intelligence.activity_policy import daily_entry_stake
from sports_intelligence.auxiliary_portfolios import open_auxiliary_markets
from sports_intelligence.broad_odds import annotate_discovered
from sports_intelligence.custom_strategies import ALL_NAMES
from sports_intelligence.daily_reference import select_daily_reference
from sports_intelligence.odds_provider import market_quotes_for_event, quotes_for_event
from sports_intelligence.portfolio_controls import PaperRiskBook
from sports_intelligence.research_pipeline import run_research, read_journal
from sports_intelligence.strategy_lab import read_jsonl, save_jsonl
from sports_intelligence.strategy_registry import ensure_registry
from test_daily_reference import NOW as REF_NOW, event as reference_event
from test_research_integration import EVENT, NOW, odds_rows, football_history, models
from test_review_regressions import event, prices


class DailyActivityTests(unittest.TestCase):
    def test_first_five_fit_existing_exposure_without_raising_any_limit(self):
        exposure = 15.0
        for opened in range(5):
            stake = daily_entry_stake(1000, exposure, opened)
            self.assertEqual(stake, 1.0)
            exposure += stake
        self.assertEqual(exposure, 20.0)
        self.assertEqual(daily_entry_stake(1000, exposure, 5), 0.0)
        self.assertEqual(daily_entry_stake(1000, 0, 5), 2.5)
        self.assertEqual(daily_entry_stake(988.85, 0, 0), 1.0)
        self.assertEqual(daily_entry_stake(1000, 19.01, 0), 0.0)
        self.assertEqual(daily_entry_stake(0, 0, 0), 0.0)

    def test_all_thirteen_wallets_reach_five_when_five_qualified_events_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = ensure_registry(root, NOW-dt.timedelta(days=2), ALL_NAMES)
            legacy = {'id':'SYNTHETIC-FROZEN', 'strategy':'ELO_STANDARD',
                      'event_id':'epl:SYNTHETIC-LEGACY', 'sport':'epl',
                      'home':'Arsenal', 'away':'Chelsea', 'market':'1X2',
                      'forecast_utc':(NOW-dt.timedelta(days=1)).isoformat(),
                      'start_utc':(NOW+dt.timedelta(hours=8)).isoformat(),
                      'status':'OPEN', 'stake_eur':15.0,
                      'probabilities':[.6,.2,.2], 'odds':[2,3,4], 'selection_index':0}
            path = root/'data/strategy_ledgers/elo_standard.jsonl'
            save_jsonl(path, [legacy])
            events = []
            for i in range(5):
                e = copy.deepcopy(EVENT)
                e['id'] = 'epl:SYNTHETIC-DAILY-'+str(i)
                e['market_quotes'] = market_quotes_for_event(e, odds_rows(), NOW)
                events.append(e)
            history = football_history()
            research = run_research(root, events, models(history), NOW,
                                    PaperRiskBook(root), football_history=history)
            self.assertEqual(research['experimental_account']['positions'], 5)
            frozen = copy.deepcopy(read_journal(root))
            added = open_auxiliary_markets(root, events, NOW, registry, PaperRiskBook(root))
            self.assertEqual(added, dict.fromkeys(ALL_NAMES, 5))
            for name in ALL_NAMES:
                rows = read_jsonl(root/'data/strategy_ledgers'/(name.lower()+'.jsonl'))
                today = [r for r in rows if r['forecast_utc']==NOW.isoformat()]
                self.assertEqual(len(today), 5)
                self.assertEqual(len({r['event_id'] for r in today}), 5)
                self.assertTrue(all(r['stake_eur']==1.0 for r in today))
            self.assertEqual(read_jsonl(path)[0], legacy)
            self.assertEqual(read_journal(root), frozen)
            self.assertEqual(open_auxiliary_markets(root, events, NOW, registry, PaperRiskBook(root)), {})
            risk = PaperRiskBook(root).summary()
            self.assertLessEqual(risk['total_open_eur'], 240)
            self.assertTrue(all(v<=36 for v in risk['event_exposure_eur'].values()))

    def test_reference_below_five_is_partial_instead_of_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = ensure_registry(root, REF_NOW, ALL_NAMES)
            e = reference_event()
            select_daily_reference(root, [e], REF_NOW, registry)
            repeat = select_daily_reference(root, [e], REF_NOW, registry)
            self.assertEqual(repeat['status'], 'PARTIAL')
            self.assertEqual(repeat['remaining_to_minimum'], 4)
            self.assertFalse(repeat['target_met'])


class DailyPricingTests(unittest.TestCase):
    def test_prices_next_day_events_within_existing_48_hour_admission_horizon(self):
        with tempfile.TemporaryDirectory() as tmp:
            e = event()
            e['start_utc'] = (NOW+dt.timedelta(hours=30)).isoformat()
            calls = []
            def request(key, *args):
                calls.append(key)
                return prices(e, NOW)
            result = annotate_discovered([e], Path(tmp), NOW, 'SYNTHETIC', request)
            self.assertEqual(calls, ['americanfootball_nfl'])
            self.assertIsNotNone(e['quote'])
            self.assertEqual(result['distinct_modelled_events_with_prices'], 1)
            self.assertEqual(result['price_lookahead_hours'], 48)
            far = dict(e, start_utc=(NOW+dt.timedelta(hours=49)).isoformat())
            other = annotate_discovered([far], Path(tmp), NOW, 'SYNTHETIC', lambda *_:self.fail('Out of horizon'))
            self.assertEqual(other['calls_this_run'], 0)

    def test_one_remaining_credit_prices_football_without_buying_extra_markets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'data').mkdir()
            clock = NOW.replace(hour=20)
            e = event('epl', 'soccer_epl', 'soccer')
            e['start_utc'] = (clock+dt.timedelta(hours=5)).isoformat()
            (root/'data/odds_cache_v3.json').write_text(json.dumps({
                'day':str(clock.date()), 'credits_today':15, 'calls_today':15, 'snapshots':{}}))
            calls = []
            def request(key, secret, markets, *_):
                calls.append(markets)
                return prices(e, clock)
            result = annotate_discovered([e], root, clock, 'SYNTHETIC', request)
            self.assertEqual(calls, ['h2h'])
            self.assertEqual(result['credits_today'], 16)
            self.assertIsNotNone(e['quote'])

    def test_five_unpriced_modelled_games_precede_refreshing_one_already_priced_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'data').mkdir()
            cached = event('nba', 'basketball_nba', 'basketball')
            cached['paper_execution_enabled'] = True
            cached['start_utc'] = (NOW+dt.timedelta(hours=1)).isoformat()
            q = quotes_for_event(cached, prices(cached, NOW), NOW)
            (root/'data/odds_cache_v3.json').write_text(json.dumps({
                'day':str(NOW.date()), 'credits_today':7, 'calls_today':7,
                'snapshots':{'nba':{'quotes':{cached['id']:q}}}}))
            upcoming = []
            for i in range(5):
                e = event(suffix=str(i))
                e['home'] += ' '+str(i)
                e['start_utc'] = (NOW+dt.timedelta(hours=3)).isoformat()
                upcoming.append(e)
            calls = []
            def request(key, *_):
                calls.append(key)
                return [p for e in upcoming for p in prices(e, NOW)]
            result = annotate_discovered([cached]+upcoming, root, NOW, 'SYNTHETIC', request)
            self.assertEqual(calls, ['americanfootball_nfl'])
            self.assertEqual(result['distinct_modelled_events_with_prices'], 6)
            self.assertEqual(result['credits_today'], 8)


if __name__ == '__main__':
    unittest.main()
