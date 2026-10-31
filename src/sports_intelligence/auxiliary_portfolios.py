"""Admit typed non-winner market forecasts to all active paper wallets.

The original winner strategies remain intact. Shared auxiliary model identity is
explicit; repeated predictions across wallets are not independent evidence.
"""
import datetime as dt
import hashlib
from collections import Counter
from zoneinfo import ZoneInfo
from .activity_policy import MIN_DAILY_ENTRIES,MAX_DAILY_ENTRIES,NEW_STAKE_FRACTION,MAX_ACCOUNT_EXPOSURE,MAX_MARKETS_PER_EVENT_ACCOUNT
from .custom_strategies import ALL_NAMES,RULES,FREQUENCY_NAMES
from .research_pipeline import read_journal
from .odds_provider import parse_utc

LOCAL=ZoneInfo('Europe/Lisbon')


def open_auxiliary_markets(root,events,now,registry,risk):
    from .strategy_lab import read_jsonl,save_jsonl
    states={};additions=Counter();today=now.astimezone(LOCAL).date()
    for name in ALL_NAMES:
        path=root/'data'/'strategy_ledgers'/(name.lower()+'.jsonl');rows=read_jsonl(path)
        states[name]={'path':path,'rows':rows,'today':sum(parse_utc(r['forecast_utc']).astimezone(LOCAL).date()==today for r in rows),
                      'bank':1000+sum(r.get('pnl_eur',0) for r in rows if r['status']=='SETTLED'),
                      'exposure':sum(r['stake_eur'] for r in rows if r['status']=='OPEN')}
    event_ids={e['id'] for e in events if e['status']=='pre' and now<parse_utc(e['start_utc'])<=now+dt.timedelta(hours=48)}
    all_rows=read_journal(root)
    forecasts=[r for r in all_rows if r['kind']=='FORECAST' and r['event_id'] in event_ids and
               now-parse_utc(r['forecast_utc'])<=dt.timedelta(hours=12)]
    forecasts.sort(key=lambda r:-r['conservative_ev'])
    for f in forecasts:
        general_blocks=[r for r in f['admission_blocks'] if r not in ('DAILY_PAPER_LIMIT','EXPERIMENTAL_EVENT_ALREADY_TRADED','AGGREGATE_RISK_LIMIT')]
        if general_blocks:continue
        if any(not 0<=(now-parse_utc(t)).total_seconds()<=8*3600 for t in f['book_quote_updates_utc']):continue
        for name in sorted(ALL_NAMES,key=lambda n:(states[n]['today'],n)):
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
            wanted=min(s['bank']*NEW_STAKE_FRACTION,max(0,s['bank']*MAX_ACCOUNT_EXPOSURE-s['exposure']))
            stake=risk.reserve(f['event_id'],wanted)
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
                 'strategy_version':'daily-multimarket-2.0.0'}
            rows.append(row);s['today']+=1;s['exposure']+=stake;additions[name]+=1
    for name,s in states.items():
        if additions[name]:save_jsonl(s['path'],s['rows'])
    return dict(additions)
