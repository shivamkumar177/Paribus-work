from fastapi import APIRouter
from app.api.v1.endpoints import health, uploads, data

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["Health"])
api_router.include_router(uploads.router, prefix="/uploads", tags=["Uploads"])
api_router.include_router(data.router, prefix="/data", tags=["Data"])
