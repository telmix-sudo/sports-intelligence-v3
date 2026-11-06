import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sports_intelligence.player_context import (parse_summary,number,features,fit,probabilities,run,collect,read_rows,
    JOURNAL,VERSION,CODE_SHA256,pending)
from sports_intelligence.selection_experiment import SelectionExperiment
from sports_intelligence.historical_learning import forward_gate,HistoricalLearning
from sports_intelligence.daily_paper_minimum import daily_selector

UTC=dt.timezone.utc
NOW=dt.datetime(2026,11,6,12,tzinfo=UTC)


def event(n=1):
    return {'id':'nba:'+str(n),'sport':'nba','home':'Home','away':'Away','start_utc':(NOW+dt.timedelta(hours=2)).isoformat(),'status':'pre'}


def payload(e=None,state='pre'):
    e=e or event()
    teams=[{'id':str(i),'homeAway':side,'score':str(100-i),'team':{'id':str(i),'displayName':e[side]}}
           for i,side in enumerate(('home','away'),1)]
    return {'header':{'id':e['id'].split(':')[1],'competitions':[{'id':e['id'].split(':')[1],'date':e['start_utc'],
            'status':{'type':{'state':state,'completed':state=='post'}},'competitors':teams}]},
            'injuries':[{'team':{'id':str(i)},'injuries':[]} for i in (1,2)]}


def cache_fixture():
    e=event();p=payload(e);p['injuries'][0]['injuries']=[{'athlete':{'id':'11','displayName':'Player A'},'status':'Out','date':NOW.isoformat()}]
    context=parse_summary(p,e,NOW,'real-shape-synthetic-values')
    history={}
    for n in range(8):
        h={'id':'nba:old'+str(n),'sport':'nba','start_utc':(NOW-dt.timedelta(days=n+1)).isoformat(),
           'captured_at_utc':NOW.isoformat(),'teams':{}}
        for i,side in enumerate(('home','away'),1):
            h['teams'][side]={'id':str(i),'name':e[side],'players':{str(10*i+j):{'id':str(10*i+j),'name':'Player '+str(j),
                'minutes':30.,'score':float(10+j+n)} for j in range(1,6)}}
        history[h['id']]=h
    return e,{'contexts':{e['id']:context},'history':history,'attempts':{}}


def observer(root):
    x=SelectionExperiment(root,NOW,'fixed-model-code',clock=lambda:NOW)
    q={'market':'ML','decimal':[2.,2.],'no_vig':[.5,.5],'quote_timestamp_available':True,'provider':'Synthetic test',
       'bookmaker_count':2,'bookmakers':['A','B'],'best_book_updates':[NOW.isoformat()]*2}
    x.observe('MULTIMARKET-1.0.0-nba',event(),[.5,.5],q,sample_min=30)
    return x


class PlayerContextTests(unittest.TestCase):
    def test_missing_is_unknown_empty_report_is_not_a_guessed_healthy_roster(self):
        p=payload();p.pop('injuries')
        r=parse_summary(p,event(),NOW)
        self.assertFalse(r['teams']['home']['injury_report']);self.assertFalse(r['teams']['home']['lineup_confirmed'])
        r=parse_summary(payload(),event(),NOW)
        self.assertTrue(r['teams']['home']['injury_report']);self.assertEqual(r['teams']['home']['players'],{})

    def test_unflagged_roster_is_not_a_lineup_and_future_injury_news_is_ignored(self):
        p=payload();p['rosters']=[{'team':{'id':'1'},'roster':[{'athlete':{'id':str(i)}} for i in range(5)]+
            [{'athlete':{'id':str(i)}} for i in range(5,11)]}]
        p['injuries'][0]['injuries']=[{'athlete':{'id':'11'},'status':'Out','date':(NOW+dt.timedelta(minutes=1)).isoformat()}]
        r=parse_summary(p,event(),NOW)
        self.assertFalse(r['teams']['home']['lineup_confirmed']);self.assertFalse(r['teams']['home']['players'])
        p['rosters'][0]['roster']=p['rosters'][0]['roster'][:5]
        for a in p['rosters'][0]['roster']:a['starter']=True
        self.assertTrue(parse_summary(p,event(),NOW)['teams']['home']['lineup_confirmed'])

    def test_identity_post_start_and_contradictory_starter_status_fail_closed(self):
        for mutate in (lambda p:p['header'].update(id='wrong'),lambda p:p['header']['competitions'][0].update(date=(NOW+dt.timedelta(hours=3)).isoformat()),
                       lambda p:p['header']['competitions'][0]['competitors'][0]['team'].update(displayName='Other')):
            p=payload();mutate(p)
            with self.assertRaises(ValueError):parse_summary(p,event(),NOW)
        with self.assertRaises(ValueError):parse_summary(payload(),event(),NOW+dt.timedelta(hours=3))
        p=payload();p['injuries'][0]['injuries']=[{'athlete':{'id':'11'},'status':'Out'}]
        p['rosters']=[{'team':{'id':'1'},'roster':[{'athlete':{'id':str(i)},'starter':True} for i in range(11,16)]}]
        with self.assertRaises(ValueError):parse_summary(p,event(),NOW)

    def test_history_uses_named_stats_and_never_current_game_stats_for_form(self):
        e=event();e['start_utc']=(NOW-dt.timedelta(days=1)).isoformat();p=payload(e,'post')
        p['boxscore']={'players':[{'team':{'id':str(i)},'statistics':[{'keys':['points','minutes'],
            'athletes':[{'athlete':{'id':str(i*10),'displayName':'P'},'stats':['20','30:30']}]}]} for i in (1,2)]}
        r=parse_summary(p,e,NOW)
        self.assertEqual(r['teams']['home']['players']['10']['minutes'],30.5)
        self.assertEqual(r['outcome'],0)
        self.assertIsNone(number('nan'));self.assertIsNone(number('10:80',True))
        pre=payload();pre['boxscore']=p['boxscore'];r=parse_summary(pre,event(),NOW)
        self.assertFalse(any(t['players'] for t in r['teams'].values()))

    def test_features_use_earlier_dates_and_preserve_unknown_player_history(self):
        e,c=cache_fixture();ctx=c['contexts'][e['id']]
        mode,x,d=features(ctx,list(c['history'].values()),NOW)
        self.assertEqual(mode,'INJURY_REPORT');self.assertGreater(x[0],0);self.assertEqual(len(x),8)
        ctx['teams']['home']['players']['999']={'id':'999','name':'Unseen','availability':'OUT'}
        mode,x,d=features(ctx,list(c['history'].values()),NOW)
        self.assertEqual(d['home']['unknown_out_count'],1);self.assertGreater(x[4],0)
        for h in c['history'].values():h['captured_at_utc']=(NOW+dt.timedelta(seconds=1)).isoformat()
        with self.assertRaisesRegex(ValueError,'THREE_EARLIER'):features(ctx,list(c['history'].values()),NOW)
        with self.assertRaises(ValueError):features(ctx,[],NOW+dt.timedelta(hours=2))

    def test_football_regulation_minutes_are_labelled_derivations_and_ambiguous_cases_remain_missing(self):
        from sports_intelligence.player_context import regulation_minutes
        state={'name':'STATUS_FULL_TIME'}
        self.assertEqual(regulation_minutes({'starter':True,'subbedOut':False},state),90)
        p={'starter':True,'subbedOut':True,'plays':[{'substitution':True,'clock':{'displayValue':"74'"}}]}
        self.assertEqual(regulation_minutes(p,state),74)
        p.update(starter=False,subbedIn=True,subbedOut=False)
        self.assertEqual(regulation_minutes(p,state),16)
        self.assertIsNone(regulation_minutes(p,{'name':'STATUS_ABANDONED'}))
        p['subbedOut']=True;self.assertIsNone(regulation_minutes(p,state))

    def test_learned_context_changes_probabilities_without_a_manual_bonus(self):
        rows=[{'base':[.5,.5],'features':[1. if i%2 else -1.], 'y':0 if i%2 else 1,'date':str(i//10)} for i in range(100)]
        self.assertIsNone(fit(rows[:20]));weights=fit(rows)
        self.assertGreater(probabilities([.5,.5],[1.],weights)[0],.7)
        self.assertLess(probabilities([.5,.5],[-1.],weights)[0],.3)
        self.assertAlmostEqual(sum(probabilities([.5,.5],[1.],weights)),1)
        p3=probabilities([.4,.3,.3],[1.],[[0.],[0.],[0.]])
        for actual,expected in zip(p3,[.4,.3,.3]):self.assertAlmostEqual(actual,expected)

    def test_future_benchmarks_gate_daily_influence_not_training_fit(self):
        rows=[{'date':str(i//7),'base':[.5,.5],'market':[.5,.5],'candidate':[.8,.2],'y':0} for i in range(105)]
        gate=forward_gate(rows);self.assertTrue(gate['eligible'])
        f={'historical_learning':{'status':'SHADOW_VALIDATION','forward_validation':gate}}
        self.assertEqual(daily_selector(f)[0],'MARKET_FAVOURITE')
        f['historical_learning']['status']='ACTIVE';self.assertEqual(daily_selector(f)[0],'MODEL_FAVOURITE')
        self.assertFalse(forward_gate(rows[:40])['eligible'])
        for r in rows:r['market']=[.9,.1]
        self.assertFalse(forward_gate(rows)['eligible'])

    def test_freezes_pre_game_features_deduplicates_and_resolves_without_any_stake(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);e,c=cache_fixture();x=observer(root)
            with patch('sports_intelligence.player_context.collect',return_value=(c,{})):
                first=run(root,[e],list(x.current_forecasts.values()),{},NOW,x,clock=lambda:NOW)
                before=(root/JOURNAL).read_bytes()
                run(root,[e],list(x.current_forecasts.values()),{},NOW,x,clock=lambda:NOW)
                self.assertEqual((root/JOURNAL).read_bytes(),before)
                self.assertEqual(first['new_observations'],1);self.assertEqual(first['fitted_streams'],0)
                self.assertIsNone(first['events'][0]['candidate_probability'])
                self.assertTrue(pending(root));after=NOW+dt.timedelta(hours=4)
                run(root,[],[],{e['id']:{'outcome':2}},after,x,clock=lambda:after)
                rows=read_rows(root);self.assertEqual(rows[-1]['outcome'],1);self.assertFalse(pending(root))
                self.assertEqual((root/JOURNAL).read_bytes()[:len(before)],before)
                self.assertTrue(all(r.get('stake_eur',0)==0 for r in rows))
                self.assertFalse((root/'data/strategy_ledgers').exists())

    def test_source_budget_cache_and_schema_rejection_are_real(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);calls=[]
            def fetch(url):calls.append(url);return json.dumps(payload()),{'sha256':'test'}
            cache,info=collect(root,[event()],NOW,fetch,clock=lambda:NOW,max_requests=2)
            self.assertEqual(info['requests'],1);self.assertEqual(len(calls),1)
            self.assertIn(event()['id'],cache['contexts'])
            cache,info=collect(root,[event()],NOW,fetch,clock=lambda:NOW,max_requests=2)
            self.assertEqual(info['requests'],0)
            self.assertNotIn('details',json.dumps(cache))

    def test_fitted_shadow_model_cannot_enter_the_wallet_candidate_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);e,c=cache_fixture();x=observer(root)
            stream='|'.join((VERSION,CODE_SHA256,'fixed-model-code','nba','ML','INJURY_REPORT','MULTIMARKET-1.0.0-nba'))
            rows=[]
            for i in range(40):
                day=NOW-dt.timedelta(days=10-i//10)
                oid='old'+str(i)
                detail={'history_latest_start_utc':(day-dt.timedelta(days=1)).isoformat(),'history_latest_capture_utc':day.isoformat()}
                rows.append({'id':oid,'kind':'OBSERVATION','version':VERSION,'code_sha256':CODE_SHA256,'stream':stream,'event_id':'nba:'+oid,
                    'sport':'nba','market':'ML','mode':'INJURY_REPORT','forecast_utc':day.isoformat(),'context_utc':day.isoformat(),
                    'start_utc':(day+dt.timedelta(hours=2)).isoformat(),'base_probabilities':[.5,.5],'market_probabilities':[.5,.5],
                    'features':[(-1.)**i,0.,0.,0.,0.,0.,0.,0.],'details':{'home':detail,'away':detail},'candidate_probabilities':None})
                rows.append({'id':'r'+oid,'kind':'RESULT','observation_id':oid,'outcome':i%2,'settlement_utc':(day+dt.timedelta(hours=4)).isoformat()})
            (root/JOURNAL).parent.mkdir(parents=True,exist_ok=True)
            (root/JOURNAL).write_text(''.join(json.dumps(r)+'\n' for r in rows))
            with patch('sports_intelligence.player_context.collect',return_value=(c,{})):
                report=run(root,[e],list(x.current_forecasts.values()),{},NOW,x,clock=lambda:NOW)
            self.assertEqual(report['fitted_streams'],1);self.assertEqual(report['active_streams'],0)
            self.assertEqual(report['events'][0]['status'],'SHADOW_MODEL')
            self.assertIsNotNone(report['events'][0]['candidate_probability'])
            self.assertEqual(len(x.current_forecasts),1)
            self.assertFalse((root/'data/strategy_ledgers').exists())

    def test_football_catalogue_and_summaries_share_the_total_request_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);e=event();e.update(id='epl:1',sport='epl');calls=[]
            def fetch(url):
                calls.append(url)
                return json.dumps({'events':[]} if '/scoreboard?' in url else payload(e)),{'sha256':'test'}
            _,info=collect(root,[e],NOW,fetch,clock=lambda:NOW,max_requests=4)
            self.assertLessEqual(len(calls),4);self.assertEqual(info['requests'],len(calls))
            self.assertGreaterEqual(info['catalog_requests'],1)

    def test_context_evidence_does_not_replace_the_context_models_own_probability_in_learning(self):
        from sports_intelligence.strategy_lab import save_jsonl
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);e=event();e['start_utc']=(NOW-dt.timedelta(days=2)).isoformat()
            row={**{k:e[k] for k in ('event_id','sport','home','away','start_utc') if k in e},'id':'p','event_id':e['id'],
                 'strategy':'ELO_STANDARD','market':'ML','model_version':VERSION+'-nba','daily_paper_exploration_policy':'DAILY-PAPER-MINIMUM-1.1',
                 'forecast_utc':(NOW-dt.timedelta(days=3)).isoformat(),'settlement_utc':(NOW-dt.timedelta(days=1)).isoformat(),
                 'status':'SETTLED','probabilities':[.7,.3],'observed_outcome':0,'selection_index':0,'stake_eur':.5,'odds':[2.,2.],
                 'historical_learning':{'version':VERSION,'base_probabilities':[.4,.6]}}
            save_jsonl(root/'data/strategy_ledgers/elo_standard.jsonl',[row]);learning=HistoricalLearning(root,NOW)
            stream=VERSION+'-nba|nba|ML'
            self.assertEqual(next(iter(learning.samples[stream].values()))['p'],[.7,.3])
