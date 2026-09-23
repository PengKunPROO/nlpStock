"""Background job runner: screen/backtest jobs with throttled progress, persisted in Storage."""
from __future__ import annotations

import threading
import time
import uuid


class JobRunner:
    def __init__(self, storage):
        self.storage = storage

    def submit(self, job_type: str, fn) -> str:
        job_id = "j_" + uuid.uuid4().hex[:12]
        self.storage.create_job(job_id, job_type)
        state = {"last_write": 0.0, "last_done": -1}

        def progress_cb(done: int, total: int, current: str) -> None:
            now = time.monotonic()
            if done == total or done - state["last_done"] >= 5 or now - state["last_write"] >= 1.0:
                self.storage.update_job(job_id, progress={"done": done, "total": total, "current": current})
                state["last_write"] = now
                state["last_done"] = done

        def wrapper() -> None:
            try:
                result = fn(progress_cb)
                self.storage.update_job(job_id, status="done", result=result)
            except Exception as e:  # noqa: BLE001 - job boundary
                self.storage.update_job(job_id, status="error", error=str(e)[:500])

        threading.Thread(target=wrapper, daemon=True, name=f"job-{job_id}").start()
        return job_id
