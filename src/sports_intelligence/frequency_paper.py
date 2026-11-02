"""Prospective daily-frequency V3 paper portfolios. Targets never force entries."""
import datetime as dt
import hashlib
from .daily_reference import quote_for_reference
from .scanner import parse_iso
from .custom_strategies import FREQUENCY_NAMES
from .portfolio_controls import PaperRiskBook, conservative_expected_value
from .activity_policy import daily_entry_stake

POLICIES = {
 "FREQ_SELECTIVE_1_3": (5,10,.06,.08,.02),
 "FREQ_BALANCED_3_5": (5,10,.045,.065,.02),
 "FREQ_BROAD_5_10": (5,10,.03,.05,.02),
}

def run_frequency_strategies(root, models, events, now, registry, risk=None):
 from .strategy_lab import read_jsonl, save_jsonl, predictions, team_name
 risk = risk or PaperRiskBook(root)
 output = {}
 for name in FREQUENCY_NAMES:
  low, cap, edge_min, uncertainty, max_exposure = POLICIES[name]
  path = root/"data"/"strategy_ledgers"/(name.lower()+".jsonl")
  rows = read_jsonl(path)
  if not path.exists(): save_jsonl(path, [])
  from zoneinfo import ZoneInfo
  today = now.astimezone(ZoneInfo("Europe/Lisbon")).date()
  daily = sum(parse_iso(r["forecast_utc"]).astimezone(ZoneInfo("Europe/Lisbon")).date() == today for r in rows)
  diag = {"daily_target":[low,cap],"opened_today":daily,"opened_this_week":daily,"remaining_to_minimum":max(0,low-daily),"new_entries":0,"eligible":0,"status":"NO_ELIGIBLE_QUOTE"}
  if now < parse_iso(registry[name]["created_utc"]) or daily >= cap:
   diag["status"] = "DAILY_CAP_OR_NOT_STARTED"
   output[name] = diag
   continue
  bank = 1000+sum(r.get("pnl_eur",0) for r in rows if r.get("status")=="SETTLED")
  exposure = sum(r["stake_eur"] for r in rows if r.get("status")=="OPEN")
  seen = {r["event_id"] for r in rows}
  candidates = []
  for e in events:
   if e["id"] in seen or e["sport"] not in models: continue
   quote = quote_for_reference(e,now)
   if quote is None: continue
   start, odds, market, stamps, books, raw = quote
   if len(odds)!=3: continue
   lm = models[e["sport"]]
   home, away = team_name(e["home"]),team_name(e["away"])
   if min(lm["ELO_STANDARD"].played.get(home,0),lm["ELO_STANDARD"].played.get(away,0))<16: continue
   p = predictions(lm,home,away)["ELO_POISSON_BLEND"]
   for i in range(3):
    edge = p[i]-market[i]
    ev_low = (p[i]-uncertainty)*odds[i]-1
    if edge>=edge_min and ev_low>0 and conservative_expected_value(p[i],odds[i],min(lm["ELO_STANDARD"].played.get(home,0),lm["ELO_STANDARD"].played.get(away,0)))>.01:
     candidates.append((ev_low,e["id"],e,i,p,odds,market,stamps,books,raw))
  candidates.sort(key=lambda x:(-x[0],x[1]))
  diag["eligible"] = len(candidates)
  for ev_low,eid,e,i,p,odds,market,stamps,books,raw in candidates:
   if daily>=cap: break
   if eid in seen: continue
   stake = daily_entry_stake(bank, exposure, daily)
   if stake<1: break
   stake = risk.reserve(eid, stake)
   if stake == 0: continue
   key = "PFREQ-"+hashlib.sha256((name+"|"+eid).encode()).hexdigest()[:14]
   rows.append({"id":key,"strategy":name,"event_id":eid,"sport":e["sport"],
    "home":e["home"],"away":e["away"],"start_utc":e["start_utc"],
    "forecast_utc":now.isoformat(),"market":"1X2","probabilities":list(p),
    "no_vig":list(market),"odds":list(odds),"selection_index":i,
    "edge_pp":round((p[i]-market[i])*100,4),"conservative_ev":round(ev_low,6),
    "uncertainty_buffer_pp":uncertainty*100,"uncertainty_calibrated":False,
    "stake_eur":stake,"status":"OPEN","price_source":raw["provider"],
    "bookmakers":books,"book_quote_updates_utc":stamps,
    "strategy_version":"1.0.0","model_status":"EXPERIMENTAL_NOT_VALIDATED"})
   daily+=1
   seen.add(eid)
   exposure+=stake
   diag["new_entries"]+=1
  if diag["new_entries"]:
   save_jsonl(path,rows)
   diag["status"]="OPENED_PAPER"
  diag["opened_this_week"]=daily
  diag["opened_today"]=daily
  diag["remaining_to_minimum"]=max(0,low-daily)
  output[name]=diag
 return output
