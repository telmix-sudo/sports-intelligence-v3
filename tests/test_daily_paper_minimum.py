import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from sports_intelligence.custom_strategies import ALL_NAMES
from sports_intelligence.strategy_registry import ensure_registry
from sports_intelligence.selection_experiment import SelectionExperiment
from sports_intelligence.daily_paper_minimum import run_daily_paper_minimum,PROFILES,EXPERIMENTAL,VERSION,candidate,GUARDED_PROFILE
from sports_intelligence.portfolio_controls import PaperRiskBook,MAX_SHARED_PREDICTION_OPEN_EUR,record_signal
from sports_intelligence.research_pipeline import read_journal,reconcile,paper_state,settlement_outcome
from sports_intelligence.strategy_lab import build_accounts
from sports_intelligence.historical_learning import HistoricalLearning
from sports_intelligence.admission_validation import AdmissionValidation
from test_selection_experiment import quote,event

UTC=dt.timezone.utc
NOW=dt.datetime(2026,11,4,18,tzinfo=UTC)


def fixtures(root,now=NOW,count=28,market='ML'):
    experiment=SelectionExperiment(root,now,'fixed-code')
    events=[]
    for n in range(count):
        e=event(now,n+1,sport='nba' if market=='ML' else 'epl');e['home']='Home '+str(n);e['away']='Away '+str(n)
        q=quote(now,market)
        p=[.45,.55]
        if market=='1X2':q.update(decimal=[2.5,3.4,3.3],no_vig=[.4,.3,.3],bookmakers=['A','B','B'],best_book_updates=[now.isoformat()]*3);p=[.45,.25,.3]
        experiment.observe('SOURCE_MODEL',e,p,q,sample_min=20)
        events.append(e)
    return events,experiment


def run(root,events,experiment,registry,now=NOW,risk=None):
    return run_daily_paper_minimum(root,events,list(experiment.current_forecasts.values()),now,registry,
                                 risk or PaperRiskBook(root),clock=lambda:now)


class DailyPaperMinimumTests(unittest.TestCase):
    def test_unvalidated_gap_and_opposite_never_override_market_favourite_daily_fill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);events,x=fixtures(root,count=1)
            f=next(iter(x.current_forecasts.values()))
            # Both the largest gap and the model favourite favour an outsider.
            f.update(probabilities=[.8,.2],market_no_vig=[.3,.7],odds=[3.,1.4])
            c=candidate(f,events[0],NOW,GUARDED_PROFILE)
            self.assertEqual(c['policy'],'MARKET_FAVOURITE');self.assertEqual(c['i'],1)
            self.assertEqual(c['p'],[.8,.2])  # Never rewrite the source probabilities.
            self.assertEqual(c['trace']['arms']['VALUE_GAP']['selection_index'],0)
            f['historical_learning']={'status':'ACTIVE','forward_validation':{'eligible':True,'n':100,'dates':13,'market_n':100,'market_dates':14}}
            self.assertEqual(candidate(f,events[0],NOW,GUARDED_PROFILE)['i'],1)
            f['historical_learning']['forward_validation']['dates']=14
            self.assertEqual(candidate(f,events[0],NOW,GUARDED_PROFILE)['policy'],'MODEL_FAVOURITE')
            self.assertEqual(candidate(f,events[0],NOW,GUARDED_PROFILE)['i'],0)

    def test_registered_statistic_rule_retains_sport_and_exact_period(self):
        from sports_intelligence.daily_paper_minimum import market_rules
        for sport in ('nba','nhl','nfl','mlb'):
            self.assertEqual(market_rules({'market':'STAT_EXAMPLE','sport':sport},{'period':'FULL_TIME'}),
                             sport.upper()+'_FULL_TIME_EXPLICIT_MARKET_SPEC')
            self.assertEqual(market_rules({'market':'ML','sport':sport},None),'FULL_GAME_INCLUDING_OVERTIME_OR_EXTRA_INNINGS')
        self.assertEqual(market_rules({'market':'1X2','sport':'epl'},None),'REGULATION_90_MINUTES_EXPLICIT_MARKET')

    def test_every_wallet_reaches_five_with_genuine_nonzero_stakes_without_passing_value_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);registry=ensure_registry(root,NOW,ALL_NAMES);events,x=fixtures(root)
            report=run(root,events,x,registry)
            self.assertTrue(report['minimum_met']);self.assertEqual(len(report['wallets']),13)
            self.assertTrue(all(w['entries']==5 for w in report['wallets'].values()))
            self.assertEqual(sum(report['new_entries'].values()),65)
            risk=PaperRiskBook(root);self.assertAlmostEqual(risk.total_exposure,32.5)
            self.assertTrue(all(v<=MAX_SHARED_PREDICTION_OPEN_EUR for v in risk.signal_exposure.values()))
            self.assertEqual(AdmissionValidation(root,NOW).report()['validated_streams'],0)
            for path in (root/'data/strategy_ledgers').glob('*.jsonl'):
                rows=[json.loads(line) for line in path.read_text().splitlines()]
                self.assertEqual(len({r['event_id'] for r in rows}),5)
                self.assertTrue(all(r['stake_eur']==.5 and r['daily_paper_exploration_policy']==VERSION and not r['admission_evidence']['validated_edge'] for r in rows))
            reference=[json.loads(line) for line in (root/'data/strategy_ledgers/market_daily_reference.jsonl').read_text().splitlines()]
            self.assertTrue(all(r['probabilities']==r['no_vig'] and r['model_version']=='MARKET_REFERENCE' for r in reference))
            self.assertTrue(all(r['selection_policy']=='MARKET_FAVOURITE' for r in reference))

    def test_existing_records_balances_and_daily_success_are_preserved_on_repeated_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);registry=ensure_registry(root,NOW,ALL_NAMES);events,x=fixtures(root)
            path=root/'data/strategy_ledgers/elo_standard.jsonl';path.parent.mkdir(parents=True)
            legacy={'id':'legacy','strategy':'ELO_STANDARD','event_id':'nba:old','forecast_utc':NOW.isoformat(),
                    'start_utc':(NOW-dt.timedelta(hours=1)).isoformat(),'status':'SETTLED','pnl_eur':-17.25,
                    'stake_eur':17.25,'market':'ML','probabilities':[.5,.5],'odds':[2.,2.],'selection_index':0,'observed_outcome':1,'brier':.5,'log_loss':.693}
            path.write_text(json.dumps(legacy)+'\n')
            report=run(root,events,x,registry);self.assertEqual(report['wallets']['ELO_STANDARD']['new_entries'],4)
            after=path.read_bytes();journal=(root/'data/market_research_v3.jsonl').read_bytes()
            report=run(root,events,x,registry)
            self.assertFalse(report['new_entries']);self.assertEqual(path.read_bytes(),after)
            self.assertEqual((root/'data/market_research_v3.jsonl').read_bytes(),journal)
            self.assertEqual(json.loads(path.read_text().splitlines()[0]),legacy)
            accounts,_=build_accounts(root,{},NOW)
            self.assertEqual(accounts['ELO_STANDARD']['bankroll_eur'],982.75)

    def test_missing_stale_future_or_mismatched_inputs_cannot_manufacture_five(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);registry=ensure_registry(root,NOW,ALL_NAMES);events,x=fixtures(root,count=2)
            snapshots=list(x.current_forecasts.values());snapshots[0]['book_quote_updates_utc']=[(NOW+dt.timedelta(minutes=1)).isoformat()]*2
            snapshots[1]['forecast_utc']=(NOW-dt.timedelta(minutes=16)).isoformat()
            report=run_daily_paper_minimum(root,events,snapshots,NOW,registry,PaperRiskBook(root))
            self.assertFalse(report['minimum_met']);self.assertFalse(report['new_entries'])
            self.assertTrue(all(v['remaining']==5 for v in report['wallets'].values()))
            self.assertFalse((root/'data/strategy_ledgers').exists())

    def test_global_limits_and_ordinary_one_euro_floor_stay_in_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);registry=ensure_registry(root,NOW,ALL_NAMES);events,x=fixtures(root)
            risk=PaperRiskBook(root);self.assertEqual(risk.reserve('a',.5),0)
            self.assertEqual(risk.reserve('a',.5,'signal',exploratory=True),.5)
            risk.total_exposure=240
            report=run(root,events,x,registry,risk=risk)
            self.assertFalse(report['new_entries']);self.assertFalse(report['minimum_met'])
            blocked=PaperRiskBook(root,allow_new_entries=False)
            self.assertFalse(run(root,events,x,registry,risk=blocked)['new_entries'])

    def test_refresh_after_dedup_keeps_the_old_comparison_but_exposes_new_current_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);events,x=fixtures(root,count=1)
            before=(root/'data/selection_experiment_v3.jsonl').read_bytes()
            later=NOW+dt.timedelta(minutes=1);x=SelectionExperiment(root,later,'fixed-code')
            self.assertFalse(x.observe('SOURCE_MODEL',events[0],[.4,.6],quote(later),sample_min=20))
            self.assertEqual((root/'data/selection_experiment_v3.jsonl').read_bytes(),before)
            f=next(iter(x.current_forecasts.values()))
            self.assertEqual(f['forecast_utc'],later.isoformat());self.assertEqual(f['probabilities'],[.4,.6])

    def test_all_native_1x2_profiles_settle_correctly_and_keep_the_actual_source_family(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);registry=ensure_registry(root,NOW,ALL_NAMES);events,x=fixtures(root,market='1X2')
            report=run(root,events,x,registry);self.assertTrue(report['minimum_met'])
            original=PaperRiskBook(root)
            self.assertTrue(all('|SOURCE_MODEL|' in key or '|MARKET_REFERENCE|' in key for key in original.signal_exposure))
            self.assertEqual(settlement_outcome({'market':'1X2'},{'outcome':2}),2)
            self.assertEqual(settlement_outcome({'market':'1X2'},{'outcome':1}),1)
            forecasts=[r for r in read_journal(root) if r['kind']=='FORECAST' and r['stake_eur']>0]
            after=NOW+dt.timedelta(hours=3)
            finals={e['id']:{'home':0,'away':1,'outcome':2} for e in events}
            build_accounts(root,finals,after)
            # A compact synthetic scoreboard resolves all journal forecasts.
            payload={'events':[{'id':e['id'].split(':')[1],'date':e['start_utc'],
                'status':{'type':{'completed':True,'state':'post'}},'competitions':[{'competitors':[
                {'homeAway':'home','team':{'displayName':e['home']},'score':'0'},
                {'homeAway':'away','team':{'displayName':e['away']},'score':'1'}]}]} for e in events]}
            reconcile(root,read_journal(root),[],after,result_fetcher=lambda *_:(payload,{}))
            settlements=[r for r in read_journal(root) if r['kind']=='SETTLEMENT']
            self.assertEqual(len(settlements),5);self.assertTrue(all(r['outcome']==2 for r in settlements))
            learner=HistoricalLearning(root,NOW+dt.timedelta(days=1))
            self.assertTrue(any(k.startswith('SOURCE_MODEL|epl|1X2') for k in learner.samples))

    def test_midnight_and_actual_kickoff_recheck_stop_late_registrations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);registry=ensure_registry(root,NOW,ALL_NAMES);events,x=fixtures(root)
            after=NOW+dt.timedelta(hours=3)
            report=run_daily_paper_minimum(root,events,list(x.current_forecasts.values()),NOW,registry,PaperRiskBook(root),clock=lambda:after)
            self.assertFalse(report['new_entries'])
            after=NOW+dt.timedelta(days=1)
            report=run_daily_paper_minimum(root,events,list(x.current_forecasts.values()),NOW,registry,PaperRiskBook(root),clock=lambda:after)
            self.assertFalse(report['new_entries'])
            self.assertIn('capture_clock_or_local_day_changed',report['blocks'])
