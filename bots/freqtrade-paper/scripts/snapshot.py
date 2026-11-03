"""Snapshot de estado com autenticação local, sem exportar palavras-passe."""

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import urllib.request

root = Path(__file__).resolve().parents[1]
data = Path(os.environ.get("PAPER_DATA_DIR", root / "runtime"))
config = json.loads((data / "config.runtime.json").read_text())
api = config["api_server"]
auth = base64.b64encode(f"{api['username']}:{api['password']}".encode()).decode()
result = {"captured_at": datetime.now(timezone.utc).isoformat()}
for endpoint in ("show_config", "health", "balance", "profit", "status", "whitelist", "count"):
    request = urllib.request.Request(f"http://127.0.0.1:{api['listen_port']}/api/v1/{endpoint}",
                                     headers={"Authorization": "Basic " + auth})
    with urllib.request.urlopen(request, timeout=15) as response:
        result[endpoint] = json.load(response)
print(json.dumps(result, indent=2))
