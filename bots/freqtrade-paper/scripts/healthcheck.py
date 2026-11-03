"""Valida o ciclo do motor; um HTTP 200 isolado não basta."""

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import urllib.request
import urllib.parse


def check(data=None):
    root = Path(__file__).resolve().parents[1]
    data = Path(data or os.environ.get("PAPER_DATA_DIR", root / "runtime"))
    config = json.loads((data / "config.runtime.json").read_text())
    api = config["api_server"]
    auth = base64.b64encode(f"{api['username']}:{api['password']}".encode()).decode()
    url = f"http://127.0.0.1:{api['listen_port']}/api/v1/health"
    req = urllib.request.Request(url, headers={"Authorization": "Basic " + auth})
    with urllib.request.urlopen(req, timeout=10) as response:
        health = json.load(response)
    timestamp = health.get("last_process_ts")
    if timestamp is None:
        raise ValueError("O motor ainda não completou um ciclo.")
    age = datetime.now(timezone.utc).timestamp() - timestamp
    if age < -60 or age > 180:
        raise ValueError(f"O motor não processa dados há {age:.0f} segundos.")
    for pair in config["exchange"]["pair_whitelist"]:
        query = urllib.parse.urlencode({"pair": pair, "timeframe": "15m", "limit": 1})
        request = urllib.request.Request(
            f"http://127.0.0.1:{api['listen_port']}/api/v1/pair_candles?{query}",
            headers={"Authorization": "Basic " + auth},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            candles = json.load(response)
        candle_ts = candles.get("data_stop_ts", 0) / 1000
        candle_age = datetime.now(timezone.utc).timestamp() - candle_ts
        if not candles.get("length") or candle_age > 2700 or candle_age < 0:
            raise ValueError(f"Dados de mercado desatualizados: {pair}")
    print(json.dumps({"healthy": True, "cycle_age_seconds": round(age), "mode": "dry_run",
                      "fresh_pairs": len(config["exchange"]["pair_whitelist"])}))
    return health


if __name__ == "__main__":
    try:
        check()
    except Exception as error:
        print(f"Healthcheck falhou: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
