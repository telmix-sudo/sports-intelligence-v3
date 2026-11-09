"""Prepara o diretório de um disco novo e executa o motor sem privilégios root."""

import os
from pathlib import Path
import pwd
import sys

root = Path(__file__).resolve().parents[1]
data = Path(os.environ.get("PAPER_DATA_DIR", root / "runtime"))
if os.environ.get("RAILWAY_PROJECT_ID"):
    mount = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    if not mount or Path(mount).resolve() != data.resolve():
        raise SystemExit(f"Railway: monte o volume persistente em {data} antes de iniciar o bot.")
data.mkdir(parents=True, exist_ok=True)
if os.geteuid() == 0:
    account = pwd.getpwnam("ftuser")
    os.chown(data, account.pw_uid, account.pw_gid)
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    os.environ["USER"] = "ftuser"
os.execv(sys.executable, [sys.executable, str(root / "scripts/supervise.py")])
