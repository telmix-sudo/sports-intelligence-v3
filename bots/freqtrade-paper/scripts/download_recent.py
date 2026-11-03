"""Guarda apenas a janela OHLC recente da Kraken para teste operacional."""

import json
from pathlib import Path

import ccxt
from freqtrade.data.converter import ohlcv_to_dataframe
from freqtrade.data.history import get_datahandler
from freqtrade.enums import CandleType

root = Path(__file__).resolve().parents[1]
config = json.loads((root / "config.json").read_text())
exchange = ccxt.kraken(config["exchange"]["ccxt_config"])
exchange.load_markets()
handler = get_datahandler(root / "user_data/data/kraken", "feather")
for pair in config["exchange"]["pair_whitelist"]:
    candles = exchange.fetch_ohlcv(pair, "15m", limit=720)
    frame = ohlcv_to_dataframe(candles, "15m", pair, fill_missing=False,
                               drop_incomplete=True)
    if len(frame) < 500 or frame["date"].duplicated().any():
        raise ValueError(f"Histórico recente insuficiente ou duplicado: {pair}")
    handler.ohlcv_store(pair, "15m", frame, CandleType.SPOT)
    print(f"{pair}: {len(frame)} velas fechadas; {frame['date'].iloc[0]} a {frame['date'].iloc[-1]}")
