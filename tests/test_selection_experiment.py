import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sports_intelligence.selection_experiment import SelectionExperiment,paired_day_interval
from sports_intelligence.selection_policy import selection_trace
from sports_intelligence.selection_audit import audit_selections,verify_official_results
from sports_intelligence.strategy_lab import enrich_pending_statistics

UTC=dt.timezone.utc
NOW=dt.datetime(2026,11,4,18,tzinfo=UTC)


def event(clock=NOW,n=1,sport='nba'):
    return {'id':sport+':'+str(n),'sport':sport,'home':'Home','away':'Away','status':'pre',
            'start_utc':(clock+dt.timedelta(hours=2)).isoformat()}


def quote(clock=NOW,market='ML'):
    return {'market':market,'decimal':[4.9,1.25],'no_vig':[.2,.8],'quote_timestamp_available':True,
            'provider':'Synthetic only','bookmaker_count':2,'bookmakers':['A','B'],'best_book_updates':[clock.isoformat()]*2}


class SelectionTrialTests(unittest.TestCase):
    def test_gap_favourite_and_opposite_are_distinct_and_1x2_has_no_fake_opposite(self):
        result=selection_trace([.45,.55],[.2,.8],[4.9,1.25])
        self.assertEqual(result['selected_index'],0)
        self.assertEqual({k:v['selection_index'] for k,v in result['arms'].items()},
                         {'VALUE_GAP':0,'MODEL_FAVOURITE':1,'MARKET_FAVOURITE':1,'OPPOSITE_VALUE_GAP':1})
        self.assertNotIn('OPPOSITE_VALUE_GAP',selection_trace([.4,.3,.3],[.5,.2,.3],[2.,4.,3.])['arms'])
        with_push=selection_trace([.45,.55],[.2,.8],[4.9,1.25],.2)
        self.assertAlmostEqual(with_push['arms']['VALUE_GAP']['model_expected_unit_pnl'],.8*(.45*4.9-1))
        for p in ([float('nan'),0.5],[.7,.7]):
            with self.assertRaises(ValueError):selection_trace(p,[.2,.8],[4.9,1.25])

    def test_freezes_same_quote_for_all_arms_deduplicates_wallet_copies_and_keeps_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);x=SelectionExperiment(root,NOW,'test-code')
            q=quote();e=event()
            self.assertTrue(x.observe('MODEL',e,[.45,.55],q))
            frozen=(root/'data/selection_experiment_v3.jsonl').read_bytes()
            self.assertFalse(x.observe('MODEL',e,[.8,.2],q))
            y=SelectionExperiment(root,NOW+dt.timedelta(hours=3),'test-code')
            y.reconcile({e['id']:{'home':97,'away':108,'outcome':2}})
            report=y.report();stream=report['streams'][0]
            self.assertEqual((stream['arms']['VALUE_GAP']['wins'],stream['arms']['OPPOSITE_VALUE_GAP']['wins']),(0,1))
            self.assertAlmostEqual(stream['arms']['OPPOSITE_VALUE_GAP']['unit_pnl_eur'],.25)
            self.assertEqual((root/'data/selection_experiment_v3.jsonl').read_bytes()[:len(frozen)],frozen)
            self.assertFalse(report['automatic_stake_activation'])
            self.assertFalse((root/'data/strategy_ledgers').exists())
            self.assertEqual(report['pending_total'],0)
            self.assertFalse(y.observe('MODEL',e,[.6,.4],q),'No backfill after kickoff')

    def test_best_value_uses_odds_and_shortlist_expires_at_kickoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            x=SelectionExperiment(Path(tmp),NOW,'test-code');e=event(sport='epl')
            q=dict(quote(market='1X2'),decimal=[2.25,3.6,2.9],no_vig=[.42,.29,.29],
                   bookmakers=['A','B','A'],best_book_updates=[NOW.isoformat()]*3)
            self.assertTrue(x.observe('model-a',e,[.48,.34,.18],q,sample_min=50))
            self.assertTrue(x.observe('model-b',e,[.47,.35,.18],q,sample_min=50))
            self.assertTrue(x.observe('few-games',event(n=2,sport='epl'),[.48,.34,.18],q,sample_min=4))
            self.assertTrue(x.observe('no-edge',event(n=3,sport='epl'),q['no_vig'],q,sample_min=50))
            entries=x.value_shortlist(NOW)['rows']
            self.assertEqual(len(entries),1)
            self.assertEqual((entries[0]['family'],entries[0]['selection_index'],entries[0]['stake_eur']),('model-b',1,0))
            first=next(r for r in x.rows if r['kind']=='FORECAST')
            self.assertEqual(first['arms']['VALUE_GAP'],0)
            self.assertEqual(first['arms']['PRICE_ADJUSTED_VALUE'],1)
            self.assertEqual(x.value_shortlist(NOW+dt.timedelta(hours=2))['candidates'],0)
            settled=SelectionExperiment(Path(tmp),NOW+dt.timedelta(hours=3),'test-code')
            settled.reconcile({e['id']:{'home':1,'away':1,'outcome':1}})
            arms=next(s['arms'] for s in settled.report()['streams'] if s['family']=='model-a')
            comparison=arms['PRICE_ADJUSTED_VALUE']['qualified_comparison']
            self.assertEqual(comparison['n'],1)
            self.assertAlmostEqual(comparison['roi_pct']['PRICE_ADJUSTED_VALUE'],260)
            self.assertEqual(comparison['roi_pct']['VALUE_GAP'],-100)

    def test_future_quote_missing_price_and_one_house_are_rejected_without_a_policy_or_forecast(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);x=SelectionExperiment(root,NOW,'code')
            invalid=[dict(quote(),bookmaker_count=1),dict(quote(),decimal=[float('inf'),1.2]),
                     dict(quote(),best_book_updates=[(NOW+dt.timedelta(minutes=1)).isoformat()]*2),
                     dict(quote(),best_book_updates=[(NOW-dt.timedelta(hours=9)).isoformat()]*2)]
            for q in invalid:self.assertFalse(x.observe('m',event(),[.45,.55],q))
            self.assertEqual(x.report()['forecasts_total'],0)
            self.assertFalse((root/'data/selection_experiment_v3.jsonl').exists())

    def test_push_and_nfl_draw_void_both_policies_but_unknown_results_remain_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);x=SelectionExperiment(root,NOW,'code')
            e=event(sport='epl');spec={'kind':'TOTAL','metric':'goals','line':3.,'team':None,'period':'FULL_TIME'}
            x.observe('goals',e,[.45,.55],quote(market='STAT_exact'),spec)
            x.observe('nfl',event(n=2,sport='nfl'),[.45,.55],quote())
            y=SelectionExperiment(root,NOW+dt.timedelta(hours=3),'code')
            y.reconcile({e['id']:{'home':2,'away':1,'outcome':0}})
            self.assertEqual(y.report()['voided_total'],1);self.assertEqual(y.report()['pending_total'],1)
            y.reconcile({'nfl:2':{'home':20,'away':20,'outcome':1}})
            self.assertEqual(y.report()['voided_total'],2)
            self.assertTrue(all(v==0 for r in y.rows if r['kind']=='SETTLEMENT' for v in r['unit_pnl'].values()))

    def test_cohort_closes_on_forecasts_and_dates_not_on_gains_and_never_accepts_late_additions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);n=0
            for day in range(14):
                clock=NOW+dt.timedelta(days=day);x=SelectionExperiment(root,clock,'fixed')
                for _ in range(8):
                    n+=1;x.observe('m',event(clock,n),[.45,.55],quote(clock))
            report=x.report();stream=report['streams'][0]
            self.assertTrue(stream['cohort_locked']);self.assertEqual(stream['forecasts'],105)
            self.assertEqual(stream['status'],'WAITING_RESULTS')
            self.assertFalse(x.observe('m',event(clock,999),[.9,.1],quote(clock)))
            # Reversing every future result cannot change the locked cohort IDs.
            ids=[r['id'] for r in x.rows if r['kind']=='FORECAST']
            y=SelectionExperiment(root,clock+dt.timedelta(days=3),'fixed')
            y.reconcile({r['event_id']:{'home':0,'away':1,'outcome':2} for r in x.rows if r['kind']=='FORECAST'})
            report=y.report();stream=report['streams'][0]
            self.assertEqual(stream['status'],'REVIEW_REQUIRED')
            self.assertEqual([r['id'] for r in y.rows if r['kind']=='FORECAST'],ids)
            self.assertFalse(stream['automatic_stake_activation'])
            self.assertIsNotNone(stream['arms']['OPPOSITE_VALUE_GAP']['paired_unit_difference_vs_VALUE_GAP'])

    def test_real_capture_clock_rejects_a_game_that_started_while_the_lab_was_working(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            delayed=SelectionExperiment(root,NOW,'code',clock=lambda:NOW+dt.timedelta(hours=3))
            self.assertFalse(delayed.observe('m',event(),[.45,.55],quote()))
            capture=NOW+dt.timedelta(minutes=1)
            current=SelectionExperiment(root,NOW,'code',clock=lambda:capture)
            self.assertTrue(current.observe('m',event(),[.45,.55],quote()))
            f=next(r for r in current.rows if r['kind']=='FORECAST')
            self.assertEqual(f['forecast_utc'],capture.isoformat())

    def test_model_code_and_exact_market_line_have_separate_cohorts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for code,market in [('v1','STAT_line9'),('v2','STAT_line9'),('v2','STAT_line10')]:
                SelectionExperiment(root,NOW,code).observe('m',event(),[.45,.55],quote(market=market),
                    {'kind':'TOTAL','metric':'goals','line':9.5 if market.endswith('9') else 10.5})
            self.assertEqual(len(SelectionExperiment(root,NOW,'v2').report()['streams']),3)

    def test_stat_only_experiment_gets_official_enrichment_without_a_funded_wallet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);x=SelectionExperiment(root,NOW,'code');e=event(sport='epl')
            x.observe('corners',e,[.45,.55],quote(market='STAT_line'),{'kind':'TOTAL','metric':'corners','line':9.5})
            finals={e['id']:{'home':2,'away':1,'outcome':0}}
            def enrich(e):return dict(e,final=dict(e['final'],team_statistics={'home':{'corners':7},'away':{'corners':5}}))
            result=enrich_pending_statistics(root,finals,NOW+dt.timedelta(hours=3),enricher=enrich)
            self.assertEqual(result['lookups'],1)
            y=SelectionExperiment(root,NOW+dt.timedelta(hours=3),'code');y.reconcile(finals)
            self.assertEqual(y.report()['settled_total'],1)

    def test_integration_records_new_model_predictions_even_with_zero_admissions(self):
        from test_research_integration import EVENT,NOW as CLOCK,football_history,models,odds_rows
        from sports_intelligence.research_pipeline import run_research
        from sports_intelligence.portfolio_controls import PaperRiskBook
        from sports_intelligence.odds_provider import market_quotes_for_event
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);history=football_history();e=copy.deepcopy(EVENT)
            e['market_quotes']=market_quotes_for_event(e,odds_rows(),CLOCK)
            x=SelectionExperiment(root,CLOCK,'code')
            run_research(root,[e],models(history),CLOCK,PaperRiskBook(root),football_history=history,selection_experiment=x)
            self.assertEqual(x.report()['forecasts_total'],3)
            self.assertFalse((root/'data/strategy_ledgers').exists())


class SelectionAuditTests(unittest.TestCase):
    def test_repetition_is_visible_and_wrong_payout_or_result_is_flagged_without_rewriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'data/strategy_ledgers/a.jsonl';path.parent.mkdir(parents=True)
            r={'id':'a','strategy':'ELO_STANDARD','event_id':'nba:1','sport':'nba','home':'Home','away':'Away',
               'forecast_utc':NOW.isoformat(),'start_utc':event()['start_utc'],'status':'SETTLED','market':'ML',
               'model_version':'shared','source_forecast_id':'one','odds':[4.9,1.25],'probabilities':[.45,.55],
               'no_vig':[.2,.8],'selection_index':0,'observed_outcome':1,'stake_eur':1.,'pnl_eur':-1.}
            path.write_text(json.dumps(r)+'\n'+json.dumps(dict(r,id='b',strategy='ELO_DYNAMIC'))+'\n');before=path.read_bytes()
            a=audit_selections(root)
            self.assertEqual(a['models']['positions'],2);self.assertEqual(a['models']['distinct_events'],1)
            self.assertEqual(a['binary_discovery_comparison']['choices'],1)
            self.assertEqual(a['issues'],[]);self.assertEqual(before,path.read_bytes())
            path.write_text(json.dumps(dict(r,pnl_eur=2.))+'\n')
            self.assertEqual(audit_selections(root)['issues'][0]['reason'],'PNL_DOES_NOT_MATCH_FROZEN_SELECTION')

    def test_official_verifier_checks_id_and_teams_caches_scores_and_never_changes_balance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'data/strategy_ledgers/a.jsonl';path.parent.mkdir(parents=True)
            r={'id':'a','strategy':'ELO_STANDARD','event_id':'nba:1','sport':'nba','home':'Home','away':'Away','status':'SETTLED'}
            path.write_text(json.dumps(r)+'\n');before=path.read_bytes();calls=[]
            def fetch(url):
                calls.append(url)
                return json.dumps({'header':{'id':'1','competitions':[{'status':{'type':{'completed':True}},'competitors':[
                    {'homeAway':'home','team':{'displayName':'Home'},'score':'97'},
                    {'homeAway':'away','team':{'displayName':'Away'},'score':'108'}]}]}}),{'sha256':'synthetic'}
            a=verify_official_results(root,NOW,fetch)
            self.assertEqual(a['verified_model_events'],1);self.assertEqual(path.read_bytes(),before)
            self.assertEqual(verify_official_results(root,NOW,fetch)['requests_this_run'],0)
            self.assertEqual(len(calls),1)
            changed=json.loads((root/'data/selection_official_audit_v3.json').read_text());changed['events']['nba:1']['home']='Wrong'
            (root/'data/selection_official_audit_v3.json').write_text(json.dumps(changed))
            self.assertEqual(verify_official_results(root,NOW,fetch)['requests_this_run'],1)

    def test_official_verifier_rejects_a_different_event_and_sanitizes_network_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'data/strategy_ledgers/a.jsonl';path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'id':'a','strategy':'ELO_STANDARD','event_id':'nba:1','sport':'nba','home':'Home','away':'Away','status':'SETTLED'})+'\n')
            def fail(url):raise ConnectionError('SECRET')
            r=verify_official_results(root,NOW,fail)
            self.assertEqual(r['verified_model_events'],0);self.assertNotIn('SECRET',json.dumps(r))
            r=verify_official_results(root,NOW,lambda _: (json.dumps({'header':{'id':'2'}}),{}))
            self.assertEqual(r['verified_model_events'],0)
