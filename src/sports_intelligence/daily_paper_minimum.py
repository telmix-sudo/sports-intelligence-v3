"""User-directed daily PAPER research activity, distinct from validated value.

Only fresh recomputed forecasts can enter. Existing records stay frozen. No real
bet or paid request is made. A 50-cent exploratory floor fits the existing shared
signal cap; ordinary positions retain their original one-euro minimum and gates.
"""
from collections import Counter
import datetime as dt
import hashlib
import json
import math
from zoneinfo import ZoneInfo

from .activity_policy import MIN_DAILY_ENTRIES,MAX_DAILY_ENTRIES,MAX_ACCOUNT_EXPOSURE
from .custom_strategies import ALL_NAMES,REFERENCE_NAMES
from .historical_learning import family_for_wallet,normalize
from .odds_provider import parse_utc
from .portfolio_controls import (DAILY_EXPLORATORY_STAKE_EUR,MAX_SHARED_PREDICTION_OPEN_EUR,
                                  prediction_signal,conservative_expected_value)
from .selection_policy import selection_trace

UTC=dt.timezone.utc
LOCAL=ZoneInfo('Europe/Lisbon')
VERSION='DAILY-PAPER-MINIMUM-1.1'
EXPERIMENTAL='EXPERIMENTAL_MULTI_MARKET'
# Unproven probability gaps and their opposites remain zero-stake comparisons.
# Only a forward-validated learned model may replace the market benchmark for
# daily fills. This never approves a value edge or a real-money position.
GUARDED_PROFILE='VALIDATED_MODEL_OR_MARKET_FAVOURITE'
PROFILES={name:GUARDED_PROFILE for name in (*ALL_NAMES,EXPERIMENTAL) if name not in REFERENCE_NAMES}


def daily_selector(f):
    """Use a learned favourite only after its frozen forward gate has passed."""
    learning=f.get('historical_learning') or {}
    gate=learning.get('forward_validation') or {}
    if (learning.get('status')=='ACTIVE' and gate.get('eligible') is True
            and all(type(gate.get(k)) is int and gate[k]>=n
                    for k,n in (('n',100),('dates',14),('market_n',100),('market_dates',14)))):
        return 'MODEL_FAVOURITE','LEARNED_MODEL_PASSED_FORWARD_BENCHMARKS'
    return 'MARKET_FAVOURITE','MODEL_NOT_FORWARD_VALIDATED_USE_MARKET_BENCHMARK'


def typed_spec(f):
    """Retain exact line, metric and period for settlement of daily positions."""
    if f.get('market_spec'):return f['market_spec']
    fixed={'OVER_UNDER_2_5':('TOTAL',2.5),'ASIAN_HOME_MINUS_0_5':('SPREAD',-.5),'BTTS':('BTTS',None)}
    if f['market'] in fixed:
        kind,line=fixed[f['market']]
        return {'kind':kind,'metric':'goals','line':line,'team':None,'period':'FULL_TIME'}
    return None


def market_rules(f,spec):
    """Describe the actual sport and period rather than labelling all as football."""
    if f['market']=='ML':return 'FULL_GAME_INCLUDING_OVERTIME_OR_EXTRA_INNINGS'
    if f['market']=='1X2':return 'REGULATION_90_MINUTES_EXPLICIT_MARKET'
    period=(spec or {}).get('period','FULL_TIME')
    if f['sport'] in ('nba','nhl','nfl','mlb'):
        return f"{f['sport'].upper()}_{period}_EXPLICIT_MARKET_SPEC"
    return f'REGULATION_90_MINUTES_{period}_EXPLICIT_MARKET_SPEC'


def candidate(f,event,now,policy):
    """Validate the current snapshot again at actual paper registration time."""
    try:
        if (not event or event.get('status')!='pre' or any(f[k]!=event[k] for k in ('sport','home','away'))
            or parse_utc(f['start_utc'])!=parse_utc(event['start_utc'])):return None
        if not now<parse_utc(f['start_utc'])<=now+dt.timedelta(hours=48):return None
        if not dt.timedelta(0)<=now-parse_utc(f['forecast_utc'])<=dt.timedelta(minutes=15):return None
        if not f.get('source_snapshot_id') or not f.get('price_source') or f['bookmaker_count']<2:return None
        p=normalize(f['probabilities']);m=normalize(f['market_no_vig']);prices=list(f['odds'])
        if len(p)!=len(m) or len(prices)!=len(p):return None
        if len(f['book_quote_updates_utc'])!=len(p) or len(f['bookmakers'])!=len(p) or not all(f['bookmakers']):return None
        if any(not dt.timedelta(0)<=now-parse_utc(t)<=dt.timedelta(hours=8) for t in f['book_quote_updates_utc']):return None
        spec=typed_spec(f)
        if spec and spec.get('settlement_block'):return None
        if f['market'] not in ('ML','1X2') and spec is None:return None
        push=f.get('selection_trace',{}).get('push_probability',0.)
        trace=selection_trace(p,m,prices,push)
        actual,reason=daily_selector(f) if policy==GUARDED_PROFILE else (policy,'MARKET_REFERENCE_CONTROL')
        if actual not in trace['arms']:return None
        i=trace['arms'][actual]['selection_index']
        return {'f':f,'p':p,'m':m,'prices':prices,'i':i,'policy':actual,'trace':trace,'spec':spec,
                'reason':reason,'signal':prediction_signal(f['event_id'],f['market'],f['family'],i)}
    except (ValueError,KeyError,TypeError,OverflowError):return None


def run_daily_paper_minimum(root,events,forecasts,now,registry,risk,clock=None):
    """Fill genuine positive-stake PAPER research slots, without changing old rows."""
    from .strategy_lab import read_jsonl,save_jsonl
    from .research_pipeline import read_journal,append,paper_state
    clock=clock or (lambda:now)
    if now.tzinfo is None:raise ValueError('Timezone required')
    day=now.astimezone(LOCAL).date();event_map={e['id']:e for e in events}
    journal=read_journal(root);states={};blocked=Counter();additions=Counter()
    def count_today(rows):return sum(r.get('stake_eur',0)>0 and parse_utc(r['forecast_utc']).astimezone(LOCAL).date()==day for r in rows)
    for name in ALL_NAMES:
        rows=read_jsonl(root/'data/strategy_ledgers'/(name.lower()+'.jsonl'))
        states[name]={'rows':rows,'today':count_today(rows),'before':count_today(rows),
                      'bank':1000+math.fsum(r.get('pnl_eur',0) for r in rows if r['status']=='SETTLED'),
                      'exposure':math.fsum(r['stake_eur'] for r in rows if r['status']=='OPEN')}
    exp_rows=[r for r in journal if r['kind']=='FORECAST' and r['stake_eur']>0]
    exp=paper_state(journal)
    states[EXPERIMENTAL]={'rows':exp_rows,'today':count_today(exp_rows),'before':count_today(exp_rows),
                          'bank':exp['bankroll_eur'] if exp['created'] else 1000.,'exposure':exp['open_exposure_eur']}
    def policy(name):return 'MARKET_FAVOURITE' if name in REFERENCE_NAMES else PROFILES[name]
    # One opening per wallet per pass, with least-concentrated eligible games
    # first. Never create multiple legs on the same game just to fill a target.
    for _ in range(MIN_DAILY_ENTRIES):
        progress=False
        for name,s in sorted(states.items(),key=lambda kv:(kv[1]['today'],kv[0])):
            if s['today']>=MIN_DAILY_ENTRIES:continue
            moment=clock().astimezone(UTC)
            if moment<now or moment.astimezone(LOCAL).date()!=day:
                blocked['capture_clock_or_local_day_changed']+=1;continue
            if not risk.allow_new_entries:blocked['stale_source_reports']+=1;continue
            if name in registry and moment<parse_utc(registry[name]['created_utc']):continue
            if s['bank']*MAX_ACCOUNT_EXPOSURE-s['exposure']<DAILY_EXPLORATORY_STAKE_EUR:
                blocked['account_exposure_limit']+=1;continue
            used={r['event_id'] for r in s['rows']}
            candidates=[]
            for f in forecasts:
                if f['event_id'] in used:continue
                c=candidate(f,event_map.get(f['event_id']),moment,policy(name))
                if c:
                    if name in REFERENCE_NAMES:
                        c['p']=list(c['m'])
                        c['trace']=selection_trace(c['p'],c['m'],c['prices'],c['trace']['push_probability'])
                        c['i']=c['trace']['arms']['MARKET_FAVOURITE']['selection_index']
                        c['f']=dict(c['f'],family='MARKET_REFERENCE',historical_learning=None,paired_forecast_id=None)
                        c['signal']=prediction_signal(f['event_id'],f['market'],'MARKET_REFERENCE',c['i'])
                    candidates.append(c)
            preferred=family_for_wallet(name)
            candidates.sort(key=lambda c:(not (c['f']['source']=='PLAYER_CONTEXT' and c['reason']=='LEARNED_MODEL_PASSED_FORWARD_BENCHMARKS'),c['f']['family']!=preferred,
                risk.event_legs[c['f']['event_id']],risk.signal_exposure[c['signal']],
                -c['m'][c['i']],c['f']['start_utc'],c['f']['event_id'],c['f']['market'],c['f']['family']))
            if not candidates:blocked['no_fresh_supported_forecast']+=1
            for c in candidates:
                f=c['f'];i=c['i']
                # Recheck the actual clock after candidate sorting.
                stamp=clock().astimezone(UTC)
                if stamp<now or stamp.astimezone(LOCAL).date()!=day:break
                if candidate(f,event_map.get(f['event_id']),stamp,policy(name)) is None:continue
                fid='DPM3-'+hashlib.sha256('|'.join((VERSION,name,str(day),f['event_id'],f['market'])).encode()).hexdigest()[:24]
                if any(r['id']==fid for r in s['rows']):continue
                stake=risk.reserve(f['event_id'],DAILY_EXPLORATORY_STAKE_EUR,c['signal'],exploratory=True)
                if not stake:blocked['shared_event_or_portfolio_limit']+=1;continue
                trace=c['trace'];ev=trace['arms'][c['policy']]['model_expected_unit_pnl']
                sample=f.get('sample_min') or 0
                conservative=(1-trace['push_probability'])*conservative_expected_value(c['p'][i],c['prices'][i],sample)
                evidence={'basis':'USER_REQUESTED_DAILY_PAPER_RESEARCH','policy':VERSION,
                          'eligible_for_paper':False,'eligible_for_small_paper':False,'validated_edge':False,'paper_daily_test_authorized':True,
                          'real_money_allowed':False,'normal_value_admission_unchanged':True}
                row={'id':fid,'strategy':name,'event_id':f['event_id'],'sport':f['sport'],'home':f['home'],'away':f['away'],
                     'start_utc':f['start_utc'],'forecast_utc':stamp.isoformat(),'market':f['market'],'market_spec':c['spec'],
                     'market_rules':market_rules(f,c['spec']),
                     'probabilities':c['p'],'no_vig':c['m'],'market_no_vig':c['m'],'odds':c['prices'],
                     'selection_index':i,'selection_policy':c['policy'],'daily_profile':policy(name),
                     'selection_trace':trace,'selection_reason':c['reason'],
                     'stake_eur':stake,'status':'OPEN','model_version':f['family'],
                     'model_status':'DAILY_EXPLORATORY_PAPER_NOT_VALIDATED','classification':'DAILY_EXPLORATORY_PAPER',
                     'source_forecast_id':f.get('paired_forecast_id'), 'source_snapshot_id':f['source_snapshot_id'],
                     'source_snapshot_utc':f['forecast_utc'],'shared_forecast_not_independent':name not in REFERENCE_NAMES,
                     'price_source':f['price_source'],'bookmakers':f['bookmakers'],'bookmaker_count':f['bookmaker_count'],
                     'book_quote_updates_utc':f['book_quote_updates_utc'],'edge_pp':100*(c['p'][i]-c['m'][i]),
                     'expected_value':ev,'selection_expected_value':ev,'conservative_ev':conservative,
                     'conservative_expected_value':conservative,'admission_evidence':evidence,
                     'admission_blocks':['DAILY_PAPER_TEST_WITHOUT_VALIDATED_MARKET_EDGE'],
                     'daily_paper_exploration_policy':VERSION,'strategy_version':VERSION,'paper_only':True,
                     'uncertainty_calibrated':False,'historical_learning':f.get('historical_learning'),
                     'training_cutoff_exclusive_utc_date':f['training_cutoff_exclusive_utc_date'],
                     'model_inputs':{'sample_min':sample,'source_model':f['family'],'source_code':f['model_code_sha256']},
                     'sizing_note':'EUR 0.50 exploratory PAPER; aggregate and shared-signal caps preserved.'}
                if name==EXPERIMENTAL:
                    row['kind']='FORECAST'
                    if not append(root,row,journal):raise ValueError('Duplicate daily experimental position after reservation')
                s['rows'].append(row);s['today']+=1;s['exposure']+=stake;additions[name]+=1;progress=True
                break
        if not progress:break
    for name,s in states.items():
        if name!=EXPERIMENTAL and additions[name]:save_jsonl(root/'data/strategy_ledgers'/(name.lower()+'.jsonl'),s['rows'])
    return {'policy':VERSION,'mode':'USER_REQUESTED_PAPER_DAILY_MINIMUM','local_date':str(day),
            'expected_wallets':len(states),'minimum_per_wallet':MIN_DAILY_ENTRIES,'maximum_per_wallet':MAX_DAILY_ENTRIES,
            'exploratory_stake_eur':DAILY_EXPLORATORY_STAKE_EUR,'requires_validated_edge_for_daily_fill':False,
            'normal_value_admission_unchanged':True,
            'ranking':'OWN_SOURCE_MODEL_THEN_LEAST_CONCENTRATION_THEN_MARKET_PROBABILITY',
            'selection_guard':{'profile':GUARDED_PROFILE,'unvalidated_fallback':'MARKET_FAVOURITE',
                               'opposite_and_value_gap_comparisons':'ZERO_STAKE_ONLY',
                               'favourite_is_not_a_profit_guarantee':True},
            'real_money_allowed':False,'new_entries':dict(additions),
            'wallets':{name:{'before':s['before'],'entries':s['today'],'new_entries':additions[name],
                             'remaining':max(0,MIN_DAILY_ENTRIES-s['today']),'profile':policy(name),
                             'exploratory_today':sum(r.get('daily_paper_exploration_policy')==VERSION and parse_utc(r['forecast_utc']).astimezone(LOCAL).date()==day for r in s['rows'])} for name,s in states.items()},
            'minimum_met':all(s['today']>=MIN_DAILY_ENTRIES for s in states.values()),'blocks':dict(blocked),
            'note':'Research positions have non-zero virtual stakes. Never invent quotes or relax freshness/exposure caps. Missing data and risk capacity are explicit blockers.'}
