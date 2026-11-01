"""A frozen rounded distribution must not block official-result settlement."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from sports_intelligence.strategy_lab import build_accounts, read_jsonl


class RoundedSettlementTests(unittest.TestCase):
    def test_rounding_boundary_settles_once_without_rewriting_forecast(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root/'data/strategy_ledgers/market_daily_reference.jsonl'
            path.parent.mkdir(parents=True)
            row = {'id':'SYNTHETIC-ROUNDING', 'event_id':'ligue_1:SYNTHETIC',
                   'strategy':'MARKET_DAILY_REFERENCE', 'sport':'ligue_1', 'market':'1X2',
                   'status':'OPEN', 'selection_index':0, 'stake_eur':2.5,
                   'probabilities':[.6785009,.1897843,.1317147],
                   'no_vig':[.6785009,.1897843,.1317147], 'odds':[2.,4.,5.],
                   'forecast_utc':'2026-10-31T12:00:00+00:00',
                   'start_utc':'2026-11-01T16:15:00+00:00'}
            path.write_text(json.dumps(row)+'\n')
            now = dt.datetime(2026,11,1,20,tzinfo=dt.timezone.utc)
            final = {row['event_id']:{'home':1,'away':0,'outcome':0}}
            accounts, counts = build_accounts(root,final,now)
            self.assertEqual(counts['settled_this_run'],1)
            self.assertEqual(accounts['MARKET_DAILY_REFERENCE']['bankroll_eur'],1002.5)
            settled = read_jsonl(path)[0]
            for key in ('probabilities','no_vig','odds','forecast_utc','selection_index','stake_eur'):
                self.assertEqual(settled[key],row[key])
            self.assertEqual(build_accounts(root,final,now)[1]['settled_this_run'],0)


if __name__=='__main__':
    unittest.main()
