"""
Thread-safe in-memory storage for upload sessions, raw chunks, and parsed CSV data.
Designed to be swapped out for a persistent store (Redis, DB) later.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ── Domain model ─────────────────────────────────────────────────────────────


@dataclass
class UploadSession:
    upload_id: str
    filename: str
    file_size: int          # total bytes
    total_chunks: int
    chunk_size: int         # bytes per chunk (last chunk may be smaller)
    status: str = "pending" # pending | uploading | assembling | processing | completed | failed
    received_chunks: set = field(default_factory=set)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    row_count: Optional[int] = None
    columns: Optional[list] = None
    error: Optional[str] = None

    def missing_chunks(self) -> list[int]:
        return sorted(set(range(self.total_chunks)) - self.received_chunks)

    def progress_pct(self) -> float:
        if self.total_chunks == 0:
            return 0.0
        return round(len(self.received_chunks) / self.total_chunks * 100, 2)


@dataclass
class RowResult:
    """Per-row outcome of a hospital bulk job (also the unit of resume)."""

    row: int                                  # 1-based row number in the CSV
    name: str
    payload: dict                             # original hospital fields
    hospital_id: Optional[int] = None
    status: str = "pending"                   # pending | created | failed | created_and_activated
    error: Optional[str] = None
    attempts: int = 0

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
    For production, replace with Redis + S3/GCS for chunks.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, UploadSession] = {}
        self._chunks: dict[str, dict[int, bytes]] = {}   # upload_id → {idx → bytes}
        self._csv_data: dict[str, list[dict]] = {}       # upload_id → [row, ...]
        self._batches: dict[str, BatchJob] = {}          # batch_id → BatchJob

    # ── Sessions ─────────────────────────────────────────────────────────────

    def create_session(
        self, filename: str, file_size: int, total_chunks: int, chunk_size: int
    ) -> UploadSession:
        with self._lock:
            session = UploadSession(
                upload_id=str(uuid.uuid4()),
                filename=filename,
                file_size=file_size,
                total_chunks=total_chunks,
                chunk_size=chunk_size,
            )
            self._sessions[session.upload_id] = session
            self._chunks[session.upload_id] = {}
            return session

    def get_session(self, upload_id: str) -> Optional[UploadSession]:
        with self._lock:
            return self._sessions.get(upload_id)

    def update_session(self, upload_id: str, **kwargs) -> Optional[UploadSession]:
        with self._lock:
            session = self._sessions.get(upload_id)
            if not session:
                return None
            for key, value in kwargs.items():
                setattr(session, key, value)
            session.updated_at = datetime.now(timezone.utc)
            return session

    def list_sessions(self) -> list[UploadSession]:
        with self._lock:
            return sorted(
                self._sessions.values(), key=lambda s: s.created_at, reverse=True
            )

    # ── Chunks ────────────────────────────────────────────────────────────────

    def store_chunk(self, upload_id: str, chunk_index: int, data: bytes) -> Optional[UploadSession]:
        with self._lock:
            session = self._sessions.get(upload_id)
            if not session:
                return None
            self._chunks[upload_id][chunk_index] = data
            session.received_chunks.add(chunk_index)
            session.status = "uploading"
            session.updated_at = datetime.now(timezone.utc)
            return session

    def get_all_chunks(self, upload_id: str) -> dict[int, bytes]:
        with self._lock:
            return dict(self._chunks.get(upload_id, {}))

    def delete_chunks(self, upload_id: str) -> None:
        """Free raw chunk memory after assembly."""
        with self._lock:
            self._chunks.pop(upload_id, None)

    # ── CSV data ──────────────────────────────────────────────────────────────

    def store_csv_data(self, upload_id: str, rows: list[dict]) -> None:
        with self._lock:
            self._csv_data[upload_id] = rows

    def get_csv_data(self, upload_id: str) -> Optional[list[dict]]:
        with self._lock:
            return self._csv_data.get(upload_id)

    # ── Hospital batch jobs ───────────────────────────────────────────────────

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
                    result.attempts += 1
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
