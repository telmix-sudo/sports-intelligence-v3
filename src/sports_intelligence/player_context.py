"""Timestamped public player context, frozen features and prequential research.

No retrospective injury/lineup backfill, guessed availability or manual probability
bonuses. Completed boxscores supply earlier player form only. Probabilistic effects
are learned from subsequently resolved, genuinely pre-game context observations.
"""
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re

from .feeds import ESPN_BASE,SCOREBOARDS,fetch_text
from .odds_provider import parse_utc
from .historical_learning import normalize,forward_gate
from .metrics import brier,log_loss
from .research_pipeline import atomic_json

UTC=dt.timezone.utc
VERSION='PLAYER-CONTEXT-1.0'
CACHE='data/player_context_cache_v3.json'
JOURNAL='data/player_context_v3.jsonl'
MIN_FIT_EVENTS,MIN_FIT_DATES=40,4
MAX_REQUESTS=24
CODE_SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
FOOTBALL={'epl','la_liga','bundesliga','serie_a','ligue_1'}
FEATURE_SPORTS=FOOTBALL|{'nba','nhl'}
OUT={'out','injured reserve','suspended','out for season','inactive'}
UNCERTAIN={'day-to-day','questionable','doubtful','probable','game time decision'}


def read_rows(root):
    path=root/JOURNAL
    rows=[json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []
    if len({r['id'] for r in rows})!=len(rows):raise ValueError('Duplicate player context record')
    return rows


def append(root,rows,row):
    if any(r['id']==row['id'] for r in rows):return False
    path=root/JOURNAL;path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf8') as f:f.write(json.dumps(row,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n')
    rows.append(row);return True


def pending(root):
    rows=read_rows(root);done={r['observation_id'] for r in rows if r['kind']=='RESULT'}
    return {(r['sport'],parse_utc(r['start_utc']).date()) for r in rows if r['kind']=='OBSERVATION' and r['id'] not in done}


def number(v,minutes=False):
    if isinstance(v,bool):return None
    try:
        if minutes and isinstance(v,str) and ':' in v:
            a,b=v.split(':');value=float(a)+float(b)/60
            if not 0<=float(b)<60:return None
        else:value=float(v)
        return value if math.isfinite(value) and value>=0 else None
    except (ValueError,TypeError):return None


def regulation_minutes(item,state):
    """Derived regulation exposure, explicitly distinct from measured minutes.

    Only a normal football full-time result is supported. Added time is excluded;
    ambiguous substitution/card records yield no estimate.
    """
    if state.get('name')!='STATUS_FULL_TIME':return None
    subs=[];reds=[]
    for play in item.get('plays') or []:
        if not (play.get('substitution') or play.get('redCard')):continue
        match=re.fullmatch(r"(\d+)'(?:\+\d+')?",str(play.get('clock',{}).get('displayValue','')))
        if not match:return None
        minute=min(90,int(match[1]))
        if play.get('substitution'):subs.append(minute)
        if play.get('redCard'):reds.append(minute)
    subs.sort()
    if item.get('starter') is True:
        if item.get('subbedIn') or (item.get('subbedOut') and len(subs)!=1):return None
        start=0;end=subs[0] if item.get('subbedOut') else 90
    elif item.get('subbedIn') is True:
        if len(subs)!=(2 if item.get('subbedOut') else 1):return None
        start=subs[0];end=subs[-1] if item.get('subbedOut') else 90
    else:return None
    end=min([end,*reds]);return max(0.,float(end-start))


def identity(payload,event,captured):
    """Require exact provider ID, scheduled clock and both home/away names."""
    comp=payload['header']['competitions'][0]
    if str(payload['header']['id'])!=event['id'].split(':',1)[1] or str(comp['id'])!=event['id'].split(':',1)[1]:raise ValueError('EVENT_ID_MISMATCH')
    if parse_utc(comp['date'])!=parse_utc(event['start_utc']):raise ValueError('START_CLOCK_MISMATCH')
    teams={}
    for side in ('home','away'):
        c=next(x for x in comp['competitors'] if x['homeAway']==side)
        if c['team']['displayName'].casefold().strip()!=event[side].casefold().strip():raise ValueError('TEAM_IDENTITY_MISMATCH')
        tid=str(c['team']['id'])
        if not tid.isdigit():raise ValueError('INVALID_TEAM_ID')
        teams[side]={'id':tid,'name':c['team']['displayName']}
    if teams['home']['id']==teams['away']['id']:raise ValueError('DUPLICATE_TEAM')
    state=comp['status']['type']
    return comp,teams,state


def parse_summary(payload,event,captured,source_hash=None):
    """Normalize IDs and public playing status; never persist diagnosis or news."""
    comp,teams,state=identity(payload,event,captured)
    start=parse_utc(event['start_utc'])
    row={k:event[k] for k in ('id','sport','home','away','start_utc')}
    row.update(captured_at_utc=captured.isoformat(),source_sha256=source_hash,teams=teams)
    if state.get('state')=='pre' and not state.get('completed') and captured<start:
        row['kind']='CONTEXT'
        for team in teams.values():team.update(injury_report=False,players={},starters=[])
        by_id={t['id']:t for t in teams.values()}
        for group in payload.get('injuries') or []:
            team=by_id.get(str(group.get('team',{}).get('id')))
            if team is None or not isinstance(group.get('injuries'),list):continue
            team['injury_report']=True
            for item in group['injuries']:
                athlete=item.get('athlete',{});pid=str(athlete.get('id',''))
                status=str(item.get('status','')).casefold()
                if not pid.isdigit() or status not in OUT|UNCERTAIN:continue
                published=item.get('date')
                if published and parse_utc(published)>captured:
                    team['injury_report']=False
                    continue
                team['players'][pid]={'id':pid,'name':athlete.get('displayName') or athlete.get('fullName') or pid,
                    'availability':'OUT' if status in OUT else 'UNCERTAIN','reported_status':status,'reported_utc':published}
        # A complete set of explicitly flagged starters is required. A roster
        # or a prediction of a lineup never becomes a confirmed starting lineup.
        groups=[(g.get('team',{}),g.get('roster') or []) for g in payload.get('rosters') or []]
        for group in payload.get('boxscore',{}).get('players',[]):
            groups.append((group.get('team',{}),[a for s in group.get('statistics',[]) for a in s.get('athletes',[])]))
        for team_info,athletes in groups:
            team=by_id.get(str(team_info.get('id')))
            if team is None:continue
            for item in athletes:
                if item.get('starter') is not True:continue
                athlete=item.get('athlete',{});pid=str(athlete.get('id',''))
                if pid.isdigit() and pid not in team['starters']:team['starters'].append(pid)
        expected=11 if event['sport'] in FOOTBALL else 5 if event['sport']=='nba' else None
        for team in teams.values():
            team['lineup_confirmed']=expected is not None and len(team['starters'])==expected
            if not team['lineup_confirmed']:team['starters']=[]
            if any(team['players'].get(pid,{}).get('availability')=='OUT' for pid in team['starters']):
                raise ValueError('STARTER_AVAILABILITY_CONFLICT')
        return row
    if state.get('state')!='post' or state.get('completed') is not True or not start<captured:raise ValueError('NOT_PRE_OR_OFFICIAL_FINAL')
    row['kind']='HISTORY'
    scores={c['homeAway']:number(c.get('score')) for c in comp['competitors']}
    if any(scores.get(s) is None for s in ('home','away')):raise ValueError('OFFICIAL_FINAL_SCORE_MISSING')
    row['outcome']=0 if scores['home']>scores['away'] else 2 if scores['home']<scores['away'] else 1
    for team in teams.values():team['players']={}
    by_id={t['id']:t for t in teams.values()}
    for group in payload.get('boxscore',{}).get('players',[]):
        team=by_id.get(str(group.get('team',{}).get('id')))
        if team is None:continue
        for table in group.get('statistics',[]):
            keys=table.get('keys') or []
            for item in table.get('athletes',[]):
                if item.get('didNotPlay') is True:continue
                athlete=item.get('athlete',{});pid=str(athlete.get('id',''))
                values=dict(zip(keys,item.get('stats') or []))
                minutes=next((number(values[k],True) for k in ('minutes','minutesPlayed','timeOnIce') if k in values),None)
                score=next((number(values[k]) for k in ('points','goals','totalGoals') if k in values),None)
                if pid.isdigit() and minutes is not None and minutes>0 and score is not None:
                    team['players'][pid]={'id':pid,'name':athlete.get('displayName') or pid,'minutes':minutes,'score':score}
    # Football's detailed rosters expose name/value statistics separately.
    for group in payload.get('rosters') or []:
        team=by_id.get(str(group.get('team',{}).get('id')))
        if team is None:continue
        for item in group.get('roster') or []:
            athlete=item.get('athlete',{});pid=str(athlete.get('id',''))
            values={v['name']:v.get('value',v.get('displayValue')) for v in item.get('stats',[]) if isinstance(v,dict) and v.get('name')}
            minutes=next((number(values[k],True) for k in ('minutesPlayed','minutes') if k in values),None)
            basis='REPORTED_MINUTES'
            if minutes is None and event['sport'] in FOOTBALL:
                minutes=regulation_minutes(item,state);basis='DERIVED_REGULATION_MINUTES_EXCLUDING_ADDED_TIME'
            goals=next((number(values[k]) for k in ('goals','totalGoals') if k in values),None)
            if pid.isdigit() and minutes is not None and minutes>0 and goals is not None:
                team['players'][pid]={'id':pid,'name':athlete.get('displayName') or pid,'minutes':minutes,'minutes_basis':basis,'score':goals}
    return row


def collect(root,events,now,fetcher=fetch_text,clock=None,max_requests=MAX_REQUESTS):
    """Bounded, cached summaries on the existing public results source."""
    clock=clock or (lambda:dt.datetime.now(UTC))
    path=root/CACHE;cache=json.loads(path.read_text()) if path.exists() else {'contexts':{},'history':{},'attempts':{}}
    cache.setdefault('attempts',{})
    future=[e for e in events if e.get('sport') in SCOREBOARDS and e.get('status')=='pre' and now<parse_utc(e['start_utc'])<=now+dt.timedelta(hours=48)]
    recent={e['id']:e for e in events if e.get('completed') and e.get('final')}
    cache.setdefault('catalog',{});cache.setdefault('catalog_days',{})
    recent.update(cache['catalog'])
    catalog_requests=0;catalog_failures=[]
    # Football's score-only CC0 archive has no provider event IDs. Obtain a
    # bounded catalogue of earlier official games before asking for boxscores.
    # These requests share the same TOTAL budget as summary requests.
    from .scanner import unpack_scoreboard
    leagues=sorted({e['sport'] for e in future if e['sport'] in FOOTBALL})
    cache.setdefault('catalog_attempts',{})
    catalogue=[]
    for offset in range(1,29):
        day=now.date()-dt.timedelta(days=offset)
        for sport in leagues:
            key=sport+':'+str(day)
            if key not in cache['catalog_days']:
                catalogue.append((cache['catalog_attempts'].get(key,''),offset,sport,key,day))
    for _,_,sport,key,day in sorted(catalogue)[:min(2,max_requests//4)]:
        catalog_requests+=1
        cache['catalog_attempts'][key]=now.isoformat()
        try:
            raw,_=fetcher(f'{ESPN_BASE}/{SCOREBOARDS[sport]}/scoreboard?dates={day:%Y%m%d}&limit=300')
            data=json.loads(raw)
            if not isinstance(data.get('events'),list) or len(data['events'])>=300:raise ValueError('Invalid player history catalogue')
            found=unpack_scoreboard(data,sport,now)
            for e in found:
                if e['completed'] and e['final'] and parse_utc(e['start_utc']).date()<now.date():
                    cache['catalog'][e['id']]=e;recent[e['id']]=e
            if all(e['completed'] or e['status'] in ('post','postponed','cancelled') for e in found):cache['catalog_days'][key]=now.isoformat()
        except Exception as exc:catalog_failures.append({'sport':sport,'date':str(day),'reason':type(exc).__name__})
    scores=root/'data/score_history_v3.json'
    if scores.exists():recent.update(json.loads(scores.read_text()).get('events',{}))
    names={(e['sport'],e[side].casefold()) for e in future for side in ('home','away')}
    histories=[e for e in recent.values() if e.get('sport') in FEATURE_SPORTS and now.date()-dt.timedelta(days=60)<=parse_utc(e['start_utc']).date()<now.date()
               and e['id'] not in cache['history'] and any((e['sport'],e[side].casefold()) in names for side in ('home','away'))]
    def due(e):
        t=cache['attempts'].get(e['id'])
        return t is None or now-parse_utc(t)>=dt.timedelta(minutes=45)
    # Rotate across leagues; missing/failing games cannot starve every other sport.
    groups=defaultdict(list)
    for e in sorted((e for e in future if due(e)),key=lambda e:(cache['attempts'].get(e['id'],''),e['start_utc'])):groups[e['sport']].append(e)
    tasks=[]
    summary_budget=max_requests-catalog_requests
    while any(groups.values()) and len(tasks)<summary_budget//2:
        for sport in sorted(groups):
            if groups[sport] and len(tasks)<summary_budget//2:tasks.append(groups[sport].pop(0))
    coverage=Counter((h['sport'],t['name'].casefold()) for h in cache['history'].values() for t in h['teams'].values())
    histories.sort(key=lambda e:(min(coverage[(e['sport'],e[side].casefold())] for side in ('home','away')),-parse_utc(e['start_utc']).timestamp()))
    for e in histories:
        if len(tasks)>=summary_budget:break
        if due(e):tasks.append(e)
    def acquire(e):
        url=f"{ESPN_BASE}/{SCOREBOARDS[e['sport']]}/summary?event={e['id'].split(':',1)[1]}"
        try:
            raw,meta=fetcher(url);captured=clock().astimezone(UTC)
            return e,parse_summary(json.loads(raw),e,captured,meta.get('sha256')),None
        except Exception as exc:return e,None,type(exc).__name__
    errors=[];success=Counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        for e,row,error in pool.map(acquire,tasks):
            cache['attempts'][e['id']]=clock().astimezone(UTC).isoformat()
            if error:errors.append({'event_id':e['id'],'reason':error});continue
            if row['kind']=='CONTEXT':cache['contexts'][e['id']]=row
            elif all(t['players'] for t in row['teams'].values()):cache['history'][e['id']]=row
            else:success['UNUSABLE_PLAYER_BOXSCORE']+=1;continue
            success[row['kind']]+=1
    cache['history']=dict(sorted(cache['history'].items(),key=lambda kv:kv[1]['start_utc'],reverse=True)[:512])
    cache['contexts']={k:v for k,v in cache['contexts'].items() if parse_utc(v['start_utc'])>now-dt.timedelta(days=2)}
    cache['attempts']={k:v for k,v in cache['attempts'].items() if parse_utc(v)>now-dt.timedelta(days=3)}
    cache['catalog_days']={k:v for k,v in cache['catalog_days'].items() if parse_utc(v)>now-dt.timedelta(days=60)}
    cache['catalog_attempts']={k:v for k,v in cache['catalog_attempts'].items() if parse_utc(v)>now-dt.timedelta(days=60)}
    cache['catalog']={k:v for k,v in cache['catalog'].items() if parse_utc(v['start_utc']).date()>=now.date()-dt.timedelta(days=60)}
    atomic_json(path,cache)
    return cache,{'requests':len(tasks)+catalog_requests,'catalog_requests':catalog_requests,'maximum_requests':max_requests,'parallel_requests':4,'success':dict(success),'failures':errors+catalog_failures,
                  'source':'ESPN_PUBLIC_SUMMARY_UNVERSIONED','source_contract':'STRICT_IDENTITY_SCHEMA_AND_CLOCK_CHECKS','paid_requests':0}


def features(context,history,at):
    """Only earlier finalized player minutes/scoring; missing remains unavailable."""
    if context['sport'] not in FEATURE_SPORTS:raise ValueError('PLAYER_FORM_MODEL_NOT_IMPLEMENTED_FOR_SPORT')
    if not parse_utc(context['captured_at_utc'])<=at<parse_utc(context['start_utc']):raise ValueError('CONTEXT_NOT_AVAILABLE_BEFORE_KICKOFF')
    if at-parse_utc(context['captured_at_utc'])>dt.timedelta(hours=2):raise ValueError('CONTEXT_STALE')
    teams=context['teams'];mode='LINEUP' if all(t['lineup_confirmed'] for t in teams.values()) else 'INJURY_REPORT'
    if mode=='INJURY_REPORT' and not all(t['injury_report'] for t in teams.values()):raise ValueError('MISSING_LINEUP_OR_AVAILABILITY_REPORT')
    details={};vectors={}
    for side,team in teams.items():
        prior=[h for h in history if h['sport']==context['sport'] and parse_utc(h['start_utc']).date()<at.date()
               and parse_utc(h['captured_at_utc'])<=at and any(t['id']==team['id'] for t in h['teams'].values())]
        prior=sorted(prior,key=lambda h:h['start_utc'],reverse=True)[:20]
        if len(prior)<3:raise ValueError('NEEDS_THREE_EARLIER_PLAYER_BOXSCORES_PER_TEAM')
        players=defaultdict(list)
        for game in prior:
            for t in game['teams'].values():
                if t['id']==team['id']:
                    for pid,p in t['players'].items():players[pid].append(p)
        avg={pid:sum(p['minutes'] for p in ps[:10])/min(len(prior),10) for pid,ps in players.items()}
        scored={pid:sum(p['score'] for p in ps[:10])/min(len(prior),10) for pid,ps in players.items()}
        total=sum(avg.values())
        if total<=0:raise ValueError('MISSING_PLAYER_MINUTES')
        chosen=set(team['starters']) if mode=='LINEUP' else set(players)
        if not chosen or any(pid not in players for pid in chosen):raise ValueError('LINEUP_PLAYER_HISTORY_MISSING')
        relevant=[p for p in team['players'].values() if p['id'] in players]
        unknown=[p['id'] for p in team['players'].values() if p['id'] not in players]
        # Unknown minutes are encoded as explicit missing-history counts, not
        # silently treated as a healthy player or a zero-impact absence.
        unknown_out=sum(p['id'] in unknown and p['availability']=='OUT' for p in team['players'].values())
        unknown_uncertain=sum(p['id'] in unknown and p['availability']=='UNCERTAIN' for p in team['players'].values())
        out=sum(avg[p['id']] for p in relevant if p['availability']=='OUT')/total
        uncertain=sum(avg[p['id']] for p in relevant if p['availability']=='UNCERTAIN')/total
        total_scored=sum(scored.values())
        if total_scored<=0:raise ValueError('SCORING_FORM_UNAVAILABLE')
        out_score=sum(scored[p['id']] for p in relevant if p['availability']=='OUT')/total_scored
        uncertain_score=sum(scored[p['id']] for p in relevant if p['availability']=='UNCERTAIN')/total_scored
        recent_scored=sum(sum(p['score'] for p in players[pid][:5]) for pid in chosen)
        recent_mins=sum(sum(p['minutes'] for p in players[pid][:5]) for pid in chosen)
        long_scored=sum(sum(p['score'] for p in players[pid]) for pid in chosen)
        long_mins=sum(sum(p['minutes'] for p in players[pid]) for pid in chosen)
        if min(recent_mins,long_mins)<=0 or long_scored<=0:raise ValueError('SCORING_FORM_UNAVAILABLE')
        form=max(-2.,min(2.,(recent_scored/recent_mins)/(long_scored/long_mins)-1))
        rest=min(7.,(at-parse_utc(prior[0]['start_utc'])).total_seconds()/86400)/7
        share=sum(avg[pid] for pid in chosen)/total
        vectors[side]=[form,share,rest] if mode=='LINEUP' else [out,uncertain,out_score,uncertain_score,unknown_out/10,unknown_uncertain/10,form,rest]
        associations={}
        for p in relevant:
            with_rows=[];without_rows=[]
            for game in prior:
                game_side=next(s for s,t in game['teams'].items() if t['id']==team['id'])
                if game.get('outcome') is None:continue
                win=game['outcome']==(0 if game_side=='home' else 2)
                (with_rows if p['id'] in game['teams'][game_side]['players'] else without_rows).append(win)
            associations[p['id']]={'played_games':len(with_rows),'wins_when_played':sum(with_rows),
                'games_without_recorded_minutes':len(without_rows),'wins_without_recorded_minutes':sum(without_rows),
                'role':'DESCRIPTIVE_ASSOCIATION_NOT_CAUSAL_OR_AN_AUTOMATIC_PROBABILITY_ADJUSTMENT'}
        details[side]={'prior_games':len(prior),'confirmed_lineup':team['lineup_confirmed'],'out_minutes_share':out,
                       'history_event_ids':[h['id'] for h in prior],'history_latest_start_utc':prior[0]['start_utc'],
                       'history_latest_capture_utc':max(h['captured_at_utc'] for h in prior),
                       'uncertain_minutes_share':uncertain,'scoring_form_change':form,'unmatched_reported_players':unknown,
                       'unknown_out_count':unknown_out,'unknown_uncertain_count':unknown_uncertain,
                       'out_scoring_share':out_score,'uncertain_scoring_share':uncertain_score,'player_results_associations':associations,
                       'players':[dict(p,average_prior_minutes=avg[p['id']]) for p in relevant]}
    return mode,[h-a for h,a in zip(vectors['home'],vectors['away'])],details


def probabilities(base,x,weights):
    logits=[math.log(max(p,1e-9))+sum(a*b for a,b in zip(w,x)) for p,w in zip(normalize(base),weights)]
    values=[math.exp(v-max(logits)) for v in logits];return [v/sum(values) for v in values]


def fit(rows):
    """Fixed regularized multinomial offset model; no tuning on future outcomes."""
    if len(rows)<MIN_FIT_EVENTS or len({r['date'] for r in rows})<MIN_FIT_DATES:return None
    k=len(rows[0]['base']);d=len(rows[0]['features']);weights=[[0.]*d for _ in range(k)]
    if any(len(r['base'])!=k or len(r['features'])!=d for r in rows):raise ValueError('MIXED_CONTEXT_FEATURE_SPACES')
    for _ in range(250):
        grad=[[5*w/len(rows) for w in ws] for ws in weights]
        for row in rows:
            p=probabilities(row['base'],row['features'],weights)
            for i in range(k):
                for j,x in enumerate(row['features']):grad[i][j]+=(p[i]-(i==row['y']))*x/len(rows)
        for i in range(k):
            for j in range(d):weights[i][j]-=.15*grad[i][j]
    return weights


def run(root,events,forecasts,finals,now,observer,fetcher=fetch_text,clock=None):
    """Collect context and learn in shadow; provide only gated current candidates."""
    clock=clock or (lambda:dt.datetime.now(UTC))
    cache,source=collect(root,events,now,fetcher,clock)
    rows=read_rows(root);done={r['observation_id'] for r in rows if r['kind']=='RESULT'}
    for r in list(rows):
        if r['kind']!='OBSERVATION' or r['id'] in done or r['event_id'] not in finals:continue
        at=clock().astimezone(UTC)
        if parse_utc(r['start_utc'])>at:continue
        outcome=finals[r['event_id']].get('outcome')
        y=outcome if r['market']=='1X2' else 0 if outcome==0 else 1 if outcome==2 else None
        if y is None and outcome!=1:continue
        append(root,rows,{'id':'PCR-'+r['id'],'kind':'RESULT','observation_id':r['id'],'event_id':r['event_id'],
                          'status':'VOID' if y is None else 'SETTLED','outcome':y,'settlement_utc':at.isoformat()})
    resolved={r['observation_id']:r for r in rows if r['kind']=='RESULT'}
    training=defaultdict(list);paired=defaultdict(list);invalid_frozen=0;seen_training=set()
    for r in rows:
        s=resolved.get(r['id'])
        if r['kind']!='OBSERVATION' or s is None or s.get('status')=='VOID' or parse_utc(s['settlement_utc']).date()>=now.date():continue
        try:
            if r.get('version')!=VERSION or r.get('code_sha256')!=CODE_SHA256:continue
            stamp,start,end=map(parse_utc,(r['forecast_utc'],r['start_utc'],s['settlement_utc']))
            if not parse_utc(r['context_utc'])<=stamp<start<=end:raise ValueError('Invalid frozen context clock')
            if (r['stream'],r['event_id']) in seen_training:continue
            base,market=normalize(r['base_probabilities']),normalize(r['market_probabilities'])
            if len(base)!=len(market) or type(s['outcome']) is not int or s['outcome'] not in range(len(base)):raise ValueError('Invalid outcome')
            if any(not math.isfinite(x) for x in r['features']):raise ValueError('Invalid feature')
            if len(r['features'])!=(3 if r['mode']=='LINEUP' else 8):raise ValueError('Invalid feature dimension')
            for detail in r['details'].values():
                if parse_utc(detail['history_latest_start_utc']).date()>=stamp.date() or parse_utc(detail['history_latest_capture_utc'])>stamp:raise ValueError('Future player history')
            if r.get('candidate_probabilities') is not None:normalize(r['candidate_probabilities'])
            seen_training.add((r['stream'],r['event_id']))
        except (ValueError,KeyError,TypeError):invalid_frozen+=1;continue
        record={'date':parse_utc(r['start_utc']).date().isoformat(),'base':r['base_probabilities'],'features':r['features'],
                'y':s['outcome'],'market':r['market_probabilities'],'candidate':r.get('candidate_probabilities')}
        training[r['stream']].append(record)
        if record['candidate'] is not None:paired[r['stream']].append(record)
    models={};gates={};views=[];blocks=Counter();new_observations=0
    selected={}
    for f in forecasts:
        if (f['market']=='1X2' and f['family']=='ELO_POISSON_BLEND') or (f['market']=='ML' and f['family']==f"MULTIMARKET-1.0.0-{f['sport']}"):
            selected[f['event_id']]=f
    for event in sorted((e for e in events if e.get('status')=='pre' and now<parse_utc(e['start_utc'])<=now+dt.timedelta(hours=48)),key=lambda e:e['start_utc']):
        context=cache['contexts'].get(event['id']);at=clock().astimezone(UTC)
        view={'event_id':event['id'],'sport':event['sport'],'home':event['home'],'away':event['away'],'start_utc':event['start_utc'],
              'context_utc':context.get('captured_at_utc') if context else None,'status':'NO_CONTEXT','candidate_probability':None,
              'influences_daily_selection':False,'teams':context.get('teams') if context else None}
        try:
            if context is None:raise ValueError('NO_CONTEXT_CAPTURED_YET')
            if any(context[k]!=event[k] for k in ('id','sport','home','away','start_utc')):raise ValueError('CACHED_EVENT_IDENTITY_CHANGED')
            mode,x,details=features(context,list(cache['history'].values()),at)
            view.update(mode=mode,features=x,details=details)
            f=selected.get(event['id'])
            if f is None:raise ValueError('NEEDS_CURRENT_BASE_MODEL_AND_VERIFIED_ODDS')
            if not dt.timedelta(0)<=at-parse_utc(f['forecast_utc'])<=dt.timedelta(minutes=15):raise ValueError('BASE_FORECAST_STALE')
            if any(not dt.timedelta(0)<=at-parse_utc(t)<=dt.timedelta(hours=8) for t in f['book_quote_updates_utc']):raise ValueError('BASE_QUOTE_EXPIRED')
            stream='|'.join((VERSION,CODE_SHA256,f['model_code_sha256'],event['sport'],f['market'],mode,f['family']))
            if stream not in models:models[stream]=fit(training[stream]);gates[stream]=forward_gate(paired[stream])
            weights=models[stream];gate=gates[stream]
            p=probabilities(f['probabilities'],x,weights) if weights is not None else None
            oid='PCO-'+hashlib.sha256((stream+'|'+event['id']).encode()).hexdigest()[:24]
            existing=next((r for r in rows if r['id']==oid),None)
            if existing is not None and existing.get('candidate_probabilities') is None:p=None
            view.update(status='FORWARD_VALIDATED' if gate['eligible'] else 'SHADOW_MODEL' if p else 'COLLECTING_PRE_GAME_TRAINING',
                        training_events=len(training[stream]),candidate_probability=p,base_probability=f['probabilities'],
                        forward_validation=gate,influences_daily_selection=bool(p and gate['eligible']))
            frozen={'id':oid,'kind':'OBSERVATION','version':VERSION,'code_sha256':CODE_SHA256,'stream':stream,'event_id':event['id'],'sport':event['sport'],
                    'home':event['home'],'away':event['away'],'start_utc':event['start_utc'],'forecast_utc':at.isoformat(),
                    'context_utc':context['captured_at_utc'],'source_sha256':context['source_sha256'],'mode':mode,'features':x,'details':details,
                    'market':f['market'],'base_family':f['family'],'base_probabilities':f['probabilities'],
                    'market_probabilities':f['market_no_vig'],'candidate_probabilities':p,'weights':weights,
                    'odds':f['odds'],'book_quote_updates_utc':f['book_quote_updates_utc'],'stake_eur':0.,'forward_validation':gate}
            if append(root,rows,frozen):new_observations+=1
            if p and gate['eligible']:
                quote={'market':f['market'],'decimal':f['odds'],'no_vig':f['market_no_vig'],'provider':f['price_source'],
                       'bookmaker_count':f['bookmaker_count'],'bookmakers':f['bookmakers'],'best_book_updates':f['book_quote_updates_utc'],'quote_timestamp_available':True}
                evidence={'version':VERSION,'status':'ACTIVE' if gate['eligible'] else 'SHADOW_VALIDATION','forward_validation':gate,
                          'base_probabilities':f['probabilities'],'candidate_probabilities':p,'context_observation_id':oid,'context_features':x,
                          'training_cutoff_exclusive_utc_date':str(now.date()),'model_sha256':hashlib.sha256(json.dumps([stream,weights]).encode()).hexdigest()}
                observer.observe(VERSION+'-'+event['sport'],event,p,quote,sample_min=len(training[stream]),source='PLAYER_CONTEXT',learning=evidence)
        except (ValueError,KeyError,TypeError,OverflowError) as exc:
            view['status']=str(exc) if isinstance(exc,ValueError) else type(exc).__name__;blocks[view['status']]+=1
        views.append(view)
    observations=[r for r in rows if r['kind']=='OBSERVATION']
    report={'version':VERSION,'asof_utc':clock().astimezone(UTC).isoformat(),'source':source,'events':views,'blocks':dict(blocks),
            'historical_player_boxscores':len(cache['history']),'observations':len(observations),'new_observations':new_observations,
            'settled_observations':len(resolved),'fitted_streams':sum(v is not None for v in models.values()),
            'active_streams':sum(g['eligible'] for g in gates.values()),'invalid_frozen_rows':invalid_frozen,'minimum_fit_events':MIN_FIT_EVENTS,'minimum_fit_dates':MIN_FIT_DATES,
            'minimum_forward_events':100,'minimum_forward_dates':14,'automatic_real_bets':False,'historical_context_backfill':False,
            'context_is_not_causal_proof':True,'missing_availability_is_not_healthy':True,
            'feature_definition':'Home-minus-away: missing/uncertain prior minutes, recent scoring per minute, lineup experience and observed game-gap proxy. Separate lineup and injury streams.',
            'probability_policy':'No invented adjustment. Frozen prospective observations train a regularized offset model. Daily selection influence requires paired future improvement against original and market Brier/log loss.',
            'streams':{s:{'training_events':len(training[s]),'forward':gates[s],'weights':models[s],
                          'scores':{p+'_'+metric:math.fsum(fn(r[p],r['y']) for r in paired[s])/len(paired[s]) if paired[s] else None
                                    for p in ('base','market','candidate') for metric,fn in (('brier',brier),('log_loss',log_loss))}} for s in models}}
    atomic_json(root/'reports/player_context_latest.json',report)
    return report
