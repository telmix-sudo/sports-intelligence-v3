"""Diverse global pricing using the EXISTING shared 16-credit daily cache.

Discovery is free; this module alone consumes paid-plan usage credits, bounded to
the already authorized free-tier budget. No subscription, wager or key is created.
"""
from collections import defaultdict
import datetime as dt
import json
from pathlib import Path

from .odds_provider import (parse_utc,quotes_for_event,market_quotes_for_event,
                            _request_odds,MAX_CREDITS_PER_DAY,MAX_BOOK_QUOTE_AGE_HOURS)
from .market_catalog import catalog_quotes,EXTRA_MARKET_PRIORITY


def annotate_discovered(events,root,now,secret,requester=_request_odds):
    path=root/'data/odds_cache_v3.json'
    old=json.loads(path.read_text()) if path.exists() else {}
    same=old.get('day')==str(now.date())
    record={'day':str(now.date()),'calls_today':int(old.get('calls_today',0)) if same else 0,
            'credits_today':max(int(old.get('credits_today',0)),int(old.get('calls_today',0))) if same else 0,
            'snapshots':old.get('snapshots',{}),'market_inventory':old.get('market_inventory',{}),
            'event_markets_done':old.get('event_markets_done',[]) if same else [],
            'global_priced_keys':old.get('global_priced_keys',[]) if same else [],
            'global_priced_families':old.get('global_priced_families',[]) if same else [],
            'family_last_priced':old.get('family_last_priced',{}),
            'credits_remaining':old.get('credits_remaining') if same else None}
    diag={'configured':bool(secret),'status':'OK' if secret else 'MISSING_KEY_NO_ODDS_API_CALLS',
          'calls_this_run':0,'errors':[],'cached_sports':[],'quotes_matched':0,
          'allocation':'DIVERSE_DYNAMIC_SPORTS_4_CREDITS_PER_6H_SLOT_CATCHUP',
          'maximum_daily_credits':MAX_CREDITS_PER_DAY}
    current_cap=min(MAX_CREDITS_PER_DAY,4*(now.hour//6+1))
    by_key=defaultdict(list)
    for e in events:
        if (e.get('odds_sport_key') and e.get('event_type')=='MATCHUP' and e.get('status')=='pre'
            and now<parse_utc(e['start_utc'])<=now+dt.timedelta(hours=12)):
            by_key[e['odds_sport_key']].append(e)
    def remaining(cost,cap):
        return (record['credits_today']+cost<=cap and
                (record['credits_remaining'] is None or record['credits_remaining']>=cost))
    def request(key,markets='h2h',event_id=None,discover=False):
        cost=1 if discover else len(markets.split(','))
        record['credits_today']+=cost;record['calls_today']+=1;diag['calls_this_run']+=1
        rows=requester(key,secret,markets,event_id,discover)
        if getattr(rows,'credits_remaining',None) is not None:record['credits_remaining']=rows.credits_remaining
        return rows
    available=set(by_key)
    while secret and available:
        key=min(available,key=lambda k:(by_key[k][0]['family'] in record['global_priced_families'],
                    record['family_last_priced'].get(by_key[k][0]['family'],''),
                    min(e['start_utc'] for e in by_key[k]),k))
        available.remove(key)
        if key in record['global_priced_keys']:continue
        matches=by_key[key];family=matches[0]['family']
        markets='h2h,totals,spreads' if key=='soccer_epl' else 'h2h'
        cost=len(markets.split(','))
        # Preserve up to 3 credits for a real event's corner/BTTS market catalog.
        if not remaining(cost,min(current_cap,MAX_CREDITS_PER_DAY-3)):continue
        record['global_priced_keys'].append(key)
        try:
            rows=request(key,markets)
            for sport in {e['sport'] for e in matches}:
                matching=[e for e in events if e.get('odds_sport_key')==key and e['sport']==sport]
                quotes={};extras={};ids={}
                for e in matching:
                    q=quotes_for_event(e,rows,now)
                    if q:quotes[e['id']]=q
                    extra=market_quotes_for_event(e,rows,now);extended,_=catalog_quotes(e,rows,now);extra.update(extended)
                    if extra:extras[e['id']]=extra
                    if e.get('odds_event_id'):ids[e['id']]=e['odds_event_id']
                record['snapshots'][sport]={'fetched_at':now.isoformat(),'quotes':quotes,'market_quotes':extras,
                                             'provider_ids':ids,'family':family,'odds_sport_key':key}
            if family not in record['global_priced_families']:record['global_priced_families'].append(family)
            record['family_last_priced'][family]=now.isoformat()
        except Exception as exc:diag['errors'].append({'sport_key':key,'reason':type(exc).__name__})
    # A broad sport catalog does not remove the user's request for corner markets.
    if secret and remaining(2,current_cap) and not record['event_markets_done']:
        soccer=sorted((e for e in events if e.get('family')=='soccer' and e.get('odds_event_id') and
                       e.get('status')=='pre' and now<parse_utc(e['start_utc'])<=now+dt.timedelta(hours=12)),key=lambda e:e['start_utc'])
        if soccer:
            e=soccer[0];key=e['odds_sport_key'];eid=e['odds_event_id']
            record['event_markets_done'].append(eid)
            try:
                data=request(key,event_id=eid,discover=True)
                keys=sorted({m['key'] for r in data for b in r.get('bookmakers',[]) for m in b.get('markets',[])})
                record['market_inventory'][e['id']]={'keys':keys,'captured_at_utc':now.isoformat()}
                cap=min(current_cap-record['credits_today'],record['credits_remaining'] if record['credits_remaining'] is not None else MAX_CREDITS_PER_DAY)
                selected=[k for k in EXTRA_MARKET_PRIORITY if k in keys][:cap]
                if selected:
                    rows=request(key,','.join(selected),eid)
                    extra=market_quotes_for_event(e,rows,now);extended,_=catalog_quotes(e,rows,now);extra.update(extended)
                    snap=record['snapshots'].setdefault(e['sport'],{'quotes':{},'provider_ids':{}})
                    snap.setdefault('market_quotes',{}).setdefault(e['id'],{}).update(extra)
                    snap['fetched_at']=now.isoformat()
            except Exception as exc:diag['errors'].append({'sport_key':key,'reason':'EVENT_MARKETS_'+type(exc).__name__})
    for e in events:
        if e.get('status')!='pre' or parse_utc(e['start_utc'])<=now:continue
        snap=record['snapshots'].get(e['sport'],{})
        def fresh(q):
            try:
                ts=q['best_book_updates']
                return len(ts)==len(q['decimal']) and all(0<=(now-parse_utc(t)).total_seconds()<=MAX_BOOK_QUOTE_AGE_HOURS*3600 for t in ts)
            except (KeyError,TypeError,ValueError):return False
        quote=snap.get('quotes',{}).get(e['id'])
        if quote and fresh(quote):
            e['quote']=quote;diag['quotes_matched']+=1
            if e['sport'] not in diag['cached_sports']:diag['cached_sports'].append(e['sport'])
        e['market_quotes']={k:q for k,q in snap.get('market_quotes',{}).get(e['id'],{}).items() if fresh(q)}
    from .global_discovery import _atomic
    _atomic(path,record)
    diag.update(calls_today=record['calls_today'],credits_today=record['credits_today'],
                credits_remaining=record['credits_remaining'],max_credits_per_day=MAX_CREDITS_PER_DAY,
                priced_families_today=record['global_priced_families'],
                eligible_competitions=len(by_key),goal_market_quotes=sum(len(e.get('market_quotes',{})) for e in events),
                market_inventory=record['market_inventory'])
    return diag
