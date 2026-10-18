"""Surface-aware ATP Elo with pre-tournament predictions (no within-event leakage).

Jeff Sackmann's tennis_atp dataset is CC BY-NC-SA: download only for permitted research
and never commit or redistribute the original dataset.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import math
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from .metrics import aggregate, brier, log_loss


def player_key(name: str) -> str:
    return " ".join(name.casefold().split())


@dataclass
class TennisElo:
    k: float = 24.0
    ratings: dict[str, float] = field(default_factory=dict)
    surfaces: dict[tuple[str, str], float] = field(default_factory=dict)

    def predict(self, player_a: str, player_b: str, surface: str) -> tuple[float, float]:
        a, b = player_key(player_a), player_key(player_b)
        s = surface.lower()
        overall_a = self.ratings.get(a, 1500)
        overall_b = self.ratings.get(b, 1500)
        sa = self.surfaces.get((a, s), overall_a)
        sb = self.surfaces.get((b, s), overall_b)
        delta = (overall_a - overall_b) * 0.5 + (sa - sb) * 0.5
        p = 1 / (1 + 10 ** (-delta / 400))
        return p, 1 - p

    def update(self, winner: str, loser: str, surface: str) -> None:
        w, l, s = player_key(winner), player_key(loser), surface.lower()
        p_w, _ = self.predict(w, l, s)
        dw = self.k * (1 - p_w)
        self.ratings[w] = self.ratings.get(w, 1500) + dw
        self.ratings[l] = self.ratings.get(l, 1500) - dw
        self.surfaces[(w, s)] = self.surfaces.get((w, s), 1500) + dw
        self.surfaces[(l, s)] = self.surfaces.get((l, s), 1500) - dw


def parse_matches(content: str, cutoff: dt.date | None = None) -> list[dict]:
    result = []
    for r in csv.DictReader(io.StringIO(content.lstrip("\ufeff"))):
        try:
            day = dt.datetime.strptime(r["tourney_date"], "%Y%m%d").date()
            if cutoff and day >= cutoff:
                continue
            winner, loser = r["winner_name"].strip(), r["loser_name"].strip()
            surface = (r.get("surface") or "unknown").strip()
            event = r["tourney_id"].strip()
            if not (winner and loser and event and player_key(winner) != player_key(loser)):
                continue
            result.append({"date": day, "event": event, "winner": winner, "loser": loser,
                           "surface": surface})
        except (KeyError, ValueError, TypeError):
            continue
    return result


def backtest_tennis(matches: list[dict], warmup: int = 200) -> tuple[TennisElo, dict]:
    """Use only ratings frozen before each tournament-start date.

    tourney_date is not match timestamp. All matches with the same date are
    predicted from a single rating snapshot; only then are results ingested.
    """
    model = TennisElo()
    groups = defaultdict(list)
    unique = {}
    for m in matches:
        key = (m["event"], m["winner"], m["loser"])
        unique[key] = m
    for m in unique.values():
        groups[m["date"]].append(m)
    history = 0
    rows = []
    for day in sorted(groups):
        for m in groups[day]:
            a, b = sorted((m["winner"], m["loser"]), key=player_key)
            predicted = model.predict(a, b, m["surface"])
            outcome = 0 if a == m["winner"] else 1
            if history >= warmup:
                rows.append({"brier": brier(predicted, outcome),
                             "log_loss": log_loss(predicted, outcome),
                             "benchmark": {"brier": 0.5, "log_loss": math.log(2)}})
        for m in groups[day]:
            model.update(m["winner"], m["loser"], m["surface"])
        history += len(groups[day])
    summary = aggregate(rows)
    summary.update({"training_matches": history, "warmup_matches": warmup,
                    "method": "prequential_tournament_start_date_grouped",
                    "model_status": "EXPERIMENTAL",
                    "benchmark_name": "constant_50_50",
                    "license_warning": "Jeff Sackmann CC BY-NC-SA — non-commercial use only; raw data not redistributed."})
    return model, summary
