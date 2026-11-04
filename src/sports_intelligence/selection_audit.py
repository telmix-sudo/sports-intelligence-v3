"""Read-only attribution of frozen choices, repetition and accounting integrity."""
from collections import defaultdict
import math

from .admission_validation import read_rows
from .historical_learning import family_for_wallet
from .selection_experiment import market_outcome


def audit_selections(root):
    rows=[r for path in sorted((root/'data/strategy_ledgers').glob('*.jsonl')) for r in read_rows(path) if r.get('status')=='SETTLED']
    research=read_rows(root/'data/market_research_v3.jsonl')
    finals={r['event_id']:r['official_final'] for r in research if r.get('kind')=='SETTLEMENT' and r.get('official_final')}
    import json
    cache_path=root/'data/selection_official_audit_v3.json'
    independently_verified=json.loads(cache_path.read_text()).get('events',{}) if cache_path.exists() else {}
    for eid,record in independently_verified.items():
        # Preserve exact archived statistics, but use independently checked scores.
        finals[eid]=dict(finals.get(eid,{}) ,**record['final'])
    issues=[];checked=0
    for r in rows:
        try:
            i,y=r['selection_index'],r['observed_outcome'];prices=r['odds'];p=r['probabilities']
            if type(i) is not int or type(y) is not int or not 0<=i<len(prices) or not 0<=y<len(prices) or len(p)!=len(prices):raise ValueError()
            expected=round(r['stake_eur']*(prices[i]-1) if i==y else -r['stake_eur'],4)
            if not math.isfinite(r['pnl_eur']) or abs(expected-r['pnl_eur'])>.00011:issues.append({'id':r['id'],'reason':'PNL_DOES_NOT_MATCH_FROZEN_SELECTION'})
            if r['event_id'] in finals:
                expected_y=market_outcome(r,finals[r['event_id']])
                if expected_y is not None:
                    checked+=1
                    if expected_y!=y:issues.append({'id':r['id'],'reason':'ARCHIVED_OFFICIAL_RESULT_MISMATCH'})
        except (ValueError,KeyError,TypeError,IndexError):issues.append({'id':r.get('id'),'reason':'INVALID_FROZEN_RECORD'})
    models=[r for r in rows if r.get('strategy')!='MARKET_DAILY_REFERENCE']
    unique={}
    for r in sorted(models,key=lambda r:(r['forecast_utc'],r['strategy'],r['id'])):
        family=r.get('model_version') or family_for_wallet(r['strategy'])
        key=(r['event_id'],r['market'],family,r['selection_index'])
        unique.setdefault(key,r)
    shared=[r for r in models if r.get('source_forecast_id') or r.get('shared_forecast_not_independent')]
    def summarize(group):
        priced=[]
        for r in group:
            try:
                p=r['no_vig'];i=r['selection_index']
                if len(p)!=len(r['probabilities']) or abs(sum(p)-1)>.001 or any(not math.isfinite(v) or not 0<=v<=1 for v in p):continue
                priced.append(r)
            except (KeyError,TypeError,ValueError):continue
        return {'positions':len(group),'distinct_events':len({r['event_id'] for r in group}),
                'wins':sum(r['pnl_eur']>0 for r in group),'losses':sum(r['pnl_eur']<0 for r in group),
                'pnl_eur':round(math.fsum(r['pnl_eur'] for r in group),4),
                'priced_decisions':len(priced),
                'non_favourite_choices':sum(r['selection_index']!=max(range(len(r['no_vig'])),key=lambda i:r['no_vig'][i]) for r in priced),
                'expected_wins_from_market':math.fsum(r['no_vig'][r['selection_index']] for r in priced),
                'mean_selected_model_probability':math.fsum(r['probabilities'][r['selection_index']] for r in priced)/len(priced) if priced else None,
                'mean_selected_market_probability':math.fsum(r['no_vig'][r['selection_index']] for r in priced)/len(priced) if priced else None}
    bins=defaultdict(list)
    for r in unique.values():
        price=r['odds'][r['selection_index']]
        bins['UNDER_2' if price<2 else '2_TO_3' if price<3 else '3_TO_5' if price<5 else '5_OR_MORE'].append(r)
    binary=[r for r in unique.values() if len(r['odds'])==2]
    inverse={}
    for name,flip in (('ORIGINAL',False),('OPPOSITE',True)):
        wins=0;pnl=0.
        for r in binary:
            i=1-r['selection_index'] if flip else r['selection_index'];win=i==r['observed_outcome']
            wins+=win;pnl+=r['odds'][i]-1 if win else -1
        inverse[name]={'wins':wins,'losses':len(binary)-wins,'theoretical_unit_pnl_eur':round(pnl,4)}
    return {'status':'ISSUES_FOUND' if issues else 'CONSISTENT_WITH_CHECKED_FROZEN_RECORDS',
            'arithmetic_rows_checked':len(rows),'archived_official_result_rows_checked':checked,
            'official_result_coverage_is_partial':checked<len(rows),'issues':issues,
            'independently_verified_model_events':len({r['event_id'] for r in models if r['event_id'] in independently_verified}),
            'models':summarize(models),'distinct_model_market_choices':summarize(list(unique.values())),
            'shared_auxiliary_positions':len(shared),'shared_auxiliary_distinct_events':len({r['event_id'] for r in shared}),
            'native_strategy_positions':len(models)-len(shared),'by_selected_odds':{key:summarize(value) for key,value in sorted(bins.items())},
            'per_wallet':{name:summarize([r for r in rows if r['strategy']==name]) for name in sorted({r['strategy'] for r in rows})},
            'binary_discovery_comparison':{'choices':len(binary),'distinct_events':len({r['event_id'] for r in binary}),'arms':inverse,
                'role':'RETROSPECTIVE_HYPOTHESIS_ONLY_EXCLUDED_FROM_FUTURE_EXPERIMENT'},
            'factors':{'current_selection_rule':'MAX_MODEL_PROBABILITY_MINUS_MARKET_PROBABILITY',
                       'not_a_highest_win_probability_rule':True,'shared_models_are_not_independent_wallet_signals':True,
                       'score_baselines':'Historical team scoring/conceding averages, shrinkage priors and provisional sport distributions; football also uses Elo/Poisson variants.',
                       'not_modelled_in_score_baselines':['injuries','lineups','rest','travel','recent_opponent_strength','current_news'],
                       'interpretation':'High odds imply lower expected hit rates. Market-implied expected wins are descriptive; repeated events/markets are correlated, so a naive binomial test across positions is invalid.'}}


def verify_official_results(root,now,fetcher=None,max_requests=24):
    """Bounded independent ID/home/away/score check; never changes a ledger.

    Reuses a dedicated audit cache. Only model positions need this additional
    forensic request; source URLs are fixed public ESPN endpoints, without keys.
    """
    import json
    from concurrent.futures import ThreadPoolExecutor
    from .feeds import ESPN_BASE,SCOREBOARDS,fetch_text
    from .football import canonical
    from .odds_provider import parse_utc
    from .global_discovery import _atomic
    fetcher=fetcher or fetch_text
    path=root/'data/selection_official_audit_v3.json'
    cache=json.loads(path.read_text()) if path.exists() else {'events':{}}
    events={}
    for file in sorted((root/'data/strategy_ledgers').glob('*.jsonl')):
        for r in read_rows(file):
            if r.get('status')=='SETTLED' and r.get('strategy')!='MARKET_DAILY_REFERENCE':events.setdefault(r['event_id'],r)
    missing=[r for eid,r in sorted(events.items()) if eid not in cache['events'] or any(canonical(cache['events'][eid][k])!=canonical(r[k]) for k in ('home','away'))][:max_requests]
    failures=[]
    def get(r):
        try:
            event_id=r['event_id'].split(':',1)[1]
            if not event_id.isdigit() or r['sport'] not in SCOREBOARDS:raise ValueError('Invalid known sport/event ID')
            url=ESPN_BASE+'/'+SCOREBOARDS[r['sport']]+'/summary?event='+event_id
            raw,provenance=fetcher(url);payload=json.loads(raw)
            header=payload['header'];competition=header['competitions'][0]
            if str(header['id'])!=event_id or not competition['status']['type']['completed']:raise ValueError('Unconfirmed event')
            teams=competition['competitors'];home=next(c for c in teams if c['homeAway']=='home');away=next(c for c in teams if c['homeAway']=='away')
            if canonical(home['team']['displayName'])!=canonical(r['home']) or canonical(away['team']['displayName'])!=canonical(r['away']):raise ValueError('Event team identity mismatch')
            h,a=int(home['score']),int(away['score'])
            if min(h,a)<0:raise ValueError('Invalid final score')
            return r['event_id'],{'source':url,'verified_at_utc':now.isoformat(),'home':r['home'],'away':r['away'],
                'final':{'home':h,'away':a,'outcome':0 if h>a else 1 if h==a else 2},
                'sha256':provenance.get('sha256')},None
        except Exception as exc:return r['event_id'],None,type(exc).__name__
    with ThreadPoolExecutor(max_workers=4) as pool:
        for eid,record,error in pool.map(get,missing):
            if record:cache['events'][eid]=record
            else:failures.append({'event_id':eid,'reason':error})
    # A cache identity must still match the frozen home/away names.
    valid={eid:record for eid,record in cache['events'].items() if eid in events and
           canonical(record['home'])==canonical(events[eid]['home']) and canonical(record['away'])==canonical(events[eid]['away'])}
    if events:
        cache.update(asof_utc=now.isoformat(),requests_this_run=len(missing),failures=failures,
                     model_events_total=len(events),verified_model_events=len(valid),pending_model_events=len(events)-len(valid))
        _atomic(path,cache)
    return {'requests_this_run':len(missing),'failures':failures,'model_events_total':len(events),
            'verified_model_events':len(valid),'pending_model_events':len(events)-len(valid)}
