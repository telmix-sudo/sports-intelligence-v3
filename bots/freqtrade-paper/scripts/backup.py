"""Backup consistente de SQLite em execução; conserva os últimos sete."""

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

root = Path(__file__).resolve().parents[1]
data = Path(os.environ.get("PAPER_DATA_DIR", root / "runtime"))
source = data / "trades.dryrun.sqlite"
if not source.exists():
    raise SystemExit("Ainda não existe base de dados para guardar.")
folder = data / "backups"
folder.mkdir(exist_ok=True)
target = folder / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + ".sqlite")
with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
for old in sorted(folder.glob("*.sqlite"))[:-7]:
    old.unlink()
print(target.name)
