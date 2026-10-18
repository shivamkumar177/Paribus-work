import asyncio

from fastapi import APIRouter, File, UploadFile

from app.schemas.hospital import BulkJobResponse, ValidationResponse
from app.services import hospital_service

router = APIRouter()


@router.post(
    "/bulk/validate",
    summary="Validate a hospital CSV without processing it",
    response_model=ValidationResponse,
)
async def validate_bulk(file: UploadFile = File(..., description="CSV with columns name,address,phone")):
    raw = await file.read()
    return hospital_service.validate_csv(raw)


@router.post(
    "/bulk",
    summary="Bulk-create hospitals from a CSV and activate the batch",
    response_model=BulkJobResponse,
    status_code=201,
)
async def bulk_create(file: UploadFile = File(..., description="CSV with columns name,address,phone")):
    """
    Accepts multipart form data with a CSV file.

    Validates the file, generates a batch id, creates each hospital against the
    Hospital Directory API, then activates the batch. Processing runs in a thread
    pool so the event loop stays responsive to progress polling.
    """
    raw = await file.read()
    job = hospital_service.start_bulk_job(file.filename or "upload.csv", raw)
    job = await asyncio.to_thread(hospital_service.process_batch, job.batch_id)
    return hospital_service.serialize_job(job)


@router.get(
    "/bulk/{batch_id}",
    summary="Poll batch progress / status",
    response_model=BulkJobResponse,
)
async def get_batch_status(batch_id: str):
    return hospital_service.get_batch(batch_id)


@router.post(
    "/bulk/{batch_id}/resume",
    summary="Resume a failed / partial batch (retries only unfinished rows)",
    response_model=BulkJobResponse,
)
async def resume_batch(batch_id: str):
    job = await asyncio.to_thread(hospital_service.process_batch, batch_id)
    return hospital_service.serialize_job(job)


@router.get(
    "/batches",
    summary="List all batch jobs",
    response_model=list[BulkJobResponse],
)
async def list_batches():
    return hospital_service.list_batches()
