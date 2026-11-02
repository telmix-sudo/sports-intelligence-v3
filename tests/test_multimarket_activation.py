"""Synthetic prices/results only. Verify market diversity without forced bets."""
import copy
import datetime as dt
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sports_intelligence.football import canonical
from sports_intelligence.broad_odds import annotate_discovered
from sports_intelligence.market_catalog import catalog_quotes, distribution, outcome_for_spec, collect_statistics
from sports_intelligence.odds_provider import market_quotes_for_event
from sports_intelligence.portfolio_controls import PaperRiskBook, market_activity
from sports_intelligence.research_pipeline import run_research, read_journal, reconcile, validation, paper_state
from test_research_integration import EVENT, NOW, football_history, models, odds_rows


class IdentityAndLinesTests(unittest.TestCase):
    def test_exact_names_join_model_history_and_quotes_without_fuzzy_names(self):
        pairs=[('US Sassuolo Calcio','Sassuolo'),('ACF Fiorentina','Fiorentina'),
               ('Atalanta BC','Atalanta'),('Parma Calcio 1913','Parma'),
               ('FC Lorient','Lorient'),('Stade Brestois 29','Brest'),
               ('CA Osasuna','Osasuna'),('FC Bayern München','Bayern Munich')]
        for official,display in pairs:self.assertEqual(canonical(official),canonical(display))
        self.assertNotEqual(canonical('Deportivo'),canonical('Deportivo La Coruña'))
        self.assertNotEqual(canonical('Deportivo'),canonical('Deportivo Alavés'))
        rows=odds_rows()
        for book in rows[0]['bookmakers']:
            for m in book['markets']:
                if m['key']=='totals':m['key']='alternate_totals'
                if m['key']=='spreads':
                    m['key']='alternate_spreads'
                    m['outcomes'] += [{'name':'Arsenal','point':-1.5,'price':4},
                                      {'name':'Chelsea','point':1.5,'price':1.2}]
        quotes=market_quotes_for_event(EVENT,rows,NOW)
        self.assertIn('OVER_UNDER_2_5',quotes)
        self.assertEqual(quotes['ASIAN_HOME_MINUS_0_5']['decimal'],[2.5,1.5])

    def test_integer_total_has_conditional_prices_and_refunded_push(self):
        spec={'kind':'TOTAL','metric':'goals','line':2.0,'team':None}
        pred=distribution(1.2,1.3,spec)
        push=math.exp(-2.5)*2.5**2/2
        over=1-math.exp(-2.5)*(1+2.5+2.5**2/2)
        self.assertAlmostEqual(pred['push_probability'],push)
        self.assertAlmostEqual(pred['probabilities'][0],over/(1-push))
        self.assertAlmostEqual(sum(pred['probabilities']),1)
        self.assertEqual(outcome_for_spec(spec,{'home':1,'away':1}),'VOID')
        self.assertEqual(outcome_for_spec(spec,{'home':2,'away':1}),0)
        spread=dict(spec,kind='SPREAD',line=-1.0)
        self.assertEqual(outcome_for_spec(spread,{'home':2,'away':1}),'VOID')
        rows=odds_rows()
        for book in rows[0]['bookmakers']:
            book['markets']=[{'key':'totals','outcomes':[{'name':'Over','point':2,'price':2},
                                                      {'name':'Under','point':2,'price':2}]}]
        quotes,_=catalog_quotes(EVENT,rows,NOW)
        self.assertIsNone(next(iter(quotes.values()))['market_spec']['settlement_block'])
        for book in rows[0]['bookmakers']:
            for outcome in book['markets'][0]['outcomes']:outcome['point']=2.25
        quotes,_=catalog_quotes(EVENT,rows,NOW)
        self.assertEqual(next(iter(quotes.values()))['market_spec']['settlement_block'],
                         'QUARTER_LINE_NEEDS_EXPLICIT_SPLIT_SETTLEMENT')


class CollectionAndAdmissionTests(unittest.TestCase):
    def test_qualified_goal_market_gets_room_before_ten_earlier_moneylines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);history=football_history();goal=copy.deepcopy(EVENT)
            goal['market_quotes']=market_quotes_for_event(goal,odds_rows(),NOW)
            events=[]
            for i in range(10):
                events.append({'id':'nba:SYNTHETIC-'+str(i),'sport':'nba','home':'Home','away':'Away',
                    'status':'pre','start_utc':(NOW+dt.timedelta(hours=1)).isoformat(),
                    'quote':{'market':'ML','decimal':[2,2],'no_vig':[.5,.5],
                             'quote_timestamp_available':True,'bookmaker_count':2,
                             'bookmakers':['SYNTHETIC_A','SYNTHETIC_B'],
                             'best_book_updates':[NOW.isoformat()]*2,'provider':'SYNTHETIC'}})
            score={'n':400,'holdout':{'n':100,'brier':.2,'log_loss':.3,'ece':.05}}
            pred={'probabilities':[.8,.2],'sample_min':50,'version':'SYNTHETIC','rules':'INCLUDING_OT'}
            with patch('sports_intelligence.research_pipeline.collect_history',return_value=([],{'games_per_sport':{}})), \
                 patch('sports_intelligence.research_pipeline.fit_sports',return_value=({'nba':object()},{'nba':score})), \
                 patch('sports_intelligence.research_pipeline.full_game_forecast',return_value=pred):
                report=run_research(root,events+[goal],models(history),NOW,PaperRiskBook(root),football_history=history)
            papers=[r for r in read_journal(root) if r['kind']=='FORECAST' and r['stake_eur']>0]
            self.assertEqual(len(papers),10)
            self.assertTrue(any(r['market']!='ML' for r in papers))
            self.assertEqual(report['experimental_account']['positions'],10)

    def test_extra_market_lane_works_after_base_slot_is_spent_within_same_16_credits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            (root/'data/odds_cache_v3.json').write_text(json.dumps({
                'day':str(NOW.date()),'credits_today':8,'calls_today':8,'snapshots':{},
                'event_markets_done':['OLD_EVENT_DOES_NOT_DISABLE_OTHER_EVENTS']}))
            events=[]
            for i in range(2):
                e=copy.deepcopy(EVENT);e.update(id='epl:SYNTHETIC-'+str(i),family='soccer',
                    event_type='MATCHUP',odds_sport_key='soccer_epl',odds_event_id='SYNTHETIC-'+str(i))
                events.append(e)
            calls=[]
            def request(key,secret,markets,eid,discover):
                self.assertIsNotNone(eid)
                self.assertFalse(discover,'Documented markets do not require a paid catalog probe')
                calls.append(markets)
                rows=odds_rows()
                for book in rows[0]['bookmakers']:
                    book['markets']=[{'key':'alternate_totals','outcomes':[
                        {'name':'Over','point':2.5,'price':3},{'name':'Under','point':2.5,'price':1.5}]},
                        {'key':'btts','outcomes':[{'name':'Yes','price':3},{'name':'No','price':1.5}]},
                        {'key':'alternate_totals_corners','outcomes':[
                        {'name':'Over','point':9.5,'price':2.1},{'name':'Under','point':9.5,'price':1.8}]}]
                    book['markets']=[m for m in book['markets'] if m['key'] in markets.split(',')]
                return rows
            report=annotate_discovered(events,root,NOW,'SYNTHETIC',request)
            self.assertEqual(calls,['alternate_totals_corners,alternate_totals,btts','alternate_totals'])
            self.assertEqual(report['credits_today'],12)
            self.assertEqual(report['event_market_credits_today'],4)
            self.assertIn('BTTS',events[0]['market_quotes'])
            self.assertTrue(any(q.get('market_spec',{}).get('metric')=='corners' for q in events[0]['market_quotes'].values()))
            again=annotate_discovered(events,root,NOW,'SYNTHETIC',lambda *_:self.fail('No repeat credits'))
            self.assertEqual(again['calls_this_run'],0)

    def test_statistics_backfill_uses_only_known_completed_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls=[]
            dates=[NOW.date()-dt.timedelta(days=i*7) for i in range(1,10)]
            history={'epl':[{'date':day} for day in dates]+[{'date':NOW.date()+dt.timedelta(days=1)}]}
            def scores(route,day):calls.append(day);return {'events':[]},{}
            _,report=collect_statistics(Path(tmp),[EVENT],NOW,scores,football_history=history)
            self.assertEqual(calls,dates[:8])
            self.assertEqual(report['scoreboard_requests'],8)
            self.assertTrue(all(day<NOW.date() for day in calls))

    def test_zero_stake_forecast_can_gain_a_new_paper_record_without_rewriting_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);history=football_history();e=copy.deepcopy(EVENT)
            e['market_quotes']=market_quotes_for_event(e,odds_rows(),NOW)
            full=PaperRiskBook(root);full.reserve(e['id'],36)
            first=run_research(root,[e],models(history),NOW,full,football_history=history)
            self.assertFalse(first['experimental_account']['created'])
            frozen=copy.deepcopy([r for r in read_journal(root) if r['kind']=='FORECAST'])
            later=NOW+dt.timedelta(minutes=1)
            second=run_research(root,[e],models(history),later,PaperRiskBook(root),football_history=history)
            self.assertEqual(second['experimental_account']['positions'],1)
            current=[r for r in read_journal(root) if r['kind']=='FORECAST']
            self.assertEqual(current[:len(frozen)],frozen)
            paper=next(r for r in current if r['stake_eur']>0)
            self.assertIn(paper['source_forecast_id'],{r['id'] for r in frozen})
            self.assertEqual(paper['forecast_utc'],later.isoformat())
            self.assertEqual(run_research(root,[e],models(history),later,PaperRiskBook(root),football_history=history)['new_forecasts'],0)
            final=dict(e,completed=True,status='post',final={'home':3,'away':0,'outcome':0})
            reconcile(root,read_journal(root),[final],NOW+dt.timedelta(hours=8))
            self.assertTrue(all(v['n_distinct_events']==1 for v in validation(read_journal(root),NOW+dt.timedelta(days=1)).values()))


class MarketSettlementAndReportingTests(unittest.TestCase):
    def test_official_corner_stats_are_shared_and_integer_push_returns_stake(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            rows=[]
            for i,line in enumerate((9.5,10.0)):
                rows.append({'id':'SYNTHETIC-CORNER-'+str(i),'kind':'FORECAST','event_id':EVENT['id'],
                    'sport':'epl','home':EVENT['home'],'away':EVENT['away'],'market':'STAT_'+str(i),
                    'market_spec':{'kind':'TOTAL','metric':'corners','line':line,'team':None},
                    'start_utc':EVENT['start_utc'],'forecast_utc':NOW.isoformat(),
                    'probabilities':[.7,.3],'market_no_vig':[.5,.5],'selection_index':0,
                    'odds':[2,2],'stake_eur':1,'model_version':'SYNTHETIC'})
            (root/'data/market_research_v3.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            final=dict(EVENT,completed=True,status='post',final={'home':0,'away':0,'outcome':1})
            def enrich(e):return dict(e,final=dict(e['final'],team_statistics={'home':{'corners':7},'away':{'corners':3}}))
            with patch('sports_intelligence.market_catalog.enrich_final',side_effect=enrich) as fetch:
                self.assertEqual(reconcile(root,read_journal(root),[final],NOW+dt.timedelta(hours=8))[0],2)
                self.assertEqual(fetch.call_count,1)
            state=paper_state(read_journal(root))
            self.assertEqual(state['bankroll_eur'],1001)
            settled=[r for r in read_journal(root) if r['kind']=='SETTLEMENT']
            self.assertEqual([r['status'] for r in settled],['SETTLED','VOID'])
            report=market_activity(root,[],NOW+dt.timedelta(hours=8))['by_market']['corners']
            self.assertEqual(report['papers_total'],2)
            self.assertEqual(report['papers_voided'],1)
            self.assertEqual(report['observations'],0)


if __name__=='__main__':unittest.main()
