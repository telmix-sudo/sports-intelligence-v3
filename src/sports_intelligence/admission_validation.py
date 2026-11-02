"""PAPER admission from frozen, timed market comparisons; zero-stake collection.

The market favourite is a control, not an edge signal. All other admissions need
independent prospective evidence, never a daily entry quota or historic scores.
"""
from collections import defaultdict
import datetime as dt
import hashlib
import json
import math

from .historical_learning import family_for_wallet, normalize
from .metrics import brier, log_loss
from .odds_provider import parse_utc

UTC=dt.timezone.utc
MIN_EVENTS, MIN_DATES=100,14
JOURNAL='data/decision_observations_v3.jsonl'
POLICY='MARKET-VALIDATED-PAPER-1.0'


def validated_learning(learning):
    if not learning or learning.get('status')!='ACTIVE':return False
    forward=learning.get('forward_validation') or {}
    if not forward.get('eligible') or any(forward.get(k,0)<minimum for k,minimum in
        (('n',MIN_EVENTS),('dates',MIN_DATES),('market_n',MIN_EVENTS),('market_dates',MIN_DATES))):return False
    return all(isinstance(forward.get(prefix+metric+'_difference_upper'),(int,float)) and
               math.isfinite(forward[prefix+metric+'_difference_upper']) and
               forward[prefix+metric+'_difference_upper'] < -1e-5
               for prefix in ('','market_') for metric in ('brier','log_loss'))


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def settled_forecasts(root):
    """Read without changing original ledgers, predictions or settlement clocks."""
    for path in sorted((root/'data/strategy_ledgers').glob('*.jsonl')):
        yield from (r for r in read_rows(path) if r.get('status')=='SETTLED')
    for filename in ('data/market_research_v3.jsonl',JOURNAL):
        rows=read_rows(root/filename)
        settled={r['forecast_id']:r for r in rows if r['kind']=='SETTLEMENT'}
        for r in rows:
            if r['kind']=='FORECAST' and r['id'] in settled:
                s=settled[r['id']]
                if s['status']=='SETTLED':
                    yield dict(r,status=s['status'],settlement_utc=s['settlement_utc'],observed_outcome=s['outcome'])


def frozen_market(record):
    """Require the full price vector and genuine pre-forecast quote clocks."""
    try:
        p=normalize(record.get('no_vig',record.get('market_no_vig',[])))
        prices=record['odds'];times=record['book_quote_updates_utc']
        clock=parse_utc(record['forecast_utc'])
        if not record.get('price_source') or not record.get('bookmakers'):return None
        if len(prices)!=len(p) or len(times)!=len(p):return None
        if any(not math.isfinite(float(v)) or v<=1 for v in prices):return None
        if any(not dt.timedelta(0)<=clock-parse_utc(t)<=dt.timedelta(hours=12) for t in times):return None
        return p
    except (KeyError,TypeError,ValueError):return None


def market_evidence(pairs):
    n=len(pairs);dates=len({p['date'] for p in pairs})
    blocks=[]
    if n<MIN_EVENTS:blocks.append('NEEDS_100_DISTINCT_MARKET_EVENTS')
    if dates<MIN_DATES:blocks.append('NEEDS_14_DISTINCT_MARKET_DATES')
    result={'n_distinct_events':n,'dates':dates,'eligible_for_paper':False,'blocks':blocks}
    if not n:return result
    for metric,score in (('brier',brier),('log_loss',log_loss)):
        model=math.fsum(score(p['p'],p['y']) for p in pairs)/n
        market=math.fsum(score(p['market'],p['y']) for p in pairs)/n
        result.update({metric:model,'market_'+metric:market})
    # Same distribution-free paired Brier safeguard as the existing research
    # validation; it is an operational gate, not a promise of profitable bets.
    upper=result['brier']-result['market_brier']+4*math.sqrt(math.log(20)/(2*n))
    result['paired_brier_difference_upper_bound']=upper
    if upper>=0:blocks.append('PAIRED_BRIER_EDGE_NOT_ESTABLISHED')
    if result['log_loss']>=result['market_log_loss']:blocks.append('LOG_LOSS_NOT_BETTER_THAN_MARKET')
    result['eligible_for_paper']=not blocks
    return result


class AdmissionValidation:
    def __init__(self,root,now):
        if now.tzinfo is None:raise ValueError('Timezone required')
        self.root,self.now=root,now.astimezone(UTC)
        self.cutoff=dt.datetime.combine(self.now.date(),dt.time(),UTC)
        self.rows=read_rows(root/JOURNAL);self.ids={r['id'] for r in self.rows}
        self.groups=defaultdict(dict);self.conflicts=set();self.rejections=defaultdict(int)
        self.new_observations=0
        for r in settled_forecasts(root):
            try:
                start,clock,settled=map(parse_utc,(r['start_utc'],r['forecast_utc'],r['settlement_utc']))
                if not clock<start<=settled<self.cutoff:continue
                market=frozen_market(r)
                if market is None:continue
                family=r.get('model_version') or family_for_wallet(r['strategy'])
                if r['market']=='1X2':family=family_for_wallet(r['strategy'])
                key='|'.join((family,r['sport'],r['market']))
                p=normalize((r.get('historical_learning') or {}).get('base_probabilities',r['probabilities']))
                y=r['observed_outcome']
                if len(p)!=len(market) or type(y) is not int or y not in range(len(p)):continue
                identity=r['event_id'];prior=self.groups[key].get(identity)
                if prior and prior['y']!=y:self.conflicts.add(key)
                if prior is None or clock<prior['clock']:
                    self.groups[key][identity]={'p':p,'market':market,'y':y,'date':str(start.date()),'clock':clock}
            except (KeyError,ValueError,TypeError):continue

    def evidence(self,family,sport,market):
        key='|'.join((family,sport,market))
        result=market_evidence(list(self.groups[key].values()))
        if key in self.conflicts:
            result['blocks'].append('CONFLICTING_OFFICIAL_OUTCOMES');result['eligible_for_paper']=False
        return result

    def decision(self,family,sport,market,learning=None):
        evidence=self.evidence(family,sport,market)
        # ACTIVE is issued only by the strengthened live learner: candidate must
        # beat both original and verified market on frozen future observations.
        if validated_learning(learning):
            evidence=dict(evidence,eligible_for_paper=True,blocks=[],basis='VALIDATED_LEARNED_CANDIDATE')
        else:evidence=dict(evidence,basis='FROZEN_ORIGINAL_VS_MARKET')
        if not evidence['eligible_for_paper']:
            self.rejections['|'.join((family,sport,market))]+=1
        return evidence

    def _append(self,row):
        if row['id'] in self.ids:return False
        path=self.root/JOURNAL;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('a',encoding='utf8') as out:out.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
        self.rows.append(row);self.ids.add(row['id']);return True

    def observe(self,family,event,p,quote,learning=None):
        if event.get('status')!='pre' or not self.now<parse_utc(event['start_utc'])<=self.now+dt.timedelta(hours=48):return
        fid='D3-'+hashlib.sha256('|'.join((POLICY,family,event['sport'],quote['market'],event['id'])).encode()).hexdigest()[:24]
        row={'id':fid,'kind':'FORECAST','event_id':event['id'],'sport':event['sport'],'strategy':family,
             'model_version':family,'home':event['home'],'away':event['away'],'start_utc':event['start_utc'],
             'forecast_utc':self.now.isoformat(),'market':quote['market'],'probabilities':list(p),
             'market_no_vig':list(quote['no_vig']),'odds':list(quote['decimal']),
             'price_source':quote.get('provider'),'bookmakers':quote.get('bookmakers'),
             'book_quote_updates_utc':quote.get('best_book_updates'),'bookmaker_count':quote.get('bookmaker_count'),
             'stake_eur':0.,'classification':'ZERO_STAKE_MARKET_VALIDATION','admission_policy':POLICY}
        if learning is not None:row['historical_learning']=learning
        if frozen_market(row) is not None and (quote.get('bookmaker_count') or 0)>=2:
            self.new_observations+=self._append(row)

    def pending(self):
        settled={r['forecast_id'] for r in self.rows if r['kind']=='SETTLEMENT'}
        return [(r['sport'],parse_utc(r['start_utc']).date()) for r in self.rows
                if r['kind']=='FORECAST' and r['id'] not in settled and parse_utc(r['start_utc'])<=self.now]

    def reconcile(self,finals):
        for f in list(self.rows):
            if f['kind']!='FORECAST' or 'SET-'+f['id'] in self.ids or f['event_id'] not in finals:continue
            if parse_utc(f['start_utc'])>self.now:continue
            y=finals[f['event_id']].get('outcome')
            if y not in (0,1,2):continue
            if len(f['probabilities'])==2:
                if y==1:continue
                y=0 if y==0 else 1
            self._append({'id':'SET-'+f['id'],'kind':'SETTLEMENT','forecast_id':f['id'],'event_id':f['event_id'],
                          'settlement_utc':self.now.isoformat(),'status':'SETTLED','outcome':y,
                          'stake_eur':0.,'pnl_eur':0.,'source':'OFFICIAL_CONFIRMED_FINAL'})

    def report(self):
        streams={key:self.evidence(*key.split('|',2)) for key in sorted(self.groups)}
        forecasts=[r for r in self.rows if r['kind']=='FORECAST']
        settled=sum(r['kind']=='SETTLEMENT' for r in self.rows)
        return {'policy':POLICY,'cutoff_exclusive_utc_date':str(self.cutoff.date()),'streams':streams,
                'validated_streams':sum(s['eligible_for_paper'] for s in streams.values()),
                'deferred_evaluations':dict(self.rejections),'new_zero_stake_observations':self.new_observations,
                'observations_total':len(forecasts),'observations_settled':settled,
                'observations_pending':len(forecasts)-settled,'control_wallet':'MARKET_DAILY_REFERENCE',
                'minimum_market_events':MIN_EVENTS,'minimum_market_dates':MIN_DATES,
                'daily_target_never_overrides_validation':True,'paper_only':True}
