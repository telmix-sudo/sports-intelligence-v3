"""Teste integrado real: mercados públicos, painel, autenticação e reinício."""

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

from healthcheck import check

root = Path(__file__).resolve().parents[1]
data = root / "runtime/verification"
data.mkdir(parents=True, exist_ok=True)
env = dict(os.environ, PAPER_DATA_DIR=str(data), PORT="18080", PAPER_BIND="127.0.0.1")
env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
evidence = {"tested_at": datetime.now(timezone.utc).isoformat(), "checks": {}}


def request(endpoint, authenticated=True):
    config = json.loads((data / "config.runtime.json").read_text())
    api = config["api_server"]
    auth = base64.b64encode(f"{api['username']}:{api['password']}".encode()).decode()
    headers = {"Authorization": "Basic " + auth} if authenticated else {}
    with urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:18080{endpoint}", headers=headers), timeout=10) as response:
        raw = response.read()
        return json.loads(raw) if endpoint.startswith("/api/") else raw


def boot(label):
    log = open(data / f"{label}.log", "w")
    process = subprocess.Popen([sys.executable, str(root / "scripts/supervise.py")],
                               cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"O motor terminou cedo; consultar {label}.log")
        try:
            check(data)
            print(f"{label}: ciclo e oito pares atualizados", flush=True)
            return process, log
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            time.sleep(2)
    process.terminate()
    process.wait(timeout=20)
    log.close()
    raise TimeoutError(f"O motor não ficou saudável; consultar {label}.log")


def stop(process, log):
    process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
    log.close()


for label in ("first_boot", "restart"):
    process, log = boot(label)
    try:
        config = request("/api/v1/show_config")
        if config["dry_run"] is not True or config["state"] != "running":
            raise AssertionError("O motor não está em simulação ativa.")
        assert config["stake_currency"] == "EUR" and config["max_open_trades"] == 3
        balance = request("/api/v1/balance")
        positions = request("/api/v1/status")
        evidence[label] = {"state": config["state"], "dry_run": config["dry_run"],
                           "balance": balance, "open_positions": positions,
                           "health": request("/api/v1/health"),
                           "whitelist": request("/api/v1/whitelist")}
        if label == "first_boot":
            assert balance["starting_capital"] == 250
            assert len(request("/api/v1/whitelist")["whitelist"]) == 8
            assert b"html" in request("/").lower()
            evidence["checks"]["web_ui"] = "pass"
            try:
                request("/api/v1/balance", authenticated=False)
                raise AssertionError("A API aceitou um pedido sem autenticação.")
            except urllib.error.HTTPError as error:
                assert error.code == 401
            evidence["checks"]["api_requires_authentication"] = "pass"
            access = (data / "access.json").read_bytes()
            subprocess.run([sys.executable, str(root / "scripts/backup.py")], env=env, check=True)
            evidence["checks"]["online_database_backup"] = "pass"
        else:
            assert access == (data / "access.json").read_bytes()
            assert balance["starting_capital"] == 250
            evidence["checks"]["restart_preserves_account_and_credentials"] = "pass"
    finally:
        stop(process, log)

folder = root / "validation"
folder.mkdir(exist_ok=True)
(folder / "runtime-verification.json").write_text(json.dumps(evidence, indent=2) + "\n")
print("Verificação integrada concluída. O processo de teste foi parado.", flush=True)
