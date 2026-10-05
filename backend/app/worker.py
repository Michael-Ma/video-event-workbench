"""Single local worker: python -m app.worker [--once]."""
from __future__ import annotations

import argparse
import fcntl
import os
import signal
import threading

from .config import Settings
from .db import Repository, now
from .pipeline import execute_run


def recover_interrupted_runs(repo: Repository) -> None:
    # Called under the process lock. A queued run can also contain a submitting intent
    # after an interrupted recovery; execute_run will not resend it.
    for run in repo.list_runs(limit=100_000):
        if run["status"] != "running":
            continue
        unknown = False
        for task in repo.list_tasks(run["id"]):
            if task["status"] == "submitting":
                repo.upsert_task(run["id"], task["task_id"], task["stage"], "request_unknown",
                                 error_code="worker_interrupted_after_intent")
                unknown = True
            elif task["status"] == "running":
                repo.upsert_task(run["id"], task["task_id"], task["stage"], "interrupted")
            elif task["status"] == "request_unknown":
                unknown = True
        repo.update_run(run["id"], status="queued", stage="recovering")
        repo.log(run["id"], "recovering", "worker_restart",
                 "Recovered interrupted run; unsettled model requests will not be repeated",
                 "warning", {"request_unknown": unknown})


def run_worker(settings: Settings, *, once: bool = False, poll_s: float = 0.75) -> None:
    settings.ensure_dirs()
    repo = Repository(settings.db_path)
    with (settings.data_dir / "worker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another worker already owns this data directory") from exc
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()
        stop = threading.Event()

        def heartbeat():
            while not stop.is_set():
                repo.set_meta("worker_heartbeat", now())
                repo.set_meta("worker_pid", str(os.getpid()))
                stop.wait(3)

        previous_handlers = {}
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, lambda *_: stop.set())
        thread = threading.Thread(target=heartbeat, name="worker-heartbeat", daemon=True)
        thread.start()
        try:
            recover_interrupted_runs(repo)
            while not stop.is_set():
                run = repo.claim_next_run()
                if run:
                    execute_run(run, repo, settings)
                if once:
                    break
                if not run:
                    stop.wait(poll_s)
        finally:
            stop.set()
            thread.join(timeout=5)
            repo.set_meta("worker_heartbeat", "")
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def main():
    parser = argparse.ArgumentParser(description="Local event-localization worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued run")
    args = parser.parse_args()
    run_worker(Settings.from_env(), once=args.once)


if __name__ == "__main__":
    main()
