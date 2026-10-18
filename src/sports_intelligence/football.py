"""Fixed-parameter football Elo; match-day updates occur AFTER that day's predictions."""
from __future__ import annotations

import csv
import datetime as dt
import io
import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from .metrics import aggregate, brier, log_loss, no_vig

ALIASES = {
    "man united": "manchester united", "man utd": "manchester united",
    "man city": "manchester city", "nott m forest": "nottingham forest",
    "nottm forest": "nottingham forest", "newcastle": "newcastle united",
    "tottenham": "tottenham hotspur", "west ham": "west ham united",
    "wolves": "wolverhampton wanderers", "brighton": "brighton hove albion",
    "bournemouth": "afc bournemouth", "leicester": "leicester city",
    "leeds": "leeds united", "sheffield united": "sheffield united",
    "ipswich": "ipswich town", "norwich": "norwich city",
}

def canonical(name: str) -> str:
    raw = unicodedata.normalize("NFKD", name.lower())
    raw = "".join(x for x in raw if not unicodedata.combining(x))
    raw = re.sub(r"[^a-z0-9]+", " ", raw).strip()
    return ALIASES.get(raw, raw)


def parse_date(text: str) -> dt.date:
    text = text.strip()
    for pattern in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    raise ValueError(f"Invalid match date: {text}")


def parse_matches(content: str, cutoff: dt.date | None = None) -> list[dict]:
    """Exclude *all* matches on cutoff day: source has date but not exact result time."""
    matches = []
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    for row in reader:
        try:
            day = parse_date(row["Date"])
            if cutoff and day >= cutoff:
                continue
            home, away = canonical(row["HomeTeam"]), canonical(row["AwayTeam"])
            hg, ag = int(row["FTHG"]), int(row["FTAG"])
            if not home or not away or home == away or hg < 0 or ag < 0:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        odds = None
        for keys in (("AvgH", "AvgD", "AvgA"), ("B365H", "B365D", "B365A")):
            try:
                odds = tuple(float(row[key]) for key in keys)
                no_vig(odds)
                break
            except (KeyError, TypeError, ValueError, OverflowError):
                odds = None
        matches.append({"date": day, "home": home, "away": away,
                        "goals_home": hg, "goals_away": ag, "odds": odds})
    return matches


@dataclass
class FootballElo:
    k: float = 20.0
    home_advantage: float = 65.0
    draw_probability: float = 0.26
    ratings: dict[str, float] = field(default_factory=dict)
    played: dict[str, int] = field(default_factory=dict)

    def predict(self, home: str, away: str) -> tuple[float, float, float]:
        h, a = canonical(home), canonical(away)
        d = self.ratings.get(h, 1500) + self.home_advantage - self.ratings.get(a, 1500)
        home_share = 1 / (1 + 10 ** (-d / 400))
        other = 1 - self.draw_probability
        return (other * home_share, self.draw_probability, other * (1 - home_share))

    def update(self, match: dict) -> None:
        h, a = match["home"], match["away"]
        current_h, current_a = self.ratings.get(h, 1500), self.ratings.get(a, 1500)
        prob = self.predict(h, a)
        expected = prob[0] + prob[1] * 0.5
        observed = 1.0 if match["goals_home"] > match["goals_away"] else (
            0.5 if match["goals_home"] == match["goals_away"] else 0.0)
        delta = self.k * (observed - expected)
        self.ratings[h] = current_h + delta
        self.ratings[a] = current_a - delta
        self.played[h] = self.played.get(h, 0) + 1
        self.played[a] = self.played.get(a, 0) + 1


def backtest_football(matches: list[dict], warmup: int = 100) -> tuple[FootballElo, dict]:
    """Chronological prequential backtest; no same-day outcomes affect same-day predictions."""
    model = FootballElo()
    groups: dict[dt.date, list[dict]] = defaultdict(list)
    # Deduplicate across downloaded season files.
    unique = {}
    for m in matches:
        key = (m["date"], m["home"], m["away"])
        unique[key] = m
    for m in unique.values():
        groups[m["date"]].append(m)
    rows = []
    history = 0
    for day in sorted(groups):
        predictions = []
        for match in groups[day]:
            probabilities = model.predict(match["home"], match["away"])
            outcome = 0 if match["goals_home"] > match["goals_away"] else (
                1 if match["goals_home"] == match["goals_away"] else 2)
            if history >= warmup:
                row = {"brier": brier(probabilities, outcome),
                       "log_loss": log_loss(probabilities, outcome), "benchmark": None}
                if match["odds"]:
                    p_market = no_vig(match["odds"])
                    row["benchmark"] = {"brier": brier(p_market, outcome),
                                        "log_loss": log_loss(p_market, outcome)}
                predictions.append(row)
        rows.extend(predictions)
        for match in groups[day]:
            model.update(match)
        history += len(groups[day])
    summary = aggregate(rows)
    summary.update({"training_matches": history, "warmup_matches": warmup,
                    "method": "prequential_walk_forward_date_grouped",
                    "parameters": {"k": model.k, "home_advantage": model.home_advantage,
                                   "draw_probability": model.draw_probability},
                    "model_status": "EXPERIMENTAL",
                    "caveat": "Historical odds may be closing prices; never use as odds available at an earlier cutoff."})
    return model, summary
