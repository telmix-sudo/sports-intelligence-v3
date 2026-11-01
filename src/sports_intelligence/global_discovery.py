"""Dynamic multi-source sport/competition discovery, independent of wagering.

The Odds API sports/events endpoints cost zero usage credits. ESPN catalogs are
public; unsupported scoreboards are reported and rotated. TheSportsDB's public
free key is deliberately labelled partial (three events per day/query). No
catalog entry is counted as an event, forecast, or bet. New sports cannot inherit
football probabilities or become stake-enabled simply by being discovered.
"""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode,urlparse
import urllib.request
import urllib.error

from .feeds import SCOREBOARDS,USER_AGENT,fetch_text
from .football import canonical
from .odds_provider import ODDS_KEY_MAP,parse_utc

UTC=dt.timezone.utc
ESPN_CORE='https://sports.core.api.espn.com/v2/sports'
TSDB='https://www.thesportsdb.com/api/v1/json/123'
ALIASES={
 'football':'american_football','american football':'american_football',
 'soccer':'soccer','basketball':'basketball','baseball':'baseball',
 'ice hockey':'ice_hockey','hockey':'ice_hockey','field hockey':'field_hockey',
 'aussie rules':'australian_football','australian football':'australian_football',
 'australian rules football':'australian_football','rugby':'rugby_union',
 'rugby union':'rugby_union','rugby league':'rugby_league','women s rugby league':'rugby_league',
 'motor sport':'motorsport','motorsport':'motorsport','racing':'motorsport',
 'formula 1':'motorsport','mma':'mma','mixed martial arts':'mma',
 'esports':'esports','esport':'esports','e sports':'esports',
 'table tennis':'table_tennis','water polo':'water_polo',
}
EXCLUDED={'politics','entertainment','lotteries','lottery','virtual sports'}
CORE_ODDS={v:k for k,v in ODDS_KEY_MAP.items()}
CORE_ESPN={v:k for k,v in SCOREBOARDS.items()}
_ODDS_DISCOVERY_LOCK=threading.Lock()
_ODDS_LAST_REQUEST=0.


def family(name):
    key=canonical(str(name).replace('-',' ').replace('_',' '))
    return ALIASES.get(key,re.sub(r'[^a-z0-9]+','_',key).strip('_'))


def _atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
    tmp.replace(path)


def _json(url):
    raw,_=fetch_text(url,timeout=8)
    return json.loads(raw)


def _odds_get(path,secret,params=None):
    global _ODDS_LAST_REQUEST
    query=dict(params or {},apiKey=secret)
    url='https://api.the-odds-api.com/v4/'+path+'?'+urlencode(query)
    for attempt in range(3):
        # Limit this free collector to <8 requests/s, including concurrent workers.
        with _ODDS_DISCOVERY_LOCK:
            wait=.14-(time.monotonic()-_ODDS_LAST_REQUEST)
            if wait>0:time.sleep(wait)
            _ODDS_LAST_REQUEST=time.monotonic()
        try:
            req=urllib.request.Request(url,headers={'User-Agent':USER_AGENT,'Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=10) as response:
                raw=response.read(3500001)
            if len(raw)>3500000:raise ValueError('Provider response limit')
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            if exc.code in (429,500,502,503,504) and attempt<2:
                time.sleep(attempt+1);continue
            raise ValueError('ODDS_FREE_DISCOVERY_HTTP_'+str(exc.code)) from None
        except Exception as exc:
            # Neither the credential-bearing URL nor arbitrary provider text is logged.
            raise ValueError('ODDS_FREE_DISCOVERY_'+type(exc).__name__) from None


def _safe_error(exc):
    message=str(exc)
    return message if re.fullmatch(r'ODDS_FREE_DISCOVERY_[A-Za-z0-9_]+',message) else type(exc).__name__


def _cached(cache,key,now,ttl,loader):
    old=cache.get(key)
    if old:
        try:
            if dt.timedelta(0)<=now-parse_utc(old['at'])<=ttl:return old['value'],True
        except (KeyError,ValueError,TypeError):pass
    value=loader()
    cache[key]={'at':now.isoformat(),'value':value}
    return value,False


def _refs(payload):
    if not isinstance(payload,dict) or not isinstance(payload.get('items'),list):raise ValueError('Catalog schema')
    values=[]
    for item in payload['items']:
        ref=item.get('$ref','');u=urlparse(ref)
        if u.hostname!='sports.core.api.espn.com' or not u.path.startswith('/v2/sports/'):continue
        slug=u.path.rstrip('/').split('/')[-1]
        if re.fullmatch('[a-z0-9._-]+',slug):values.append(slug)
    return values


def _espn_event(raw,competition,sport,league,now):
    date=competition.get('date') or competition.get('startDate') or raw.get('date')
    try:start=parse_utc(date)
    except (ValueError,TypeError,AttributeError):return None
    state=(competition.get('status') or raw.get('status') or {}).get('type',{})
    if state.get('completed') or state.get('state') not in ('pre',None) or not now<start<=now+dt.timedelta(hours=48):return None
    competitors=competition.get('competitors',[])
    def name(c):return (c.get('team') or c.get('athlete') or {}).get('displayName') or c.get('displayName','')
    names=[name(c) for c in competitors if name(c)]
    route=sport+'/'+league
    internal=CORE_ESPN.get(route,'espn_'+sport.replace('-','_')+'_'+league)
    eid=str(competition.get('id') or raw.get('id',''))
    if not eid:return None
    paired=len(names)==2
    if paired:
        ordered=sorted(competitors,key=lambda c:0 if c.get('homeAway')=='home' else 1)
        home,away=name(ordered[0]),name(ordered[1])
    else:home,away=raw.get('name') or competition.get('name') or league,''
    return {'id':internal+':'+eid,'sport':internal,'family':family(sport),
            'competition':league,'home':home,'away':away,'title':raw.get('name') or f'{home} — {away}',
            'start_utc':start.isoformat(),'status':'pre','completed':False,'final':None,'quote':None,
            'event_type':'MATCHUP' if paired else 'MULTI_PARTICIPANT',
            'participants':names,'paper_execution_enabled':internal in SCOREBOARDS,
            'sources':[{'provider':'ESPN','source_event_id':eid,'route':route}],
            'model_status':'EXISTING_MODEL_SCOPE' if internal in SCOREBOARDS else 'NO_TRAINED_MODEL'}


def espn_discovery(cache,now,requester=_json,route_budget=96):
    diagnostics={'provider':'ESPN','catalog_families':[],'competitions_catalogued':0,
                 'competitions_queried':0,'competitions_deferred':0,'requests':0,'errors':[], 'queries':[]}
    def catalog():
        diagnostics['requests']+=1
        return _refs(requester(ESPN_CORE+'?limit=1000'))
    sports,_=_cached(cache,'espn_sports',now,dt.timedelta(hours=24),catalog)
    diagnostics['catalog_families']=[family(s) for s in sports]
    def leagues(s):
        def get():
            diagnostics['requests']+=1
            data=requester(ESPN_CORE+'/'+s+'/leagues?limit=1000')
            result=_refs(data)
            if data.get('pageCount',1)>1:diagnostics['errors'].append({'sport':s,'reason':'CATALOG_PAGINATION_DEFERRED'})
            return result
        try:return s,_cached(cache,'espn_leagues:'+s,now,dt.timedelta(hours=24),get)[0],None
        except Exception as exc:return s,[],type(exc).__name__
    all_leagues={}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for s,leagues_for_sport,error in pool.map(leagues,sports):
            all_leagues[s]=leagues_for_sport
            if error:diagnostics['errors'].append({'sport':s,'stage':'catalog','reason':error})
    diagnostics['competitions_catalogued']=sum(map(len,all_leagues.values()))
    routes=[]
    # Interleave categories: 220 soccer leagues cannot starve tennis/volleyball/etc.
    per_sport={}
    for s,league_names in all_leagues.items():
        per_sport[s]=sorted(league_names,key=lambda league:(cache.get('espn_route:'+s+'/'+league,{}).get('at',''),
                                     0 if s+'/'+league in CORE_ESPN else 1,league))
    # Give each category a slot, then visit never/least recently queried routes.
    # Already covered small catalogs must not hold back 220 soccer competitions.
    for s in sorted(per_sport):
        if per_sport[s] and len(routes)<route_budget:routes.append((s,per_sport[s].pop(0)))
    while any(per_sport.values()) and len(routes)<route_budget:
        oldest=min(cache.get('espn_route:'+s+'/'+v[0],{}).get('at','') for s,v in per_sport.items() if v)
        for s in sorted(per_sport):
            if (per_sport[s] and len(routes)<route_budget and
                cache.get('espn_route:'+s+'/'+per_sport[s][0],{}).get('at','')==oldest):
                routes.append((s,per_sport[s].pop(0)))
    diagnostics['competitions_queried']=len(routes)
    diagnostics['competitions_deferred']=diagnostics['competitions_catalogued']-len(routes)
    days=sorted({now.date(),(now+dt.timedelta(hours=24)).date(),(now+dt.timedelta(hours=48)).date()})
    events=[]
    def scoreboard(route):
        sport,league=route;key='espn_route:'+sport+'/'+league
        def get():
            found=[];success=0;errors=[]
            for day in days:
                diagnostics['requests']+=1
                url=f'https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard?'+urlencode({'dates':day.strftime('%Y%m%d'),'limit':300})
                try:
                    data=requester(url)
                    if not isinstance(data.get('events'),list):raise ValueError('Scoreboard schema')
                    success+=1
                    for raw in data['events']:
                        for c in raw.get('competitions',[]):
                            event=_espn_event(raw,c,sport,league,now)
                            if event:found.append(event)
                except Exception as exc:errors.append({'date':str(day),'reason':type(exc).__name__})
            return {'events':found,'successful_days':success,'errors':errors}
        try:
            value,reused=_cached(cache,key,now,dt.timedelta(minutes=50),get)
            return sport,league,value,reused
        except Exception as exc:return sport,league,{'events':[],'successful_days':0,'errors':[{'reason':type(exc).__name__}]},False
    with ThreadPoolExecutor(max_workers=12) as pool:
        for s,league,value,reused in pool.map(scoreboard,routes):
            current=[e for e in value['events'] if now<parse_utc(e['start_utc'])<=now+dt.timedelta(hours=48)]
            events.extend(current)
            diagnostics['queries'].append({'family':family(s),'competition':league,'events':len(current),
                                          'status':('CACHED' if reused else 'OK') if value['successful_days'] else 'UNAVAILABLE'})
            for err in value['errors']:diagnostics['errors'].append(dict(err,sport=s,competition=league))
    # Keep still-future discoveries from deferred routes; querying a new subset
    # must not make yesterday's verified calendar disappear from the universe.
    reused_routes=0
    for sport,leagues in per_sport.items():
        for league in leagues:
            old=cache.get('espn_route:'+sport+'/'+league,{})
            try:
                if not dt.timedelta(0)<=now-parse_utc(old['at'])<=dt.timedelta(hours=24):continue
                current=[e for e in old['value']['events'] if now<parse_utc(e['start_utc'])<=now+dt.timedelta(hours=48)]
                events.extend(current);reused_routes+=1
            except (KeyError,TypeError,ValueError):continue
    diagnostics['deferred_cached_competitions_reused']=reused_routes
    diagnostics['status']=('UNAVAILABLE' if not any(q['status']!='UNAVAILABLE' for q in diagnostics['queries']) else
                           'PARTIAL' if diagnostics['errors'] else 'OK')
    return events,diagnostics


def odds_discovery(cache,now,secret,requester=_odds_get):
    diag={'provider':'The Odds API','usage_credits':0,'requests':0,'catalog_families':[],
          'competitions_catalogued':0,'competitions_queried':0,'queries':[],'errors':[],'catalog':[]}
    if not secret:diag['status']='MISSING_KEY';return [],diag
    def catalog():
        diag['requests']+=1;data=requester('sports',secret,{'all':'true'})
        if not isinstance(data,list):raise ValueError('Odds catalog schema')
        return [{k:r.get(k) for k in ('key','group','title','active','has_outrights')} for r in data
                if re.fullmatch('[a-z0-9_]+',str(r.get('key',''))) and canonical(r.get('group','')) not in EXCLUDED]
    rows,_=_cached(cache,'odds_sports',now,dt.timedelta(hours=6),catalog)
    diag['catalog']=rows
    diag['catalog_families']=sorted({family(r['group']) for r in rows})
    diag['competitions_catalogued']=len(rows)
    active=[r for r in rows if r.get('active')]
    def games(r):
        key=r['key']
        def get():
            diag['requests']+=1
            data=requester('sports/'+key+'/events',secret,{'dateFormat':'iso'})
            if not isinstance(data,list):raise ValueError('Odds events schema')
            result=[]
            for raw in data:
                try:
                    start=parse_utc(raw['commence_time'])
                    if not now<start<=now+dt.timedelta(hours=48):continue
                    h,a=raw.get('home_team'),raw.get('away_team');paired=isinstance(h,str) and isinstance(a,str) and bool(h and a)
                    internal=CORE_ODDS.get(key,key)
                    result.append({'id':'odds:'+key+':'+str(raw['id']),'sport':internal,'family':family(r['group']),
                        'competition':r['title'],'home':h or r['title'],'away':a or '',
                        'title':f'{h} — {a}' if paired else r['title'],'start_utc':start.isoformat(),
                        'status':'pre','completed':False,'final':None,'quote':None,
                        'event_type':'MATCHUP' if paired else 'MULTI_PARTICIPANT',
                        'paper_execution_enabled':False,'odds_sport_key':key,'odds_event_id':str(raw['id']),
                        'sources':[{'provider':'The Odds API','source_event_id':str(raw['id']),'sport_key':key}],
                        'model_status':'NO_TRAINED_MODEL'})
                except (KeyError,TypeError,ValueError):continue
            return result
        try:return r,_cached(cache,'odds_events:'+key,now,dt.timedelta(minutes=50),get)[0],None
        except Exception as exc:return r,[],_safe_error(exc)
    events=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for r,found,error in pool.map(games,active):
            found=[e for e in found if now<parse_utc(e['start_utc'])<=now+dt.timedelta(hours=48)]
            events.extend(found);diag['competitions_queried']+=1
            diag['queries'].append({'family':family(r['group']),'competition':r['title'],'events':len(found),'status':'ERROR' if error else 'OK'})
            if error:diag['errors'].append({'sport':r['key'],'reason':error})
    diag['status']='OK' if not diag['errors'] else 'PARTIAL'
    return events,diag


def tsdb_discovery(cache,now,requester=_json,pause=time.sleep,max_calls=26):
    diag={'provider':'TheSportsDB free','catalog_families':[],'requests':0,'queries':[],'errors':[],
          'free_query_event_limit':3,'coverage':'PARTIAL_FREE_TIER_NOT_EXHAUSTIVE','untimed_events':0}
    last_request=[0.]
    def get(url):
        wait=2.2-(time.monotonic()-last_request[0])
        if wait>0:pause(wait)
        last_request[0]=time.monotonic();diag['requests']+=1
        return requester(url)
    def catalog():
        payload=get(TSDB+'/all_sports.php')
        if not isinstance(payload.get('sports'),list):raise ValueError('SportsDB catalog schema')
        return [{'id':r['idSport'],'name':r['strSport']} for r in payload['sports'] if r.get('strSport')]
    sports,_=_cached(cache,'tsdb_sports',now,dt.timedelta(hours=24),catalog)
    diag['catalog_families']=[family(r['name']) for r in sports]
    days=[(now+dt.timedelta(hours=24)).date(),now.date(),(now+dt.timedelta(hours=48)).date()]
    events=[]
    for day in dict.fromkeys(days):
        for sport in sorted(sports,key=lambda r:cache.get('tsdb:'+r['id']+':'+str(day),{}).get('at','')):
            if diag['requests']>=max_calls:break
            key='tsdb:'+sport['id']+':'+str(day)
            def games():
                data=get(TSDB+'/eventsday.php?'+urlencode({'d':str(day),'s':sport['name']}))
                found=[];untimed=0
                for r in data.get('events') or []:
                    try:start=parse_utc(r.get('strTimestamp',''))
                    except (ValueError,AttributeError,TypeError):untimed+=1;continue
                    if not now<start<=now+dt.timedelta(hours=48):continue
                    h,a=r.get('strHomeTeam'),r.get('strAwayTeam');paired=bool(h and a)
                    found.append({'id':'tsdb:'+str(r['idEvent']),'sport':'tsdb_'+sport['id'],
                        'family':family(sport['name']),'competition':r.get('strLeague') or sport['name'],
                        'home':h or r.get('strEvent') or sport['name'],'away':a or '',
                        'title':r.get('strEvent') or '', 'start_utc':start.isoformat(),'status':'pre',
                        'completed':False,'final':None,'quote':None,'paper_execution_enabled':False,
                        'event_type':'MATCHUP' if paired else 'MULTI_PARTICIPANT',
                        'sources':[{'provider':'TheSportsDB','source_event_id':str(r['idEvent'])}],
                        'model_status':'NO_TRAINED_MODEL','upstream_free_sample':True})
                return {'events':found,'untimed':untimed}
            try:
                value,_=_cached(cache,key,now,dt.timedelta(hours=3),games)
                current=[e for e in value['events'] if now<parse_utc(e['start_utc'])<=now+dt.timedelta(hours=48)]
                events.extend(current);diag['untimed_events']+=value['untimed']
                diag['queries'].append({'family':family(sport['name']),'date':str(day),'events':len(current),'status':'PARTIAL_FREE_TIER'})
            except Exception as exc:diag['errors'].append({'sport':sport['name'],'date':str(day),'reason':type(exc).__name__})
    diag['status']='PARTIAL_FREE_TIER' if diag['queries'] else 'UNAVAILABLE'
    return events,diag


def merge_events(seed,additional):
    """Prefer existing IDs; merge only exact competitor identity and close times.

    Unknown aliases remain separate source fixtures, visibly flagged. Nothing is
    guessed into a portfolio. No title-only matching of races/tournaments.
    """
    merged=[];index=defaultdict(list)
    for raw in list(seed)+list(additional):
        e=dict(raw);e.setdefault('sources',[])
        if 'family' not in e:
            route=SCOREBOARDS.get(e['sport'],'')
            e['family']=family(route.split('/')[0]) if route else family(e['sport'])
        e.setdefault('event_type','MATCHUP');e.setdefault('competition',e['sport'])
        paired=e['event_type']=='MATCHUP' and bool(e.get('home') and e.get('away'))
        k=(e['family'],canonical(e.get('home','')),canonical(e.get('away',''))) if paired else ('id',e['id'])
        previous=None
        for p in index[k]:
            if abs((parse_utc(p['start_utc'])-parse_utc(e['start_utc'])).total_seconds())<=1800:
                previous=p;break
        if previous:
            previous['sources']+= [s for s in e['sources'] if s not in previous['sources']]
            for field in ('odds_sport_key','odds_event_id'):
                if e.get(field):previous[field]=e[field]
            if e.get('quote') and not previous.get('quote'):previous['quote']=e['quote']
        else:
            merged.append(e);index[k].append(e)
    return merged


def summarize(events,sources,now):
    groups={}
    for s in sources:
        for f in s.get('catalog_families',[]):groups.setdefault(f,{'catalog_sources':[],'queries':0,'query_errors':0,'future_events':0,'events_with_odds':0,'events_with_model':0})['catalog_sources'].append(s['provider'])
        for q in s.get('queries',[]):
            f=q['family'];g=groups.setdefault(f,{'catalog_sources':[s['provider']],'queries':0,'query_errors':0,'future_events':0,'events_with_odds':0,'events_with_model':0})
            g['queries']+=1;g['query_errors']+=q.get('status') in ('ERROR','UNAVAILABLE')
    future=[e for e in events if e.get('status')=='pre' and now<parse_utc(e['start_utc'])<=now+dt.timedelta(hours=48)]
    for e in future:
        g=groups.setdefault(e['family'],{'catalog_sources':[],'queries':0,'query_errors':0,'future_events':0,'events_with_odds':0,'events_with_model':0})
        g['future_events']+=1;g['events_with_odds']+=bool(e.get('quote') and e['quote'].get('quote_timestamp_available'))
    for f,g in groups.items():
        g['catalog_sources']=sorted(set(g['catalog_sources']))
        g['status']=('FUTURE_EVENTS_FOUND' if g['future_events'] else
                     'SOURCE_UNAVAILABLE' if g['queries'] and g['query_errors']==g['queries'] else
                     'QUERIED_NO_FUTURE_EVENTS' if g['queries'] else 'CATALOG_ONLY')
        g['model_status']='EXPERIMENTAL_PARTIAL_COMPETITION_COVERAGE' if f in ('soccer','basketball','ice_hockey','american_football','baseball') else 'NO_TRAINED_MODEL'
    return {'asof_utc':now.isoformat(),'window_hours':48,'modalities_in_catalog':len(groups),
            'modalities_queried':sum(g['queries']>0 for g in groups.values()),
            'modalities_with_future_events':sum(g['future_events']>0 for g in groups.values()),
            'modalities_with_timestamped_odds':sum(g['events_with_odds']>0 for g in groups.values()),
            'future_events':len(future),'by_modality':dict(sorted(groups.items())),
            'sources':sources,'paper_only':True,'new_sports_automatically_stake_enabled':False,
            'caveat':'Discovery is not pricing, modeling or a bet. Catalog/source coverage may be partial.'}


def discover_universe(root:Path,now,seed=(),secret=None,public_requester=_json,odds_requester=_odds_get,pause=time.sleep):
    if now.tzinfo is None:raise ValueError('Timezone required')
    now=now.astimezone(UTC)
    path=root/'data/global_discovery_cache_v3.json'
    cache=json.loads(path.read_text()) if path.exists() else {}
    secret=os.getenv('ODDS_API_KEY','') if secret is None else secret
    config_path=root/'config/global_discovery.json'
    config=json.loads(config_path.read_text()) if config_path.exists() else {}
    providers=[('ESPN',lambda:espn_discovery(cache,now,public_requester,config.get('espn_competitions_per_cycle',96))),
               ('The Odds API',lambda:odds_discovery(cache,now,secret,odds_requester)),
               ('TheSportsDB free',lambda:tsdb_discovery(cache,now,public_requester,pause,config.get('sportsdb_free_calls_per_cycle',26)))]
    all_events=[];diagnostics=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[(name,pool.submit(fn)) for name,fn in providers]
        for name,task in futures:
            try:found,diag=task.result();all_events.extend(found);diagnostics.append(diag)
            except Exception as exc:diagnostics.append({'provider':name,'catalog_families':[], 'queries':[],
                'status':'UNAVAILABLE','errors':[{'reason':_safe_error(exc)}]})
    events=merge_events(seed,all_events)
    report=summarize(events,diagnostics,now)
    _atomic(path,cache)
    _atomic(root/'data/global_events_v3.json',{'asof_utc':now.isoformat(),'events':events})
    _atomic(root/'reports/global_discovery_latest.json',report)
    return events,report
