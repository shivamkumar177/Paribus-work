import asyncio
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.schemas.upload import (
    ChunkUploadResponse,
    InitiateUploadRequest,
    ResumeInfoResponse,
    UploadSessionResponse,
)
from app.services import upload_service
from app.storage.memory import store

router = APIRouter()


@router.get(
    "/",
    summary="List all upload sessions",
    response_model=list[UploadSessionResponse],
)
async def list_uploads():
    sessions = store.list_sessions()
    return [upload_service._session_to_dict(s) for s in sessions]


@router.post(
    "/initiate",
    summary="Initiate a resumable upload session",
    response_model=UploadSessionResponse,
    status_code=201,
)
async def initiate_upload(body: InitiateUploadRequest):
    """
    Step 1 — call this before sending any chunks.
    Returns an `upload_id` the client uses for all subsequent requests.
    """
    return upload_service.initiate_upload(
        filename=body.filename,
        file_size=body.file_size,
        total_chunks=body.total_chunks,
        chunk_size=body.chunk_size,
    )


@router.post(
    "/{upload_id}/chunk",
    summary="Upload a single chunk",
    response_model=ChunkUploadResponse,
)
async def upload_chunk(
    upload_id: str,
    chunk_index: int = Form(..., description="0-based chunk index"),
    file: UploadFile = File(..., description="Raw chunk bytes"),
):
    """
    Step 2 — send each chunk independently.
    Chunks can be sent in any order and re-sent safely (idempotent).
    """
    data = await file.read()
    return upload_service.receive_chunk(upload_id, chunk_index, data)


@router.get(
    "/{upload_id}/resume",
    summary="Get resume info (which chunks are still missing)",
    response_model=ResumeInfoResponse,
)
async def get_resume_info(upload_id: str):
    """
    Call this after a disconnection to find which chunks still need uploading.
    """
    return upload_service.get_resume_info(upload_id)


@router.post(
    "/{upload_id}/complete",
    summary="Finalise upload — assemble chunks and parse CSV",
    response_model=UploadSessionResponse,
)
async def complete_upload(upload_id: str):
    """
    Step 3 — call once all chunks are uploaded.
    Runs assembly + CSV parsing in a thread pool so the event loop stays free.
    """
    return await asyncio.to_thread(upload_service.complete_upload, upload_id)


@router.get(
    "/{upload_id}/status",
    summary="Poll upload status",
    response_model=UploadSessionResponse,
)
async def get_status(upload_id: str):
    session = store.get_session(upload_id)
    if not session:
        raise HTTPException(status_code=404, detail="Upload session not found")
    return upload_service._session_to_dict(session)
