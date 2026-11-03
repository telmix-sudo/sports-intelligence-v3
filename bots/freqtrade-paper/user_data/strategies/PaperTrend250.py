"""Baseline experimental: tendência e recuperação de RSI; apenas posições longas."""

from datetime import datetime, timedelta

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy
from technical import qtpylib


class PaperTrend250(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "15m"
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 210
    stoploss = -0.03
    trailing_stop = False
    position_adjustment_enable = False
    use_exit_signal = True
    exit_profit_only = False
    minimal_roi = {"0": 0.06, "240": 0.03, "720": 0.015, "1440": 0.0}

    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 4},
            {
                "method": "StoplossGuard", "lookback_period_candles": 96,
                "trade_limit": 3, "stop_duration_candles": 96,
                "only_per_pair": False,
            },
            {
                "method": "MaxDrawdown", "lookback_period_candles": 192,
                "trade_limit": 6, "stop_duration_candles": 96,
                "max_allowed_drawdown": 0.08,
            },
        ]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Médias simples têm uma janela finita e evitam dependência do início do histórico.
        dataframe["sma20"] = ta.SMA(dataframe, timeperiod=20)
        dataframe["sma50"] = ta.SMA(dataframe, timeperiod=50)
        dataframe["sma200"] = ta.SMA(dataframe, timeperiod=200)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr_pct"] = ta.ATR(dataframe, timeperiod=14) / dataframe["close"]
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe.loc[
            (dataframe["sma20"] > dataframe["sma50"])
            & (dataframe["close"] > dataframe["sma200"])
            & qtpylib.crossed_above(dataframe["rsi"], 45)
            & (dataframe["atr_pct"] < 0.025)
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "trend_rsi_recovery")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe.loc[
            (dataframe["close"] < dataframe["sma50"])
            & (dataframe["rsi"] < 40)
            & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "trend_break")
        return dataframe

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time: datetime,
                            entry_tag: str | None, side: str, **kwargs) -> bool:
        # Este pacote é exclusivamente de simulação, incluindo se iniciado pelo CLI.
        if self.config.get("dry_run") is not True or side != "long":
            return False
        # Spread real no dry-run; o backtest OHLCV não dispõe de livro histórico.
        if self.config.get("runmode").value in ("dry_run", "live"):
            book = self.dp.orderbook(pair, 1)
            if not book or not book.get("bids") or not book.get("asks"):
                return False
            bid, ask = book["bids"][0][0], book["asks"][0][0]
            return bid > 0 and ask >= bid and (ask - bid) / bid <= 0.003
        return True

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        if current_time - trade.open_date_utc >= timedelta(hours=48):
            return "time_limit_48h"
        return None
