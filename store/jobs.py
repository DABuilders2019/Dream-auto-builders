"""Runs catalog imports in the background for the admin page.

One job at a time. Each job is a list of importer commands run one after another in a separate
process (so a big import can't slow down or crash the shop), with their output written to a log
file. The job's state is kept in a small JSON file next to the log, so the admin page can show it
after a restart (a job cut off by a restart or deploy shows as "interrupted"; photo downloads pick
up where they stopped when started again).
"""
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent


class Jobs:
    def __init__(self, data_dir, dsn, on_finish=None, subdir="jobs", module="importer"):
        self.dir = pathlib.Path(data_dir) / subdir
        self.module = module
        self.dir.mkdir(parents=True, exist_ok=True)
        self.dsn = dsn
        self.on_finish = on_finish
        self.lock = threading.Lock()
        self.thread = None
        state = self._read()
        if state and state.get("status") == "running":      # the server restarted while it ran
            state.update(status="interrupted", finished=_now())
            self._write(state)

    # ------------------------------------------------------------------ state
    @property
    def state_file(self):
        return self.dir / "current.json"

    def _read(self):
        try:
            return json.loads(self.state_file.read_text())
        except (FileNotFoundError, ValueError):
            return None

    def _write(self, state):
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(self.state_file)

    def current(self):
        state = self._read()
        if state:
            log = pathlib.Path(state["log"])
            state["log_tail"] = _tail(log) if log.exists() else ""
        return state

    def running(self):
        s = self._read()
        return bool(s and s.get("status") == "running")

    # ------------------------------------------------------------------ run
    def start(self, title, steps, brand_id=None):
        """steps: [(label, [importer args...]), ...]. Returns False if a job is already running."""
        with self.lock:
            if self.running():
                return False
            job_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            log = self.dir / f"{job_id}.log"
            state = {"id": job_id, "title": title, "brand_id": brand_id, "status": "running", "started": _now(),
                     "finished": None, "steps": [{"label": label, "status": "waiting"} for label, _ in steps],
                     "log": str(log)}
            self._write(state)
            self.thread = threading.Thread(target=self._run, args=(state, steps), daemon=True)
            self.thread.start()
            return True

    def _run(self, state, steps):
        env = {**os.environ, "DATABASE_URL": self.dsn, "PYTHONUNBUFFERED": "1",
               "MALLOC_ARENA_MAX": "2"}       # otherwise each download thread keeps its own memory pool
        ok = True
        with open(state["log"], "a") as log:
            for i, (label, args) in enumerate(steps):
                state["steps"][i]["status"] = "running"
                self._write(state)
                log.write(f"\n=== {label} ===\n")
                log.flush()
                t0 = time.time()
                try:
                    rc = subprocess.run([sys.executable, "-m", self.module, *args], cwd=ROOT, env=env,
                                        stdout=log, stderr=subprocess.STDOUT).returncode
                except Exception as e:      # noqa: BLE001 - report anything to the admin page
                    log.write(f"could not start: {e!r}\n")
                    rc = -1
                state["steps"][i]["status"] = "done" if rc == 0 else "failed"
                state["steps"][i]["seconds"] = round(time.time() - t0)
                self._write(state)
                if rc != 0:
                    ok = False
                    break
        state.update(status="done" if ok else "failed", finished=_now())
        self._write(state)
        self._prune()
        if self.on_finish:
            self.on_finish()

    def _prune(self, keep=20):
        logs = sorted(self.dir.glob("*.log"))
        for old in logs[:-keep]:
            old.unlink(missing_ok=True)

    def wait(self, timeout=None):
        if self.thread:
            self.thread.join(timeout)


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _tail(path, max_bytes=6000):
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        text = f.read().decode("utf-8", "replace")
    return text if size <= max_bytes else "…" + text[text.find("\n") + 1:]
