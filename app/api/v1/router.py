from fastapi import APIRouter
from app.api.v1.endpoints import health, hospitals

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["Health"])
api_router.include_router(hospitals.router, prefix="/hospitals", tags=["Hospitals"])
