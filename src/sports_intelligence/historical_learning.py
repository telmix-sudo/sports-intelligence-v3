"""Date-separated calibration, frozen shadow forecasts and guarded PAPER use.

Scores train probabilities, not hypothetical bookmaker profits. New calibrators
first run in shadow; only earlier, independently settled shadow events can enable
their use. The daily market reference remains an unmodified control.
"""
from collections import defaultdict
import datetime as dt
import hashlib
import json
import math

from .metrics import brier, log_loss
from .odds_provider import parse_utc

UTC = dt.timezone.utc
VERSION = 'HISTORICAL-CALIBRATION-1.0'
MIN_ROWS, MIN_FORWARD, MIN_DATES = 300, 100, 14


def family_for_wallet(name):
    if name.startswith('FREQ_'):return 'ELO_POISSON_BLEND'
    if name == 'MARKET_DAILY_REFERENCE':return 'MARKET_REFERENCE'
    return name


def event_key(day, home, away):
    from .strategy_lab import team_name
    return '|'.join((str(day), team_name(home), team_name(away)))


def normalize(p):
    p = [float(v) for v in p]
    if len(p) not in (2, 3) or any(not math.isfinite(v) or v < 0 for v in p) or abs(sum(p)-1) > .001:
        raise ValueError('Invalid learning probabilities')
    return [v/sum(p) for v in p]


def transform(p, ratios, strength, temperature):
    values = [(max(v, 1e-12)*r**strength)**(1/temperature) for v,r in zip(normalize(p),ratios)]
    return [v/sum(values) for v in values]


def losses(rows, parameters=None):
    predicted = [transform(r['p'], **parameters) if parameters else r['p'] for r in rows]
    return {'n':len(rows), 'brier':math.fsum(brier(p,r['y']) for p,r in zip(predicted,rows))/len(rows),
            'log_loss':math.fsum(log_loss(p,r['y']) for p,r in zip(predicted,rows))/len(rows)}


def fit(rows):
    rows = sorted(rows, key=lambda r:(r['date'],r['event']))
    result = {'status':'INSUFFICIENT_HISTORY', 'n':len(rows), 'historical_roi':None}
    dates = sorted({r['date'] for r in rows})
    if len(rows)<MIN_ROWS or len(dates)<40:return result
    cuts = [dates[int(len(dates)*v)] for v in (.55,.75,.85)]
    blocks = [[r for r in rows if sum(r['date']>=c for c in cuts)==i] for i in range(4)]
    if min(map(len,blocks))<30:return result
    training,tuning,calibration,holdout = blocks
    k = len(rows[0]['p'])
    if any(len(r['p'])!=k for r in rows):raise ValueError('Mixed learning outcome spaces')
    if {r['y'] for r in training}!=set(range(k)):return result
    ratios = [max(.5,min(2.,(1+sum(r['y']==i for r in training))/(1+sum(r['p'][i] for r in training)))) for i in range(k)]
    strength = min((0.,.25,.5,1.), key=lambda s:losses(tuning,dict(ratios=ratios,strength=s,temperature=1.))['log_loss'])
    temperature = min((1.,.8,1.2,1.5,2.), key=lambda t:losses(calibration,dict(ratios=ratios,strength=strength,temperature=t))['log_loss'])
    parameters = dict(ratios=ratios,strength=strength,temperature=temperature)
    base,candidate = losses(holdout),losses(holdout,parameters)
    better = candidate['brier']<base['brier']-1e-5 and candidate['log_loss']<base['log_loss']-1e-5
    result.update(status='SHADOW_VALIDATION' if better else 'HOLDOUT_NOT_BETTER',parameters=parameters,
                  holdout={'base':base,'candidate':candidate},
                  splits={name:{'n':len(block),'first_date':block[0]['date'],'last_date':block[-1]['date']}
                          for name,block in zip(('training','tuning','calibration','final_evaluation'),blocks)})
    result['model_sha256'] = hashlib.sha256(json.dumps([VERSION,rows,parameters],sort_keys=True).encode()).hexdigest()
    return result


def forward_gate(pairs):
    """Paired prequential scoring, one event per stream, all before today's UTC."""
    dates = {r['date'] for r in pairs}
    market_pairs=[r for r in pairs if r.get('market') is not None]
    result = {'n':len(pairs),'dates':len(dates),'eligible':False,
              'market_n':len(market_pairs),'market_dates':len({r['date'] for r in market_pairs})}
    if (len(pairs)<MIN_FORWARD or len(dates)<MIN_DATES or
        result['market_n']<MIN_FORWARD or result['market_dates']<MIN_DATES):return result
    for benchmark,rows in (('base',pairs),('market',market_pairs)):
        for metric,score in (('brier',brier),('log_loss',log_loss)):
            differences = [score(r['candidate'],r['y'])-score(r[benchmark],r['y']) for r in rows]
            mean = math.fsum(differences)/len(rows)
            variance = math.fsum((v-mean)**2 for v in differences)/(len(rows)-1)
            name=metric+'_difference_upper' if benchmark=='base' else 'market_'+metric+'_difference_upper'
            result[name] = mean+1.96*math.sqrt(variance/len(rows))
    result['eligible'] = all(result[prefix+m+'_difference_upper'] < -1e-5
                             for prefix in ('','market_') for m in ('brier','log_loss'))
    return result


class HistoricalLearning:
    def __init__(self, root, now):
        if now.tzinfo is None:raise ValueError('Timezone required')
        self.cutoff = now.astimezone(UTC).date().isoformat()
        self.samples = defaultdict(dict)
        self.forward = defaultdict(dict)
        self.models = {}
        self.wallets = defaultdict(set)
        self.conflicts = set()
        self.invalid = 0
        self._read_frozen(root)

    def add(self, family, sport, market, day, identity, probabilities, outcome):
        day = str(day)
        if day>=self.cutoff:return
        p = normalize(probabilities)
        if type(outcome) is not int or outcome not in range(len(p)):raise ValueError('Invalid learning outcome')
        key = '|'.join((family,sport,market))
        if (key,identity) in self.conflicts:return
        existing=self.samples[key].get(identity)
        if existing and existing['y']!=outcome:
            self.conflicts.add((key,identity))
            self.samples[key].pop(identity)
            self.forward[key].pop(identity,None)
            self.models.pop(key,None)
            return
        self.samples[key].setdefault(identity,{'date':day,'event':identity,'p':p,'y':outcome})
        self.models.pop(key,None)

    def _read_frozen(self, root):
        from .admission_validation import settled_forecasts, frozen_market
        records=list(settled_forecasts(root))
        for r in sorted(records,key=lambda r:r.get('forecast_utc','')):
            if r.get('status')!='SETTLED':continue
            try:
                settled = parse_utc(r['settlement_utc'])
                start = parse_utc(r['start_utc'])
                clock = parse_utc(r['forecast_utc'])
                if not clock<start<=settled or settled.date().isoformat()>=self.cutoff:continue
                family = r.get('model_version') or family_for_wallet(r['strategy'])
                if r.get('market')=='1X2':family=family_for_wallet(r['strategy'])
                market,sport = r['market'],r['sport']
                learning = r.get('historical_learning') or {}
                base = learning.get('base_probabilities',r['probabilities'])
                identity = event_key(start.date(),r['home'],r['away'])
                self.add(family,sport,market,settled.date(),identity,base,r['observed_outcome'])
                key = '|'.join((family,sport,market))
                if (learning.get('version')==VERSION and learning.get('candidate_probabilities') and
                    learning.get('model_sha256') and
                    learning.get('training_cutoff_exclusive_utc_date','9999')<=clock.date().isoformat() and
                    (key,identity) not in self.conflicts and
                    learning.get('status') in ('SHADOW_VALIDATION','ACTIVE')):
                    # The exact candidate was frozen BEFORE this event started.
                    candidate=normalize(learning['candidate_probabilities'])
                    if len(candidate)!=len(base):self.invalid+=1;continue
                    market=frozen_market(r)
                    if market is not None and len(market)!=len(base):market=None
                    self.forward[key].setdefault(identity,{'date':start.date().isoformat(),'y':r['observed_outcome'],
                        'candidate':candidate,'base':normalize(base),'market':market})
            except (KeyError,ValueError,TypeError):self.invalid+=1

    def model(self,key):
        if key not in self.models:
            result=fit(list(self.samples[key].values()))
            result['forward']=forward_gate(list(self.forward[key].values()))
            if result['status']=='SHADOW_VALIDATION' and result['forward']['eligible']:result['status']='ACTIVE'
            if any(k==key for k,_ in self.conflicts):result['status']='CONFLICTING_HISTORY'
            self.models[key]=result
        return self.models[key]

    def apply(self,family,sport,market,probabilities,wallet=None,control=False):
        base=normalize(probabilities)
        key='|'.join((family,sport,market))
        self.wallets[wallet or family].add(key)
        model=self.model(key)
        candidate=transform(base,**model['parameters']) if 'parameters' in model else None
        evidence={'version':VERSION,'stream':key,'status':'CONTROL_SHADOW_ONLY' if control else model['status'],
                  'base_probabilities':base,'candidate_probabilities':candidate,
                  'model_sha256':model.get('model_sha256'),'training_cutoff_exclusive_utc_date':self.cutoff}
        evidence['forward_validation']=model['forward']
        return (candidate if model['status']=='ACTIVE' and not control else list(probabilities)),evidence

    def report(self,names):
        streams={key:self.model(key) for key in sorted(self.samples)}
        shared={key for key in streams if key.split('|')[2]!='1X2' and not key.startswith('MARKET_REFERENCE|')}
        wallets={}
        for name in names:
            family=family_for_wallet(name)
            keys=({k for k in streams if k.startswith(family+'|')}|self.wallets[name]|
                  (set() if name=='MARKET_DAILY_REFERENCE' else shared))
            wallets[name]={'status':'ACTIVE' if any(self.model(k)['status']=='ACTIVE' for k in keys) else
                           'CONTROL_SHADOW_ONLY' if name=='MARKET_DAILY_REFERENCE' else 'LEARNING_IN_SHADOW',
                           'streams':len(keys),'active_streams':sum(self.model(k)['status']=='ACTIVE' for k in keys),
                           'history_samples':sum(self.model(k)['n'] for k in keys),
                           'primary_is_control':name=='MARKET_DAILY_REFERENCE',
                           'note':'Shared forecasts are counted once per event/model, not once per wallet.'}
        return {'version':VERSION,'cutoff_exclusive_utc_date':self.cutoff,'wallets':wallets,'streams':streams,
                'invalid_frozen_rows_skipped':self.invalid,'historical_roi':None,
                'conflicting_events_excluded':len(self.conflicts),
                'conflicting_events':[{'stream':k,'event':identity} for k,identity in sorted(self.conflicts)],
                'policy':{'minimum_history_rows':MIN_ROWS,'minimum_forward_events':MIN_FORWARD,'minimum_forward_dates':MIN_DATES,
                          'required_metrics':['brier','log_loss'],'required_benchmarks':['original','verified_market'],
                          'reference_remains_control':True,
                          'new_models_start_in_shadow':True,'real_money_enabled':False},
                'caveat':'Score history measures probabilities, not bookmaker profit. Chronological holdout reuse is exploratory; promotion also requires frozen prequential shadow results.'}
