from typing import Any, Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class BaseResponse(BaseModel, Generic[T]):
    """Standard API response wrapper."""

    success: bool = True
    message: str = "OK"
    data: T | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated API response wrapper."""

    success: bool = True
    message: str = "OK"
    data: list[T] = []
    total: int = 0
    page: int = 1
    page_size: int = 10
    has_next: bool = False
    has_prev: bool = False
