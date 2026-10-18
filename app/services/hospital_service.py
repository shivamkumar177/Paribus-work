"""
Hospital Bulk Processing System — business logic.

Flow (mirrors the assignment):
  1. validate + parse CSV                     (required: name, address; optional: phone)
  2. generate a UUID batch id
  3. POST /hospitals/ for each row with creation_batch_id  (concurrent, with retries)
  4. PATCH /hospitals/batch/{batch_id}/activate  once every row succeeded
  5. return a comprehensive result

Progress is recorded per row in the in-memory store, so a long job can be polled
and *resumed* (only rows that are not yet `created` are retried).
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from fastapi import HTTPException

from app.core.config import settings
from app.core.logging import logger
from app.services.csv_parser import parse_csv
from app.services.hospital_client import hospital_client
from app.storage.memory import BatchJob, RowResult, store

REQUIRED_COLUMNS = ("name", "address")
OPTIONAL_COLUMNS = ("phone",)
ALL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS


# ── Validation ────────────────────────────────────────────────────────────────


def validate_csv(raw: bytes, max_hospitals: int | None = None) -> dict:
    max_hospitals = max_hospitals or settings.BULK_MAX_HOSPITALS
    errors: list[dict] = []

    try:
        rows, columns = parse_csv(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"CSV parsing failed: {exc}")

    for col in REQUIRED_COLUMNS:
        if col not in columns:
            errors.append({"row": 0, "field": col, "message": f"Missing required column '{col}'"})

    valid_rows = 0
    if not any(e["field"] in REQUIRED_COLUMNS for e in errors):
        for idx, row in enumerate(rows, start=1):
            row_ok = True
            for col in REQUIRED_COLUMNS:
                if not str(row.get(col, "")).strip():
                    errors.append({"row": idx, "field": col, "message": f"'{col}' is required"})
                    row_ok = False
            valid_rows += 1 if row_ok else 0

    if len(rows) > max_hospitals:
        errors.append(
            {
                "row": 0,
                "field": "__file__",
                "message": f"CSV has {len(rows)} rows but the maximum is {max_hospitals} hospitals",
            }
        )

    return {
        "valid": len(errors) == 0,
        "total_rows": len(rows),
        "valid_rows": valid_rows,
        "columns": columns,
        "errors": errors,
    }


def _normalize_rows(rows: list[dict], columns: list[str]) -> list[dict]:
    normalized = []
    for row in rows:
        entry = {col: str(row.get(col, "")).strip() for col in ALL_COLUMNS if col in columns}
        for col in REQUIRED_COLUMNS:
            entry.setdefault(col, "")
        normalized.append(entry)
    return normalized


def start_bulk_job(filename: str, raw: bytes) -> BatchJob:
    """Validate + parse a CSV upload and create a resumable batch job."""
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are supported")

    report = validate_csv(raw)
    if not report["valid"]:
        raise HTTPException(status_code=422, detail={"message": "CSV validation failed", "errors": report["errors"]})
    if report["total_rows"] == 0:
        raise HTTPException(status_code=422, detail="CSV contains no data rows")

    rows, columns = parse_csv(raw)
    job = store.create_batch(filename, _normalize_rows(rows, columns))
    logger.info(f"[bulk] Batch {job.batch_id} created | file={filename} rows={report['total_rows']}")
    return job


# ── Processing ────────────────────────────────────────────────────────────────


def _create_one(batch_id: str, result: RowResult) -> None:
    """Create a single hospital with retries; records the outcome in the store."""
    payload = {"name": result.payload.get("name", ""), "address": result.payload.get("address", "")}
    phone = result.payload.get("phone")
    if phone:
        payload["phone"] = phone
    payload["creation_batch_id"] = batch_id

    last_error = None
    for attempt in range(1, settings.HOSPITAL_API_MAX_RETRIES + 2):
        try:
            response = hospital_client.create_hospital(payload)
            hospital_id = response.get("id") if isinstance(response, dict) else None
            store.set_row_result(batch_id, result.row, status="created", hospital_id=hospital_id, error=None)
            return
        except Exception as exc:  # noqa: BLE001 - record any failure per row
            last_error = str(exc)
            if attempt <= settings.HOSPITAL_API_MAX_RETRIES:
                logger.debug(f"[bulk] Row {result.row} attempt {attempt} failed: {exc} — retrying")

    store.set_row_result(batch_id, result.row, status="failed", error=last_error)
    logger.warning(f"[bulk] Row {result.row} failed after retries: {last_error}")


def process_batch(batch_id: str) -> BatchJob:
    """Run / resume processing for a batch. Safe to call repeatedly."""
    job = store.get_batch(batch_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Batch '{batch_id}' not found")
    if job.batch_activated and not job.pending_results():
        return job

    started = time.perf_counter()
    store.update_batch(batch_id, status="processing", error=None)

    targets = job.pending_results()
    if targets:
        with ThreadPoolExecutor(max_workers=settings.HOSPITAL_API_CONCURRENCY) as pool:
            futures = [pool.submit(_create_one, batch_id, result) for result in targets]
            for _ in as_completed(futures):
                pass  # per-row state is written inside _create_one

    job = store.get_batch(batch_id)

    # Activate only when every hospital was created successfully.
    if job.failed_count() == 0 and job.processed_count() == job.total_hospitals and job.total_hospitals > 0:
        store.update_batch(batch_id, status="activating")
        try:
            hospital_client.activate_batch(batch_id)
            store.mark_batch_activated(batch_id)
            logger.info(f"[bulk] Batch {batch_id} activated ({job.total_hospitals} hospitals)")
        except Exception as exc:  # noqa: BLE001
            store.update_batch(batch_id, status="partial", error=f"Batch activation failed: {exc}")
            logger.error(f"[bulk] Batch {batch_id} activation failed: {exc}")
            job = store.get_batch(batch_id)
            job.processing_time_seconds = round(time.perf_counter() - started, 2)
            return job

    job = store.get_batch(batch_id)
    failed = job.failed_count()
    if failed and job.processed_count() == 0:
        status = "failed"
    elif failed:
        status = "partial"
    else:
        status = "completed"

    store.update_batch(
        batch_id,
        status=status,
        processing_time_seconds=round(time.perf_counter() - started, 2),
        completed_at=datetime.now(timezone.utc),
    )
    return store.get_batch(batch_id)


# ── Serialization ─────────────────────────────────────────────────────────────


def serialize_job(job: BatchJob) -> dict:
    return {
        "batch_id": job.batch_id,
        "filename": job.filename,
        "status": job.status,
        "total_hospitals": job.total_hospitals,
        "processed_hospitals": job.processed_count(),
        "failed_hospitals": job.failed_count(),
        "pending_hospitals": len(job.pending_results()),
        "progress_pct": job.progress_pct(),
        "processing_time_seconds": job.processing_time_seconds,
        "batch_activated": job.batch_activated,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "completed_at": job.completed_at,
        "hospitals": [
            {
                "row": r.row,
                "name": r.name,
                "hospital_id": r.hospital_id,
                "status": r.status,
                "error": r.error,
            }
            for r in job.results
        ],
    }


def get_batch(batch_id: str) -> dict:
    job = store.get_batch(batch_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Batch '{batch_id}' not found")
    return serialize_job(job)


def list_batches() -> list[dict]:
    return [serialize_job(j) for j in store.list_batches()]
