"""Diverse global pricing using the EXISTING shared 16-credit daily cache.

Discovery is free; this module alone consumes paid-plan usage credits, bounded to
the already authorized free-tier budget. No subscription, wager or key is created.
"""
from collections import defaultdict
import datetime as dt
import json
from pathlib import Path

from .odds_provider import (parse_utc,quotes_for_event,market_quotes_for_event,
                            _request_odds,MAX_CREDITS_PER_DAY,MAX_BOOK_QUOTE_AGE_HOURS,
                            ODDS_KEY_MAP,attach_cached_odds,fresh_quote)
from .market_catalog import catalog_quotes,EXTRA_MARKET_PRIORITY
from .activity_policy import MIN_DAILY_ENTRIES

PRICE_LOOKAHEAD_HOURS = 48


def prune_expired_prices(snapshots, now):
    """Bound the live cache; frozen audit prices live in journals and ledgers.

    Only discard prices whose latest known bookmaker time has expired. Opaque
    legacy entries and future/invalid times are retained for diagnosis, not used.
    """
    removed = 0
    def expired(quote):
        try:
            updates = quote['best_book_updates']
            return bool(updates) and all(now-parse_utc(t) > dt.timedelta(hours=MAX_BOOK_QUOTE_AGE_HOURS)
                                         for t in updates)
        except (AttributeError,KeyError,TypeError,ValueError,OverflowError):
            return False
    for snap in snapshots.values():
        touched = set()
        quotes = snap.get('quotes', {})
        markets = snap.get('market_quotes', {})
        for eid, quote in list(quotes.items()):
            if expired(quote):
                del quotes[eid]; touched.add(eid); removed += 1
        for eid, values in list(markets.items()):
            for name, quote in list(values.items()):
                if expired(quote):
                    del values[name]; touched.add(eid); removed += 1
            if not values:
                del markets[eid]
        for eid in touched:
            if eid not in quotes and eid not in markets:
                snap.get('provider_ids', {}).pop(eid, None)
    return removed


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
            'global_poll_windows':old.get('global_poll_windows',{}) if same else {},
            'key_last_priced':old.get('key_last_priced',{}),
            'family_last_priced':old.get('family_last_priced',{}),
            'polled_windows':old.get('polled_windows',[]) if same else [],
            'btts_attempted':old.get('btts_attempted',[]) if same else [],
            'credits_remaining':old.get('credits_remaining') if same else None}
    diag={'configured':bool(secret),'status':'OK' if secret else 'MISSING_KEY_NO_ODDS_API_CALLS',
          'calls_this_run':0,'errors':[],'cached_sports':[],'quotes_matched':0,
          'allocation':'UNPRICED_MODELLED_EVENTS_THEN_DIVERSITY_4_CREDITS_PER_6H_SLOT',
          'price_lookahead_hours':PRICE_LOOKAHEAD_HOURS,
          'maximum_daily_credits':MAX_CREDITS_PER_DAY}
    current_cap=min(MAX_CREDITS_PER_DAY,4*(now.hour//6+1))
    by_key=defaultdict(list)
    for e in events:
        if (e.get('odds_sport_key') and e.get('event_type')=='MATCHUP' and e.get('status')=='pre'
            and now<parse_utc(e['start_utc'])<=now+dt.timedelta(hours=PRICE_LOOKAHEAD_HOURS)):
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
    def executable(key):
        return any(e['sport'] in ODDS_KEY_MAP and e.get('paper_execution_enabled') is not False
                   for e in by_key[key])
    def priced(event):
        quote=record['snapshots'].get(event['sport'],{}).get('quotes',{}).get(event['id'])
        return fresh_quote(quote,now) and (quote.get('bookmaker_count') or 0)>=2
    def covered_count():
        return len({e['id'] for k in by_key if executable(k) for e in by_key[k] if priced(e)})
    def priority(key):
        missing=sum(not priced(e) for e in by_key[key])
        covered=covered_count()
        # Buy enough distinct useful fixtures before refreshing already priced
        # competitions. After coverage, retain the global sport rotation.
        return (not executable(key),not bool(missing),
                -missing if covered<MIN_DAILY_ENTRIES else 0,
                by_key[key][0]['family'] in record['global_priced_families'],
                record['key_last_priced'].get(key,''),
                min(e['start_utc'] for e in by_key[key]),key)
    soccer=[e for matches in by_key.values() for e in matches
            if e.get('family')=='soccer' and e.get('odds_event_id') and executable(e['odds_sport_key'])]
    window=now.hour//6
    available=set(by_key)
    while secret and available:
        key=min(available,key=priority)
        available.remove(key)
        # Modelled competitions may refresh in later slots. Discovery-only
        # leagues rotate once daily so they cannot starve executable prices.
        if record['global_poll_windows'].get(key)==window:continue
        if not executable(key) and key in record['global_priced_keys']:continue
        matches=by_key[key];family=matches[0]['family']
        markets='h2h,totals,spreads' if family=='soccer' and executable(key) else 'h2h'
        cost=len(markets.split(','))
        # Preserve up to 3 credits for a real event's corner/BTTS market catalog.
        reserve_extra=3 if soccer and not record['event_markets_done'] and covered_count()>=MIN_DAILY_ENTRIES else 0
        price_cap=min(current_cap,MAX_CREDITS_PER_DAY-reserve_extra)
        if not remaining(cost,price_cap):
            # One remaining credit can still price an entire football league.
            # Never leave all 1X2 quotes missing merely because extras cost more.
            if cost>1 and remaining(1,price_cap):markets='h2h';cost=1
            else:continue
        if key not in record['global_priced_keys']:record['global_priced_keys'].append(key)
        record['global_poll_windows'][key]=window
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
                # Different provider leagues can share an internal sport. A
                # partial/empty response must not erase still-valid old quotes.
                snap=record['snapshots'].setdefault(sport,{})
                snap.setdefault('quotes',{}).update(quotes)
                snap.setdefault('provider_ids',{}).update(ids)
                for eid,markets_for_event in extras.items():
                    snap.setdefault('market_quotes',{}).setdefault(eid,{}).update(markets_for_event)
                snap.update(fetched_at=now.isoformat(),family=family,odds_sport_key=key)
            if family not in record['global_priced_families']:record['global_priced_families'].append(family)
            record['family_last_priced'][family]=now.isoformat()
            record['key_last_priced'][key]=now.isoformat()
        except Exception as exc:diag['errors'].append({'sport_key':key,'reason':type(exc).__name__})
    # A broad sport catalog does not remove the user's request for corner markets.
    if secret and remaining(2,current_cap) and not record['event_markets_done']:
        soccer=sorted(soccer,key=lambda e:e['start_utc'])
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
    diag['expired_cached_quotes_removed']=prune_expired_prices(record['snapshots'],now)
    diag['quotes_matched']=attach_cached_odds(events,record['snapshots'],now)
    diag['cached_sports']=sorted({e['sport'] for e in events if e.get('quote') and
                                 e['quote'].get('provider')=='The Odds API v4'})
    from .global_discovery import _atomic
    _atomic(path,record)
    diag.update(calls_today=record['calls_today'],credits_today=record['credits_today'],
                credits_remaining=record['credits_remaining'],max_credits_per_day=MAX_CREDITS_PER_DAY,
                priced_families_today=record['global_priced_families'],
                eligible_competitions=len(by_key),goal_market_quotes=sum(len(e.get('market_quotes',{})) for e in events),
                distinct_modelled_events_with_prices=covered_count(),minimum_priced_event_target=MIN_DAILY_ENTRIES,
                market_inventory=record['market_inventory'])
    return diag
