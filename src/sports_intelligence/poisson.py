"""Transparent shrinkage Poisson 1X2 baseline; never sees outcomes from match day."""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from dataclasses import dataclass, field

from .football import canonical
from .metrics import brier, log_loss, no_vig, aggregate


@dataclass
class TeamCounts:
    matches: int = 0
    scored: int = 0
    conceded: int = 0


@dataclass
class FootballPoisson:
    prior_games: float = 8.0
    home_prior: float = 1.50
    away_prior: float = 1.20
    home: dict[str, TeamCounts] = field(default_factory=dict)
    away: dict[str, TeamCounts] = field(default_factory=dict)
    league_games: int = 0
    league_home_goals: int = 0
    league_away_goals: int = 0
    played: dict[str, int] = field(default_factory=dict)

    @staticmethod
    def smooth(goals: int, games: int, prior: float, pseudo_games: float) -> float:
        return (goals + pseudo_games * prior) / (games + pseudo_games)

    @staticmethod
    def poisson_pmf(lmbda: float, max_goals: int = 12) -> list[float]:
        p = math.exp(-lmbda)
        values = [p]
        for goals in range(1, max_goals + 1):
            p *= lmbda / goals
            values.append(p)
        return values

    def predict(self, home: str, away: str) -> tuple[float, float, float]:
        h, a = canonical(home), canonical(away)
        hstats = self.home.get(h, TeamCounts())
        astats = self.away.get(a, TeamCounts())
        # Always learned solely from prior matches; same-day score updates come later.
        league_h = self.smooth(self.league_home_goals, self.league_games, self.home_prior, 20)
        league_a = self.smooth(self.league_away_goals, self.league_games, self.away_prior, 20)
        h_attack = self.smooth(hstats.scored, hstats.matches, league_h, self.prior_games) / league_h
        h_defense = self.smooth(hstats.conceded, hstats.matches, league_a, self.prior_games) / league_a
        a_attack = self.smooth(astats.scored, astats.matches, league_a, self.prior_games) / league_a
        a_defense = self.smooth(astats.conceded, astats.matches, league_h, self.prior_games) / league_h
        mu_h = min(4.5, max(0.25, league_h * h_attack * a_defense))
        mu_a = min(4.5, max(0.25, league_a * a_attack * h_defense))
        home_pmfs = self.poisson_pmf(mu_h)
        away_pmfs = self.poisson_pmf(mu_a)
        scores = [0.0, 0.0, 0.0]
        for i, ph in enumerate(home_pmfs):
            for j, pa in enumerate(away_pmfs):
                scores[0 if i > j else (1 if i == j else 2)] += ph * pa
        normalizer = sum(scores)
        return tuple(x / normalizer for x in scores)

    def update(self, match: dict) -> None:
        home, away = match["home"], match["away"]
        h, a = match["goals_home"], match["goals_away"]
        for tbl, team, scored, conceded in ((self.home, home, h, a), (self.away, away, a, h)):
            old = tbl.setdefault(team, TeamCounts())
            old.matches += 1
            old.scored += scored
            old.conceded += conceded
        self.league_games += 1
        self.league_home_goals += h
        self.league_away_goals += a
        self.played[home] = self.played.get(home, 0) + 1
        self.played[away] = self.played.get(away, 0) + 1


def backtest_poisson(matches: list[dict], warmup: int = 100) -> tuple[FootballPoisson, dict]:
    model = FootballPoisson()
    groups = defaultdict(list)
    unique = {(m["date"], m["home"], m["away"]): m for m in matches}
    for m in unique.values():
        groups[m["date"]].append(m)
    rows, history = [], 0
    for day in sorted(groups):
        for match in groups[day]:
            p = model.predict(match["home"], match["away"])
            result = 0 if match["goals_home"] > match["goals_away"] else (
                1 if match["goals_home"] == match["goals_away"] else 2)
            if history >= warmup:
                entry = {"brier": brier(p, result), "log_loss": log_loss(p, result), "benchmark": None}
                if match["odds"]:
                    market = no_vig(match["odds"])
                    entry["benchmark"] = {"brier": brier(market, result), "log_loss": log_loss(market, result)}
                rows.append(entry)
        for match in groups[day]:
            model.update(match)
        history += len(groups[day])
    score = aggregate(rows)
    score.update({"training_matches": history, "warmup_matches": warmup,
                  "model_status": "EXPERIMENTAL",
                  "method": "prequential_independent_poisson_shrinkage_date_grouped",
                  "parameters": {"prior_games": model.prior_games,
                                 "home_prior": model.home_prior, "away_prior": model.away_prior},
                  "caveat": "Closing market odds are retrospective benchmarks only, not real-time actionable prices."})
    return model, score
