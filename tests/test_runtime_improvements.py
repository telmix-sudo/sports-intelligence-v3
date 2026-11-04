import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

from sports_intelligence.runtime_freshness import freshness,request_recovery
from sports_intelligence.portfolio_controls import PaperRiskBook
from sports_intelligence.historical_learning import HistoricalLearning,sample_identity
from sports_intelligence.validation_progress import validation_progress
from sports_intelligence.broad_odds import annotate_discovered,model_readiness
from test_global_discovery import fixture,NOW


class RecoveryTests(unittest.TestCase):
    def test_missing_stale_and_future_sources_block_new_papers_without_editing_wallets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'reports').mkdir()
            stamp=NOW.isoformat()
            for name,key in [('latest','generated_at_utc'),('global_discovery_latest','asof_utc'),('strategy_lab_latest','asof_utc')]:
                (root/f'reports/{name}.json').write_text(json.dumps({key:stamp}))
            self.assertFalse(freshness(root,NOW)['needs_scanner'])
            self.assertTrue(freshness(root,NOW+dt.timedelta(minutes=91))['needs_scanner'])
            state=freshness(root,NOW-dt.timedelta(minutes=1));self.assertEqual(state['components']['scanner']['status'],'FUTURE_TIMESTAMP')
            (root/'reports/latest.json').unlink();state=freshness(root,NOW)
            book=PaperRiskBook(root,allow_new_entries=not state['needs_scanner'])
            self.assertEqual(book.reserve('event',2),0)
            self.assertEqual(book.summary()['limit_rejections'],{'source_reports_stale':1})
            self.assertFalse((root/'data').exists())

    def test_dispatch_is_bounded_by_pending_and_cooldown_and_never_leaks_credentials(self):
        state={'needs_scanner':True,'needs_laboratory':True};calls=[];runs=[]
        def request(method,path,token,payload=None):
            calls.append((method,path,payload));return {'workflow_runs':runs}
        result=request_recovery(state,'owner/repo','SECRET',NOW,request)
        self.assertEqual(result,{'status':'REQUESTED','workflow':'scanner.yml'})
        self.assertEqual(calls[-1],('POST','/repos/owner/repo/actions/workflows/scanner.yml/dispatches',{'ref':'main'}))
        runs.append({'status':'in_progress'});calls.clear()
        self.assertEqual(request_recovery(state,'owner/repo','SECRET',NOW,request)['status'],'ALREADY_PENDING')
        self.assertEqual(len(calls),1)
        runs[:]=[{'status':'completed','event':'workflow_dispatch','created_at':NOW.isoformat()}]
        self.assertEqual(request_recovery(state,'owner/repo','SECRET',NOW,request)['status'],'COOLDOWN')
        def fail(*args):raise HTTPError('https://example/?key=SECRET',403,'SECRET',{},None)
        result=request_recovery(state,'owner/repo','SECRET',NOW,fail)
        self.assertEqual(result['http_status'],403);self.assertNotIn('SECRET',json.dumps(result))
        self.assertEqual(request_recovery({'needs_scanner':False,'needs_laboratory':False},'', '',NOW,request)['status'],'NOT_NEEDED')
        runs.clear()
        self.assertEqual(request_recovery({'needs_scanner':False,'needs_laboratory':True},'owner/repo','KEY',NOW,request)['workflow'],'strategy-lab.yml')



class IdentityTests(unittest.TestCase):
    def test_same_matchup_on_same_utc_date_is_two_games_but_same_id_conflict_is_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            learner=HistoricalLearning(Path(tmp),NOW)
            a=sample_identity('nba','2026-02-01','Miami Heat','Chicago Bulls','nba:401810560')
            b=sample_identity('nba','2026-02-01','Miami Heat','Chicago Bulls','nba:401810547')
            self.assertNotEqual(a,b)
            for identity,y in [(a,0),(b,1),(a,0)]:learner.add('model','nba','ML','2026-02-01',identity,[.6,.4],y)
            self.assertEqual(len(learner.samples['model|nba|ML']),2);self.assertFalse(learner.conflicts)
            learner.add('model','nba','ML','2026-02-01',a,[.6,.4],1)
            self.assertEqual(learner.model('model|nba|ML')['status'],'CONFLICTING_HISTORY')
            with self.assertRaises(ValueError):sample_identity('nba','2026-02-01','Miami','Chicago')
            self.assertEqual(sample_identity('epl','2026-02-01','A','B','source1'),sample_identity('epl','2026-02-01','A','B','source2'))


class ReadinessTests(unittest.TestCase):
    def test_actual_forecast_precedes_declared_scope_and_discovery_spending_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'reports').mkdir()
            ready=dict(fixture('nba','basketball',1),odds_sport_key='basketball_nba',paper_execution_enabled=True)
            insufficient=dict(fixture('nfl','football',2),odds_sport_key='americanfootball_nfl',paper_execution_enabled=True)
            snapshot={'asof_utc':NOW.isoformat(),'training_cutoff_exclusive_utc_date':str(NOW.date()),'events':[
                {k:e[k] for k in ('id','sport','home','away','start_utc')}|{'status':status,'forecasts':[{'family':'actual','market':'ML'}] if status=='READY' else []}
                for e,status in [(ready,'READY'),(insufficient,'INSUFFICIENT_HISTORY')]]}
            path=root/'reports/strategy_lab_latest.json';path.write_text(json.dumps({'pricing_readiness':snapshot}))
            calls=[]
            annotate_discovered([insufficient,fixture('tennis','tennis',3),ready],root,NOW,'TEST',lambda key,*_:calls.append(key) or [])
            self.assertEqual(calls[0],'basketball_nba');self.assertEqual(len(calls),2)
            again=annotate_discovered([fixture('handball','handball',4)],root,NOW+dt.timedelta(hours=6),'TEST',lambda *_:self.fail('Discovery ceiling'))
            self.assertEqual(again['discovery_only_credits_today'],1)
            self.assertEqual(model_readiness(root,[ready],NOW+dt.timedelta(hours=25))[1],'STALE_OR_FUTURE')
            self.assertEqual(model_readiness(root,[dict(ready,start_utc='different')],NOW)[0],{})
            snapshot['asof_utc']=(NOW+dt.timedelta(hours=1)).isoformat();path.write_text(json.dumps({'pricing_readiness':snapshot}))
            self.assertEqual(model_readiness(root,[ready],NOW)[0],{})


class ProgressTests(unittest.TestCase):
    def test_historical_rows_and_elapsed_days_do_not_count_as_forward_evidence(self):
        research={'validation_by_sport_market_version':{'nba|ML|model':{'n_distinct_events':8,'dates':2,'days':90,'blocks':['CALIBRATION_ERROR_OVER_10PP']}}}
        admission={'streams':{'model|nba|ML':{'n_distinct_events':50,'dates':10},'MARKET_REFERENCE|nba|ML':{}}}
        learning={'streams':{'model|nba|ML':{'n':8424,'status':'SHADOW_VALIDATION','forward':{'n':4,'dates':3,'market_n':2,'market_dates':1}}}}
        rows=validation_progress(admission,learning,research)['rows']
        self.assertEqual(len(rows),2)
        original,candidate=rows
        self.assertEqual((original['events'],original['dates'],original['remaining_events']),(8,2,92))
        self.assertIn('CALIBRATION_ERROR_OVER_10PP',original['blocks'])
        self.assertEqual((candidate['events'],candidate['remaining_dates'],candidate['history_samples']),(2,13,8424))
        self.assertFalse(candidate['eligible'])

    def test_line_identity_is_preserved_and_complete_sample_is_not_approval(self):
        admission={'streams':{f'm|epl|STAT_{line}':{'n_distinct_events':100,'dates':14,'eligible_for_paper':False,'blocks':['LOG_LOSS_NOT_BETTER_THAN_MARKET']} for line in (9,10)}}
        rows=validation_progress(admission,{}, {})['rows']
        self.assertEqual(len(rows),2);self.assertNotEqual(rows[0]['market'],rows[1]['market'])
        self.assertTrue(all(not r['eligible'] and r['remaining_events']==0 for r in rows))
