"""Start the local API, worker and web UI; stop all three together."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    if not shutil.which("npm"):
        raise SystemExit("npm is required. Install Node.js, then run npm --prefix web install.")
    if not (ROOT / "web/node_modules").exists():
        raise SystemExit("Install the frontend first: npm --prefix web install")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend")}
    commands = [
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        [sys.executable, "-m", "app.worker"],
        ["npm", "--prefix", "web", "run", "dev", "--", "--strictPort"],
    ]
    children = []

    def stop(_signum=None, _frame=None):
        for child in children:
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        for command in commands:
            children.append(subprocess.Popen(command, cwd=ROOT, env=env))
        print("\nVideo Event Workbench: http://127.0.0.1:5173\n", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.3)
        failed = next((child.returncode for child in children if child.returncode), 0)
    finally:
        stop()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
