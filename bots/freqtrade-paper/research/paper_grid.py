"""BTC/EUR grid research. The only outbound HTTP request is Kraken public OHLC."""
import argparse
import datetime as dt
import json
import math
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

BANK = 250.
LOT = 30.
FEE = .008
BUFFER = .0001
DURATION = 7 * 86400
STRATEGY = 'BTC/EUR grid v1; 6 intervals; ±5%; 0.8% per side'

def stamp(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec='minutes')

def candles(interval):
    url = f'https://api.kraken.com/0/public/OHLC?pair=XBTEUR&interval={interval}'
    with urlopen(Request(url, headers={'User-Agent': 'BTC-EUR-paper-grid/1.0'}), timeout=15) as response:
        body = json.load(response)
    if body['error']:
        raise RuntimeError(body['error'])
    raw = body['result']['XXBTZEUR'][:-1]  # Kraken includes the unclosed candle.
    out = [(int(r[0]), *map(float, r[1:5])) for r in raw]
    if len(out) < 2 or any(a[0] >= b[0] for a, b in zip(out, out[1:])):
        raise ValueError('Invalid Kraken history')
    if any(l > min(o, c) or max(o, c) > h or l <= 0 for _, o, h, l, c in out):
        raise ValueError('Invalid OHLC')
    return out

def init(ref, t):
    s = dict(start=t, cursor=t, price=ref, anchor=ref, cash=BANK, cells=[], buys=0,
             sells=0, fees=0., realized=0., ambiguous=0, empty_minutes=0, peak=BANK, max_drawdown=0., done=False)
    step = ref * .10 / 6
    for i in range(6):
        lo, hi = ref * .95 + i * step, ref * .95 + (i + 1) * step
        s['cells'].append(dict(lo=lo, hi=hi, qty=0., cost=0.))
        if i >= 3:
            buy(s, s['cells'][-1], ref)
    mark(s, ref)
    return s

def buy(s, cell, price):
    if s['cash'] + 1e-8 < LOT:
        raise ValueError('Invalid paper balance')
    amount = LOT / (1 + FEE)
    s['cash'] -= LOT
    cell.update(qty=amount / price, cost=LOT)
    s['fees'] += LOT - amount
    s['buys'] += 1

def sell(s, cell, price):
    gross = cell['qty'] * price
    net = gross * (1 - FEE)
    s['cash'] += net
    s['realized'] += net - cell['cost']
    s['fees'] += gross - net
    s['sells'] += 1
    cell.update(qty=0., cost=0.)

def inventory(s):
    return sum(c['qty'] for c in s['cells'])

def mark(s, price):
    equity = s['cash'] + inventory(s) * price
    s['peak'] = max(s['peak'], equity)
    s['max_drawdown'] = max(s['max_drawdown'], 1 - equity / s['peak'])

def advance(s, bar, interval, strict=True):
    t, _, hi, lo, close = bar
    end = t + interval
    if t < s['cursor']:
        return
    if strict and t != s['cursor']:
        raise ValueError(f'Missing candle from {stamp(s["cursor"])} to {stamp(t)}')
    if end > s['start'] + DURATION:
        return
    # Only orders already open at this bar's start qualify. Newly created
    # opposing orders become eligible next bar; no intrabar round trips.
    buy_hits = [c for c in s['cells'] if not c['qty'] and lo <= c['lo'] * (1 - BUFFER)]
    sell_hits = [c for c in s['cells'] if c['qty'] and hi >= c['hi'] * (1 + BUFFER)]
    if buy_hits and sell_hits:
        # These are independent fixed lots; cash never falls below six lots'
        # combined budget, so ordering cannot change the resulting inventory.
        s['ambiguous'] += 1
    for cell in buy_hits:
        buy(s, cell, cell['lo'])
    for cell in sell_hits:
        sell(s, cell, cell['hi'])
    s.update(price=close, cursor=end)
    mark(s, close)
    s['done'] = end >= s['start'] + DURATION

def report(s):
    inv = inventory(s) * s['price']
    equity = s['cash'] + inv
    hold = BANK / (1 + FEE) * s['price'] / s['anchor']
    return dict(strategy=STRATEGY, period_start=stamp(s['start']),
                period_end=stamp(s['cursor']), complete=s['done'], cash=round(s['cash'], 2),
                btc_value=round(inv, 2), equity=round(equity, 2), grid_pnl=round(equity-BANK, 2),
                hold_pnl=round(hold-BANK, 2), realized=round(s['realized'], 2),
                fees=round(s['fees'], 2), buys=s['buys'], sells=s['sells'],
                close_drawdown_pct=round(100*s['max_drawdown'], 2),
                anchor=round(s['anchor'], 2), last_price=round(s['price'], 2),
                range_low=round(s['cells'][0]['lo'], 2), range_high=round(s['cells'][-1]['hi'], 2),
                two_sided_bars=s['ambiguous'], empty_minutes=s['empty_minutes'])

def backtest(interval):
    bars = candles(interval)
    step = interval * 60
    last = bars[-1][0] + step
    monday = dt.datetime.fromtimestamp(last, dt.timezone.utc).replace(hour=0, minute=0, second=0)
    monday -= dt.timedelta(days=monday.weekday())
    first = int(monday.timestamp())
    indexed = {r[0]:r for r in bars}
    summaries = []
    for end in reversed(range(first, bars[0][0] + DURATION, -DURATION)):
        start = end - DURATION
        if start-step not in indexed:
            continue
        state = init(indexed[start-step][4], start)
        for t in range(start, end, step):
            advance(state, indexed[t], step)
        summaries.append(report(state))
    print(json.dumps({'source':'Kraken BTC/EUR', 'interval_minutes':interval, 'data_until':stamp(last),
                      'weeks':summaries},indent=2))

def live():
    path = Path(os.environ.get('GRID_DB', '/data/grid.sqlite'))
    if os.environ.get('RAILWAY_PROJECT_ID') and os.environ.get('RAILWAY_VOLUME_MOUNT_PATH') != str(path.parent):
        raise SystemExit('Persistent grid volume required')
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS closes (t INTEGER PRIMARY KEY, metrics TEXT NOT NULL)')
    last = conn.execute('SELECT payload FROM state WHERE id=1').fetchone()
    s = json.loads(last[0]) if last else None

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/health':
                self.send_error(404)
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'ok')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT',8080))), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def save(state):
        payload = json.dumps(report(state), separators=(',',':'))
        with conn:
            conn.execute('INSERT OR REPLACE INTO state VALUES (1,?)', (json.dumps(state),))
            conn.execute('INSERT OR REPLACE INTO closes VALUES (?,?)', (state['cursor'],payload))
        return payload

    armed_at = math.ceil(time.time()/60)*60
    next_log = 0
    while not s or not s['done']:
        try:
            bars = candles(1)
            if not s:
                bar = bars[-1]
                if abs(time.time() - bar[0] - 60) > 180:
                    raise ValueError('Kraken candle is stale')
                if bar[0]+60 < armed_at:
                    time.sleep(10)
                    continue
                s = init(bar[4], bar[0]+60)
                print('GRID START '+save(s),flush=True)
                next_log = time.monotonic()+3600
            if bars[0][0] > s['cursor']:
                print('GRID STOP: missing interval outside Kraken history',flush=True)
                break
            for bar in bars:
                if bar[0] < s['cursor']:
                    continue
                if bar[0] > s['cursor']:
                    # Within Kraken's returned history an empty minute has
                    # no reported trades; never fill an order in that gap.
                    until = min(bar[0], s['start'] + DURATION)
                    s['empty_minutes'] += (until-s['cursor'])//60
                    s['cursor'] = until
                    if until >= s['start']+DURATION:
                        s['done'] = True
                        save(s)
                        break
                previous = (s['buys'],s['sells'])
                advance(s, bar, 60)
                payload = save(s)
                if (s['buys'],s['sells']) != previous or time.monotonic() >= next_log or s['done']:
                    print('GRID '+payload,flush=True)
                    next_log = time.monotonic()+3600
                if s['done']:
                    break
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            print(f'GRID DATA ERROR: {exc}',flush=True)
        if s and time.time() > s['start'] + DURATION + 1800 and not s['done']:
            print('GRID STOP: deadline passed without complete data',flush=True)
            break
        if not s and time.time() > armed_at + 600:
            raise SystemExit('GRID STOP: no fresh data at startup')
        if not s or not s['done']:
            time.sleep(35)
    print('GRID FINAL '+json.dumps(report(s)),flush=True)
    server.shutdown()

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['backtest','live'])
    p.add_argument('--interval',type=int,default=60)
    a = p.parse_args()
    backtest(a.interval) if a.mode == 'backtest' else live()
