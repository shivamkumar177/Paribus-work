from datetime import datetime, timezone
from fastapi import APIRouter
from app.core.config import settings

router = APIRouter()


@router.get("/", summary="Health check")
async def health_check() -> dict:
    """
    Returns the current health status of the API.
    """
    return {
        "success": True,
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ping", summary="Ping")
async def ping() -> dict:
    return {"ping": "pong"}
