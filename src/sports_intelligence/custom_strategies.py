"""Pre-registered original research strategies.

No optimisation against the reported 2024-26 evaluation window.
All coefficients are FIXED hypotheses, not learned calibration.
MARKET_ANCHORED necessarily has no historical score without point-in-time odds.
"""
from __future__ import annotations

import math

ORIGINAL_NAMES = (
    "ELO_STANDARD", "ELO_DYNAMIC", "POISSON_SHRINK",
    "DIXON_COLES_LOW", "ELO_POISSON_BLEND",
)
CUSTOM_NAMES = ("CONSENSUS_SHRUNK", "DISAGREEMENT_VETO", "MARKET_ANCHORED")
REFERENCE_NAMES = ("MARKET_DAILY_REFERENCE",)
FREQUENCY_NAMES = ("FREQ_SELECTIVE_1_3", "FREQ_BALANCED_3_5", "FREQ_BROAD_5_10", "FREQ_DAILY_5_10")
ALL_NAMES = ORIGINAL_NAMES + CUSTOM_NAMES + REFERENCE_NAMES + FREQUENCY_NAMES

# Values are exploratory selection rules, not statistical confidence intervals.
RULES = {
    "CONSENSUS_SHRUNK": {
        "version": "1.0.0", "min_team_matches": 12,
        "min_edge_pp": 4.0, "min_ev": .02, "max_quote_age_hours": 8,
        "definition": "35% Elo, 35% Poisson, 15% fast Elo, 15% DC; shrink 15% to fixed league prior."
    },
    "DISAGREEMENT_VETO": {
        "version": "1.0.0", "min_team_matches": 16,
        "min_edge_pp": 5.0, "min_ev": .04, "max_quote_age_hours": 4,
        "definition": "Blend Elo/Poisson; reject candidate unless >=3 of 4 base forecasts vote it first, and choice range <=12pp."
    },
    "MARKET_ANCHORED": {
        "version": "1.0.0", "min_team_matches": 12,
        "min_edge_pp": 4.0, "min_ev": .03, "max_quote_age_hours": 6,
        "definition": "70% fixed Elo/Poisson blend + 30% independent bookmaker consensus no-vig; at least two bookmakers."
    },
}

PRIOR_1X2 = (.43, .27, .30)
ALPHA_PRIOR = .15


def normalize(values) -> tuple[float, float, float]:
    vals = tuple(float(v) for v in values)
    if len(vals) != 3 or any(not math.isfinite(x) or x < 0 for x in vals):
        raise ValueError("Invalid 1X2 distribution")
    total = sum(vals)
    if not math.isfinite(total) or total <= 0:
        raise ValueError("Invalid probability total")
    return tuple(v / total for v in vals)


def experimental_forecasts(
    base: dict[str, tuple[float, float, float]],
    market_no_vig: list[float] | tuple[float, ...] | None = None
) -> dict[str, tuple[float, float, float] | None]:
    """Produces derived probabilities but never chooses or stakes on a match."""
    elo = normalize(base["ELO_STANDARD"])
    fast = normalize(base["ELO_DYNAMIC"])
    poisson = normalize(base["POISSON_SHRINK"])
    dc = normalize(base["DIXON_COLES_LOW"])
    shrunk = normalize([
        (1-ALPHA_PRIOR) * (.35*elo[i] + .35*poisson[i] + .15*fast[i] + .15*dc[i]) +
        ALPHA_PRIOR * PRIOR_1X2[i] for i in range(3)
    ])
    ensemble = normalize(base["ELO_POISSON_BLEND"])
    anchored = (
        normalize([.70*ensemble[i] + .30*normalize(market_no_vig)[i] for i in range(3)])
        if market_no_vig is not None else None
    )
    return {
        "CONSENSUS_SHRUNK": shrunk,
        "DISAGREEMENT_VETO": ensemble,
        "MARKET_ANCHORED": anchored,
    }


def accepts_candidate(
    strategy: str,
    selection: int,
    base: dict[str, tuple[float, float, float]],
    team_matches: int,
    edge: float,
    expected_value: float,
    quote_age_hours: float,
    bookmaker_count: int,
) -> bool:
    """Conservative fail-closed per-strategy paper-only gate."""
    if strategy not in RULES:
        return False
    rule = RULES[strategy]
    if (selection not in (0, 1, 2) or
        team_matches < rule["min_team_matches"] or
        not all(math.isfinite(v) for v in (edge, expected_value, quote_age_hours)) or
        quote_age_hours < 0 or quote_age_hours > rule["max_quote_age_hours"] or
        edge < rule["min_edge_pp"]/100 or expected_value < rule["min_ev"]):
        return False
    if strategy == "MARKET_ANCHORED" and bookmaker_count < 2:
        return False
    if strategy == "DISAGREEMENT_VETO":
        eligible = [normalize(base[k]) for k in
                    ("ELO_STANDARD", "ELO_DYNAMIC", "POISSON_SHRINK", "DIXON_COLES_LOW")]
        votes = sum(max(range(3), key=lambda idx: p[idx]) == selection for p in eligible)
        if votes < 3 or max(p[selection] for p in eligible) - min(p[selection] for p in eligible) > .12:
            return False
    return True
