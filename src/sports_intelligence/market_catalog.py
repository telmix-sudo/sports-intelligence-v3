"""Typed market prices and official team-stat settlement. No goal proxy for corners.

Catalog coverage and model availability are separate. Unsupported periods, card
booking rules, player props and incomplete markets are inventoried, never guessed.
"""
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

from .feeds import ESPN_BASE,SCOREBOARDS,fetch_text
from .football import canonical
from .metrics import no_vig,brier,log_loss


def merge_priced_market(first,second,now):
    """Join paired quotes for the SAME line, deduplicating regional operators."""
    def identity(q):return {k:v for k,v in (q.get('market_spec') or {}).items() if k!='provider_market_key'}
    if first and identity(first)!=identity(second):raise ValueError('Market identities differ')
    books={}
    for quote in (first,second):
        if not quote:continue
        for book in quote.get('complete_book_quotes',[]):
            try:
                stamp=_time(book['updated_utc']);prices=list(book['decimal']);prob=list(book['no_vig'])
                if (not 0<=(now-stamp).total_seconds()<=12*3600 or len(prices)!=2 or len(prob)!=2 or
                    any(not math.isfinite(v) or v<=1 for v in prices) or
                    any(not math.isfinite(v) or not 0<v<1 for v in prob) or abs(sum(prob)-1)>.001):continue
                # A regional variant of the same operator is not a second house.
                key=book['bookmaker']
                if not isinstance(key,str) or not key:continue
                parts=key.rsplit('_',1)
                group=parts[0] if len(parts)==2 and parts[1] in ('eu','uk','us','au','fr','se','nl','es','it') else key
                if group in ('betfair_ex','betfair_sb'):group='betfair'
                if group not in books or stamp>_time(books[group]['updated_utc']):books[group]=book
            except (KeyError,ValueError,TypeError):continue
    if not books:return second
    values=list(books.values());best=[max(values,key=lambda b:b['decimal'][i]) for i in range(2)]
    return dict(second,decimal=[best[i]['decimal'][i] for i in range(2)],
                no_vig=[sum(b['no_vig'][i] for b in values)/len(values) for i in range(2)],
                bookmakers=[b['bookmaker'] for b in best],
                best_book_updates=[b['updated_utc'] for b in best],
                bookmaker_count=len(values),bookmaker_count_is_unique=True,
                complete_book_quotes=values,
                pricing_regions=sorted(set(first.get('pricing_regions',['eu']) if first else [])|set(second.get('pricing_regions',[]))))

FOOTBALL = {'epl','la_liga','bundesliga','serie_a','ligue_1'}
STAT_KEYS = {'wonCorners':'corners','cornerKicks':'corners','corners':'corners','offsides':'offsides',
             'foulsCommitted':'fouls','fouls':'fouls','yellowCards':'yellow_cards',
             'redCards':'red_cards','totalShots':'shots','shotsOnTarget':'shots_on_target',
             'shotsOnGoal':'shots_on_target','throwIns':'throw_ins'}
PRIOR = {'goals':1.35,'corners':5.,'offsides':2.,'fouls':12.,'yellow_cards':2.,
         'red_cards':.1,'shots':12.,'shots_on_target':4.,'throw_ins':20.}
EXTRA_MARKET_PRIORITY = ('alternate_totals_corners','btts','alternate_spreads_corners',
                         'corners_1x2','alternate_totals_cards','alternate_team_totals_corners',
                         'double_chance','correct_score','player_shots_on_target','player_shots')


def _time(s):
    from .odds_provider import parse_utc
    return parse_utc(s)


def catalog_quotes(event,rows,now):
    """Normalize full paired bookmaker books by exact metric/team/period/line."""
    groups=defaultdict(dict); inventory=Counter()
    if (event['sport'] not in FOOTBALL and event.get('family')!='soccer') or event['status']!='pre' or _time(event['start_utc'])<=now:
        return {},{}
    for row in rows:
        try:
            if (canonical(row['home_team'])!=canonical(event['home']) or
                canonical(row['away_team'])!=canonical(event['away']) or
                abs((_time(row['commence_time'])-_time(event['start_utc'])).total_seconds())>1800):continue
            for book in row['bookmakers']:
                for raw in book.get('markets',[]):
                    key=raw['key'];inventory[key]+=1
                    # Existing binary BTTS/2.5/-0.5 parser remains authoritative.
                    if key=='btts' or not ('totals' in key or 'spreads' in key):continue
                    metric=('corners' if 'corners' in key else 'cards' if 'cards' in key else
                            'offsides' if 'offside' in key else 'fouls' if 'fouls' in key else
                            'throw_ins' if 'throw_in' in key else 'goals' if key in ('totals','spreads','alternate_totals','alternate_spreads','team_totals') else None)
                    if metric is None:continue
                    stamp=_time(raw.get('last_update') or book['last_update'])
                    if not 0<=(now-stamp).total_seconds()<=12*3600:continue
                    pairs=defaultdict(dict)
                    for o in raw['outcomes']:
                        line=float(o.get('point',math.nan))
                        if not math.isfinite(line):continue
                        team=o.get('description','') if 'team_totals' in key else ''
                        group=(line,team) if 'totals' in key else (line if canonical(o['name'])==canonical(event['home']) else -line,'')
                        pairs[group][canonical(o['name'])]=o
                    for (line,team),pair in pairs.items():
                        try:
                            kind='TOTAL' if 'totals' in key else 'SPREAD'
                            if kind=='TOTAL':
                                outcomes=[pair['over'],pair['under']]
                                if float(outcomes[0]['point'])!=float(outcomes[1]['point']):continue
                                if team and canonical(team) not in (canonical(event['home']),canonical(event['away'])):continue
                                side=('home' if canonical(team)==canonical(event['home']) else 'away') if team else None
                            else:
                                outcomes=[pair[canonical(event['home'])],pair[canonical(event['away'])]]
                                line=float(outcomes[0]['point']);side=None
                                if line!=-float(outcomes[1]['point']):continue
                            if metric=='goals' and ((kind=='TOTAL' and line==2.5 and side is None) or (kind=='SPREAD' and line==-.5)):continue
                            prices=[float(o['price']) for o in outcomes];nv=no_vig(prices)
                            period='FULL_TIME' if not any(t in key for t in ('_h1','_h2','_q1','_q2','_p1','_p2')) else 'UNSUPPORTED_PERIOD'
                            reason=None
                            if abs(line*2-round(line*2))>1e-8:reason='QUARTER_LINE_NEEDS_EXPLICIT_SPLIT_SETTLEMENT'
                            if metric=='cards':reason='BOOKMAKER_CARD_BOOKING_RULES_REQUIRED'
                            if period!='FULL_TIME':reason='PERIOD_STATISTICS_NOT_AVAILABLE'
                            spec={'kind':kind,'metric':metric,'line':line,'team':side,'period':period,
                                  'settlement_block':reason,'provider_market_key':key}
                            mid='STAT_'+hashlib.sha256(json.dumps({k:v for k,v in spec.items() if k!='provider_market_key'},sort_keys=True).encode()).hexdigest()[:16]
                            quote={'decimal':prices,'no_vig':nv,'bookmaker':book['key'],'updated':stamp.isoformat(),'spec':spec}
                            prior=groups[mid].get(book['key'])
                            if prior is None or prior['updated']<quote['updated']:groups[mid][book['key']]=quote
                        except (KeyError,ValueError,TypeError):continue
        except (KeyError,ValueError,TypeError):continue
    result={}
    for mid,books in groups.items():
        quotes=list(books.values());best=[max(quotes,key=lambda q:q['decimal'][i]) for i in range(2)]
        result[mid]={'market':mid,'market_spec':quotes[0]['spec'],'decimal':[best[i]['decimal'][i] for i in range(2)],
                     'no_vig':[sum(q['no_vig'][i] for q in quotes)/len(quotes) for i in range(2)],
                     'bookmakers':[q['bookmaker'] for q in best],'best_book_updates':[q['updated'] for q in best],
                     'bookmaker_count':len(books),'quote_timestamp_available':True,'provider':'The Odds API v4',
                     'complete_book_quotes':[{'bookmaker':q['bookmaker'],'decimal':q['decimal'],'no_vig':q['no_vig'],'updated_utc':q['updated']} for q in quotes]}
    return result,dict(inventory)


def team_statistics(payload,event):
    """Map ESPN boxscore teams to home/away using IDs or exact names."""
    output={}
    comp=(payload.get('header',{}).get('competitions') or [{}])[0]
    sides={str(c.get('id',c.get('team',{}).get('id'))):c.get('homeAway') for c in comp.get('competitors',[])}
    box=payload.get('boxscore',{})
    teams=box.get('teams',[]) or box.get('players',[])
    for entry in teams:
        team=entry.get('team',{});side=entry.get('homeAway') or sides.get(str(team.get('id')))
        if side not in ('home','away'):
            name=canonical(team.get('displayName',''))
            side='home' if name==canonical(event['home']) else 'away' if name==canonical(event['away']) else None
        if not side:continue
        values={}
        for stat in entry.get('statistics',[]):
            key=STAT_KEYS.get(stat.get('name'))
            if key:
                try:
                    v=float(stat.get('displayValue',stat.get('value')))
                    if v>=0 and v.is_integer():values[key]=int(v)
                except (ValueError,TypeError):pass
        if values:output[side]=values
    return output if set(output)=={'home','away'} else None


def enrich_final(event,fetcher=fetch_text):
    url=f"{ESPN_BASE}/{SCOREBOARDS[event['sport']]}/summary?event={event['id'].split(':',1)[1]}"
    raw,meta=fetcher(url);payload=json.loads(raw)
    stats=team_statistics(payload,event)
    if stats:
        event=dict(event);event['final']=dict(event['final'],team_statistics=stats,statistics_source=url)
    return event


def collect_statistics(root,events,now,score_fetcher,fetcher=fetch_text,max_summaries=120,football_history=None):
    """Incremental cache of official completed football event/team-stat records."""
    path=root/'data/football_statistics_v3.json'
    cache=json.loads(path.read_text()) if path.exists() else {'events':{},'days':[]}
    candidates=dict(cache.get('pending',{}))
    candidates.update({e['id']:e for e in events if e['sport'] in FOOTBALL and e.get('completed') and e.get('final') and _time(e['start_utc']).date()<now.date()})
    tasks=[]
    active={e['sport'] for e in events if e['sport'] in FOOTBALL and e.get('status')=='pre'
            and now<_time(e['start_utc'])<=now+dt.timedelta(hours=48)} or FOOTBALL
    for sport in sorted(active):
        # Query actual completed match dates, not months of empty off-season
        # dates. This builds the per-team corner sample using free scoreboards.
        known={m['date'] for m in (football_history or {}).get(sport,[])
               if now.date()-dt.timedelta(days=730)<=m['date']<now.date()}
        days=sorted(known,reverse=True) if known else [now.date()-dt.timedelta(days=i) for i in range(1,731)]
        for day in days:
            key=sport+':'+str(day)
            if key not in cache['days']:
                tasks.append((sport,day,key))
                if sum(t[0]==sport for t in tasks)>=8:break
    from .scanner import unpack_scoreboard
    failures=[]
    def dates(task):
        sport,day,key=task
        try:
            payload,_=score_fetcher(SCOREBOARDS[sport],day)
            return task,unpack_scoreboard(payload,sport,now),None
        except Exception as exc:return task,[],type(exc).__name__
    with ThreadPoolExecutor(max_workers=8) as pool:
        for (sport,day,key),found,error in pool.map(dates,tasks):
            if error:failures.append({'stage':'scoreboard','sport':sport,'date':str(day),'reason':error});continue
            cache['days'].append(key)
            candidates.update({e['id']:e for e in found if e['completed'] and e['final'] and _time(e['start_utc']).date()<now.date()})
    waiting=defaultdict(list)
    for e in sorted(candidates.values(),key=lambda e:e['start_utc'],reverse=True):
        if e['id'] not in cache['events'] and cache.get('attempts',{}).get(e['id'],0)<3:waiting[e['sport']].append(e)
    pending=[]
    order=sorted(waiting,key=lambda sport:(sport not in active,sport))
    while len(pending)<max_summaries:
        batch=[waiting[sport].pop(0) for sport in order if waiting[sport]]
        if not batch:break
        pending.extend(batch[:max_summaries-len(pending)])
    def summary(e):
        try:return enrich_final(e,fetcher),None
        except Exception as exc:return e,type(exc).__name__
    with ThreadPoolExecutor(max_workers=8) as pool:
        for e,error in pool.map(summary,pending):
            cache.setdefault('attempts',{})[e['id']]=cache.get('attempts',{}).get(e['id'],0)+1
            if error:failures.append({'stage':'summary','event_id':e['id'],'reason':error});continue
            # Keep stats-missing responses for one cycle only; don't invent zeros.
            if e['final'].get('team_statistics'):cache['events'][e['id']]=e
    cache['pending']={eid:e for eid,e in candidates.items() if eid not in cache['events'] and cache.get('attempts',{}).get(eid,0)<3}
    from .research_pipeline import atomic_json
    atomic_json(path,cache)
    eligible=[e for e in cache['events'].values() if _time(e['start_utc']).date()<now.date()]
    return eligible,{'scoreboard_requests':len(tasks),'summary_requests':len(pending),
                    'events_with_statistics':len(eligible),'failures':failures,
                    'missing_stats':len(pending)-sum(e['id'] in cache['events'] for e in pending),
                    'backfill_policy':'KNOWN_COMPLETED_DATES_2_SEASONS_ROUND_ROBIN_120_SUMMARIES',
                    'events_by_sport':dict(Counter(e['sport'] for e in eligible))}


class CountModel:
    def __init__(self,metric):
        self.metric=metric;self.rows=[]
    def update(self,e):self.rows.append(e)
    def forecast(self,home,away,spec):
        if spec.get('settlement_block'):return None
        metric=spec['metric'];default=PRIOR.get(metric)
        if default is None:return None
        rows=[e for e in self.rows if all(metric in e['final'].get('team_statistics',{}).get(s,{}) for s in ('home','away'))]
        h=[e for e in rows if canonical(e['home'])==canonical(home)]
        a=[e for e in rows if canonical(e['away'])==canonical(away)]
        if min(len(h),len(a))<8:return None
        def rate(games,side):return (sum(e['final']['team_statistics'][side][metric] for e in games)+8*default)/(len(games)+8)
        mh=math.sqrt(rate(h,'home')*rate(a,'home'));ma=math.sqrt(rate(a,'away')*rate(h,'away'))
        return distribution(mh,ma,spec)|{'sample_min':min(len(h),len(a)), 'version':'COUNT-POISSON-1.0-'+metric,
                                      'rules':'REGULATION_90_MINUTES_EXPLICIT_TEAM_STAT', 'expected_home':mh,'expected_away':ma}


def distribution(mh,ma,spec):
    def pmf(mu):
        vals=[math.exp(-mu)]
        for i in range(1,max(30,int(mu+10*math.sqrt(mu)+10))):vals.append(vals[-1]*mu/i)
        return vals
    ph,pa=pmf(mh),pmf(ma);yes=push=0.
    if spec['kind']=='TOTAL':
        if spec.get('team'):
            pm=ph if spec['team']=='home' else pa
            yes=sum(p for i,p in enumerate(pm) if i>spec['line'])/sum(pm)
            push=sum(p for i,p in enumerate(pm) if i==spec['line'])/sum(pm)
        else:
            pm=pmf(mh+ma);yes=sum(p for i,p in enumerate(pm) if i>spec['line'])/sum(pm)
            push=sum(p for i,p in enumerate(pm) if i==spec['line'])/sum(pm)
    else:
        norm=sum(ph)*sum(pa)
        yes=sum(x*y for i,x in enumerate(ph) for j,y in enumerate(pa) if i+spec['line']>j)/norm
        push=sum(x*y for i,x in enumerate(ph) for j,y in enumerate(pa) if i+spec['line']==j)/norm
    # Book prices on integer Asian lines condition on the non-refunded stake.
    # Score only decisive results; a push is VOID and returns the whole stake.
    conditional=yes/(1-push)
    return {'probabilities':[conditional,1-conditional], 'push_probability':push}


def outcome_for_spec(spec,final):
    if spec.get('settlement_block'):return None
    if spec['metric']=='goals':h,a=final.get('home'),final.get('away')
    else:
        stats=final.get('team_statistics',{});h=stats.get('home',{}).get(spec['metric']);a=stats.get('away',{}).get(spec['metric'])
    if h is None or a is None:return None
    if spec['kind']=='BTTS':return 0 if h>0 and a>0 else 1
    if spec['kind']=='TOTAL':
        value=(h if spec.get('team')=='home' else a) if spec.get('team') else h+a
        if value==spec['line']:return 'VOID'
        return 0 if value>spec['line'] else 1
    delta=h+spec['line']-a
    return 'VOID' if delta==0 else 0 if delta>0 else 1


def generic_backtests(football_history,stat_history,specs,learning=None):
    from .research_pipeline import summarize_score_rows
    from .poisson import FootballPoisson
    from .experimental_markets import goal_rates
    output={}
    for sport,definitions in specs.items():
        rows=defaultdict(list)
        grouped=defaultdict(list)
        for m in football_history.get(sport,[]):grouped[m['date']].append(m)
        goal_model=FootballPoisson()
        for day in sorted(grouped):
            for m in grouped[day]:
                h,a=m['home'],m['away']
                if h not in goal_model.home or a not in goal_model.away or min(goal_model.home[h].matches,goal_model.away[a].matches)<8:continue
                mh,ma=goal_rates(goal_model,h,a)
                for mid,spec in definitions.items():
                    if spec['metric']!='goals' or spec.get('settlement_block'):continue
                    p=distribution(mh,ma,spec)['probabilities'];idx=outcome_for_spec(spec,{'home':m['goals_home'],'away':m['goals_away']})
                    if idx in (0,1) and learning is not None:
                        from .historical_learning import event_key
                        from .research_pipeline import VERSION
                        learning.add(VERSION+'-goal-lines',sport,mid,day,event_key(day,h,a),p,idx)
                    if idx in (0,1):rows[mid].append({'p':p[0],'outcome':idx,'date':str(day),'brier':brier(p,idx),'log_loss':log_loss(p,idx)})
            for m in grouped[day]:goal_model.update(m)
        models={metric:CountModel(metric) for metric in PRIOR if metric!='goals'}
        grouped=defaultdict(list)
        for e in stat_history:
            if e['sport']==sport:grouped[_time(e['start_utc']).date()].append(e)
        for day in sorted(grouped):
            for e in grouped[day]:
                for mid,spec in definitions.items():
                    if spec['metric'] not in models or spec.get('settlement_block'):continue
                    pred=models[spec['metric']].forecast(e['home'],e['away'],spec)
                    idx=outcome_for_spec(spec,e['final'])
                    if pred and idx in (0,1):
                        p=pred['probabilities']
                        if learning is not None:
                            from .historical_learning import event_key
                            learning.add(pred['version'],sport,mid,day,event_key(day,e['home'],e['away']),p,idx)
                        rows[mid].append({'p':p[0],'outcome':idx,'date':str(day),'brier':brier(p,idx),'log_loss':log_loss(p,idx)})
            for e in grouped[day]:
                for model in models.values():model.update(e)
        for mid,score in rows.items():output[sport+'|'+mid]=summarize_score_rows(score)
    return output
