import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, File, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from app.schemas.hospital import BulkJobResponse, ValidationResponse
from app.services import hospital_service

router = APIRouter()

WAIT_QUERY = Query(
    True,
    description="true: respond when the batch is finished. "
    "false: respond 202 immediately and follow progress via GET /hospitals/bulk/{batch_id}/events",
)
FINISHED_STATUSES = ("completed", "partial", "failed")


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
async def bulk_create(
    response: Response,
    background: BackgroundTasks,
    file: UploadFile = File(..., description="CSV with columns name,address,phone"),
    wait: bool = WAIT_QUERY,
):
    """
    Accepts multipart form data with a CSV file.

    Validates the file, generates a batch id, creates each hospital against the
    Hospital Directory API, then activates the batch. Processing runs in a thread
    pool so the event loop stays responsive to progress requests.
    """
    raw = await file.read()
    job = hospital_service.start_bulk_job(file.filename or "upload.csv", raw)
    return await _run(job.batch_id, wait, response, background)


@router.get(
    "/bulk/{batch_id}",
    summary="Poll batch progress / status",
    response_model=BulkJobResponse,
)
async def get_batch_status(batch_id: str):
    return hospital_service.get_batch(batch_id)


@router.get(
    "/bulk/{batch_id}/events",
    summary="Stream batch progress as Server-Sent Events",
    response_class=StreamingResponse,
)
async def batch_events(batch_id: str, request: Request):
    """
    Sends the batch (same shape as `GET /hospitals/bulk/{batch_id}`) as an SSE
    `data:` event whenever it changes, and closes once the batch is completed,
    partial or failed.
    """
    hospital_service.get_batch(batch_id)  # 404 before opening the stream

    async def stream():
        last, idle = None, 0
        while not await request.is_disconnected():
            batch = hospital_service.get_batch(batch_id)
            data = json.dumps(jsonable_encoder(batch))
            if data != last:
                yield f"data: {data}\n\n"
                last, idle = data, 0
            else:
                idle += 1
                if idle % 30 == 0:
                    yield ": keep-alive\n\n"  # stops proxies closing a quiet stream
            if batch["status"] in FINISHED_STATUSES:
                return
            # ponytail: polls the in-memory store every 0.5s; push via pub/sub if listeners grow
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/bulk/{batch_id}/resume",
    summary="Resume a failed / partial batch (retries only unfinished rows)",
    response_model=BulkJobResponse,
)
async def resume_batch(
    batch_id: str,
    response: Response,
    background: BackgroundTasks,
    wait: bool = WAIT_QUERY,
):
    return await _run(batch_id, wait, response, background)


@router.get(
    "/batches",
    summary="List all batch jobs",
    response_model=list[BulkJobResponse],
)
async def list_batches():
    return hospital_service.list_batches()


async def _run(batch_id: str, wait: bool, response: Response, background: BackgroundTasks) -> dict:
    if wait:
        job = await asyncio.to_thread(hospital_service.process_batch, batch_id)
        return hospital_service.serialize_job(job)

    # Claim before responding, so a 404/409 is returned now and the event
    # stream never sees a stale "finished" status for a batch about to run.
    if hospital_service.claim_for_processing(batch_id):
        background.add_task(hospital_service.run_batch, batch_id)
        response.status_code = 202
    return hospital_service.get_batch(batch_id)
