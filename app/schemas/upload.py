from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


# ── Request schemas ───────────────────────────────────────────────────────────


class InitiateUploadRequest(BaseModel):
    filename: str = Field(..., description="Original CSV filename", example="sales_data.csv")
    file_size: int = Field(..., gt=0, description="Total file size in bytes")
    total_chunks: int = Field(..., gt=0, description="Number of chunks the client will send")
    chunk_size: int = Field(..., gt=0, description="Bytes per chunk (last may differ)")


# ── Response schemas ──────────────────────────────────────────────────────────


class UploadSessionResponse(BaseModel):
    upload_id: str
    filename: str
    file_size: int
    total_chunks: int
    chunk_size: int
    status: str
    received_chunks: list[int]
    missing_chunks: list[int]
    progress_pct: float
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    row_count: Optional[int] = None
    columns: Optional[list[str]] = None
    error: Optional[str] = None


class ChunkUploadResponse(BaseModel):
    upload_id: str
    chunk_index: int
    received_chunks: list[int]
    missing_chunks: list[int]
    total_chunks: int
    progress_pct: float
    all_chunks_received: bool


class ResumeInfoResponse(BaseModel):
    upload_id: str
    filename: str
    status: str
    total_chunks: int
    received_chunks: list[int]
    missing_chunks: list[int]
    progress_pct: float


class DataResponse(BaseModel):
    upload_id: str
    filename: str
    total: int
    page: int
    page_size: int
    has_next: bool
    has_prev: bool
    columns: list[str]
    data: list[dict[str, Any]]
