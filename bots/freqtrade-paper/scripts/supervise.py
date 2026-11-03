"""Reinicia o processo se terminar ou deixar de analisar dados atuais."""

from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

root = Path(__file__).resolve().parents[1]
stopping = threading.Event()
child = None


def shutdown(*_):
    stopping.set()
    if child is not None and child.poll() is None:
        child.terminate()


for sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(sig, shutdown)


def stop_child(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)


while not stopping.is_set():
    child = subprocess.Popen([sys.executable, str(root / "scripts/start.py")])
    next_check = time.monotonic() + 180
    next_backup = next_check
    failures = 0
    try:
        while not stopping.is_set() and child.poll() is None:
            now = time.monotonic()
            if now >= next_check:
                try:
                    probe = subprocess.run([sys.executable, str(root / "scripts/healthcheck.py")],
                                           timeout=95, capture_output=True, text=True)
                    failures = 0 if probe.returncode == 0 else failures + 1
                except subprocess.TimeoutExpired:
                    failures += 1
                print(f"Supervisor: falhas de saúde consecutivas={failures}", flush=True)
                next_check = time.monotonic() + 60
                if failures >= 3:
                    break
                if not failures and now >= next_backup:
                    try:
                        subprocess.run([sys.executable, str(root / "scripts/backup.py")],
                                       timeout=30, check=False)
                    except subprocess.TimeoutExpired:
                        print("Supervisor: backup excedeu o tempo limite.", flush=True)
                    next_backup = time.monotonic() + 6 * 3600
            stopping.wait(2)
    finally:
        stop_child(child)
    if not stopping.is_set():
        print("Supervisor: reinício dentro de 30 segundos, preservando a base de dados.", flush=True)
        stopping.wait(30)
