import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from sports_intelligence.ml_lab import (
    build_features, calibrated, chronological_split, scores, train,
    update_observations, timed_market, FEATURES,
)

UTC=dt.timezone.utc
HAS_ML=all(importlib.util.find_spec(x) for x in ('numpy','lightgbm','optuna'))


def matches(days=240):
    result=[]
    start=dt.date(2023,1,1)
    for n in range(days):
        for home,away,shift in [('a','b',0),('c','d',1)]:
            outcome=(n+shift)%3
            result.append({'league':'epl','date':start+dt.timedelta(days=n),'home':home,'away':away,
                           'goals_home':2 if outcome==0 else 1 if outcome==1 else 0,
                           'goals_away':2 if outcome==2 else 1 if outcome==1 else 0,'odds':None})
    return result


class FeatureIntegrityTests(unittest.TestCase):
    def test_no_current_day_or_same_day_result_leakage(self):
        history=matches(30); cutoff=dt.date(2023,2,1)
        original,_=build_features({'epl':history},cutoff)
        day=history[-1]['date']
        changed=[dict(m,goals_home=10,goals_away=0) if m['date']==day else dict(m) for m in history]
        altered,_=build_features({'epl':changed},cutoff)
        self.assertEqual([r['x'] for r in original if r['date']==day.isoformat()],
                         [r['x'] for r in altered if r['date']==day.isoformat()])
        before,_=build_features({'epl':history},day)
        self.assertTrue(all(r['date']<day.isoformat() for r in before))
        self.assertEqual(len(original[0]['x']),len(FEATURES))

    def test_same_team_in_another_league_does_not_share_state(self):
        rows,_=build_features({'epl':matches(30),'la_liga':[dict(m,league='la_liga',goals_home=12) for m in matches(30)]},dt.date(2024,1,1))
        epl,_=build_features({'epl':matches(30)},dt.date(2024,1,1))
        self.assertEqual([r['x'] for r in rows if r['league']=='epl'],[r['x'] for r in epl])

    def test_conflicting_finals_are_rejected(self):
        rows=matches(1)
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            build_features({'epl':rows+[dict(rows[0],goals_home=8)]},dt.date(2024,1,1))

    def test_split_never_divides_a_match_day(self):
        rows,_=build_features({'epl':matches()},dt.date(2024,1,1))
        blocks=chronological_split(rows)
        for earlier,later in zip(blocks,blocks[1:]):
            self.assertLess(max(r['date'] for r in earlier),min(r['date'] for r in later))
        self.assertEqual(sum(map(len,blocks)),len(rows))

    def test_probability_calibration_normalizes_without_new_outcomes(self):
        p=calibrated([.7,.2,.1],2)
        self.assertAlmostEqual(sum(p),1)
        self.assertLess(p[0],.7)
        with self.assertRaises(ValueError):calibrated([.5,.5],1)
        with self.assertRaises(ValueError):calibrated([float('nan'),.3,.7],1)

    def test_benchmark_requires_complete_stamped_real_market(self):
        now=dt.datetime(2026,11,1,12,tzinfo=UTC)
        quote={'decimal':[2.1,3.4,3.8],'no_vig':[.5,.3,.2],
               'best_book_updates':[now.isoformat()]*3,'quote_timestamp_available':True,
               'bookmakers':['example']*3,'provider':'Test provider'}
        self.assertEqual(timed_market({'quote':quote},now)['best_book_updates'],quote['best_book_updates'])
        stale=dict(quote,best_book_updates=[(now-dt.timedelta(hours=13)).isoformat()]*3)
        self.assertIsNone(timed_market({'quote':stale},now))
        self.assertIsNone(timed_market({'quote':dict(quote,decimal=[2.,3.])},now))


@unittest.skipUnless(HAS_ML,'Optional ML packages only required in daily ML workflow')
class MLExecutionTests(unittest.TestCase):
    def test_real_training_keeps_final_evaluation_out_of_selection(self):
        rows,_=build_features({'epl':matches()},dt.date(2024,1,1))
        _,_,first=train(rows,trials=2,timeout=30)
        cutoff=first['splits']['final_evaluation']['first_date']
        changed=[dict(r,y=(r['y']+1)%3) if r['date']>=cutoff else r for r in rows]
        _,_,second=train(changed,trials=2,timeout=30)
        self.assertEqual(first['best_parameters'],second['best_parameters'])
        self.assertEqual(first['temperature'],second['temperature'])
        self.assertNotEqual(first['holdout']['candidate'],second['holdout']['candidate'])
        self.assertIsNone(first['historical_roi'])

    def test_observations_immutable_idempotent_zero_stake_and_official_settlement(self):
        from sports_intelligence.ml_lab import FeatureState
        now=dt.datetime(2026,11,1,10,tzinfo=UTC)
        state=FeatureState();state.elo['epl'].played.update(a=10,b=10)
        class Model:
            def predict(self,x):return [[.5,.3,.2] for _ in x]
        event={'id':'epl:1','sport':'epl','home':'a','away':'b','status':'pre',
               'completed':False,'start_utc':(now+dt.timedelta(hours=1)).isoformat(),'final':None}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ml.jsonl'
            result=update_observations(path,[event],Model(),state,1,now,'hash')
            frozen=path.read_bytes()
            self.assertEqual(result['new_forecasts'],1)
            again=update_observations(path,[event],Model(),state,2,now,'changed-model')
            self.assertEqual(again['new_forecasts'],0)
            self.assertEqual(path.read_bytes(),frozen)
            update_observations(path,[event],Model(),state,1,now+dt.timedelta(hours=4),'hash')
            self.assertEqual(path.read_bytes(),frozen,'The clock cannot invent a final')
            final=dict(event,completed=True,status='post',final={'home':2,'away':1})
            result=update_observations(path,[final],Model(),state,1,now+dt.timedelta(hours=4),'hash')
            self.assertEqual(result['new_settlements'],1)
            self.assertTrue(path.read_bytes().startswith(frozen))
            records=[json.loads(x) for x in path.read_text().splitlines()]
            self.assertTrue(all(r['stake_eur']==0 for r in records))
            self.assertEqual(records[-1]['outcome'],0)


if __name__=='__main__':unittest.main()
