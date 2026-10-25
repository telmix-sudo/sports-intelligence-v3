"""Experimental football goal-market forecasts. Research only until timestamped odds and settlement are wired.
All inputs are pre-match learned team rates, never the fixture's result.
"""
from __future__ import annotations
import math
from .poisson import FootballPoisson, TeamCounts
from .football import canonical

def goal_rates(model: FootballPoisson, home: str, away: str) -> tuple[float,float]:
    h, a = canonical(home), canonical(away)
    hs, ass = model.home.get(h, TeamCounts()), model.away.get(a, TeamCounts())
    lh = model.smooth(model.league_home_goals, model.league_games, model.home_prior, 20)
    la = model.smooth(model.league_away_goals, model.league_games, model.away_prior, 20)
    mh = min(4.5, max(.25, lh * (model.smooth(hs.scored,hs.matches,lh,model.prior_games)/lh) * (model.smooth(ass.conceded,ass.matches,lh,model.prior_games)/lh)))
    ma = min(4.5, max(.25, la * (model.smooth(ass.scored,ass.matches,la,model.prior_games)/la) * (model.smooth(hs.conceded,hs.matches,la,model.prior_games)/la)))
    return mh, ma

def football_goal_markets(model: FootballPoisson, home: str, away: str) -> dict:
    """Returns distributions for O/U 2.5, BTTS and Asian handicap -0.5/+0.5.
    Handicap -0.5 is mathematically 1X2 home win; do not treat as independent evidence.
    """
    mh, ma = goal_rates(model, home, away)
    ph, pa = model.poisson_pmf(mh, 16), model.poisson_pmf(ma, 16)
    norm = sum(ph)*sum(pa)
    over = btts = win = 0.
    for i, x in enumerate(ph):
        for j, y in enumerate(pa):
            p = x*y/norm
            if i+j >= 3: over += p
            if i>0 and j>0: btts += p
            if i>j: win += p
    return {"OVER_UNDER_2_5":(over,1-over),
            "BTTS":(btts,1-btts),
            "ASIAN_HOME_MINUS_0_5":(win,1-win),
            "expected_goals_home":mh,"expected_goals_away":ma,
            "model_status":"RESEARCH_NOT_STAKE_ENABLED"}

def settle_goal_market(market: str, selection_index: int, home_goals: int, away_goals: int) -> bool:
    if not (isinstance(home_goals,int) and isinstance(away_goals,int) and min(home_goals,away_goals)>=0):
        raise ValueError("Final scores required")
    outcomes={"OVER_UNDER_2_5":home_goals+away_goals>=3,
              "BTTS":home_goals>0 and away_goals>0,
              "ASIAN_HOME_MINUS_0_5":home_goals>away_goals}
    if market not in outcomes or selection_index not in (0,1): raise ValueError("Unsupported market")
    return outcomes[market] == (selection_index==0)
