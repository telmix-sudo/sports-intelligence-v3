"""Integration checks: real market lines, quota units, frozen forecasts, risk and results.
All events/prices in this module are explicitly synthetic test fixtures.
"""
import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sports_intelligence.odds_provider import market_quotes_for_event, annotate_odds
from sports_intelligence.portfolio_controls import PaperRiskBook
from sports_intelligence.poisson import FootballPoisson
from sports_intelligence.research_pipeline import (
    run_research, read_journal, reconcile, validation, fit_sports, full_game_forecast,
    collect_history, experimental_admission, paper_state,
)

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 31, 8, 30, tzinfo=UTC)
EVENT = {'id':'epl:TEST-1','sport':'epl','home':'Arsenal','away':'Chelsea',
         'status':'pre','start_utc':(NOW+dt.timedelta(hours=4)).isoformat(), 'completed':False,'final':None}


def odds_rows():
    books=[]
    for book in ('SYNTHETIC_A', 'SYNTHETIC_B'):
        books.append({'key':book, 'last_update':NOW.isoformat(), 'markets':[
            {'key':'h2h','outcomes':[{'name':'Arsenal','price':2},{'name':'Draw','price':3.5},{'name':'Chelsea','price':4}]},
            {'key':'totals','outcomes':[{'name':'Over','point':2.5,'price':3.0},{'name':'Under','point':2.5,'price':1.5}]},
            {'key':'spreads','outcomes':[{'name':'Arsenal','point':-.5,'price':2.5},{'name':'Chelsea','point':.5,'price':1.5}]},
            {'key':'btts','outcomes':[{'name':'Yes','price':3},{'name':'No','price':1.5}]},
        ]})
    return [{'id':'SYNTHETIC_PROVIDER_ID', 'home_team':'Arsenal','away_team':'Chelsea',
             'commence_time':EVENT['start_utc'], 'bookmakers':books}]


def football_history():
    # Fixed scoring process, no prices/outcomes from any future event used in fit.
    start=dt.date(2024,1,1)
    return {'epl':[{'date':start+dt.timedelta(days=i), 'home':'arsenal','away':'chelsea',
                    'goals_home':3,'goals_away':0} for i in range(400)]}


def models(history):
    model=FootballPoisson()
    for m in history['epl']: model.update(m)
    return {'epl':{'POISSON_SHRINK':model}}


class ExpandedOddsTests(unittest.TestCase):
    def test_independent_binary_quotes_and_no_cross_line_pairing(self):
        rows=odds_rows()
        q=market_quotes_for_event(EVENT,rows,NOW)
        self.assertEqual(set(q),{'OVER_UNDER_2_5','BTTS','ASIAN_HOME_MINUS_0_5'})
        self.assertEqual(q['OVER_UNDER_2_5']['bookmaker_count'],2)
        for b in rows[0]['bookmakers']:
            b['markets'][1]['outcomes'][1]['point']=3.5
            b['markets'][2]['outcomes'][0]['point']=-1.5
        q=market_quotes_for_event(EVENT,rows,NOW)
        self.assertEqual(set(q),{'BTTS'})
        rows[0]['bookmakers'].append(copy.deepcopy(rows[0]['bookmakers'][0]))
        self.assertEqual(market_quotes_for_event(EVENT,rows,NOW)['BTTS']['bookmaker_count'],2)

    def test_multi_market_requests_charge_credits_and_no_repeated_btts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); calls=[]
            def fake(sport, secret):
                calls.append(sport)
                return odds_rows()
            first=annotate_odds([dict(EVENT)],root,NOW,secret='SYNTHETIC_NOT_A_KEY',requester=fake)
            self.assertEqual(first['credits_today'],4)  # EPL(3) + BTTS(1)
            self.assertEqual(first['calls_today'],2)
            later=annotate_odds([dict(EVENT)],root,NOW.replace(hour=21),secret='SYNTHETIC_NOT_A_KEY',requester=fake)
            self.assertEqual(later['credits_today'],10)
            self.assertEqual(later['calls_today'],8)
            again=annotate_odds([dict(EVENT)],root,NOW.replace(hour=22),secret='SYNTHETIC_NOT_A_KEY',requester=fake)
            self.assertEqual(again['calls_this_run'],0)
            self.assertNotIn('SYNTHETIC_NOT_A_KEY',(root/'data/odds_cache_v3.json').read_text())

    def test_quota_exhaustion_does_not_trigger_paid_or_unbounded_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'data').mkdir()
            (root/'data/odds_cache_v3.json').write_text(json.dumps({'day':str(NOW.date()),'calls_today':0,
                'credits_today':0,'credits_remaining':0,'snapshots':{},'polled_windows':[]}))
            result=annotate_odds([dict(EVENT)],root,NOW,secret='SYNTHETIC',
                                 requester=lambda *args:self.fail('quota exhausted'))
            self.assertEqual(result['calls_this_run'],0)


class ResearchExecutionTests(unittest.TestCase):
    def test_price_to_forecast_to_official_result_is_immutable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); history=football_history(); event=dict(EVENT)
            event['market_quotes']=market_quotes_for_event(event,odds_rows(),NOW)
            report=run_research(root,[event],models(history),NOW,PaperRiskBook(root),football_history=history)
            self.assertEqual(report['new_forecasts'],3)
            forecasts=[r for r in read_journal(root) if r['kind']=='FORECAST']
            self.assertTrue(any(r['stake_eur']==2.5 for r in forecasts),'Eligible 0.25% virtual experiment should execute')
            self.assertEqual(sum(r['stake_eur']>0 for r in forecasts),1,'Correlated markets share one event risk position')
            frozen=copy.deepcopy(forecasts)
            report=run_research(root,[event],models(history),NOW,PaperRiskBook(root),football_history=history)
            self.assertEqual(report['new_forecasts'],0)
            final=dict(event,status='post',completed=True,final={'home':3,'away':0,'outcome':0})
            after=NOW+dt.timedelta(hours=8)
            count,errors=reconcile(root,read_journal(root),[final],after)
            self.assertEqual(count,3)
            self.assertFalse(errors)
            self.assertEqual([r for r in read_journal(root) if r['kind']=='FORECAST'],frozen)
            self.assertEqual(reconcile(root,read_journal(root),[final],after)[0],0)
            state=paper_state(read_journal(root))
            self.assertEqual(state["bankroll_eur"],1003.75)
            self.assertEqual(PaperRiskBook(root).total_exposure,0)
            self.assertFalse((root/'data/strategy_ledgers').exists(),'Original portfolios are untouched')

    def test_missing_price_no_forecast_unvalidated_history_zero_stake(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); history=football_history(); event=dict(EVENT)
            r=run_research(root,[event],models(history),NOW,PaperRiskBook(root),football_history=history)
            self.assertEqual(r['forecasts_total'],0)
            event['market_quotes']=market_quotes_for_event(event,odds_rows(),NOW)
            r=run_research(root,[event],models(history),NOW,PaperRiskBook(root),football_history={})
            self.assertEqual(r['zero_stake_forecasts'],3)
            self.assertFalse(r['experimental_account']['created'])

    def test_prestart_and_actual_timestamps_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); history=football_history(); event=dict(EVENT)
            event['market_quotes']=market_quotes_for_event(event,odds_rows(),NOW)
            for q in event['market_quotes'].values(): q['best_book_updates']=[(NOW+dt.timedelta(minutes=5)).isoformat()]*2
            r=run_research(root,[event],models(history),NOW,PaperRiskBook(root),football_history=history)
            self.assertEqual(r['forecasts_total'],0)
            event['status']='in'
            r=run_research(root,[event],models(history),NOW,PaperRiskBook(root),football_history=history)
            self.assertEqual(r['forecasts_total'],0)

    def test_unavailable_official_result_stays_open_and_is_retried_by_event_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); history=football_history(); event=dict(EVENT)
            event['market_quotes']=market_quotes_for_event(event,odds_rows(),NOW)
            run_research(root,[event],models(history),NOW,PaperRiskBook(root),football_history=history)
            calls=[]
            def empty(route,day): calls.append((route,day)); return {'events':[]},{}
            count,errors=reconcile(root,read_journal(root),[],NOW+dt.timedelta(days=10),empty)
            self.assertEqual(count,0)
            self.assertEqual(len(calls),1,'One lookup for a match date despite three different markets')
            self.assertEqual(paper_state(read_journal(root))['open'],1)

    def test_shared_risk_includes_new_account_but_preserves_prior_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); history=football_history(); event=dict(EVENT)
            event['market_quotes']=market_quotes_for_event(event,odds_rows(),NOW)
            risk=PaperRiskBook(root); risk.reserve(event['id'],35.5)
            r=run_research(root,[event],models(history),NOW,risk,football_history=history)
            self.assertFalse(r['experimental_account']['created'])
            self.assertEqual(risk.summary()['max_event_open_eur'],36)

    def test_market_validation_uses_only_pre_day_results_no_duplicate_N(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); history=football_history(); event=dict(EVENT)
            event['market_quotes']=market_quotes_for_event(event,odds_rows(),NOW)
            run_research(root,[event],models(history),NOW,PaperRiskBook(root),football_history=history)
            final=dict(event,status='post',completed=True,final={'home':3,'away':0,'outcome':0})
            reconcile(root,read_journal(root),[final],NOW+dt.timedelta(hours=8))
            cutoff=NOW.replace(hour=0,minute=0)
            self.assertEqual(validation(read_journal(root),cutoff),{})
            result=validation(read_journal(root),cutoff+dt.timedelta(days=1))
            self.assertEqual(len(result),3)
            self.assertTrue(all(r['n_distinct_events']==1 for r in result.values()))
            self.assertTrue(all(not r['eligible_for_small_paper'] for r in result.values()))

    def test_na_two_way_forecast_explicitly_resolves_ties(self):
        history=[]
        for i in range(50):
            history.append({'id':f'nhl:TEST-{i}','sport':'nhl','home':'A','away':'B',
                'start_utc':(NOW-dt.timedelta(days=60-i)).isoformat(),'completed':True,
                'final':{'home':3,'away':2,'outcome':0,'regulation':{'home':2,'away':2}}})
        models,score=fit_sports(history)
        p=full_game_forecast(models['nhl'],'A','B')
        self.assertEqual(p['tie_resolution_sample'],50)
        self.assertAlmostEqual(sum(p['probabilities']),1)
        self.assertIn('OVERTIME',p['rules'])
        self.assertGreater(score['nhl']['n'],0)

    def test_prospective_underperformance_stops_new_experimental_stakes(self):
        score={'n':1000,'holdout':{'n':200,'brier':.4,'log_loss':.6,'ece':.05}}
        self.assertTrue(experimental_admission(score,{})['eligible_for_small_paper'])
        forward={'n_distinct_events':100,'brier':.55,'market_brier':.45,'log_loss':.8,'market_log_loss':.6}
        self.assertFalse(experimental_admission(score,forward)['eligible_for_small_paper'])


if __name__=='__main__': unittest.main()

class OperationalPersistenceTests(unittest.TestCase):
    def test_daily_results_cache_advances_without_reloading_completed_days(self):
        from urllib.parse import urlparse, parse_qs
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);called=[]
            def source(url):
                day=parse_qs(urlparse(url).query)['dates'][0];called.append(day)
                return json.dumps({'events':[]}),{'sha256':'SYNTHETIC_EMPTY_DAY'}
            collect_history(root,{'nba'},NOW,source,max_days_per_sport=3)
            first=list(called);called.clear()
            collect_history(root,{'nba'},NOW,source,max_days_per_sport=3)
            self.assertEqual(len(called),3)
            self.assertNotIn(first[1],called,'Completed in-season historical day should be cached')
            self.assertIn('20261030',called,'Yesterday is refreshed for overnight final results')
            self.assertTrue((root/'data/score_history_v3.json').is_file())

    def test_new_forecast_requires_bookmaker_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);history=football_history();e=dict(EVENT)
            e['market_quotes']=market_quotes_for_event(e,odds_rows(),NOW)
            for q in e['market_quotes'].values():q.pop('bookmakers')
            r=run_research(root,[e],models(history),NOW,PaperRiskBook(root),football_history=history)
            self.assertEqual(r['forecasts_total'],0)
