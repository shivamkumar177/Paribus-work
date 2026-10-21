"""
Thread-safe in-memory storage for hospital batch jobs.
Designed to be swapped out for a persistent store (Redis, DB) later.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ── Domain model ─────────────────────────────────────────────────────────────


@dataclass
class RowResult:
    """Per-row outcome of a hospital bulk job (also the unit of resume)."""

    row: int                                  # 1-based row number in the CSV
    name: str
    payload: dict                             # original hospital fields
    hospital_id: Optional[int] = None
    status: str = "pending"                   # pending | created | failed | created_and_activated
    error: Optional[str] = None

    @property
    def done(self) -> bool:
        return self.status in ("created", "created_and_activated")


@dataclass
class BatchJob:
    batch_id: str
    filename: str
    status: str = "pending"                   # pending | processing | activating | completed | partial | failed
    total_hospitals: int = 0
    batch_activated: bool = False
    results: list = field(default_factory=list)   # list[RowResult]
    error: Optional[str] = None
    processing_time_seconds: Optional[float] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    def processed_count(self) -> int:
        return sum(1 for r in self.results if r.done)

    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")

    def pending_results(self) -> list:
        return [r for r in self.results if not r.done]

    def progress_pct(self) -> float:
        if not self.results:
            return 0.0
        return round((self.processed_count() + self.failed_count()) / len(self.results) * 100, 2)


# ── Store ─────────────────────────────────────────────────────────────────────


class InMemoryStore:
    """
    All public methods are thread-safe via a single reentrant lock.
    For production, replace with Redis or a database.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._batches: dict[str, BatchJob] = {}          # batch_id → BatchJob

    def create_batch(
        self, filename: str, rows: list[dict]
    ) -> BatchJob:
        with self._lock:
            job = BatchJob(
                batch_id=str(uuid.uuid4()),
                filename=filename,
                total_hospitals=len(rows),
                results=[
                    RowResult(row=i + 1, name=str(r.get("name", "")), payload=r)
                    for i, r in enumerate(rows)
                ],
            )
            self._batches[job.batch_id] = job
            return job

    def get_batch(self, batch_id: str) -> Optional[BatchJob]:
        with self._lock:
            return self._batches.get(batch_id)

    def claim_batch(self, batch_id: str) -> bool:
        """Atomically mark a batch as processing; False if a run is already in flight."""
        with self._lock:
            job = self._batches[batch_id]
            if job.status in ("processing", "activating"):
                return False
            job.status = "processing"
            job.error = None
            job.updated_at = datetime.now(timezone.utc)
            return True

    def update_batch(self, batch_id: str, **kwargs) -> Optional[BatchJob]:
        with self._lock:
            job = self._batches.get(batch_id)
            if not job:
                return None
            for key, value in kwargs.items():
                setattr(job, key, value)
            job.updated_at = datetime.now(timezone.utc)
            return job

    def set_row_result(
        self, batch_id: str, row: int, **kwargs
    ) -> Optional[BatchJob]:
        """Thread-safe per-row update; called concurrently by worker threads."""
        with self._lock:
            job = self._batches.get(batch_id)
            if not job:
                return None
            for result in job.results:
                if result.row == row:
                    for key, value in kwargs.items():
                        setattr(result, key, value)
                    break
            job.updated_at = datetime.now(timezone.utc)
            return job

    def mark_batch_activated(self, batch_id: str) -> Optional[BatchJob]:
        """Flip every successfully-created row to created_and_activated."""
        with self._lock:
            job = self._batches.get(batch_id)
            if not job:
                return None
            job.batch_activated = True
            for result in job.results:
                if result.status == "created":
                    result.status = "created_and_activated"
            job.updated_at = datetime.now(timezone.utc)
            return job

    def list_batches(self) -> list[BatchJob]:
        with self._lock:
            return sorted(
                self._batches.values(), key=lambda b: b.created_at, reverse=True
            )


# Global singleton — import this everywhere
store = InMemoryStore()
