"""Append-only, preregistered future comparisons. Never reserves PAPER funds.

The paired arms see exactly the same events, quote clocks and unit stakes. The
fixed cohort closes on forecast count/dates, never on observed gains. Old losses
are discovery data and cannot be backfilled into this new confirmation cohort.
"""
from collections import defaultdict
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import random

from .odds_provider import parse_utc
from .historical_learning import normalize
from .metrics import brier,log_loss
from .selection_policy import selection_trace

UTC=dt.timezone.utc
VERSION='SELECTION-PAIRED-1.0'
JOURNAL='data/selection_experiment_v3.jsonl'
MIN_EVENTS,MIN_DATES=100,14
ARMS=('VALUE_GAP','OPPOSITE_VALUE_GAP','MODEL_FAVOURITE','MARKET_FAVOURITE')


def model_code_digest():
    base=Path(__file__).parent
    names=('football.py','poisson.py','north_america_models.py','strategy_lab.py','research_pipeline.py',
           'custom_strategies.py','historical_learning.py','experimental_markets.py','market_catalog.py',
           'selection_policy.py','selection_experiment.py')
    return hashlib.sha256(b''.join(n.encode()+b'\0'+(base/n).read_bytes() for n in names)).hexdigest()


def market_outcome(f,final):
    if f.get('market_spec'):
        from .market_catalog import outcome_for_spec
        return outcome_for_spec(f['market_spec'],final)
    if f['market']=='1X2':return final.get('outcome')
    if f['market']=='ML':
        y=final.get('outcome')
        if y==1:return 'VOID' if f['sport']=='nfl' else None
        return 0 if y==0 else 1 if y==2 else None
    from .research_pipeline import settlement_outcome,GOAL_MARKETS
    if f['market'] in GOAL_MARKETS:return settlement_outcome(f,final)
    return None


def paired_day_interval(pairs,arm,benchmark,seed,trials=2000):
    """Descriptive day-block bootstrap; no promotion or repeated-look claim.

    Only calculated after the fixed cohort closes and every result is resolved.
    Dates are resampled as blocks to retain same-day event correlation.
    """
    groups=defaultdict(list)
    for f,s in pairs:
        if s['status']=='SETTLED':groups[str(parse_utc(f['start_utc']).date())].append(s['unit_pnl'][arm]-s['unit_pnl'][benchmark])
    days=list(groups.values())
    if len(days)<MIN_DATES:return None
    rng=random.Random(seed);values=[]
    for _ in range(trials):
        blocks=[days[rng.randrange(len(days))] for _ in days]
        values.append(math.fsum(math.fsum(b) for b in blocks)/sum(map(len,blocks)))
    values.sort()
    return {'low':values[int(.025*(trials-1))],'high':values[int(.975*(trials-1))],
            'method':'DESCRIPTIVE_95_PERCENT_PAIRED_DAY_BLOCK_BOOTSTRAP', 'resamples':trials,
            'caveat':'Multiple model/market comparisons require independent confirmation; not automatic stake approval.'}


class SelectionExperiment:
    def __init__(self,root,now,code_digest=None,clock=None):
        if now.tzinfo is None:raise ValueError('Timezone required')
        self.root,self.now=root,now.astimezone(UTC)
        self.digest=code_digest or model_code_digest()
        self.clock=clock or (lambda:self.now)
        path=root/JOURNAL
        self.rows=[json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []
        self.ids={r['id'] for r in self.rows}
        if len(self.ids)!=len(self.rows):raise ValueError('Duplicate selection experiment record')
        self.current_forecasts={}
        self.new_forecasts=self.new_settlements=0
        self.rejections=defaultdict(int)

    def append(self,row):
        if row['id'] in self.ids:return False
        path=self.root/JOURNAL;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('a',encoding='utf8') as out:out.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
        self.rows.append(row);self.ids.add(row['id']);return True

    def observe(self,family,event,p,quote,market_spec=None,sample_min=None,source='MODEL',learning=None,push_probability=0.):
        """Called on current forecasts, before admission; never replays old records."""
        try:
            clock=self.clock()
            if clock.tzinfo is None or clock<self.now:raise ValueError('Invalid capture clock')
            clock=clock.astimezone(UTC)
            if any(not isinstance(event.get(k),str) or not event[k].strip() for k in ('id','sport','home','away')):raise ValueError('incomplete_event_identity')
            start=parse_utc(event['start_utc']);market=quote['market']
            if event.get('status')!='pre' or not clock<start<=clock+dt.timedelta(hours=48):raise ValueError('not_future_event')
            if market_spec and market_spec.get('settlement_block'):raise ValueError('unsupported_settlement')
            from .research_pipeline import GOAL_MARKETS
            if market not in ('1X2','ML',*GOAL_MARKETS) and not (market.startswith('STAT_') and market_spec):raise ValueError('unsupported_market')
            p=normalize(p);q=normalize(quote['no_vig']);prices=[float(v) for v in quote['decimal']]
            if (len(p)!=len(q) or len(prices)!=len(p) or (market=='1X2' and len(p)!=3) or
                (market!='1X2' and len(p)!=2)):raise ValueError('outcome_dimensions')
            if not quote.get('quote_timestamp_available') or not quote.get('provider'):raise ValueError('unverified_quote')
            if int(quote.get('bookmaker_count',0))<2:raise ValueError('needs_two_bookmakers')
            times=quote['best_book_updates'];books=quote['bookmakers']
            if len(times)!=len(p) or len(books)!=len(p) or not all(isinstance(b,str) and b for b in books):raise ValueError('incomplete_quote')
            if any(not dt.timedelta(0)<=clock-parse_utc(t)<=dt.timedelta(hours=8) for t in times):raise ValueError('quote_clock')
            trace=selection_trace(p,q,prices,push_probability)
        except (ValueError,KeyError,TypeError,OverflowError):
            self.rejections['ineligible_event_or_quote']+=1;return False
        stream='|'.join((VERSION,self.digest,family,event['sport'],market))
        digest=hashlib.sha256(stream.encode()).hexdigest()[:24]
        fid='SEL3-'+hashlib.sha256((stream+'|'+event['id']).encode()).hexdigest()[:24]
        row={k:event[k] for k in ('sport','home','away','start_utc')}
        row.update(id=fid,kind='FORECAST',version=VERSION,stream=stream,event_id=event['id'],family=family,
                   model_code_sha256=self.digest,forecast_utc=clock.isoformat(),market=market,market_spec=market_spec,
                   probabilities=p,market_no_vig=q,odds=prices,bookmakers=books,book_quote_updates_utc=times,
                   bookmaker_count=quote['bookmaker_count'],price_source=quote['provider'],stake_eur=0.,unit_stake_eur=1.,
                   source=source,sample_min=sample_min,selection_trace=trace,arms={k:v['selection_index'] for k,v in trace['arms'].items()},
                   training_cutoff_exclusive_utc_date=str(self.now.date()),historical_learning=learning)
        snapshot_key=(family,event['sport'],market,event['id'])
        snapshot_id='LIVE-'+hashlib.sha256(json.dumps(row,sort_keys=True,allow_nan=False).encode()).hexdigest()[:24]
        self.current_forecasts[snapshot_key]=dict(row,source_snapshot_id=snapshot_id,paired_forecast_id=fid)
        if 'LOCK-'+digest in self.ids:return False
        if fid in self.ids:return False
        self.append({'id':'POLICY-'+VERSION,'kind':'POLICY','version':VERSION,'registered_at_utc':clock.isoformat(),
                     'arms':list(ARMS),'unit_stake_eur':1.0,'funded_stake_eur':0.,'minimum_events':MIN_EVENTS,
                     'minimum_dates':MIN_DATES,'cohort_stop_rule':'FIRST_PREFIX_REACHING_100_FORECAST_EVENTS_AND_14_KICKOFF_DATES',
                     'historical_backfill_allowed':False,'automatic_stake_activation':False,
                     'probability_inversion_tested':False,'opposite_scope':'TWO_COMPLEMENTARY_OUTCOMES_ONLY',
                     'assessment':'Fixed cohort; all results resolved; paired day intervals vs original and market favourite; independent confirmation before adoption.'})
        self.new_forecasts+=self.append(row)
        cohort=[r for r in self.rows if r.get('kind')=='FORECAST' and r['stream']==stream]
        if len(cohort)>=MIN_EVENTS and len({parse_utc(r['start_utc']).date() for r in cohort})>=MIN_DATES:
            self.append({'id':'LOCK-'+digest,'kind':'COHORT_LOCK','stream':stream,'locked_at_utc':clock.isoformat(),
                         'forecast_ids':[r['id'] for r in cohort],'rule':'FORECAST_COUNT_AND_DATES_ONLY_NO_OUTCOME_OR_PNL_INPUT'})
        return True

    def pending(self):
        settled={r['forecast_id'] for r in self.rows if r['kind']=='SETTLEMENT'}
        return [(r['sport'],parse_utc(r['start_utc']).date()) for r in self.rows
                if r['kind']=='FORECAST' and r['id'] not in settled and parse_utc(r['start_utc'])<=self.now]

    def reconcile(self,finals):
        for f in list(self.rows):
            if f['kind']!='FORECAST' or 'SET-'+f['id'] in self.ids or f['event_id'] not in finals:continue
            if parse_utc(f['start_utc'])>self.now:continue
            final=finals[f['event_id']]
            try:y=market_outcome(f,final)
            except (KeyError,TypeError,ValueError):continue
            if y is None:continue
            if y!='VOID' and (type(y) is not int or y not in range(len(f['probabilities']))):continue
            pnl={arm:(0. if y=='VOID' else f['odds'][i]-1 if y==i else -1.) for arm,i in f['arms'].items()}
            row={'id':'SET-'+f['id'],'kind':'SETTLEMENT','forecast_id':f['id'],'event_id':f['event_id'],
                 'settlement_utc':self.now.isoformat(),'status':'VOID' if y=='VOID' else 'SETTLED','outcome':y,
                 'unit_pnl':pnl,'pnl_eur':0.,'official_final':final,'source':'OFFICIAL_CONFIRMED_FINAL'}
            if y!='VOID':row.update(model_brier=brier(f['probabilities'],y),market_brier=brier(f['market_no_vig'],y),
                                    model_log_loss=log_loss(f['probabilities'],y),market_log_loss=log_loss(f['market_no_vig'],y))
            self.new_settlements+=self.append(row)

    def report(self):
        settlements={r['forecast_id']:r for r in self.rows if r['kind']=='SETTLEMENT'}
        locked={r['stream'] for r in self.rows if r['kind']=='COHORT_LOCK'}
        groups=defaultdict(list)
        for r in self.rows:
            if r['kind']=='FORECAST':groups[r['stream']].append(r)
        streams=[]
        for key,forecasts in sorted(groups.items()):
            pairs=[(f,settlements[f['id']]) for f in forecasts if f['id'] in settlements]
            decisive=[(f,s) for f,s in pairs if s['status']=='SETTLED']
            dates=len({parse_utc(f['start_utc']).date() for f,s in decisive})
            complete=key in locked and len(pairs)==len(forecasts)
            arms={}
            for arm in forecasts[0]['arms']:
                n=len(decisive);pnl=math.fsum(s['unit_pnl'][arm] for f,s in decisive)
                arms[arm]={'n':n,'wins':sum(f['arms'][arm]==s['outcome'] for f,s in decisive),
                           'unit_pnl_eur':pnl,'roi_pct':100*pnl/n if n else None}
                if arm=='OPPOSITE_VALUE_GAP' and complete and n>=MIN_EVENTS and dates>=MIN_DATES:
                    for benchmark in ('VALUE_GAP','MARKET_FAVOURITE'):
                        seed=int(hashlib.sha256((key+benchmark).encode()).hexdigest()[:16],16)
                        arms[arm]['paired_unit_difference_vs_'+benchmark]=paired_day_interval(pairs,arm,benchmark,seed)
            first=forecasts[0]
            streams.append({'stream':key,'family':first['family'],'sport':first['sport'],'market':first['market'],
                            'market_spec':first['market_spec'],'model_code_sha256':first['model_code_sha256'],
                            'forecasts':len(forecasts),'settled':len(decisive),'voided':len(pairs)-len(decisive),
                            'pending':len(forecasts)-len(pairs),'dates':dates,'cohort_locked':key in locked,
                            'remaining_events':max(0,MIN_EVENTS-len(decisive)),'remaining_dates':max(0,MIN_DATES-dates),
                            'status':'SOURCE_VERSION_RETIRED' if first['model_code_sha256']!=self.digest and key not in locked else 'REVIEW_REQUIRED' if complete and len(decisive)>=MIN_EVENTS and dates>=MIN_DATES else
                                     'COHORT_RESOLVED_INSUFFICIENT_DECISIVE_DATA' if complete else 'WAITING_RESULTS' if key in locked else 'COLLECTING',
                            'arms':arms,'automatic_stake_activation':False,
                            'scoring':{k:math.fsum(s[k] for f,s in decisive)/len(decisive) if decisive else None
                                       for k in ('model_brier','market_brier','model_log_loss','market_log_loss')}})
        forecasts=[r for r in self.rows if r['kind']=='FORECAST']
        return {'version':VERSION,'asof_utc':self.now.isoformat(),'registered_at_utc':next((r['registered_at_utc'] for r in self.rows if r['kind']=='POLICY'),None),
                'mode':'ZERO_STAKE_FIXED_FUTURE_COHORT','automatic_stake_activation':False,'historical_backfill_allowed':False,
                'current_model_code_sha256':self.digest,'unit_stake_eur':1.,'minimum_events':MIN_EVENTS,'minimum_dates':MIN_DATES,'forecasts_total':len(forecasts),
                'distinct_events':len({r['event_id'] for r in forecasts}),'settled_total':sum(r['status']=='SETTLED' for r in settlements.values()),
                'voided_total':sum(r['status']=='VOID' for r in settlements.values()),'pending_total':len(forecasts)-len(settlements),
                'new_forecasts':self.new_forecasts,'new_settlements':self.new_settlements,'rejections':dict(self.rejections),'streams':streams,
                'latest_choices':[{k:f[k] for k in ('event_id','family','sport','home','away','start_utc','forecast_utc','market','market_spec','selection_trace')} for f in forecasts[-3:]],
                'interpretation':'Selection-policy comparison, not inverted probabilities. Returns are theoretical before execution, commission and slippage. Samples across streams may share events; do not sum them as independent proof.'}
