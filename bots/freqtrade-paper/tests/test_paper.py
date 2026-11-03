import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = load("bootstrap", ROOT / "scripts/start.py")
Strategy = load("paper_strategy", ROOT / "user_data/strategies/PaperTrend250.py").PaperTrend250


class PaperSafetyTests(unittest.TestCase):
    def setup_root(self, temp):
        root = Path(temp)
        (root / "config.json").write_text((ROOT / "config.json").read_text())
        return root

    def test_rejects_real_money_and_overrides(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            root = self.setup_root(temp)
            config = json.loads((root / "config.json").read_text())
            config["dry_run"] = False
            (root / "config.json").write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                bootstrap.prepare(root)
            (root / "config.json").write_text((ROOT / "config.json").read_text())
            os.environ["FREQTRADE__DRY_RUN"] = "false"
            with self.assertRaises(ValueError):
                bootstrap.prepare(root)

    def test_restart_keeps_database_and_credentials(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            root = self.setup_root(temp)
            first = json.loads(bootstrap.prepare(root).read_text())
            db = root / "runtime/trades.dryrun.sqlite"
            db.write_bytes(b"existing ledger")
            second = json.loads(bootstrap.prepare(root).read_text())
            self.assertEqual(first["api_server"], second["api_server"])
            self.assertEqual(db.read_bytes(), b"existing ledger")
            self.assertEqual(second["dry_run_wallet"], 250)
            self.assertEqual(second["stake_currency"], "EUR")
            self.assertFalse(second["exchange"]["key"])

    def test_configuration_passes_freqtrade_schema(self):
        from freqtrade.configuration.config_validation import validate_config_schema
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            config = json.loads(bootstrap.prepare(self.setup_root(temp)).read_text())
            validate_config_schema(config)

    def test_entry_rejects_live_mode_and_wide_spread(self):
        from freqtrade.enums import RunMode
        strategy = Strategy({"dry_run": True, "runmode": RunMode.DRY_RUN})
        book = {"bids": [[100, 1]], "asks": [[101, 1]]}
        strategy.dp = SimpleNamespace(orderbook=lambda *_: book)
        args = ("BTC/EUR", "market", 0.001, 100, "GTC", datetime.now(timezone.utc), "test", "long")
        self.assertFalse(strategy.confirm_trade_entry(*args))
        book["asks"][0][0] = 100.1
        self.assertTrue(strategy.confirm_trade_entry(*args))
        strategy.config["dry_run"] = False
        self.assertFalse(strategy.confirm_trade_entry(*args))

    def test_time_limit_exit(self):
        strategy = Strategy({"dry_run": True})
        now = datetime.now(timezone.utc)
        trade = SimpleNamespace(open_date_utc=now - timedelta(hours=48))
        self.assertEqual(strategy.custom_exit("BTC/EUR", trade, now, 100, -0.01), "time_limit_48h")

    def test_indicators_and_signals_do_not_look_into_future(self):
        rng = np.random.default_rng(7)
        close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.006, 800)))
        frame = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=800, freq="15min", tz="UTC"),
                              "open": close, "high": close * 1.003, "low": close * 0.997,
                              "close": close, "volume": 1000.0})
        strategy = Strategy({"dry_run": True})
        def analyze(df):
            df = strategy.populate_indicators(df.copy(), {})
            df = strategy.populate_entry_trend(df, {})
            return strategy.populate_exit_trend(df, {})
        full, past = analyze(frame), analyze(frame.iloc[:600])
        for column in ("sma20", "sma50", "sma200", "rsi", "atr_pct", "enter_long", "exit_long"):
            pd.testing.assert_series_equal(full[column].iloc[:600], past[column])
        self.assertEqual(strategy.stoploss, -0.03)
        self.assertFalse(strategy.can_short)


if __name__ == "__main__":
    unittest.main()
