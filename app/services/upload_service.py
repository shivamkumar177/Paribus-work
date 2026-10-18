"""
Upload business logic — initiate, chunk receipt, resume, completion, and CSV parsing.
All storage operations are delegated to the InMemoryStore singleton.
"""

from datetime import datetime, timezone
from fastapi import HTTPException

from app.storage.memory import store, UploadSession
from app.services.csv_parser import parse_csv
from app.core.logging import logger


# ── Helpers ───────────────────────────────────────────────────────────────────


def _session_to_dict(session: UploadSession) -> dict:
    received = sorted(session.received_chunks)
    missing = session.missing_chunks()
    return {
        "upload_id": session.upload_id,
        "filename": session.filename,
        "file_size": session.file_size,
        "total_chunks": session.total_chunks,
        "chunk_size": session.chunk_size,
        "status": session.status,
        "received_chunks": received,
        "missing_chunks": missing,
        "progress_pct": session.progress_pct(),
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "completed_at": session.completed_at,
        "row_count": session.row_count,
        "columns": session.columns,
        "error": session.error,
    }


def _require_session(upload_id: str) -> UploadSession:
    session = store.get_session(upload_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Upload session '{upload_id}' not found")
    return session


# ── Core operations ───────────────────────────────────────────────────────────


def initiate_upload(
    filename: str, file_size: int, total_chunks: int, chunk_size: int
) -> dict:
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are supported")
    if file_size <= 0:
        raise HTTPException(status_code=400, detail="file_size must be > 0")

    session = store.create_session(filename, file_size, total_chunks, chunk_size)
    logger.info(
        f"[upload] Session created | id={session.upload_id} file={filename} "
        f"size={file_size}B chunks={total_chunks}"
    )
    return _session_to_dict(session)


def receive_chunk(upload_id: str, chunk_index: int, data: bytes) -> dict:
    session = _require_session(upload_id)

    if session.status in ("completed", "failed", "assembling", "processing"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot upload chunks — session status is '{session.status}'",
        )
    if not (0 <= chunk_index < session.total_chunks):
        raise HTTPException(
            status_code=400,
            detail=f"chunk_index {chunk_index} out of range [0, {session.total_chunks - 1}]",
        )
    if len(data) == 0:
        raise HTTPException(status_code=400, detail="Empty chunk data")

    session = store.store_chunk(upload_id, chunk_index, data)
    missing = session.missing_chunks()

    logger.debug(
        f"[upload] Chunk received | id={upload_id} idx={chunk_index} "
        f"progress={session.progress_pct():.1f}%"
    )
    return {
        "upload_id": upload_id,
        "chunk_index": chunk_index,
        "received_chunks": sorted(session.received_chunks),
        "missing_chunks": missing,
        "total_chunks": session.total_chunks,
        "progress_pct": session.progress_pct(),
        "all_chunks_received": len(missing) == 0,
    }


def get_resume_info(upload_id: str) -> dict:
    session = _require_session(upload_id)
    received = sorted(session.received_chunks)
    missing = session.missing_chunks()
    return {
        "upload_id": upload_id,
        "filename": session.filename,
        "status": session.status,
        "total_chunks": session.total_chunks,
        "received_chunks": received,
        "missing_chunks": missing,
        "progress_pct": session.progress_pct(),
    }


def complete_upload(upload_id: str) -> dict:
    """
    Assemble chunks → parse CSV → store rows.
    Run via asyncio.to_thread to avoid blocking the event loop.
    """
    session = _require_session(upload_id)

    if session.status == "completed":
        return _session_to_dict(session)

    missing = session.missing_chunks()
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Upload incomplete — {len(missing)} chunk(s) missing: {missing[:10]}{'…' if len(missing) > 10 else ''}",
        )

    # ── Assemble ──────────────────────────────────────────────────────────────
    store.update_session(upload_id, status="assembling")
    logger.info(f"[upload] Assembling chunks | id={upload_id}")

    try:
        chunks = store.get_all_chunks(upload_id)
        raw = b"".join(chunks[i] for i in range(session.total_chunks))
        store.delete_chunks(upload_id)          # free memory immediately
    except Exception as exc:
        store.update_session(upload_id, status="failed", error=f"Assembly failed: {exc}")
        logger.error(f"[upload] Assembly error | id={upload_id} err={exc}")
        raise HTTPException(status_code=500, detail=f"Chunk assembly failed: {exc}")

    # ── Parse ─────────────────────────────────────────────────────────────────
    store.update_session(upload_id, status="processing")
    logger.info(f"[upload] Parsing CSV | id={upload_id} size={len(raw)}B")

    try:
        rows, columns = parse_csv(raw)
    except Exception as exc:
        store.update_session(upload_id, status="failed", error=f"CSV parse failed: {exc}")
        logger.error(f"[upload] Parse error | id={upload_id} err={exc}")
        raise HTTPException(status_code=422, detail=f"CSV parsing failed: {exc}")

    store.store_csv_data(upload_id, rows)
    store.update_session(
        upload_id,
        status="completed",
        row_count=len(rows),
        columns=columns,
        completed_at=datetime.now(timezone.utc),
    )
    logger.info(
        f"[upload] Complete | id={upload_id} rows={len(rows)} cols={len(columns)}"
    )
    return _session_to_dict(store.get_session(upload_id))


# ── Data retrieval ────────────────────────────────────────────────────────────


def get_data(
    upload_id: str,
    page: int,
    page_size: int,
    search: str = "",
    sort_by: str = "",
    sort_order: str = "asc",
) -> dict:
    session = _require_session(upload_id)
    if session.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Data not ready — upload status is '{session.status}'",
        )

    data: list[dict] = store.get_csv_data(upload_id) or []

    # Search (case-insensitive, across all columns)
    if search:
        needle = search.lower()
        data = [r for r in data if any(needle in str(v).lower() for v in r.values())]

    # Sort
    if sort_by and data and sort_by in (session.columns or []):
        reverse = sort_order.lower() == "desc"
        data = sorted(data, key=lambda r: str(r.get(sort_by, "")).lower(), reverse=reverse)

    # Paginate
    total = len(data)
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "upload_id": upload_id,
        "filename": session.filename,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_next": end < total,
        "has_prev": page > 1,
        "columns": session.columns or [],
        "data": data[start:end],
    }
