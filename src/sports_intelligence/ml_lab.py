"""Optional, zero-stake Optuna/LightGBM research with disjoint temporal splits.

Only this module's training path imports third-party ML packages. Core V3
workflows and their wallets do not depend on it. No historical odds are invented.
"""
from __future__ import annotations

from collections import defaultdict, deque
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

from .football import FootballElo
from .poisson import FootballPoisson
from .metrics import brier, log_loss
from .strategy_lab import collect_openfootball, LEAGUE_CODES, team_name
from .odds_provider import parse_utc

UTC = dt.timezone.utc
VERSION = 'LIGHTGBM_OPTUNA_0.1'
LEAGUES = tuple(LEAGUE_CODES)
FEATURES = ('elo_home','elo_draw','elo_away','poisson_home','poisson_draw',
            'poisson_away','home_played','away_played','home_form_points',
            'away_form_points','home_scored','away_scored','home_conceded',
            'away_conceded') + tuple('league_'+s for s in LEAGUES)


class FeatureState:
    def __init__(self):
        self.elo = {s: FootballElo() for s in LEAGUES}
        self.poisson = {s: FootballPoisson() for s in LEAGUES}
        self.form = defaultdict(lambda: deque(maxlen=8))

    def vector(self, league, home, away):
        home, away = team_name(home), team_name(away)
        elo, poisson = self.elo[league], self.poisson[league]
        ep, pp = elo.predict(home,away), poisson.predict(home,away)
        def means(team):
            rows = self.form[(league,team)]
            return tuple(sum(r[i] for r in rows)/len(rows) if rows else default
                         for i,default in enumerate((1.35,1.35,1.35)))
        hp,hg,hc = means(home); ap,ag,ac = means(away)
        return [*ep,*pp,min(elo.played.get(home,0),100),min(elo.played.get(away,0),100),
                hp,ap,hg,ag,hc,ac,*[float(s==league) for s in LEAGUES]]

    def update(self, m):
        league=m['league']; self.elo[league].update(m); self.poisson[league].update(m)
        h,a=m['goals_home'],m['goals_away']
        self.form[(league,m['home'])].append((3 if h>a else 1 if h==a else 0,h,a))
        self.form[(league,m['away'])].append((3 if a>h else 1 if h==a else 0,a,h))


def build_features(grouped, cutoff):
    """Batch all same-date predictions before applying any of that day's scores."""
    unique={}
    for league,matches in grouped.items():
        if league not in LEAGUES:continue
        for raw in matches:
            m=dict(raw,league=league,home=team_name(raw['home']),away=team_name(raw['away']))
            if m['date']>=cutoff:continue
            key=(league,m['date'],m['home'],m['away'])
            if key in unique and any(unique[key][f]!=m[f] for f in ('goals_home','goals_away')):
                raise ValueError('Conflicting historical final scores')
            unique[key]=m
    days=defaultdict(list)
    for m in unique.values():days[m['date']].append(m)
    state=FeatureState(); rows=[]
    for day in sorted(days):
        matches=sorted(days[day],key=lambda m:(m['league'],m['home'],m['away']))
        for m in matches:
            vec=state.vector(m['league'],m['home'],m['away'])
            if min(vec[6:8])>=8:
                rows.append({'date':day.isoformat(),'league':m['league'],
                             'home':m['home'],'away':m['away'],'x':vec,
                             'y':0 if m['goals_home']>m['goals_away'] else 1 if m['goals_home']==m['goals_away'] else 2})
        for m in matches:state.update(m)
    return rows,state


def chronological_split(rows):
    dates=sorted({r['date'] for r in rows})
    if len(dates)<40:raise ValueError('Need at least 40 distinct completed match dates')
    cuts=[dates[int(len(dates)*f)] for f in (.55,.75,.85)]
    blocks=[[],[],[],[]]
    for r in rows:
        block=sum(r['date']>=cut for cut in cuts);blocks[block].append(r)
    if min(map(len,blocks))<30:raise ValueError('Insufficient chronological split size')
    return blocks


def calibrated(probabilities, temperature):
    if not math.isfinite(temperature) or temperature<=0:raise ValueError('Invalid temperature')
    if len(probabilities)!=3 or any(not math.isfinite(float(p)) or p<0 for p in probabilities) or sum(probabilities)<=0:
        raise ValueError('Invalid three-way probabilities')
    values=[max(1e-12,float(p))**(1/temperature) for p in probabilities]
    total=sum(values)
    if len(values)!=3 or not math.isfinite(total):raise ValueError('Invalid three-way probabilities')
    return [p/total for p in values]


def timed_market(event,now):
    q=event.get('quote') or {}
    try:
        if not q.get('quote_timestamp_available'):return None
        if len(q['decimal'])!=3 or len(q['no_vig'])!=3 or len(q['best_book_updates'])!=3:return None
        if not all(math.isfinite(float(p)) and p>1 for p in q['decimal']):return None
        if not all(math.isfinite(float(p)) and 0<p<1 for p in q['no_vig']):return None
        if abs(sum(q['no_vig'])-1)>1e-5:return None
        if not all(dt.timedelta(0)<=now-parse_utc(t)<=dt.timedelta(hours=12) for t in q['best_book_updates']):return None
        if not q.get('provider') or not q.get('bookmakers'):return None
        return {k:q[k] for k in ('decimal','no_vig','best_book_updates','bookmakers','provider')}
    except (KeyError,ValueError,TypeError):return None


def scores(probabilities, rows):
    if len(probabilities)!=len(rows) or not rows:raise ValueError('Evaluation count mismatch')
    n=len(rows)
    return {'n':n,'log_loss':round(sum(log_loss(p,r['y']) for p,r in zip(probabilities,rows))/n,7),
            'brier':round(sum(brier(p,r['y']) for p,r in zip(probabilities,rows))/n,7)}


def train(rows, trials=12, timeout=180):
    import importlib.metadata
    import lightgbm as lgb
    import numpy as np
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    training,tuning,calibration,holdout=chronological_split(rows)
    if {r['y'] for r in training}!={0,1,2}:raise ValueError('Training lacks outcome classes')
    def matrix(block):return np.asarray([r['x'] for r in block],dtype=float)
    train_x,train_y=matrix(training),np.asarray([r['y'] for r in training])
    def fit(params, block=None):
        x,y=(train_x,train_y) if block is None else (matrix(block),np.asarray([r['y'] for r in block]))
        p=dict(params);iterations=p.pop('num_boost_round')
        p.update(objective='multiclass',num_class=3,metric='multi_logloss',verbosity=-1,
                 num_threads=2,seed=20261101,deterministic=True,force_col_wise=True)
        return lgb.train(p,lgb.Dataset(x,label=y,feature_name=list(FEATURES)),num_boost_round=iterations)
    def objective(trial):
        params={'learning_rate':trial.suggest_float('learning_rate',.02,.12,log=True),
                'num_leaves':trial.suggest_int('num_leaves',7,31),
                'min_data_in_leaf':trial.suggest_int('min_data_in_leaf',30,150),
                'lambda_l2':trial.suggest_float('lambda_l2',.1,20,log=True),
                'num_boost_round':trial.suggest_int('num_boost_round',30,120)}
        model=fit(params)
        return scores(model.predict(matrix(tuning)),tuning)['log_loss']
    study=optuna.create_study(direction='minimize',sampler=optuna.samplers.TPESampler(seed=20261101,n_startup_trials=5))
    study.optimize(objective,n_trials=max(1,min(int(trials),30)),timeout=max(1,min(int(timeout),300)),n_jobs=1)
    best=dict(study.best_params)
    # Refit on the training and tuning blocks. Calibration remains unseen.
    chosen=fit(best,training+tuning)
    raw_cal=chosen.predict(matrix(calibration))
    temperature=min((.75,1.,1.25,1.5,2.,3.),key=lambda t:scores([calibrated(p,t) for p in raw_cal],calibration)['log_loss'])
    held=[calibrated(p,temperature) for p in chosen.predict(matrix(holdout))]
    result={'candidate':scores(held,holdout),
            'elo':scores([r['x'][:3] for r in holdout],holdout),
            'poisson':scores([r['x'][3:6] for r in holdout],holdout)}
    per_league={}
    for league in LEAGUES:
        indices=[i for i,r in enumerate(holdout) if r['league']==league]
        if indices:per_league[league]=scores([held[i] for i in indices],[holdout[i] for i in indices])
    # This final fit is for FUTURE observations only, never historical evaluation.
    final=fit(best,rows)
    info={'packages':{p:importlib.metadata.version(p) for p in ('lightgbm','optuna','numpy')},
          'seed':20261101,'trials_completed':len(study.trials),'best_parameters':best,
          'temperature':temperature,'holdout':result,'holdout_by_league':per_league,
          'splits':{name:{'n':len(block),'first_date':block[0]['date'],'last_date':block[-1]['date']}
                    for name,block in zip(('training','tuning','calibration','final_evaluation'),(training,tuning,calibration,holdout))},
          'feature_names':list(FEATURES),'model_sha256':hashlib.sha256(final.model_to_string().encode()).hexdigest(),
          'historical_roi':None,'historical_market_benchmark':None,
          'validation_caveat':'A chronological holdout measures this experiment; repeated daily reuse is not a new independent test. Score-only data cannot prove profit.'}
    return final,temperature,info


def update_observations(path,events,model,state,temperature,now,model_hash):
    """Append immutable probability observations; never create a funded account."""
    import numpy as np
    rows=[json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    ids={r['id'] for r in rows}; additions=[]
    if len(ids)!=len(rows):raise ValueError('Duplicate ML journal IDs')
    forecasts={r['event_id']:r for r in rows if r['kind']=='FORECAST'}
    settled={r['forecast_id'] for r in rows if r['kind']=='SETTLEMENT'}
    for event in events:
        prior=forecasts.get(event['id'])
        if prior and prior['id'] not in settled and event.get('completed') and event.get('final'):
            if parse_utc(prior['start_utc'])>now:continue
            final=event['final'];outcome=0 if final['home']>final['away'] else 1 if final['home']==final['away'] else 2
            entry={'id':'settlement:'+prior['id'],'kind':'SETTLEMENT','forecast_id':prior['id'],
                   'recorded_at_utc':now.isoformat(),'outcome':outcome,'brier':brier(prior['probabilities'],outcome),
                   'log_loss':log_loss(prior['probabilities'],outcome),'stake_eur':0.,'pnl_eur':0.,
                   'source':'ESPN_CONFIRMED_FINAL'}
            if prior.get('market'):
                entry.update(market_brier=brier(prior['market']['no_vig'],outcome),
                             market_log_loss=log_loss(prior['market']['no_vig'],outcome))
            if entry['id'] not in ids:additions.append(entry);ids.add(entry['id']);settled.add(prior['id'])
        league=event.get('sport')
        if league not in LEAGUES or event.get('status')!='pre' or event['id'] in forecasts:continue
        start=parse_utc(event['start_utc'])
        if not now<start<=now+dt.timedelta(hours=48):continue
        vec=state.vector(league,event['home'],event['away'])
        if min(vec[6:8])<8:continue
        p=calibrated(model.predict(np.asarray([vec]))[0],temperature)
        fid='ml:'+hashlib.sha256((VERSION+'|'+event['id']).encode()).hexdigest()[:24]
        entry={'id':fid,'kind':'FORECAST','event_id':event['id'],'sport':league,
               'home':event['home'],'away':event['away'],'start_utc':start.isoformat(),
               'recorded_at_utc':now.isoformat(),'model_version':VERSION,'model_sha256':model_hash,
               'probabilities':p,'stake_eur':0.,'paper_execution_enabled':False}
        entry['market']=timed_market(event,now)
        if fid not in ids:additions.append(entry);ids.add(fid);forecasts[event['id']]=entry
    if additions:
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('a') as out:
            for row in additions:out.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
    all_rows=rows+additions
    completed=[r for r in all_rows if r['kind']=='SETTLEMENT']
    benchmark=[r for r in completed if 'market_brier' in r]
    return {'new_forecasts':sum(r['kind']=='FORECAST' for r in additions),
            'new_settlements':sum(r['kind']=='SETTLEMENT' for r in additions),
            'forecasts_total':sum(r['kind']=='FORECAST' for r in all_rows),
            'settled_total':sum(r['kind']=='SETTLEMENT' for r in all_rows),
            'papers_with_stake':0,'profit_eur':None,
            'settled_scoring':{'n':len(completed),'brier':sum(r['brier'] for r in completed)/len(completed),
                               'log_loss':sum(r['log_loss'] for r in completed)/len(completed)} if completed else None,
            'market_benchmark_n':len(benchmark)}


def run(root,now,trials=12,timeout=180,collector=collect_openfootball):
    if now.tzinfo is None:raise ValueError('Timezone required')
    now=now.astimezone(UTC)
    grouped,provenance=collector(now.date())
    rows,state=build_features(grouped,now.date())
    report={'asof_utc':now.isoformat(),'model_version':VERSION,'mode':'ZERO_STAKE_RESEARCH',
            'live_bets_allowed':False,'stake_enabled':False,'historical_rows':len(rows),
            'provenance':provenance,'status':'INSUFFICIENT_HISTORY'}
    if len(rows)>=1000:
        model,temp,info=train(rows,trials,timeout)
        report.update(status='EXPERIMENTAL',evaluation=info)
        path=root/'data/global_events_v3.json'
        if path.exists():
            calendar=json.loads(path.read_text());age=now-parse_utc(calendar['asof_utc'])
            if dt.timedelta(0)<=age<=dt.timedelta(hours=4):
                report['prospective']=update_observations(root/'data/ml_observations_v3.jsonl',calendar['events'],model,state,temp,now,info['model_sha256'])
            else:report['prospective']={'status':'STALE_CALENDAR_NO_NEW_OBSERVATIONS'}
        else:report['prospective']={'status':'CALENDAR_UNAVAILABLE'}
    from .global_discovery import _atomic
    _atomic(root/'reports/ml_lab_latest.json',report)
    return report


if __name__=='__main__':
    import argparse
    cli=argparse.ArgumentParser()
    cli.add_argument('--root',type=Path,default=Path('.'))
    cli.add_argument('--trials',type=int,default=12)
    cli.add_argument('--timeout',type=int,default=180)
    args=cli.parse_args()
    report=run(args.root,dt.datetime.now(UTC),args.trials,args.timeout)
    print(json.dumps({k:report.get(k) for k in ('asof_utc','status','historical_rows','prospective')}))
