from datetime import datetime
from typing import Optional
from pydantic import BaseModel


# ── Bulk processing ───────────────────────────────────────────────────────────


class HospitalResult(BaseModel):
    row: int
    name: str
    hospital_id: Optional[int] = None
    status: str
    error: Optional[str] = None


class BulkJobResponse(BaseModel):
    """Matches the shape expected by the assignment's `POST /hospitals/bulk`."""

    batch_id: str
    filename: str
    status: str
    total_hospitals: int
    processed_hospitals: int
    failed_hospitals: int
    pending_hospitals: int
    progress_pct: float
    processing_time_seconds: Optional[float] = None
    batch_activated: bool
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    hospitals: list[HospitalResult]


# ── CSV validation ────────────────────────────────────────────────────────────


class ValidationIssue(BaseModel):
    row: int
    field: str
    message: str


class ValidationResponse(BaseModel):
    valid: bool
    total_rows: int
    valid_rows: int
    columns: list[str]
    errors: list[ValidationIssue]
