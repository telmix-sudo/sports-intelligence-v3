from market_validation_fixtures import approved_research,ApprovedOriginal
import copy,datetime as dt,json,tempfile,unittest
from pathlib import Path
from test_research_integration import EVENT,NOW,odds_rows,football_history,models
from sports_intelligence.market_catalog import catalog_quotes,outcome_for_spec,team_statistics,CountModel
from sports_intelligence.research_pipeline import run_research,read_journal
from sports_intelligence.odds_provider import market_quotes_for_event
from sports_intelligence.portfolio_controls import PaperRiskBook
from sports_intelligence.strategy_lab import build_accounts,read_jsonl
from sports_intelligence.strategy_registry import ensure_registry
from sports_intelligence.custom_strategies import ALL_NAMES
from sports_intelligence.auxiliary_portfolios import open_auxiliary_markets
from sports_intelligence.activity_policy import MIN_DAILY_ENTRIES,MAX_DAILY_ENTRIES

class MarketCatalogTests(unittest.TestCase):
 def test_pair_corners_by_line_and_settle_on_corners_not_goals(self):
  rows=odds_rows()
  for b in rows[0]['bookmakers']:
   b['markets'] += [{'key':'alternate_totals_corners','outcomes':[
    {'name':'Over','point':9.5,'price':2.05},{'name':'Under','point':9.5,'price':1.8},
    {'name':'Over','point':10.5,'price':2.5},{'name':'Under','point':10.5,'price':1.5}]}]
  qs,inventory=catalog_quotes(EVENT,rows,NOW)
  corners=[q for q in qs.values() if q['market_spec']['metric']=='corners']
  self.assertEqual(len(corners),2)
  q=next(q for q in corners if q['market_spec']['line']==9.5)
  self.assertEqual(q['bookmaker_count'],2)
  final={'home':0,'away':0,'team_statistics':{'home':{'corners':7},'away':{'corners':4}}}
  self.assertEqual(outcome_for_spec(q['market_spec'],final),0)
  self.assertIsNone(outcome_for_spec(q['market_spec'],{'home':3,'away':2}))

 def test_market_stat_identity_and_card_rules_are_not_assumed(self):
  rows=odds_rows()
  for b in rows[0]['bookmakers']:
   b['markets'] += [{'key':'alternate_totals_cards','outcomes':[{'name':'Over','point':4.5,'price':2},{'name':'Under','point':4.5,'price':2}]}]
  qs,_=catalog_quotes(EVENT,rows,NOW)
  card=next(q for q in qs.values() if q['market_spec']['metric']=='cards')
  self.assertEqual(card['market_spec']['settlement_block'],'BOOKMAKER_CARD_BOOKING_RULES_REQUIRED')
  self.assertIsNone(outcome_for_spec(card['market_spec'],{'home':1,'away':0}))

 def test_real_espn_stat_names_map_to_the_right_team(self):
  payload={'boxscore':{'teams':[{'team':{'displayName':name},'homeAway':side,'statistics':[
    {'name':'wonCorners','displayValue':'7'},{'name':'offsides','displayValue':'2'},{'name':'foulsCommitted','displayValue':'12'}]}
    for name,side in [('Arsenal','home'),('Chelsea','away')]]}}
  stats=team_statistics(payload,EVENT)
  self.assertEqual(stats['home']['corners'],7)
  self.assertEqual(stats['away']['offsides'],2)
  self.assertEqual(stats['home']['fouls'],12)
  self.assertNotIn('throw_ins',stats['home'],'Missing statistics cannot become zero')

 @approved_research
 def test_validated_shared_markets_preserve_control_and_concentration_budget(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);registry=ensure_registry(root,NOW,ALL_NAMES);h=football_history();e=dict(EVENT)
   e['market_quotes']=market_quotes_for_event(e,odds_rows(),NOW)
   run_research(root,[e],models(h),NOW,PaperRiskBook(root),football_history=h)
   # Avoid duplicate home-win evidence; the eligible -0.5 example is standalone.
   added=open_auxiliary_markets(root,[e],NOW,registry,PaperRiskBook(root),admission=ApprovedOriginal())
   self.assertTrue(added)
   self.assertNotIn('MARKET_DAILY_REFERENCE',added)
   self.assertLess(sum(added.values()),len(ALL_NAMES))
   self.assertEqual(open_auxiliary_markets(root,[e],NOW,registry,PaperRiskBook(root),admission=ApprovedOriginal()),{})
   for name in added:
    rows=read_jsonl(root/'data/strategy_ledgers'/(name.lower()+'.jsonl'))
    self.assertEqual(len(rows),1)
    self.assertTrue(rows[0]['shared_forecast_not_independent'])
   final={'home':3,'away':0,'outcome':0}
   accounts,count=build_accounts(root,{e['id']:final},NOW+dt.timedelta(hours=8))
   self.assertEqual(count['settled_this_run'],sum(added.values()))
   self.assertEqual(accounts['MARKET_DAILY_REFERENCE']['bankroll_eur'],1000)
   self.assertTrue(all(a['bankroll_eur']>=1000 for a in accounts.values()))
   self.assertEqual(MIN_DAILY_ENTRIES,5);self.assertEqual(MAX_DAILY_ENTRIES,10)

 def test_winner_does_not_settle_a_pending_corner_position(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);ensure_registry(root,NOW,ALL_NAMES)
   path=root/'data/strategy_ledgers/elo_standard.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
   row={'id':'SYNTHETIC-CORNER','event_id':EVENT['id'],'strategy':'ELO_STANDARD','sport':'epl',
        'market':'corners_9.5','market_spec':{'kind':'TOTAL','metric':'corners','line':9.5,'team':None},
        'start_utc':EVENT['start_utc'],'forecast_utc':NOW.isoformat(),'status':'OPEN','stake_eur':2.5,
        'selection_index':0,'probabilities':[.6,.4],'odds':[2,2],'no_vig':[.5,.5]}
   path.write_text(json.dumps(row)+'\n');after=NOW+dt.timedelta(hours=8)
   self.assertEqual(build_accounts(root,{EVENT['id']:{'home':1,'away':0,'outcome':0}},after)[0]['ELO_STANDARD']['open'],1)
   f={'home':1,'away':0,'outcome':0,'team_statistics':{'home':{'corners':3},'away':{'corners':4}}}
   result,_=build_accounts(root,{EVENT['id']:f},after)
   self.assertEqual(result['ELO_STANDARD']['bankroll_eur'],997.5)

if __name__=='__main__':unittest.main()
