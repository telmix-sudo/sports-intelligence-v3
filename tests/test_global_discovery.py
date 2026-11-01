import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,MagicMock
import urllib.error
from urllib.parse import urlparse

from sports_intelligence.global_discovery import (
    ESPN_CORE, _espn_event, _odds_get, discover_universe, espn_discovery,
    merge_events, odds_discovery, summarize, tsdb_discovery,
)
from sports_intelligence.broad_odds import annotate_discovered
from sports_intelligence.daily_reference import quote_for_reference
from sports_intelligence.scanner import classify
from sports_intelligence.odds_provider import quotes_for_event

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 31, 8, tzinfo=UTC)


def fixture(sport='tennis_atp_test', group='tennis', n=1):
    return {'id': f'{sport}:{n}', 'sport': sport, 'family': group,
            'home': f'Home {n}', 'away': f'Away {n}', 'start_utc': (NOW+dt.timedelta(hours=2)).isoformat(),
            'status': 'pre', 'quote': None, 'event_type': 'MATCHUP',
            'paper_execution_enabled': False, 'odds_sport_key': sport, 'odds_event_id': str(n)}


def refs(slugs, prefix=ESPN_CORE):
    return {'items': [{'$ref': prefix+'/'+s} for s in slugs], 'pageCount': 1}


class GlobalDiscoveryTests(unittest.TestCase):
    def test_free_provider_retries_transient_rate_limit_and_sanitizes_failures(self):
        response=MagicMock()
        response.__enter__.return_value.read.return_value=b'[]'
        limited=urllib.error.HTTPError('https://example/?apiKey=SECRET',429,'Too many',{},None)
        with (patch('sports_intelligence.global_discovery.urllib.request.urlopen',side_effect=[limited,response]) as request,
              patch('sports_intelligence.global_discovery.time.sleep')):
            self.assertEqual(_odds_get('sports','SECRET'),[])
            self.assertEqual(request.call_count,2)
        denied=urllib.error.HTTPError('https://example/?apiKey=SECRET',401,'denied',{},None)
        with (patch('sports_intelligence.global_discovery.urllib.request.urlopen',side_effect=denied),
              patch('sports_intelligence.global_discovery.time.sleep')):
            with self.assertRaisesRegex(ValueError,'^ODDS_FREE_DISCOVERY_HTTP_401$'):
                _odds_get('sports','SECRET')

    def test_free_odds_catalog_queries_all_active_sports_and_never_prices(self):
        calls=[]
        def get(path, key, params):
            calls.append(path)
            if path=='sports':
                self.assertEqual(params, {'all': 'true'})
                return [dict(key='tennis_atp_new', group='Tennis', title='ATP New', active=True),
                        dict(key='handball_new', group='Handball', title='New league', active=True),
                        dict(key='golf_old', group='Golf', title='Off season', active=False),
                        dict(key='politics_test', group='Politics', title='Other', active=True)]
            return [{'id': path, 'home_team': 'Home', 'away_team': 'Away',
                     'commence_time': (NOW+dt.timedelta(hours=3)).isoformat()}]
        cache={}
        events,report=odds_discovery(cache,NOW,'SECRET_TEST_ONLY',get)
        self.assertEqual(len(events),2)
        self.assertEqual(report['competitions_catalogued'],3)
        self.assertEqual(report['competitions_queried'],2)
        self.assertEqual(report['usage_credits'],0)
        self.assertTrue(all(p=='sports' or p.endswith('/events') for p in calls))
        self.assertTrue(all(not e['paper_execution_enabled'] for e in events))
        self.assertNotIn('SECRET_TEST_ONLY',json.dumps(cache))
        odds_discovery(cache,NOW+dt.timedelta(minutes=20),'SECRET_TEST_ONLY',get)
        self.assertEqual(len(calls),3,'Free catalogs and calendars should be cached')

    def test_espn_interleaves_sports_and_rotates_deferred_competitions(self):
        def get(url):
            path=urlparse(url).path
            if path=='/v2/sports':return refs(['soccer','tennis','volleyball'])
            if path.endswith('/leagues'):
                sport=path.split('/')[-2]
                return refs(['l1','l2','l3'],ESPN_CORE+'/'+sport+'/leagues')
            return {'events':[]}
        cache={}
        _,first=espn_discovery(cache,NOW,get,route_budget=3)
        self.assertEqual({q['family'] for q in first['queries']},{'soccer','tennis','volleyball'})
        self.assertEqual(first['competitions_deferred'],6)
        _,second=espn_discovery(cache,NOW+dt.timedelta(hours=1),get,route_budget=3)
        self.assertEqual({q['competition'] for q in second['queries']},{'l2'})

    def test_cached_failed_scoreboards_remain_unavailable(self):
        def get(url):
            if '/scoreboard' in url:raise ConnectionError('offline')
            if '/leagues' in url:return refs(['atp'],ESPN_CORE+'/tennis/leagues')
            return refs(['tennis'])
        cache={}
        espn_discovery(cache,NOW,get,1)
        _,diag=espn_discovery(cache,NOW+dt.timedelta(minutes=10),get,1)
        self.assertEqual(diag['queries'][0]['status'],'UNAVAILABLE')
        self.assertEqual(summarize([], [diag], NOW)['by_modality']['tennis']['status'],'SOURCE_UNAVAILABLE')

    def test_deferred_route_keeps_its_still_future_events(self):
        event=fixture('espn_soccer_l2','soccer')
        cache={'espn_route:soccer/l2':{'at':NOW.isoformat(),'value':{
            'events':[event],'successful_days':3,'errors':[]}}}
        def get(url):
            if '/scoreboard' in url:return {'events':[]}
            if '/leagues' in url:return refs(['l1','l2'],ESPN_CORE+'/soccer/leagues')
            return refs(['soccer'])
        events,diag=espn_discovery(cache,NOW+dt.timedelta(minutes=5),get,1)
        self.assertEqual(diag['queries'][0]['competition'],'l1')
        self.assertEqual(diag['deferred_cached_competitions_reused'],1)
        self.assertEqual(events,[event])

    def test_athlete_and_tournament_formats_do_not_inherit_football_model(self):
        c={'id':'1','date':(NOW+dt.timedelta(hours=2)).isoformat(),
           'competitors':[{'athlete':{'displayName':'Player One'}}, {'athlete':{'displayName':'Player Two'}}]}
        event=_espn_event({'name':'Tennis match'},c,'tennis','atp',NOW)
        self.assertEqual(event['event_type'],'MATCHUP')
        self.assertEqual(classify(event,object(),NOW)['classification'],'FIXTURE_ONLY')
        self.assertIsNone(quote_for_reference(event,NOW))
        c['competitors'].append({'athlete':{'displayName':'Player Three'}})
        self.assertEqual(_espn_event({},c,'golf','pga',NOW)['event_type'],'MULTI_PARTICIPANT')

    def test_merge_preserves_existing_ids_and_never_fuzzy_matches(self):
        legacy=fixture('epl','soccer');legacy.pop('odds_sport_key');legacy.pop('odds_event_id')
        additional=dict(fixture('soccer_epl','soccer'),id='odds:new',sources=[{'provider':'The Odds API'}])
        other=dict(additional,id='odds:unrelated',home='Home Different')
        result=merge_events([legacy],[additional,other])
        self.assertEqual(len(result),2)
        self.assertEqual(result[0]['id'],legacy['id'])
        self.assertEqual(result[0]['odds_event_id'],'1')

    def test_sportsdb_partial_missing_timezone_is_not_invented(self):
        def get(url):
            if 'all_sports' in url:return {'sports':[{'idSport':'1','strSport':'Badminton'}]}
            return {'events':[{'idEvent':'1','strTimestamp':'2026-10-31T10:00:00'},
                              {'idEvent':'2','strTimestamp':(NOW+dt.timedelta(hours=2)).isoformat(),
                               'strHomeTeam':'A','strAwayTeam':'B'}]}
        events,diag=tsdb_discovery({},NOW,get,pause=lambda _:None,max_calls=4)
        self.assertEqual(diag['requests'],4)
        self.assertEqual(diag['untimed_events'],3)
        self.assertEqual(len(merge_events([],events)),1)
        self.assertEqual(diag['coverage'],'PARTIAL_FREE_TIER_NOT_EXHAUSTIVE')

    def test_new_soccer_league_requires_three_way_market(self):
        event=fixture('soccer_portugal','soccer')
        rows=[{'home_team':event['home'],'away_team':event['away'],'commence_time':event['start_utc'],
               'bookmakers':[{'key':'example','last_update':NOW.isoformat(),
               'markets':[{'key':'h2h','last_update':NOW.isoformat(),'outcomes':[
                   {'name':event['home'],'price':2}, {'name':event['away'],'price':3}]}]}]}]
        self.assertIsNone(quotes_for_event(event,rows,NOW))
        rows[0]['bookmakers'][0]['markets'][0]['outcomes'].append({'name':'Draw','price':3.5})
        self.assertEqual(quotes_for_event(event,rows,NOW)['market'],'1X2')

    def test_failure_diagnostics_sanitized_and_existing_wallets_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            wallet=root/'data/strategy_accounts_v3.json';wallet.write_text('{"balance":891.76}')
            def fail(*args):raise ConnectionError('https://api.example/?apiKey=NEVER_STORE')
            events,report=discover_universe(root,NOW,secret='NEVER_STORE',public_requester=fail,
                                            odds_requester=fail,pause=lambda _:None)
            self.assertEqual(events,[])
            self.assertTrue(all(s['status']=='UNAVAILABLE' for s in report['sources']))
            self.assertEqual(wallet.read_text(),'{"balance":891.76}')
            self.assertNotIn('NEVER_STORE',(root/'reports/global_discovery_latest.json').read_text())


class GlobalPricingTests(unittest.TestCase):
    def test_shared_credit_budget_and_old_quotes_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').mkdir()
            old={'day':str(NOW.date()),'calls_today':15,'credits_today':16,
                 'snapshots':{'epl':{'fetched_at':NOW.isoformat(),'quotes':{'legacy':{'x':1}}}}}
            (root/'data/odds_cache_v3.json').write_text(json.dumps(old))
            report=annotate_discovered([fixture()],root,NOW,'KEY',lambda *_:self.fail('Budget exceeded'))
            self.assertEqual(report['calls_this_run'],0)
            self.assertEqual(report['credits_today'],16)
            self.assertEqual(json.loads((root/'data/odds_cache_v3.json').read_text())['snapshots'],old['snapshots'])

    def test_new_families_precede_already_priced_and_daily_ceiling_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);calls=[]
            def get(sport,*args):calls.append(sport);return []
            events=[fixture('tennis_one','tennis',1),fixture('tennis_two','tennis',2),
                    fixture('handball_one','handball',3),fixture('basketball_one','basketball',4)]
            report=annotate_discovered(events,root,NOW,'KEY',get)
            self.assertEqual(len(calls),4)
            self.assertNotEqual(calls[0].split('_')[0],calls[1].split('_')[0])
            again=annotate_discovered(events,root,NOW,'KEY',get)
            self.assertEqual(again['calls_this_run'],0)
            later=NOW.replace(hour=20)
            many=[dict(fixture('league_'+str(n),'family_'+str(n),n),
                       start_utc=(later+dt.timedelta(hours=1)).isoformat()) for n in range(30)]
            result=annotate_discovered(many,root,later,'KEY',get)
            self.assertLessEqual(result['credits_today'],16)


if __name__=='__main__':unittest.main()
