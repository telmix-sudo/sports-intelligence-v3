"""Sport-specific NBA/NHL exploratory baselines; no staking or fabricated team inputs.

Fit only chronologically prior finalized scores. Do not use bookmaker prices as
training labels. NBA normal margin and NHL independent Poisson goals are distinct
from football 1X2 models. Overtime/shootout settlement requires market rules.
"""
from __future__ import annotations
from collections import defaultdict
import math

def normal_cdf(x: float) -> float:
    return .5*(1+math.erf(x/math.sqrt(2)))

def poisson_pmf(mu:float,n:int=18)->list[float]:
    p=math.exp(-mu); out=[p]
    for i in range(1,n+1):
        p*=mu/i;out.append(p)
    return out

class TeamScoreBaseline:
    def __init__(self,sport:str,prior_games:float=12):
        if sport not in ("nba","nhl"):raise ValueError("Unsupported sport")
        self.sport=sport
        self.prior_games=prior_games
        self.teams=defaultdict(lambda:[0,0.,0.])
        self.games=0
    def update(self,home:str,away:str,home_score:int,away_score:int):
        if min(home_score,away_score)<0:raise ValueError("Invalid score")
        for team,scored,conceded in ((home,home_score,away_score),(away,away_score,home_score)):
            a=self.teams[team];a[0]+=1;a[1]+=scored;a[2]+=conceded
        self.games+=1
    def forecast(self,home:str,away:str)->dict:
        h,a=self.teams[home],self.teams[away]
        minimum=12 if self.sport=="nba" else 16
        if min(h[0],a[0])<minimum:
            return {"status":"INSUFFICIENT_PRIOR_GAMES","sample_min":min(h[0],a[0])}
        base=112.0 if self.sport=="nba" else 3.0
        def sm(total,n):return (total+self.prior_games*base)/(n+self.prior_games)
        home_mu=(sm(h[1],h[0])+sm(a[2],a[0]))/2
        away_mu=(sm(a[1],a[0])+sm(h[2],h[0]))/2
        if self.sport=="nba":
            # Exploratory fixed home edge and game-margin SD, NOT calibrated.
            p=normal_cdf((home_mu-away_mu+2.0)/12.0)
        else:
            # Regulation 60-minute probabilities; ties retained, not a moneyline.
            ph,pa=poisson_pmf(home_mu),poisson_pmf(away_mu)
            norm=sum(ph)*sum(pa)
            win=sum(x*y for i,x in enumerate(ph) for j,y in enumerate(pa) if i>j)/norm
            tie=sum(x*y for i,x in enumerate(ph) for j,y in enumerate(pa) if i==j)/norm
            p=win
        return {"status":"RESEARCH_ONLY_UNCALIBRATED","sport":self.sport,
                "sample_min":min(h[0],a[0]),"home_expected":home_mu,"away_expected":away_mu,
                "home_probability":p,"away_probability":1-p if self.sport=="nba" else 1-p-tie,
                "draw_regulation_probability":tie if self.sport=="nhl" else None,
                "warning":"NBA: overtime-inclusive provisional margin; NHL: regulation only; verify settlement rules"}
