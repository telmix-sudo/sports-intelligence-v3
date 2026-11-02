"""Prospective-only aggregate paper risk, diagnostics and conservative EV checks.

Controls never edit existing ledgers or rewrite historical predictions.
They affect only newly registered paper entries. Totals are across all 12 wallets.
"""
from __future__ import annotations

from collections import defaultdict
import datetime as dt
import json
import math
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from zoneinfo import ZoneInfo

INITIAL_TOTAL_BANK = 12_000.0
MAX_EVENT_OPEN_FRACTION = .003  # 36 EUR aggregate per distinct game
MAX_PORTFOLIO_OPEN_FRACTION = .02  # 240 EUR aggregate
MIN_MEANINGFUL_STAKE_EUR = 1.0
LOCAL_TZ = ZoneInfo("Europe/Lisbon")


class PaperRiskBook:
    """Single mutable budget shared by all account openers in one lab execution."""

    def __init__(self, root: Path):
        self.event_exposure = defaultdict(float)
        self.total_exposure = 0.0
        self.event_legs = defaultdict(int)
        for path in (root / "data" / "strategy_ledgers").glob("*.jsonl"):
            for line in path.read_text(encoding="utf8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("status") != "OPEN":
                    continue
                stake = float(record.get("stake_eur") or 0)
                if not math.isfinite(stake) or stake < 0:
                    raise ValueError("Invalid virtual open exposure")
                self.event_exposure[record["event_id"]] += stake
                self.total_exposure += stake
                self.event_legs[record["event_id"]] += 1
        journal = root / "data" / "market_research_v3.jsonl"
        if journal.exists():
            rows = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
            settled = {r["forecast_id"] for r in rows if r.get("kind")=="SETTLEMENT"}
            for r in rows:
                if r.get("kind")!="FORECAST" or r["id"] in settled:
                    continue
                stake = float(r.get("stake_eur",0))
                if not math.isfinite(stake) or stake<0:
                    raise ValueError("Invalid experimental paper exposure")
                if stake:
                    self.event_exposure[r["event_id"]] += stake
                    self.total_exposure += stake
                    self.event_legs[r["event_id"]] += 1
        self.rejections = defaultdict(int)

    def reserve(self, event_id: str, requested: float) -> float:
        if not event_id or not math.isfinite(requested) or requested <= 0:
            self.rejections["invalid_stake"] += 1
            return 0.0
        available = min(
            requested,
            INITIAL_TOTAL_BANK * MAX_EVENT_OPEN_FRACTION - self.event_exposure[event_id],
            INITIAL_TOTAL_BANK * MAX_PORTFOLIO_OPEN_FRACTION - self.total_exposure,
        )
        # Rounding up could exceed the remaining account/event budget. Virtual
        # stakes are whole cents and must always stay below the requested size.
        size = float(Decimal(str(max(0.0, available))).quantize(Decimal("0.01"), rounding=ROUND_DOWN))
        if size < MIN_MEANINGFUL_STAKE_EUR:
            self.rejections["aggregate_event_or_portfolio_limit"] += 1
            return 0.0
        self.event_exposure[event_id] += size
        self.total_exposure += size
        self.event_legs[event_id] += 1
        return size

    def summary(self) -> dict:
        return {
            "max_event_open_eur": round(INITIAL_TOTAL_BANK * MAX_EVENT_OPEN_FRACTION, 2),
            "max_total_open_eur": round(INITIAL_TOTAL_BANK * MAX_PORTFOLIO_OPEN_FRACTION, 2),
            "minimum_new_stake_eur": MIN_MEANINGFUL_STAKE_EUR,
            "total_open_eur": round(self.total_exposure, 2),
            "event_exposure_eur": {
                key: round(value, 2)
                for key, value in sorted(self.event_exposure.items()) if value > 0
            },
            "event_position_counts": dict(sorted(self.event_legs.items())),
            "limit_rejections": dict(self.rejections),
            "existing_positions_never_retroactively_changed": True,
        }


def conservative_expected_value(prob: float, price: float, sample_min: int,
                                baseline_uncertainty: float = .03) -> float:
    """Lower-bound sensitivity check, NOT a statistically calibrated CI.

    Conservative buffer grows with extreme odds and decreases slightly as
    training history accumulates. Prevents treating marginal model EV as certain.
    """
    if not (math.isfinite(prob) and math.isfinite(price)) or not 0 <= prob <= 1 or price <= 1:
        return -math.inf
    extra = max(0.0, .05 * min(1.0, (price - 2.0) / 6.0))
    history = .02 if sample_min < 20 else .01 if sample_min < 40 else 0
    return max(0.0, prob - baseline_uncertainty - extra - history) * price - 1


def audit_ledgers(root: Path, now: dt.datetime) -> dict:
    """Audit frozen papers by kickoff date, settlement date, market and league.

    Duplicate picks by distinct models are correlated and are explicitly counted.
    """
    today = now.astimezone(LOCAL_TZ).date()
    trades = []
    for path in sorted((root / "data" / "strategy_ledgers").glob("*.jsonl")):
        for line in path.read_text(encoding="utf8").splitlines():
            if line.strip():
                trades.append(json.loads(line))
    def local_date(r: dict, key: str) -> dt.date | None:
        val = r.get(key)
        if not val:
            return None
        stamp = dt.datetime.fromisoformat(val.replace("Z", "+00:00"))
        return stamp.astimezone(LOCAL_TZ).date() if stamp.tzinfo else None
    def summarize(group: list[dict]) -> dict:
        stake = sum(float(r["stake_eur"]) for r in group)
        pnl = sum(float(r.get("pnl_eur") or 0) for r in group)
        return {
            "n": len(group),
            "independent_events": len({r["event_id"] for r in group}),
            "wins": sum(float(r.get("pnl_eur") or 0) > 0 for r in group),
            "losses": sum(float(r.get("pnl_eur") or 0) < 0 for r in group),
            "staked_eur": round(stake, 2),
            "pnl_eur": round(pnl, 2),
            "roi_pct": round(100*pnl/stake, 2) if stake else None,
        }
    opened_today = [r for r in trades if local_date(r,"forecast_utc") == today]
    settled = [r for r in trades if r.get("status") == "SETTLED"]
    games_today = [r for r in settled if local_date(r,"start_utc") == today]
    settled_today = [r for r in settled if local_date(r,"settlement_utc") == today]
    overdue = []
    for r in trades:
        if r.get("status") != "OPEN":
            continue
        kickoff = dt.datetime.fromisoformat(r["start_utc"].replace("Z","+00:00"))
        if (now-kickoff).total_seconds() > 5*3600:
            overdue.append({"event_id":r["event_id"],"strategy":r.get("strategy"),
                            "kickoff_utc":r["start_utc"],
                            "delay_hours":round((now-kickoff).total_seconds()/3600,1)})
    dimensions = {}
    for key, label in (("sport","by_sport"),("market","by_market"),("strategy","by_strategy")):
        dimensions[label] = {
            k: summarize([r for r in settled if r.get(key)==k])
            for k in sorted({r.get(key) for r in settled if r.get(key)})
        }
    bins = {"under_2": [], "2_to_3": [], "3_to_5": [], "over_5": []}
    for r in settled:
        try:
            price = float(r["odds"][r["selection_index"]])
        except (KeyError,ValueError,IndexError,TypeError):
            continue
        bucket = "under_2" if price<2 else "2_to_3" if price<3 else "3_to_5" if price<=5 else "over_5"
        bins[bucket].append(r)
    dimensions["by_odds_band"] = {k:summarize(v) for k,v in bins.items()}
    # True prospective comparisons only, never impute market performance.
    comparable = [r for r in settled if r.get("brier") is not None and r.get("market_brier") is not None]
    model_brier = sum(r["brier"] for r in comparable)/len(comparable) if comparable else None
    market_brier = sum(r["market_brier"] for r in comparable)/len(comparable) if comparable else None
    return {
        "local_date": today.isoformat(),
        "opened_today": {"n": len(opened_today),
                         "independent_events": len({r["event_id"] for r in opened_today})},
        "games_played_today": summarize(games_today),
        "settled_today": summarize(settled_today),
        "total": len(trades),
        "open": sum(r.get("status")=="OPEN" for r in trades),
        "closed": len(settled),
        "voided": sum(r.get("status")=="VOID" for r in trades),
        "all_time": summarize(settled),
        "prospective_benchmark": {
            "n": len(comparable),
            "model_brier": round(model_brier, 6) if model_brier is not None else None,
            "market_brier": round(market_brier, 6) if market_brier is not None else None,
            "beats_market_on_sample": (model_brier<market_brier if comparable else None),
            "note": "No market forecast invented; beware event correlation and low sample size.",
        },
        **dimensions,
        "overdue_open_positions": overdue[:50],
        "overdue_count": len(overdue),
        "attention_required": bool(overdue),
        "independent_bets_caveat": "Multiple model wagers on the same match are NOT independent observations.",
        "closing_line_value": None,
        "closing_line_value_reason": "No verified point-in-time closing prices saved.",
        "paper_only": True,
    }


def market_activity(root: Path, events: list[dict], now: dt.datetime) -> dict:
    """Distinguish live prices, zero-stake research and actual PAPER positions."""
    from .odds_provider import fresh_quote, parse_utc
    labels = {'1X2':'Resultado 1X2', 'ML':'Vencedor (ML)', 'GOALS_TOTAL':'Mais/menos golos',
              'GOALS_HANDICAP':'Handicap de golos', 'BTTS':'Ambas marcam', 'corners':'Cantos',
              'offsides':'Foras de jogo', 'fouls':'Faltas', 'cards':'Cartões', 'throw_ins':'Lançamentos laterais'}
    groups = {k:{'label':v,'papers_total':0,'papers_open':0,'papers_settled':0,'papers_voided':0,
                 'papers_today':0,'observations':0,'priced_event_count':0} for k,v in labels.items()}
    def family(row):
        market=row.get('market')
        if market in ('1X2','ML','BTTS'):return market
        if market=='OVER_UNDER_2_5':return 'GOALS_TOTAL'
        if market=='ASIAN_HOME_MINUS_0_5':return 'GOALS_HANDICAP'
        spec=row.get('market_spec') or {}
        if spec.get('metric')=='goals':return 'GOALS_TOTAL' if spec.get('kind')=='TOTAL' else 'GOALS_HANDICAP'
        return spec.get('metric')
    today=now.astimezone(LOCAL_TZ).date()
    def count(row):
        key=family(row)
        if key not in groups:return
        g=groups[key]
        if row.get('stake_eur',0)<=0:g['observations']+=1;return
        g['papers_total']+=1
        g['papers_open']+=row.get('status')=='OPEN'
        g['papers_settled']+=row.get('status')=='SETTLED'
        g['papers_voided']+=row.get('status')=='VOID'
        g['papers_today']+=parse_utc(row['forecast_utc']).astimezone(LOCAL_TZ).date()==today
    for path in (root/'data/strategy_ledgers').glob('*.jsonl'):
        for line in path.read_text().splitlines():
            if line.strip():count(json.loads(line))
    journal=root/'data/market_research_v3.jsonl'
    if journal.exists():
        rows=[json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
        settlements={r['forecast_id']:r for r in rows if r.get('kind')=='SETTLEMENT'}
        for row in rows:
            if row.get('kind')=='FORECAST':count(dict(row,status=settlements.get(row['id'],{}).get('status','OPEN')))
    priced=defaultdict(set)
    for event in events:
        for quote in [event.get('quote')]+list(event.get('market_quotes',{}).values()):
            if not quote or not fresh_quote(quote,now) or quote.get('bookmaker_count',0)<2:continue
            key=family(quote)
            if key in groups:priced[key].add(event['id'])
    for key,g in groups.items():
        g['priced_event_count']=len(priced[key])
        g['status']='PAPER_RECORDED' if g['papers_total'] else 'OBSERVATION_ONLY' if g['observations'] else 'PRICE_AVAILABLE_NO_PAPER' if priced[key] else 'NO_ELIGIBLE_PRICE'
    return {'asof_utc':now.isoformat(),'by_market':groups,
            'note':'Counts include repeated strategies on the same event; zero-stake observations are not bets.'}
