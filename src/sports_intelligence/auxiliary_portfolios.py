"""Admit typed non-winner market forecasts to all active paper wallets.

The original winner strategies remain intact. Shared auxiliary model identity is
explicit; repeated predictions across wallets are not independent evidence.
"""
import datetime as dt
import hashlib
from collections import Counter
from zoneinfo import ZoneInfo
from .portfolio_controls import prediction_signal
from .activity_policy import MIN_DAILY_ENTRIES,MAX_DAILY_ENTRIES,NEW_STAKE_FRACTION,MAX_ACCOUNT_EXPOSURE,MAX_MARKETS_PER_EVENT_ACCOUNT
from .activity_policy import daily_entry_stake
from .custom_strategies import ALL_NAMES,RULES,FREQUENCY_NAMES,REFERENCE_NAMES
from .research_pipeline import read_journal
from .odds_provider import parse_utc

LOCAL=ZoneInfo('Europe/Lisbon')


def open_auxiliary_markets(root,events,now,registry,risk,market_scope='all',admission=None,learning=None):
    from .strategy_lab import read_jsonl,save_jsonl
    from .admission_validation import AdmissionValidation
    admission=admission or AdmissionValidation(root,now)
    states={};additions=Counter();today=now.astimezone(LOCAL).date()
    for name in ALL_NAMES:
        path=root/'data'/'strategy_ledgers'/(name.lower()+'.jsonl');rows=read_jsonl(path)
        states[name]={'path':path,'rows':rows,'today':sum(parse_utc(r['forecast_utc']).astimezone(LOCAL).date()==today for r in rows),
                      'bank':1000+sum(r.get('pnl_eur',0) for r in rows if r['status']=='SETTLED'),
                      'exposure':sum(r['stake_eur'] for r in rows if r['status']=='OPEN')}
    event_ids={e['id'] for e in events if e['status']=='pre' and now<parse_utc(e['start_utc'])<=now+dt.timedelta(hours=48)}
    all_rows=read_journal(root)
    latest={}
    for r in all_rows:
        if (r['kind']=='FORECAST' and r['event_id'] in event_ids and
            dt.timedelta(0)<=now-parse_utc(r['forecast_utc'])<=dt.timedelta(hours=12)):
            key=(r['event_id'],r['market'],r['model_version'])
            if key not in latest or parse_utc(r['forecast_utc'])>=parse_utc(latest[key]['forecast_utc']):latest[key]=r
    forecasts=list(latest.values())
    forecasts.sort(key=lambda r:-r['conservative_ev'])
    for f in forecasts:
        learned=None
        prior_learning=f.get('historical_learning') or {}
        if learning is not None and prior_learning.get('stream'):
            current=learning.model(prior_learning['stream'])
            if current['status']=='ACTIVE' and current.get('model_sha256')==prior_learning.get('model_sha256'):
                learned=prior_learning
        gate=admission.decision(f['model_version'],f['sport'],f['market'],learned)
        if not gate['eligible_for_paper']:continue
        if not f.get('admission_evidence',{}).get('eligible_for_small_paper'):continue
        if market_scope=='non_winner' and f['market']=='ML':continue
        if market_scope=='moneyline' and f['market']!='ML':continue
        general_blocks=[r for r in f['admission_blocks'] if r not in ('DAILY_PAPER_LIMIT','EXPERIMENTAL_EVENT_ALREADY_TRADED','AGGREGATE_RISK_LIMIT')]
        if general_blocks:continue
        if any(not 0<=(now-parse_utc(t)).total_seconds()<=8*3600 for t in f['book_quote_updates_utc']):continue
        for name in sorted(ALL_NAMES,key=lambda n:(states[n]['today'],n)):
            if name in REFERENCE_NAMES:continue  # Pure, unmodified market control.
            s=states[name];rows=s['rows']
            if s['today']>=MAX_DAILY_ENTRIES or now<parse_utc(registry[name]['created_utc']):continue
            if any(r['event_id']==f['event_id'] and r['market']==f['market'] for r in rows):continue
            if sum(r['event_id']==f['event_id'] for r in rows)>=MAX_MARKETS_PER_EVENT_ACCOUNT:continue
            # Avoid re-labelling an existing home-win 1X2 as a new -0.5 bet.
            if f['market']=='ASIAN_HOME_MINUS_0_5' and any(r['event_id']==f['event_id'] and r['market']=='1X2' and r['selection_index']==0 for r in rows):continue
            rule=RULES.get(name,{})
            edge_min=rule.get('min_edge_pp',4.)
            if name in FREQUENCY_NAMES:edge_min={'FREQ_SELECTIVE_1_3':6,'FREQ_BALANCED_3_5':4.5,'FREQ_BROAD_5_10':4}[name]
            if (f['edge_pp']<edge_min or f['expected_value']<rule.get('min_ev',.02) or
                f['model_inputs']['sample_min']<rule.get('min_team_matches',8) or
                any((now-parse_utc(t)).total_seconds()>rule.get('max_quote_age_hours',8)*3600 for t in f['book_quote_updates_utc'])):continue
            wanted=daily_entry_stake(s['bank'],s['exposure'],s['today'])
            stake=risk.reserve(f['event_id'],wanted,signal_key=prediction_signal(f['event_id'],f['market'],f['model_version'],f['selection_index']))
            if not stake:continue
            spec=f.get('market_spec')
            if spec is None and f['market']!='ML':
                # Existing fixed goal markets gain an explicit typed final rule.
                spec=({'kind':'TOTAL','metric':'goals','line':2.5,'team':None} if f['market']=='OVER_UNDER_2_5' else
                      {'kind':'SPREAD','metric':'goals','line':-.5,'team':None} if f['market']=='ASIAN_HOME_MINUS_0_5' else
                      {'kind':'BTTS','metric':'goals','line':None,'team':None})
            row={'id':'PAUX-'+hashlib.sha256((name+'|'+f['id']).encode()).hexdigest()[:18],
                 'strategy':name,'event_id':f['event_id'],'sport':f['sport'],'home':f['home'],'away':f['away'],
                 'start_utc':f['start_utc'],'forecast_utc':now.isoformat(),'market':f['market'],'market_spec':spec,
                 'probabilities':f['probabilities'],'no_vig':f['market_no_vig'],'odds':f['odds'],
                 'selection_index':f['selection_index'],'stake_eur':stake,'status':'OPEN','edge_pp':f['edge_pp'],
                 'price_source':f['price_source'],'bookmakers':f['bookmakers'],
                 'book_quote_updates_utc':f['book_quote_updates_utc'],
                 'model_status':'SHARED_AUXILIARY_EXPERIMENT_NOT_MARKET_VALIDATED',
                 'model_version':f['model_version'],'source_forecast_id':f['id'],
                 'shared_forecast_not_independent':True,'conservative_expected_value':f['conservative_ev'],
                 'admission_evidence':gate,
                 'strategy_version':'daily-multimarket-2.0.0'}
            if f.get('historical_learning'):row['historical_learning']=f['historical_learning']
            rows.append(row);s['today']+=1;s['exposure']+=stake;additions[name]+=1
    for name,s in states.items():
        if additions[name]:save_jsonl(s['path'],s['rows'])
    return dict(additions)
