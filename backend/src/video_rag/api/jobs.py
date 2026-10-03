"""In-process job store. Ephemeral: retention is JOB_RETENTION_SECONDS."""

from __future__ import annotations

import time
import uuid


class JobStore:
    def __init__(self, retention: int = 3600):
        self.retention = retention
        self.jobs: dict = {}

    def create(self, total: int = 0) -> str:
        jid = uuid.uuid4().hex[:12]
        self.jobs[jid] = {
            "job_id": jid,
            "status": "running",
            "total": total,
            "completed": 0,
            "created": time.time(),
        }
        return jid

    def set_total(self, job_id: str, total: int) -> None:
        """Called once the catalog is expanded and the video count is known."""
        j = self.jobs.get(job_id)
        if j:
            j["total"] = total

    def bump(self, job_id: str) -> None:
        j = self.jobs.get(job_id)
        if j:
            j["completed"] += 1

    def finish(self, job_id: str, report: dict, course_id: str = "") -> None:
        j = self.jobs.get(job_id)
        if j:
            j.update(status="succeeded", report=report, course_id=course_id)

    def fail(self, job_id: str, error: str) -> None:
        j = self.jobs.get(job_id)
        if j:
            j.update(status="failed", error=error)

    def get(self, job_id: str) -> dict | None:
        j = self.jobs.get(job_id)
        if not j:
            return None
        if time.time() - j["created"] > self.retention:
            self.jobs.pop(job_id, None)
            return None
        return j
