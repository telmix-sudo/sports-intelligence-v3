"""Real fail-closed gates with synthetic, genuinely pre-event frozen fixtures."""
import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from sports_intelligence.admission_validation import AdmissionValidation, JOURNAL, frozen_market, validated_learning
from sports_intelligence.historical_learning import HistoricalLearning, forward_gate, VERSION
from sports_intelligence.portfolio_controls import PaperRiskBook, prediction_signal
from sports_intelligence.strategy_lab import open_accounts, make_models, read_jsonl, save_jsonl
from sports_intelligence.research_pipeline import run_research, read_journal, experimental_admission
from sports_intelligence.auxiliary_portfolios import open_auxiliary_markets
from sports_intelligence.strategy_registry import ensure_registry
from sports_intelligence.custom_strategies import ALL_NAMES
from test_research_integration import EVENT, NOW, models, football_history, odds_rows
from sports_intelligence.odds_provider import market_quotes_for_event
from sports_intelligence.metrics import brier, log_loss

UTC=dt.timezone.utc


def frozen_record(i=0,**changes):
    start=NOW-dt.timedelta(days=25-i%20)
    row={'id':'SYNTHETIC-FROZEN-'+str(i),'strategy':'ELO_STANDARD','event_id':'epl:SYNTHETIC-'+str(i),
         'sport':'epl','home':'Arsenal','away':'Chelsea','market':'1X2','status':'SETTLED',
         'start_utc':start.isoformat(),'forecast_utc':(start-dt.timedelta(hours=2)).isoformat(),
         'settlement_utc':(start+dt.timedelta(hours=3)).isoformat(),'observed_outcome':0,
         'probabilities':[.98,.01,.01],'no_vig':[.2,.4,.4],'odds':[5.,2.5,2.5],
         'price_source':'SYNTHETIC','bookmakers':['SYNTHETIC_A','SYNTHETIC_B','SYNTHETIC_A'],
         'book_quote_updates_utc':[(start-dt.timedelta(hours=2)).isoformat()]*3,'stake_eur':1.,'pnl_eur':4.}
    row.update(changes);return row


def primary_event():
    event=copy.deepcopy(EVENT)
    event['quote']={'market':'1X2','decimal':[2.,3.7,4.5],'no_vig':[.5,.3,.2],
                    'quote_timestamp_available':True,'bookmaker_count':2,'provider':'SYNTHETIC',
                    'bookmakers':['SYNTHETIC_A']*3,'best_book_updates':[NOW.isoformat()]*3}
    league=make_models()
    for model in league.values():
        if model is not None:model.played.update(arsenal=20,chelsea=20)
    for name in ('ELO_STANDARD','ELO_DYNAMIC'):league[name].ratings.update(arsenal=1700,chelsea=1450)
    return event,{'epl':league}


class AdmissionGateTests(unittest.TestCase):
    def test_no_market_evidence_collects_zero_stake_without_creating_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);e,models=primary_event();diagnostics={}
            _,added=open_accounts(root,models,[e],NOW,diagnostics=diagnostics)
            self.assertEqual(added,{})
            self.assertGreater(diagnostics['awaiting_prospective_market_validation'],0)
            gate=AdmissionValidation(root,NOW)
            self.assertGreater(gate.report()['observations_total'],0)
            self.assertTrue(all(r['stake_eur']==0 for r in gate.rows))
            self.assertTrue(all(not read_jsonl(p) for p in (root/'data/strategy_ledgers').glob('*.jsonl')))
            frozen=(root/JOURNAL).read_bytes()
            open_accounts(root,models,[e],NOW+dt.timedelta(minutes=1))
            self.assertEqual((root/JOURNAL).read_bytes(),frozen)
            gate.reconcile({e['id']:{'outcome':0}})  # Future kickoff cannot settle.
            self.assertEqual((root/JOURNAL).read_bytes(),frozen)
            after=AdmissionValidation(root,NOW+dt.timedelta(hours=8))
            self.assertTrue(after.pending())
            after.reconcile({})
            self.assertEqual((root/JOURNAL).read_bytes(),frozen)
            after.reconcile({e['id']:{'outcome':0}})
            self.assertTrue((root/JOURNAL).read_bytes().startswith(frozen))
            self.assertTrue(all(r.get('pnl_eur',0)==0 for r in after.rows))
            # Results recorded today do not enter today's decision or learning.
            self.assertFalse(AdmissionValidation(root,NOW+dt.timedelta(hours=9)).groups)
            tomorrow=NOW+dt.timedelta(days=1)
            self.assertTrue(AdmissionValidation(root,tomorrow).groups)
            self.assertTrue(HistoricalLearning(root,tomorrow).samples)

    def test_100_distinct_frozen_market_events_can_enable_a_qualified_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'data/strategy_ledgers/elo_standard.jsonl'
            records=[frozen_record(i) for i in range(100)];save_jsonl(path,records)
            self.assertTrue(AdmissionValidation(root,NOW).evidence('ELO_STANDARD','epl','1X2')['eligible_for_paper'])
            e,models=primary_event();_,added=open_accounts(root,models,[e],NOW)
            self.assertEqual(added.get('ELO_STANDARD'),1)
            after=read_jsonl(path);self.assertEqual(after[:100],records)
            self.assertTrue(after[-1]['admission_evidence']['eligible_for_paper'])

    def test_duplicates_same_day_future_or_bad_quote_clocks_cannot_inflate_N(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'data/strategy_ledgers/elo_standard.jsonl'
            save_jsonl(path,[frozen_record(i,event_id='epl:SYNTHETIC-SAME') for i in range(100)])
            gate=AdmissionValidation(root,NOW);self.assertEqual(gate.evidence('ELO_STANDARD','epl','1X2')['n_distinct_events'],1)
            records=[frozen_record(i,start_utc=(NOW-dt.timedelta(days=2)).isoformat(),
                     forecast_utc=(NOW-dt.timedelta(days=2,hours=2)).isoformat(),
                     book_quote_updates_utc=[(NOW-dt.timedelta(days=2,hours=2)).isoformat()]*3,
                     settlement_utc=(NOW-dt.timedelta(days=2)+dt.timedelta(hours=3)).isoformat()) for i in range(100)]
            save_jsonl(path,records)
            evidence=AdmissionValidation(root,NOW).evidence('ELO_STANDARD','epl','1X2')
            self.assertEqual(evidence['dates'],1);self.assertFalse(evidence['eligible_for_paper'])
            save_jsonl(path,[frozen_record(i,settlement_utc=NOW.isoformat()) for i in range(100)])
            self.assertFalse(AdmissionValidation(root,NOW).groups)
            self.assertIsNone(frozen_market(frozen_record(book_quote_updates_utc=[NOW.isoformat()]*3)))
            self.assertIsNone(frozen_market(frozen_record(book_quote_updates_utc=[])))

    def test_improving_a_bad_model_does_not_pass_if_market_is_better(self):
        pairs=[{'date':str((NOW-dt.timedelta(days=1+i%20)).date()),'base':[.8,.2],
                'candidate':[.5,.5],'market':[.1,.9],'y':1} for i in range(100)]
        self.assertFalse(forward_gate(pairs)['eligible'])
        self.assertGreater(forward_gate(pairs)['market_brier_difference_upper'],0)
        self.assertFalse(forward_gate([dict(r,market=None) for r in pairs])['eligible'])
        good=forward_gate([dict(r,market=[.7,.3]) for r in pairs])
        self.assertTrue(good['eligible'])
        self.assertFalse(validated_learning({'status':'ACTIVE'}))
        self.assertTrue(validated_learning({'status':'ACTIVE','forward_validation':good}))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(AdmissionValidation(Path(tmp),NOW).decision('m','epl','1X2',{'status':'ACTIVE'})['eligible_for_paper'])

    def test_historical_score_success_alone_does_not_admit_research_or_cached_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);h=football_history();e=copy.deepcopy(EVENT)
            e['market_quotes']=market_quotes_for_event(e,odds_rows(),NOW)
            report=run_research(root,[e],models(h),NOW,PaperRiskBook(root),football_history=h)
            self.assertFalse(report['experimental_account']['created'])
            forecasts=[r for r in read_journal(root) if r['kind']=='FORECAST']
            self.assertTrue(forecasts);self.assertTrue(all(r['stake_eur']==0 for r in forecasts))
            registry=ensure_registry(root,NOW,ALL_NAMES)
            # Simulate an old cached approval: the auxiliary opener still checks
            # CURRENT independent market evidence and cannot bypass the gate.
            path=root/'data/market_research_v3.jsonl';rows=read_journal(root)
            for r in rows:
                if r['kind']=='FORECAST':r['admission_blocks']=[];r['admission_evidence']['eligible_for_small_paper']=True
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            self.assertEqual(open_auxiliary_markets(root,[e],NOW,registry,PaperRiskBook(root)),{})

    def test_real_forward_market_journal_enables_research_and_bounded_auxiliary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);rows=[]
            for i in range(100):
                record=frozen_record(i,market='ASIAN_HOME_MINUS_0_5',model_version='MULTIMARKET-1.0.0-goals',
                                     probabilities=[.98,.02],no_vig=[.2,.8],odds=[5.,1.25],
                                     book_quote_updates_utc=frozen_record(i)['book_quote_updates_utc'][:2],stake_eur=0.)
                record.update(kind='FORECAST',selection_index=0,market_no_vig=record.pop('no_vig'))
                record.pop('status');record.pop('settlement_utc')
                rows.extend([record,{'id':'SET-'+record['id'],'kind':'SETTLEMENT','forecast_id':record['id'],
                    'event_id':record['event_id'],'status':'SETTLED','settlement_utc':frozen_record(i)['settlement_utc'],
                    'outcome':0,'pnl_eur':0.,'brier':brier(record['probabilities'],0),
                    'log_loss':log_loss(record['probabilities'],0),'market_brier':brier(record['market_no_vig'],0),
                    'market_log_loss':log_loss(record['market_no_vig'],0)}])
            path=root/'data/market_research_v3.jsonl';path.parent.mkdir(parents=True)
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows));frozen=path.read_bytes()
            h=football_history();e=copy.deepcopy(EVENT);e['market_quotes']=market_quotes_for_event(e,odds_rows(),NOW)
            report=run_research(root,[e],models(h),NOW,PaperRiskBook(root),football_history=h)
            self.assertEqual(report['experimental_account']['positions'],1)
            self.assertTrue(path.read_bytes().startswith(frozen))
            registry=ensure_registry(root,NOW,ALL_NAMES)
            added=open_auxiliary_markets(root,[e],NOW,registry,PaperRiskBook(root))
            self.assertEqual(sum(added.values()),1)
            self.assertNotIn('MARKET_DAILY_REFERENCE',added)
            self.assertTrue(all(v<=2.5 for v in PaperRiskBook(root).summary()['shared_prediction_open_eur'].values()))

    def test_shared_signal_limit_survives_reload_and_never_changes_old_stakes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);signal=prediction_signal('nba:SYNTHETIC','ML','shared-model',0)
            risk=PaperRiskBook(root)
            self.assertEqual(risk.reserve('nba:SYNTHETIC',1,signal),1)
            self.assertEqual(risk.reserve('nba:SYNTHETIC',1,signal),1)
            self.assertEqual(risk.reserve('nba:SYNTHETIC',1,signal),0)
            row={'id':'OLD','event_id':'nba:SYNTHETIC','market':'ML','model_version':'shared-model',
                 'selection_index':0,'stake_eur':8.,'status':'OPEN'}
            path=root/'data/strategy_ledgers/old.jsonl';save_jsonl(path,[row]);frozen=path.read_bytes()
            loaded=PaperRiskBook(root)
            self.assertEqual(loaded.reserve('nba:SYNTHETIC',1,signal),0)
            self.assertEqual(path.read_bytes(),frozen)
            self.assertEqual(loaded.summary()['max_event_open_eur'],36)
            self.assertEqual(loaded.summary()['max_total_open_eur'],240)
