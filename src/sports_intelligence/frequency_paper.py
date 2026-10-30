"""Prospective weekly-frequency V3 paper portfolios. Targets never force entries."""
import datetime as dt
import hashlib
from .daily_reference import quote_for_reference
from .scanner import parse_iso
from .custom_strategies import FREQUENCY_NAMES

POLICIES = {
 "FREQ_SELECTIVE_1_3": (1,3,.06,.08,.01),
 "FREQ_BALANCED_3_5": (3,5,.045,.065,.015),
 "FREQ_BROAD_5_10": (5,10,.03,.05,.02),
 "FREQ_DAILY_5_10": (5,10,.025,.04,.02),
}

def run_frequency_strategies(root, models, events, now, registry):
 from .strategy_lab import read_jsonl, save_jsonl, predictions, team_name
 output = {}
 for name in FREQUENCY_NAMES:
  low, cap, edge_min, uncertainty, max_exposure = POLICIES[name]
  path = root/"data"/"strategy_ledgers"/(name.lower()+".jsonl")
  rows = read_jsonl(path)
  if not path.exists(): save_jsonl(path, [])
  year, week, _ = now.isocalendar()
  daily = name == "FREQ_DAILY_5_10"
  period = (lambda t: t.astimezone(dt.timezone.utc).date() == now.date()) if daily else (lambda t: t.isocalendar()[:2] == (year,week))
  weekly = sum(period(parse_iso(r["forecast_utc"])) for r in rows)
  diag = {"target_period":"day" if daily else "week","target":[low,cap],"opened_this_period":weekly,"new_entries":0,"eligible":0,"status":"NO_ELIGIBLE_QUOTE"}
  if not daily:
   diag["weekly_target"]=[low,cap]
   diag["opened_this_period"]=weekly
  if not daily: diag["opened_this_week"]=weekly
  if now < parse_iso(registry[name]["created_utc"]) or weekly >= cap:
   diag["status"] = "PERIOD_CAP_OR_NOT_STARTED"
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
    if edge>=edge_min and ev_low>0:
     candidates.append((ev_low,e["id"],e,i,p,odds,market,stamps,books,raw))
  candidates.sort(key=lambda x:(-x[0],x[1]))
  diag["eligible"] = len(candidates)
  opened_event_ids = set(seen)
  for ev_low,eid,e,i,p,odds,market,stamps,books,raw in candidates:
   if weekly>=cap: break
   if eid in opened_event_ids: continue
   stake = round(min(bank*.0025,bank*max_exposure-exposure),2)
   if stake<=0: break
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
   weekly+=1
   opened_event_ids.add(eid)
   exposure+=stake
   diag["new_entries"]+=1
  if diag["new_entries"]:
   save_jsonl(path,rows)
   diag["status"]="OPENED_PAPER"
  diag["opened_this_week"]=weekly
  output[name]=diag
 return output
