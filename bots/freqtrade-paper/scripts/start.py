"""Arranque reprodutível com carteira de 250 EUR, credenciais locais e modo dry-run."""

import json
import os
from pathlib import Path
import secrets
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]


def prepare(root=ROOT):
    # O Freqtrade aplica estas variáveis depois do ficheiro: impedir qualquer override.
    overrides = [key for key in os.environ if key.startswith("FREQTRADE__")]
    if overrides:
        raise ValueError("Remova overrides FREQTRADE__; este pacote fixa a simulação em EUR.")
    config = json.loads((root / "config.json").read_text())
    if (config.get("dry_run") is not True or config.get("dry_run_wallet") != 250
            or config.get("stake_currency") != "EUR" or config.get("trading_mode") != "spot"):
        raise ValueError("Configuração inválida: requer dry-run, spot e carteira inicial de 250 EUR.")
    if any(config["exchange"].get(k) for k in ("key", "api_key", "secret", "password")):
        raise ValueError("Não são aceites chaves de negociação neste pacote de simulação.")
    data = Path(os.environ.get("PAPER_DATA_DIR", root / "runtime")).resolve()
    data.mkdir(parents=True, exist_ok=True)
    access_file = data / "access.json"
    if access_file.exists():
        access = json.loads(access_file.read_text())
    else:
        access = {"username": "telmo", "password": secrets.token_urlsafe(24),
                  "jwt_secret_key": secrets.token_urlsafe(48), "ws_token": secrets.token_urlsafe(32)}
    for key, env in (("password", "PAPER_UI_PASSWORD"), ("username", "PAPER_UI_USERNAME")):
        if os.environ.get(env):
            access[key] = os.environ[env]
    if len(access["password"]) < 16:
        raise ValueError("A palavra-passe do painel deve ter pelo menos 16 caracteres.")
    access_file.write_text(json.dumps(access, indent=2) + "\n")
    access_file.chmod(0o600)
    config["api_server"].update(access)
    config["api_server"]["listen_ip_address"] = os.environ.get("PAPER_BIND", "127.0.0.1")
    config["api_server"]["listen_port"] = int(os.environ.get("PORT", "8080"))
    config["user_data_dir"] = str(root / "user_data")
    config["strategy_path"] = str(root / "user_data" / "strategies")
    config["db_url"] = "sqlite:///" + str(data / "trades.dryrun.sqlite")
    config["logfile"] = str(data / "freqtrade.log")
    target = data / "config.runtime.json"
    target.write_text(json.dumps(config, indent=2) + "\n")
    target.chmod(0o600)
    return target


if __name__ == "__main__":
    target = prepare()
    if "--prepare-only" in sys.argv:
        print("Configuração preparada: dry-run, 250 EUR, sem chaves de negociação.")
    else:
        executable = shutil.which("freqtrade")
        if not executable:
            raise SystemExit("Freqtrade não está instalado. Utilize Docker Compose ou requirements.txt.")
        print("A iniciar simulação: 250 EUR; máximo 3 posições de 30 EUR.", flush=True)
        os.execv(executable, [executable, "trade", "--config", str(target)])
