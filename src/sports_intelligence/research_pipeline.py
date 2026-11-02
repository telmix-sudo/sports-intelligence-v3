"""Live prospective multi-market experiment, immutable observations and settlements.

No real execution. Existing twelve wallets are never rewritten. A separate EUR1000
paper account is created ONLY on the first statistically eligible experimental
trade; until then predictions run with zero stake. Admission rules are fixed in
advance and evaluated using only results already known before today's cutoff.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import calendar
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .experimental_markets import football_goal_markets, settle_goal_market
from .feeds import SCOREBOARDS, ESPN_BASE, fetch_text, fetch_espn
from .football import canonical
from .metrics import brier, log_loss
from .north_america_models import TeamScoreBaseline
from .odds_provider import parse_utc
from .scanner import unpack_scoreboard
from .portfolio_controls import conservative_expected_value

UTC = dt.timezone.utc
LOCAL = ZoneInfo('Europe/Lisbon')
VERSION = 'MULTIMARKET-1.0.0'
NA_SPORTS = ('nba', 'nhl', 'nfl', 'mlb')
GOAL_MARKETS = ('OVER_UNDER_2_5', 'BTTS', 'ASIAN_HOME_MINUS_0_5')
MIN_PROSPECTIVE_EVENTS = 100
MIN_PROSPECTIVE_DAYS = 14
JOURNAL = 'market_research_v3.jsonl'


def read_journal(root):
    path = root / 'data' / JOURNAL
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    ids = [row['id'] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate multi-market journal ID')
    return rows


def append(root, row, rows):
    if any(r['id'] == row['id'] for r in rows):
        return False
    path = root / 'data' / JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf8') as sink:
        sink.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)+'\n')
        sink.flush()
    rows.append(row)
    return True


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    tmp.replace(path)


def collect_history(root, sports, now, fetcher=fetch_text, max_days_per_sport=32):
    path = root / 'data' / 'score_history_v3.json'
    cache = json.loads(path.read_text()) if path.exists() else {'events': {}, 'days': {}}
    cache.setdefault('days', {})
    diagnostics = {'requests': 0, 'failures': [], 'sources': [], 'games_per_sport': {}}
    tasks = []
    for sport in sorted(sports):
        seed_month = {'nba':3, 'nhl':3, 'nfl':12, 'mlb':9}[sport]
        year = now.year if now.month>seed_month else now.year-1
        recent = [now.date()-dt.timedelta(days=i) for i in range(1,33)]
        seed_end = dt.date(year,seed_month,calendar.monthrange(year,seed_month)[1])
        seed = [seed_end-dt.timedelta(days=i) for i in range(31)]
        # Interleave current season with a complete in-season month, avoiding
        # an offseason crawl before a usable baseline can be fitted.
        dates = []
        for a,b in zip(recent,seed): dates.extend((a,b))
        dates += [now.date()-dt.timedelta(days=i) for i in range(1,366)]
        selected = 0
        for day in dict.fromkeys(dates):
            if day>=now.date(): continue
            key = sport+':'+str(day)
            if key in cache['days']: continue
            query = urlencode({'dates':day.strftime('%Y%m%d'), 'limit':300})
            url = f'{ESPN_BASE}/{SCOREBOARDS[sport]}/scoreboard?{query}'
            tasks.append((sport,day,key,url))
            selected += 1
            if selected>=max_days_per_sport: break
    diagnostics['requests'] = len(tasks)
    def acquire(task):
        sport,day,key,url = task
        try:
            raw,metadata = fetcher(url)
            payload = json.loads(raw)
            if not isinstance(payload.get('events'),list) or len(payload['events'])>=300:
                raise ValueError('Invalid or possibly truncated daily results')
            events = unpack_scoreboard(payload,sport,now)
            eligible = [e for e in events if e['completed'] and e['final']
                        and day<=parse_utc(e['start_utc']).date()<=day+dt.timedelta(days=1)
                        and parse_utc(e['start_utc']).date()<now.date()]
            # Don't freeze an incomplete day forever; overnight games may not
            # have finished when this UTC day first gets queried.
            complete = all(e['completed'] or e['status'] in ('post','postponed','cancelled') for e in events)
            return task,eligible,metadata,complete,None
        except Exception as exc:
            return task,[],{},False,type(exc).__name__
    with ThreadPoolExecutor(max_workers=8) as pool:
        for task,events,meta,complete,error in pool.map(acquire,tasks):
            sport,day,key,url=task
            if error:
                diagnostics['failures'].append({'sport':sport,'date':str(day),'reason':error})
                continue
            for event in events: cache['events'][event['id']]=event
            if complete and day<now.date()-dt.timedelta(days=1): cache['days'][key]=now.isoformat()
            diagnostics['sources'].append({'url':url,'sha256':meta.get('sha256'),
                                           'captured_at_utc':now.isoformat(),'completed_games':len(events)})
    atomic_json(path,cache)
    eligible = [e for e in cache['events'].values() if e['completed'] and e['final']
                and parse_utc(e['start_utc']).date()<now.date()]
    diagnostics['games_per_sport']=dict(Counter(e['sport'] for e in eligible))
    diagnostics['cutoff_exclusive_utc_date']=now.date().isoformat()
    diagnostics['max_parallel_requests']=8
    return eligible,diagnostics


def fit_sports(history):
    models = {sport:TeamScoreBaseline(sport) for sport in NA_SPORTS}
    scores = defaultdict(list)
    grouped = defaultdict(list)
    for event in history:
        grouped[parse_utc(event['start_utc']).date()].append(event)
    for day in sorted(grouped):
        # All predictions of one date are made BEFORE ANY update of that date.
        for event in grouped[day]:
            model = models[event['sport']]
            pred = full_game_forecast(model, event['home'], event['away'])
            final = event['final']
            if pred and final['outcome'] != 1:
                outcome = 0 if final['outcome'] == 0 else 1
                scores[event['sport']].append({'brier':brier(pred['probabilities'], outcome),
                                               'log_loss':log_loss(pred['probabilities'], outcome),
                                               'p':pred['probabilities'][0], 'outcome':outcome, 'date':str(day)})
        for event in grouped[day]:
            f = event['final']
            model = models[event['sport']]
            model.update(canonical(event['home']), canonical(event['away']), f['home'], f['away'])
            reg = f.get('regulation')
            if reg and reg['home'] == reg['away'] and f['outcome'] != 1:
                model.tied_decisions += 1
                model.home_tied_wins += f['outcome'] == 0
    metrics = {sport:summarize_score_rows(rows) for sport,rows in scores.items() if rows}
    return models, metrics


def summarize_score_rows(rows):
    # Parameters are fixed; the final 20% of calendar dates is a chronological
    # holdout for diagnostics, never used to tune or select parameter values.
    dates = sorted({r['date'] for r in rows})
    split = dates[int(len(dates)*.8)] if dates else None
    holdout = [r for r in rows if r['date']>=split] if split else []
    bins = defaultdict(list)
    for r in holdout:
        bins[min(9,int(r['p']*10))].append(r)
    ece = sum(len(b)/len(holdout)*abs(sum(r['p'] for r in b)/len(b)-
                      sum(r['outcome']==0 for r in b)/len(b)) for b in bins.values()) if holdout else None
    return {'n':len(rows), 'brier':sum(r['brier'] for r in rows)/len(rows) if rows else None,
            'log_loss':sum(r['log_loss'] for r in rows)/len(rows) if rows else None,
            'uniform_brier':.5, 'uniform_log_loss':math.log(2), 'market_brier':None, 'roi':None,
            'holdout':{'from':split, 'n':len(holdout),
                       'brier':sum(r['brier'] for r in holdout)/len(holdout) if holdout else None,
                       'log_loss':sum(r['log_loss'] for r in holdout)/len(holdout) if holdout else None, 'ece':ece},
            'scope':'DATE_GROUPED_FIXED_PARAMETERS_SCORE_BACKTEST_NO_HISTORICAL_ODDS'}


def goal_backtests(matches_by_league):
    from .poisson import FootballPoisson
    scores = defaultdict(list)
    for league, matches in matches_by_league.items():
        model = FootballPoisson()
        grouped = defaultdict(list)
        for match in matches: grouped[match['date']].append(match)
        for day in sorted(grouped):
            for m in grouped[day]:
                h,a=m['home'],m['away']
                if h not in model.home or a not in model.away or min(model.home[h].matches,model.away[a].matches)<8:
                    continue
                probs = football_goal_markets(model,h,a)
                for market in GOAL_MARKETS:
                    idx = _goal_outcome(market, {'home':m['goals_home'], 'away':m['goals_away']})
                    scores[league+'|'+market].append({'brier':brier(probs[market],idx),
                        'log_loss':log_loss(probs[market],idx),'p':probs[market][0], 'outcome':idx,'date':str(day)})
            for m in grouped[day]: model.update(m)
    return {key:summarize_score_rows(rows) for key,rows in scores.items()}


def experimental_admission(score, forward):
    """Admit bounded virtual experiments, never declare a profitable/validated model.

    Initial PAPER testing follows chronological score validation against a trivial
    baseline. Once 100 prospective prices/results exist, a losing comparison with
    the market blocks further experimental stakes. Stronger paired evidence is
    reported separately for validation; it is not a prerequisite to collect paper
    test results in the first place.
    """
    reasons = []
    holdout = score.get('holdout', {})
    if score.get('n',0)<200: reasons.append('NEEDS_200_CHRONOLOGICAL_SCORE_FORECASTS')
    if holdout.get('n',0)<50: reasons.append('NEEDS_50_HOLDOUT_FORECASTS')
    if holdout.get('brier') is None or holdout['brier']>=.5: reasons.append('HOLDOUT_BRIER_NOT_BETTER_THAN_UNIFORM')
    if holdout.get('log_loss') is None or holdout['log_loss']>=math.log(2): reasons.append('HOLDOUT_LOG_LOSS_NOT_BETTER_THAN_UNIFORM')
    if holdout.get('ece') is None or holdout['ece']>.15: reasons.append('HOLDOUT_CALIBRATION_ERROR_OVER_15PP')
    if forward.get('n_distinct_events',0)>=100 and (forward['brier']>=forward['market_brier'] or
                                                   forward['log_loss']>=forward['market_log_loss']):
        reasons.append('PROSPECTIVE_MODEL_UNDERPERFORMS_MARKET')
    return {'eligible_for_small_paper':not reasons, 'blocks':reasons,
            'status':'EXPERIMENTAL_PAPER_NOT_MARKET_VALIDATED', 'score_holdout':holdout}


def full_game_forecast(model, home, away):
    pred = model.forecast(canonical(home), canonical(away))
    if pred['status'] == 'INSUFFICIENT_PRIOR_GAMES':
        return None
    p = pred['home_probability']
    if model.sport in ('nhl', 'mlb'):
        # A declared, separately evaluated tie-resolution assumption. Never
        # mislabel a 3-way regulation forecast as a 2-way moneyline.
        tie_rate = (model.home_tied_wins+5)/(model.tied_decisions+10)
        p += pred['draw_regulation_probability']*tie_rate
        pred['tie_resolution_home_probability'] = tie_rate
        pred['tie_resolution_sample'] = model.tied_decisions
        pred['tie_resolution_method'] = 'BETA_5_5_EMPIRICAL_PRIOR'
    pred['probabilities'] = [p, 1-p]
    pred['rules'] = 'FULL_GAME_INCLUDING_OVERTIME_OR_EXTRA_INNINGS'
    pred['version'] = VERSION+'-'+model.sport
    return pred


def _goal_outcome(market, final):
    return 0 if settle_goal_market(market, 0, final['home'], final['away']) else 1


def settlement_outcome(forecast, final):
    if forecast['market'] in GOAL_MARKETS:
        return _goal_outcome(forecast['market'], final)
    if final['outcome'] == 1:
        return None  # NFL two-way draw void; never count in model scoring.
    return 0 if final['outcome'] == 0 else 1


def reconcile(root, rows, events, now, result_fetcher=fetch_espn):
    forecasts = {r['id']:r for r in rows if r['kind']=='FORECAST'}
    settled = {r['forecast_id'] for r in rows if r['kind']=='SETTLEMENT'}
    finals = {e['id']:e for e in events if e.get('completed') and e.get('final')}
    requests = {}
    errors = []
    for f in forecasts.values():
        if f['id'] in settled or parse_utc(f['start_utc']) > now or f['event_id'] in finals:
            continue
        key = (f['sport'], parse_utc(f['start_utc']).date())
        if key in requests:
            continue
        requests[key] = True
        try:
            payload, _ = result_fetcher(SCOREBOARDS[key[0]], key[1])
            for e in unpack_scoreboard(payload, key[0], now):
                if e['completed'] and e['final']:
                    finals[e['id']] = e
        except Exception as exc:
            errors.append({'sport':key[0], 'date':str(key[1]), 'reason':type(exc).__name__})
    count = 0
    enriched = set()
    for fid, f in forecasts.items():
        if fid in settled or f['event_id'] not in finals or parse_utc(f['start_utc']) > now:
            continue
        e = finals[f['event_id']]
        if f.get('market_spec'):
            from .market_catalog import outcome_for_spec,enrich_final
            if f['market_spec']['metric']!='goals' and not e['final'].get('team_statistics') and e['id'] not in enriched:
                enriched.add(e['id'])
                try:
                    e=enrich_final(e)
                    finals[e['id']]=e
                except Exception as exc:
                    errors.append({'event_id':e['id'],'reason':'STAT_RESULT_'+type(exc).__name__});continue
            outcome=outcome_for_spec(f['market_spec'],e['final'])
            if outcome is None:continue
            if outcome=='VOID':outcome=None
        else:outcome = settlement_outcome(f, e['final'])
        row = {'id':'SET-'+fid, 'kind':'SETTLEMENT', 'forecast_id':fid,
               'event_id':e['id'], 'settlement_utc':now.isoformat(),
               'status':'VOID' if outcome is None else 'SETTLED',
               'outcome':outcome, 'official_final':e['final'],
               'source':f"{ESPN_BASE}/{SCOREBOARDS[e['sport']]}/summary?event={e['id'].split(':',1)[1]}",
               'pnl_eur':0.0, 'closing_line_value':None,
               'closing_line_reason':'No same-bookmaker quote captured in last 30 minutes before start.'}
        closing = []
        for snapshot in rows:
            if snapshot['kind']!='QUOTE' or snapshot['forecast_id']!=fid:
                continue
            i = f['selection_index']
            updated = parse_utc(snapshot['book_quote_updates_utc'][i])
            start = parse_utc(f['start_utc'])
            if (snapshot['bookmakers'][i]==f['bookmakers'][i] and
                parse_utc(snapshot['captured_at_utc'])<start and
                dt.timedelta(0)<=start-updated<=dt.timedelta(minutes=30)):
                closing.append(snapshot)
        if closing:
            latest = max(closing, key=lambda q:q['captured_at_utc'])
            row['closing_line_value'] = f['odds'][f['selection_index']]/latest['odds'][f['selection_index']]-1
            row['closing_line_reason'] = 'Same-bookmaker last observed quote within 30 minutes; not guaranteed exact closing price.'
        if outcome is not None:
            row.update(brier=brier(f['probabilities'], outcome), log_loss=log_loss(f['probabilities'], outcome),
                       market_brier=brier(f['market_no_vig'], outcome),
                       market_log_loss=log_loss(f['market_no_vig'], outcome))
            row['pnl_eur'] = round(f['stake_eur']*(f['odds'][f['selection_index']]-1)
                                   if outcome==f['selection_index'] else -f['stake_eur'], 4)
        count += append(root, row, rows)
    return count, errors


def validation(rows, before):
    """Frozen admission: paired Brier uncertainty, log loss, ECE and sample size.

    Use only results recorded before the UTC start of today's forecast date.
    One event per sport/market/version; multiple positions never inflate N.
    This is a research admission threshold, not proof of profitable execution.
    """
    forecasts = {r['id']:r for r in rows if r['kind']=='FORECAST'}
    groups = defaultdict(dict)
    for r in rows:
        if r['kind']!='SETTLEMENT' or r['status']!='SETTLED' or parse_utc(r['settlement_utc']) >= before:
            continue
        f = forecasts[r['forecast_id']]
        groups[(f['sport'], f['market'], f['model_version'])][f['event_id']] = (f,r)
    result = {}
    for (sport,market,version), unique in groups.items():
        pairs = list(unique.values())
        n = len(pairs)
        diffs = [r['brier']-r['market_brier'] for _,r in pairs]
        mean = sum(diffs)/n
        # Brier pair differences are bounded [-2,2]. Use a fixed, distribution-free
        # Hoeffding bound rather than an uncalibrated normal interval.
        upper = mean+4*math.sqrt(math.log(20)/(2*n))
        avg_log = sum(r['log_loss'] for _,r in pairs)/n
        market_log = sum(r['market_log_loss'] for _,r in pairs)/n
        bins = defaultdict(list)
        for f,r in pairs:
            p = f['probabilities'][0]
            bins[min(9,int(p*10))].append((p, r['outcome']==0))
        ece = sum(len(b)/n*abs(sum(p for p,y in b)/len(b)-sum(y for p,y in b)/len(b)) for b in bins.values())
        dates = sorted({parse_utc(f['forecast_utc']).date() for f,r in pairs})
        span = (dates[-1]-dates[0]).days+1
        reasons = []
        if n < MIN_PROSPECTIVE_EVENTS: reasons.append('NEEDS_100_DISTINCT_EVENTS')
        if span < MIN_PROSPECTIVE_DAYS: reasons.append('NEEDS_14_DAY_FORWARD_PERIOD')
        if upper >= 0: reasons.append('PAIRED_BRIER_EDGE_NOT_ESTABLISHED')
        if avg_log >= market_log: reasons.append('LOG_LOSS_NOT_BETTER_THAN_MARKET')
        if ece > .10: reasons.append('CALIBRATION_ERROR_OVER_10PP')
        key = '|'.join((sport,market,version))
        result[key] = {'n_distinct_events':n, 'days':span, 'brier':sum(r['brier'] for _,r in pairs)/n,
                       'market_brier':sum(r['market_brier'] for _,r in pairs)/n,
                       'log_loss':avg_log, 'market_log_loss':market_log,
                       'paired_brier_difference_upper_bound':upper, 'ece':ece,
                       'eligible_for_small_paper':not reasons, 'blocks':reasons,
                       'method':'FIXED_VERSION_PRE_DAY_PAIRED_HOEFFDING_BOUND_95_PERCENT'}
    return result


def paper_state(rows):
    forecasts = {r['id']:r for r in rows if r['kind']=='FORECAST'}
    settled = {r['forecast_id']:r for r in rows if r['kind']=='SETTLEMENT'}
    trades = [f for f in forecasts.values() if f['stake_eur']>0]
    pnl = sum(settled[f['id']]['pnl_eur'] for f in trades if f['id'] in settled)
    return {'created':bool(trades), 'initial_bankroll_eur':1000 if trades else None,
            'bankroll_eur':round(1000+pnl,4) if trades else None,
            'pnl_eur':round(pnl,4), 'positions':len(trades),
            'open_exposure_eur':sum(f['stake_eur'] for f in trades if f['id'] not in settled),
            'open':sum(f['id'] not in settled for f in trades),
            'note':'Separate experimental virtual account; original twelve wallets preserved.'}


def run_research(root, events, football_models, now, risk, football_history=None, history_fetcher=fetch_text,
                 result_fetcher=fetch_espn, previous_settlement_result=None, collect_team_stats=False):
    from .activity_policy import daily_entry_stake
    rows = read_journal(root)
    newly_settled, settlement_errors = (previous_settlement_result if previous_settlement_result is not None
                                       else reconcile(root, rows, events, now, result_fetcher))
    cutoff = dt.datetime.combine(now.date(), dt.time(), UTC)
    readiness = validation(rows, cutoff)
    sports = {e['sport'] for e in events if e['sport'] in NA_SPORTS}
    history, history_info = collect_history(root, sports, now, history_fetcher) if sports else ([], {'games_per_sport':{}, 'requests':0, 'failures':[]})
    models, backtest = fit_sports(history)
    goal_scores = goal_backtests(football_history or {})
    from .market_catalog import collect_statistics,CountModel,generic_backtests,distribution
    stat_history=[];stat_info={"events_with_statistics":0,"failures":[]}
    if collect_team_stats and any(e['sport'] in football_models for e in events):
        stat_history,stat_info=collect_statistics(root,events,now,result_fetcher,history_fetcher,football_history=football_history)
    specs=defaultdict(dict)
    for e in events:
        for mid,q in e.get('market_quotes',{}).items():
            if q.get('market_spec'):specs[e['sport']][mid]=q['market_spec']
    generic_scores=generic_backtests(football_history or {},stat_history,specs)
    stat_models={}
    for e in stat_history:
        for metric in ('corners','offsides','fouls','throw_ins','shots','shots_on_target'):
            stat_models.setdefault((e['sport'],metric),CountModel(metric)).update(e)
    digest = hashlib.sha256('|'.join(sorted(e['id'] for e in history)).encode()).hexdigest()
    blocks = Counter()
    candidates = 0
    added = 0
    state = paper_state(rows)
    today = now.astimezone(LOCAL).date()
    daily_papers = sum(r['kind']=='FORECAST' and r['stake_eur']>0 and
                       parse_utc(r['forecast_utc']).astimezone(LOCAL).date()==today for r in rows)
    # Qualified goal/stat markets get admission room before the ML-only queue;
    # missing or unqualified extra markets never force an entry or waste a slot.
    for e in sorted(events, key=lambda e:(not bool(e.get('market_quotes')),e['start_utc'])):
        if e['status']!='pre' or not now < parse_utc(e['start_utc']) <= now+dt.timedelta(hours=48):
            continue
        outputs = {}
        if e['sport'] in football_models:
            from .strategy_lab import team_name
            model = football_models[e['sport']]['POISSON_SHRINK']
            h,a = team_name(e['home']), team_name(e['away'])
            sample = min(model.home.get(h).matches if h in model.home else 0,
                         model.away.get(a).matches if a in model.away else 0)
            if sample >= 8:
                goal = football_goal_markets(model, h,a)
                outputs = {m:{'probabilities':list(goal[m]), 'sample_min':sample,
                              'version':VERSION+'-goals', 'rules':'REGULATION_90_MINUTES',
                              'expected_home':goal['expected_goals_home'],
                              'expected_away':goal['expected_goals_away']} for m in GOAL_MARKETS}
            else: blocks['insufficient_football_home_away_history'] += 1
        elif e['sport'] in models:
            pred = full_game_forecast(models[e['sport']], e['home'], e['away'])
            if pred: outputs['ML'] = pred
            else: blocks['insufficient_'+e['sport']+'_history'] += 1
        for market,q in e.get('market_quotes',{}).items():
            spec=q.get('market_spec')
            if not spec:continue
            if spec.get('settlement_block'):
                blocks[spec['settlement_block']]+=1;continue
            if spec['metric']=='goals' and e['sport'] in football_models:
                from .experimental_markets import goal_rates
                from .strategy_lab import team_name
                m=football_models[e['sport']]['POISSON_SHRINK'];h,a=team_name(e['home']),team_name(e['away'])
                sample=min(m.home[h].matches if h in m.home else 0,m.away[a].matches if a in m.away else 0)
                if sample<8:continue
                mh,ma=goal_rates(m,h,a)
                pred=distribution(mh,ma,spec)|{'sample_min':sample,'version':VERSION+'-goal-lines',
                    'expected_home':mh,'expected_away':ma,'rules':'REGULATION_90_MINUTES'}
            else:
                m=stat_models.get((e['sport'],spec['metric']))
                pred=m.forecast(e['home'],e['away'],spec) if m else None
            if pred:outputs[market]=dict(pred,market_spec=spec)
            else:blocks['insufficient_'+spec['metric']+'_history']+=1
        for market,pred in outputs.items():
            candidates += 1
            q = e.get('quote') if market=='ML' else e.get('market_quotes',{}).get(market)
            if not q or q.get('market')!=market or not q.get('quote_timestamp_available'):
                blocks['missing_timestamped_'+market+'_odds'] += 1
                continue
            try:
                prices = [float(v) for v in q['decimal']]
                p_market = [float(v) for v in q['no_vig']]
                times = q['best_book_updates']
                books = q.get('bookmakers')
                if (not isinstance(books,list) or len(books)!=2 or not all(isinstance(b,str) and b for b in books) or
                    len(prices)!=2 or len(p_market)!=2 or len(times)!=2 or
                    any(not math.isfinite(v) or v<=1 for v in prices) or
                    any(not math.isfinite(v) or not 0<v<1 for v in p_market) or
                    abs(sum(p_market)-1)>.001 or
                    any(not 0 <= (now-parse_utc(t)).total_seconds() <= 12*3600 for t in times)):
                    raise ValueError('Invalid paired quotes')
            except (ValueError, KeyError, TypeError):
                blocks['invalid_quote'] += 1
                continue
            fid = 'R3-'+hashlib.sha256(f"{e['id']}|{market}|{pred['version']}".encode()).hexdigest()[:20]
            quote_id = 'Q-'+fid+'-'+hashlib.sha256(json.dumps([prices,times,q.get('bookmakers')],sort_keys=True).encode()).hexdigest()[:14]
            previous = next((r for r in rows if r['id']==fid),None)
            if previous:
                append(root, {'id':quote_id,'kind':'QUOTE','forecast_id':fid,'event_id':e['id'],
                              'captured_at_utc':now.isoformat(),'odds':prices,'bookmakers':q.get('bookmakers'),
                              'book_quote_updates_utc':times},rows)
                if any(r['kind']=='FORECAST' and r['event_id']==e['id'] and r['market']==market and
                       r['model_version']==pred['version'] and r['stake_eur']>0 for r in rows):
                    continue
            p = pred['probabilities']
            choice = max(range(2), key=lambda i:p[i]-p_market[i])
            decisive = 1-pred.get('push_probability',0.0)
            ev = decisive*(p[choice]*prices[choice]-1)
            conservative_ev = decisive*conservative_expected_value(p[choice], prices[choice], pred['sample_min'])
            gate_key = '|'.join((e['sport'],market,pred['version']))
            forward = readiness.get(gate_key, {})
            score = backtest.get(e['sport'], {}) if market=='ML' else (generic_scores if pred.get('market_spec') else goal_scores).get(e['sport']+'|'+market,{})
            gate = experimental_admission(score, forward)
            reasons = list(gate['blocks'])
            if (q.get('bookmaker_count') or 0)<2: reasons.append('NEEDS_TWO_BOOKMAKERS')
            if p[choice]-p_market[choice]<.04: reasons.append('EDGE_BELOW_4PP')
            if conservative_ev<=.01: reasons.append('CONSERVATIVE_EV_BELOW_1_PERCENT')
            if daily_papers>=10: reasons.append('DAILY_PAPER_LIMIT')
            # Only one experimental risk-bearing position per distinct event.
            if any(r['kind']=='FORECAST' and r['event_id']==e['id'] and r['stake_eur']>0 for r in rows):
                reasons.append('EXPERIMENTAL_EVENT_ALREADY_TRADED')
            stake = 0.0
            if not reasons:
                bank = state['bankroll_eur'] if state['created'] else 1000.0
                requested = daily_entry_stake(bank,state['open_exposure_eur'],daily_papers)
                stake = risk.reserve(e['id'], requested)
                if not stake: reasons.append('AGGREGATE_RISK_LIMIT')
            if previous and not stake:
                for reason in reasons:blocks[reason]+=1
                continue
            source_forecast_id=fid if previous else None
            if previous:
                # Append a new prospective position; never edit the earlier
                # zero-stake forecast, its price, clock or admission evidence.
                fid += '-PAPER'
                quote_id = 'Q-'+fid+'-'+hashlib.sha256(json.dumps([prices,times,q.get('bookmakers')],sort_keys=True).encode()).hexdigest()[:14]
            row = {'id':fid, 'kind':'FORECAST', 'event_id':e['id'], 'sport':e['sport'],
                   'home':e['home'], 'away':e['away'], 'market':market, 'market_spec':pred.get('market_spec'),
                   'start_utc':e['start_utc'], 'forecast_utc':now.isoformat(),
                   'model_version':pred['version'], 'probabilities':p,
                   'model_inputs':pred, 'training_cutoff_exclusive_utc_date':now.date().isoformat(),
                   'history_event_ids_sha256':digest if e['sport'] in NA_SPORTS else None,
                   'market_no_vig':p_market, 'odds':prices, 'selection_index':choice,
                   'bookmakers':q.get('bookmakers'), 'book_quote_updates_utc':times,
                   'bookmaker_count':q.get('bookmaker_count'), 'price_source':q.get('provider'),
                   'market_rules':pred['rules'], 'edge_pp':100*(p[choice]-p_market[choice]),
                   'expected_value':ev, 'conservative_ev':conservative_ev,
                   'stake_eur':stake, 'paper_only':True, 'admission_blocks':reasons,
                   'classification':'PAPER_POSITION' if stake else 'ZERO_STAKE_VALIDATION',
                   'admission_evidence':gate, 'uncertainty_calibrated':False, 'critical_news_inputs':'NOT_MODELED_IN_BASELINE'}
            if source_forecast_id:row['source_forecast_id']=source_forecast_id
            if append(root, row, rows):
                added += 1
                append(root, {'id':quote_id,'kind':'QUOTE','forecast_id':fid,'event_id':e['id'],
                              'captured_at_utc':now.isoformat(),'odds':prices,'bookmakers':q.get('bookmakers'),
                              'book_quote_updates_utc':times},rows)
                if stake:
                    daily_papers += 1
                    state = paper_state(rows)
            for reason in reasons: blocks[reason] += 1
    f = [r for r in rows if r['kind']=='FORECAST']
    s = [r for r in rows if r['kind']=='SETTLEMENT']
    settled_ids = {r['forecast_id'] for r in s}
    report = {'asof_utc':now.isoformat(), 'status':'OPERATIONAL_PROSPECTIVE_PAPER_RESEARCH',
              'new_forecasts':added, 'new_settlements':newly_settled,
              'forecasts_total':len(f), 'zero_stake_forecasts':sum(r['stake_eur']==0 for r in f),
              'open_observations':sum(r['id'] not in settled_ids and r['stake_eur']==0 for r in f),
              'settlements_total':len(s), 'candidate_markets':candidates,
              'closing_line_values_available':sum(r.get('closing_line_value') is not None for r in s),
              'blocks':dict(blocks), 'history':history_info, 'score_backtests':backtest, 'goal_backtests':goal_scores,
              'team_statistics_history':stat_info,'additional_market_backtests':generic_scores,
              'market_catalog':{'requested':['goals','corners','offsides','fouls','cards','shots','throw_ins'],
                                'priced_specs':{s:dict(v) for s,v in specs.items()},
                                'provider_limitations':{'offsides':'NO_CURRENT_PROVIDER_MARKET','throw_ins':'NO_CURRENT_PROVIDER_MARKET','fouls':'NO_CURRENT_PROVIDER_MARKET'}},
              'validation_by_sport_market_version':readiness, 'experimental_account':paper_state(rows),
              'settlement_errors':settlement_errors, 'paper_only':True,
              'goal_markets':list(GOAL_MARKETS), 'sports':list(NA_SPORTS),
              'next_step':'Automatic timestamped forecasts and official result settlement on every lab run.',
              'admission_policy':{'paper_entry_after_score_backtest':True, 'minimum_score_forecasts':200,
                                   'minimum_score_holdout':50,
                                   'market_validation_minimum_independent_events':MIN_PROSPECTIVE_EVENTS,
                                   'minimum_forward_days':MIN_PROSPECTIVE_DAYS,
                                   'stake_fraction':.0025, 'first_five_stake_fraction':.001,
                                   'minimum_stake_eur':1.0,'max_exposure_fraction':.02,
                                   'min_papers_per_day':5, 'max_papers_per_day':10, 'real_money_allowed':False}}
    atomic_json(root/'reports'/'market_research_latest.json', report)
    return report
