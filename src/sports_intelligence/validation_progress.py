"""Read-only validation progress. Historical samples never count as future proof."""
MIN_EVENTS,MIN_DATES=100,14


def validation_progress(admission,learning,research):
    groups=dict(admission.get('streams',{}))
    # Research gates also include calibration; do not replace them with a weaker
    # generic gate. Market identity includes the exact line/period hash.
    for key,value in research.get('validation_by_sport_market_version',{}).items():
        sport,market,family=key.split('|',2)
        groups['|'.join((family,sport,market))]=value
    for event in research.get('pricing_readiness',{}).get('events',[]):
        for forecast in event.get('forecasts',[]):
            key='|'.join((forecast['family'],event['sport'],forecast['market']))
            groups.setdefault(key,{})
    rows=[]
    def row(key,role,events,dates,eligible,blocks,history=0):
        family,sport,market=key.split('|',2)
        if family=='MARKET_REFERENCE':return
        spec=research.get('market_catalog',{}).get('priced_specs',{}).get(sport,{}).get(market)
        rows.append({'family':family,'sport':sport,'market':market,'market_spec':spec,'role':role,
                     'events':events,'dates':dates,'required_events':MIN_EVENTS,'required_dates':MIN_DATES,
                     'remaining_events':max(0,MIN_EVENTS-events),'remaining_dates':max(0,MIN_DATES-dates),
                     'eligible':bool(eligible),'blocks':list(dict.fromkeys(blocks)), 'history_samples':history})
    for key,value in sorted(groups.items()):
        n=value.get('n_distinct_events',0);dates=value.get('dates',0)
        blocks=list(value.get('blocks',[]))
        if n<MIN_EVENTS:blocks.append('NEEDS_100_DISTINCT_MARKET_EVENTS')
        if dates<MIN_DATES:blocks.append('NEEDS_14_DISTINCT_MARKET_DATES')
        row(key,'ORIGINAL',n,dates,value.get('eligible_for_paper',value.get('eligible_for_small_paper',False)),blocks)
    for key,value in sorted(learning.get('streams',{}).items()):
        forward=value.get('forward',{})
        n=min(forward.get('n',0),forward.get('market_n',0))
        dates=min(forward.get('dates',0),forward.get('market_dates',0))
        blocks=[]
        if value['status'] not in ('ACTIVE','SHADOW_VALIDATION'):blocks.append(value['status'])
        if n<MIN_EVENTS:blocks.append('NEEDS_100_DISTINCT_MARKET_EVENTS')
        if dates<MIN_DATES:blocks.append('NEEDS_14_DISTINCT_MARKET_DATES')
        if n>=MIN_EVENTS and dates>=MIN_DATES and not forward.get('eligible'):blocks.append('CANDIDATE_NOT_BETTER_THAN_BASE_AND_MARKET')
        row(key,'CALIBRATION',n,dates,value['status']=='ACTIVE' and forward.get('eligible'),blocks,value.get('n',0))
    return {'minimum_events':MIN_EVENTS,'minimum_dates':MIN_DATES,'rows':rows,
            'note':'Necessary sample thresholds are not automatic approval. Distinct settled frozen future events with verified market prices only; no historical rows, wallet copies or elapsed-day substitutions.'}
