from fastapi import APIRouter, Query
from app.schemas.upload import DataResponse
from app.services import upload_service

router = APIRouter()


@router.get(
    "/{upload_id}",
    summary="Retrieve paginated, searchable CSV data",
    response_model=DataResponse,
)
async def get_data(
    upload_id: str,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=1000, description="Rows per page"),
    search: str = Query("", description="Case-insensitive search across all columns"),
    sort_by: str = Query("", description="Column name to sort by"),
    sort_order: str = Query("asc", pattern="^(asc|desc)$", description="asc or desc"),
):
    """
    Returns paginated rows from a completed CSV upload.
    Supports full-text search across all columns and single-column sorting.
    """
    return upload_service.get_data(upload_id, page, page_size, search, sort_by, sort_order)
