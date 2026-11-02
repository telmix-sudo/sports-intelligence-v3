from market_validation_fixtures import approved_research,ApprovedOriginal
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from sports_intelligence.historical_learning import HistoricalLearning, VERSION, fit, forward_gate
from sports_intelligence.custom_strategies import ALL_NAMES
from sports_intelligence.strategy_lab import historical_comparison, read_jsonl, save_jsonl
from sports_intelligence.portfolio_controls import PaperRiskBook
from sports_intelligence.strategy_registry import ensure_registry
from sports_intelligence.research_pipeline import run_research, read_journal
from sports_intelligence.auxiliary_portfolios import open_auxiliary_markets
from sports_intelligence.odds_provider import market_quotes_for_event
from sports_intelligence.ml_lab import prospective_calendar
from test_ml_lab import matches
from test_research_integration import EVENT, NOW, odds_rows, football_history, models

UTC=dt.timezone.utc
CLOCK=dt.datetime(2026,11,2,12,tzinfo=UTC)


def sample_rows():
    return [{'date':str(dt.date(2023,1,1)+dt.timedelta(days=i)), 'event':str(i),
             'p':[.8,.2], 'y':0 if i%4==0 else 1} for i in range(600)]


class HistoricalLearningTests(unittest.TestCase):
    def test_holdout_labels_cannot_select_parameters_and_dates_are_disjoint(self):
        rows=sample_rows();first=fit(rows)
        self.assertEqual(first['status'],'SHADOW_VALIDATION')
        periods=list(first['splits'].values())
        for a,b in zip(periods,periods[1:]):self.assertLess(a['last_date'],b['first_date'])
        cutoff=periods[-1]['first_date']
        changed=[dict(r,y=1-r['y']) if r['date']>=cutoff else r for r in rows]
        second=fit(changed)
        self.assertEqual(first['parameters'],second['parameters'])
        self.assertNotEqual(first['holdout'],second['holdout'])
        self.assertIsNone(first['historical_roi'])

    def test_history_improvement_alone_cannot_change_paper_probabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            learner=HistoricalLearning(Path(tmp),CLOCK)
            for r in sample_rows():learner.add('model','epl','TOTAL_2_5',r['date'],r['event'],r['p'],r['y'])
            p,e=learner.apply('model','epl','TOTAL_2_5',[.8,.2])
            self.assertEqual(p,[.8,.2]);self.assertEqual(e['status'],'SHADOW_VALIDATION')
            self.assertLess(e['candidate_probabilities'][0],p[0])
            self.assertAlmostEqual(sum(e['candidate_probabilities']),1)
            self.assertEqual(learner.apply('model','epl','TOTAL_3_5',[.8,.2])[1]['status'],'INSUFFICIENT_HISTORY')

    def test_both_metrics_and_forward_dates_required_before_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            learner=HistoricalLearning(Path(tmp),CLOCK)
            for r in sample_rows():learner.add('model','epl','ML',r['date'],r['event'],r['p'],r['y'])
            pairs=[{'date':str(dt.date(2026,8,1)+dt.timedelta(days=i)), 'base':[.8,.2],'candidate':[.5,.5],'market':[.7,.3],'y':1} for i in range(100)]
            self.assertFalse(forward_gate(pairs[:99])['eligible'])
            self.assertFalse(forward_gate([dict(r,date='2026-08-01') for r in pairs])['eligible'])
            learner.forward['model|epl|ML']={str(i):r for i,r in enumerate(pairs)}
            p,e=learner.apply('model','epl','ML',[.8,.2])
            self.assertEqual(e['status'],'ACTIVE');self.assertLess(p[0],.8)
            unchanged,control=learner.apply('model','epl','ML',[.8,.2],control=True)
            self.assertEqual(unchanged,[.8,.2]);self.assertEqual(control['status'],'CONTROL_SHADOW_ONLY')
            self.assertFalse(forward_gate([dict(r,candidate=r['base']) for r in pairs])['eligible'])

    def test_excludes_current_day_and_conflicting_outcomes_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            learner=HistoricalLearning(Path(tmp),CLOCK)
            learner.add('m','epl','ML',CLOCK.date(),'today',[.6,.4],0)
            self.assertFalse(learner.samples)
            learner.add('m','epl','ML','2026-11-01','one',[.6,.4],0)
            learner.add('m','epl','ML','2026-11-01','one',[.6,.4],1)
            self.assertEqual(learner.apply('m','epl','ML',[.6,.4])[1]['status'],'CONFLICTING_HISTORY')

    def test_chronological_replay_never_sees_own_day_result(self):
        history=matches(35);last=history[-1]['date']
        changed=[dict(r,goals_home=12,goals_away=0) if r['date']==last else r for r in history]
        with tempfile.TemporaryDirectory() as tmp:
            a,b=[HistoricalLearning(Path(tmp),CLOCK) for _ in range(2)]
            historical_comparison({'epl':history},a)
            historical_comparison({'epl':changed},b)
            key='ELO_STANDARD|epl|1X2'
            self.assertEqual([r['p'] for r in a.samples[key].values()],[r['p'] for r in b.samples[key].values()])
            self.assertGreater(len(a.samples[key]),0)

    def test_frozen_results_use_base_not_adjusted_probability_and_deduplicate_wallets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            row={'id':'f1','strategy':'FREQ_SELECTIVE_1_3','sport':'epl','event_id':'epl:1','market':'1X2',
                 'home':'Arsenal','away':'Chelsea','forecast_utc':'2026-10-30T12:00:00+00:00',
                 'start_utc':'2026-10-31T12:00:00+00:00','settlement_utc':'2026-10-31T15:00:00+00:00',
                 'status':'SETTLED','observed_outcome':0,'probabilities':[.5,.25,.25],
                 'historical_learning':{'version':VERSION,'status':'SHADOW_VALIDATION',
                 'base_probabilities':[.7,.2,.1],'candidate_probabilities':[.5,.25,.25],
                 'model_sha256':'frozen-model','training_cutoff_exclusive_utc_date':'2026-10-30'}}
            for name in ('FREQ_SELECTIVE_1_3','FREQ_BALANCED_3_5'):
                save_jsonl(root/'data/strategy_ledgers'/(name.lower()+'.jsonl'),[dict(row,strategy=name)])
            # A current-day settlement and a post-start forecast are ineligible.
            bad=[dict(row,id='f2',event_id='epl:2',home='Other',settlement_utc=CLOCK.isoformat()),
                 dict(row,id='f3',home='Late',forecast_utc=row['settlement_utc'])]
            save_jsonl(root/'data/strategy_ledgers/elo_standard.jsonl',bad)
            frozen=(root/'data/strategy_ledgers/freq_selective_1_3.jsonl').read_bytes()
            learner=HistoricalLearning(root,CLOCK);key='ELO_POISSON_BLEND|epl|1X2'
            self.assertEqual(len(learner.samples[key]),1);self.assertEqual(len(learner.forward[key]),1)
            self.assertEqual(next(iter(learner.samples[key].values()))['p'],[.7,.2,.1])
            self.assertEqual((root/'data/strategy_ledgers/freq_selective_1_3.jsonl').read_bytes(),frozen)
            report=learner.report((*ALL_NAMES,'EXPERIMENTAL_MULTI_MARKET'))
            self.assertEqual(len(report['wallets']),13)

    @approved_research
    def test_auxiliary_learning_evidence_reaches_all_thirteen_without_rewriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);registry=ensure_registry(root,NOW-dt.timedelta(days=2),ALL_NAMES)
            learner=HistoricalLearning(root,NOW);history=football_history()
            e=copy.deepcopy(EVENT);e['market_quotes']=market_quotes_for_event(e,odds_rows(),NOW)
            run_research(root,[e],models(history),NOW,PaperRiskBook(root),football_history=history,learning=learner)
            forecasts=[r for r in read_journal(root) if r['kind']=='FORECAST']
            self.assertTrue(forecasts);self.assertTrue(all('historical_learning' in r for r in forecasts))
            frozen=copy.deepcopy(read_journal(root))
            added=open_auxiliary_markets(root,[e],NOW,registry,PaperRiskBook(root),admission=ApprovedOriginal())
            self.assertTrue(added)
            self.assertNotIn('MARKET_DAILY_REFERENCE',added)
            self.assertLess(len(added),len(ALL_NAMES))
            for name in added:
                row=read_jsonl(root/'data/strategy_ledgers'/(name.lower()+'.jsonl'))[0]
                source=next(f for f in forecasts if f['id']==row['source_forecast_id'])
                self.assertEqual(row['historical_learning'],source['historical_learning'])
            self.assertEqual(read_journal(root),frozen)


class MLCalendarTests(unittest.TestCase):
    def test_stale_missing_or_future_calendar_refreshes_only_supported_leagues(self):
        for stamp in (None,'2026-10-31T12:00:00+00:00','2026-11-03T12:00:00+00:00'):
            with self.subTest(stamp=stamp),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);(root/'data').mkdir()
                if stamp:(root/'data/global_events_v3.json').write_text(json.dumps({'asof_utc':stamp,'events':[{'id':'stale'}]}))
                calls=[]
                def discover(now,**kwargs):
                    calls.append(kwargs)
                    return [{'id':'fresh'}],{'sources_ok':20,'sources_failed':0}
                events,info=prospective_calendar(root,CLOCK,discover)
                self.assertEqual(events,[{'id':'fresh'}]);self.assertEqual(info['source'],'DIRECT_OFFICIAL_REFRESH')
                self.assertEqual(len(calls[0]['sports']),5)

    def test_fresh_calendar_uses_original_snapshot_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            path=root/'data/global_events_v3.json'
            path.write_text(json.dumps({'asof_utc':CLOCK.isoformat(),'events':[{'id':'fresh'}]}))
            frozen=path.read_bytes()
            events,info=prospective_calendar(root,CLOCK,lambda *_:self.fail('Unnecessary refresh'))
            self.assertEqual(events,[{'id':'fresh'}]);self.assertEqual(info['source'],'FRESH_GLOBAL_CALENDAR')
            self.assertEqual(path.read_bytes(),frozen)
