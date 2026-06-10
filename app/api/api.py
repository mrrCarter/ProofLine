from fastapi import APIRouter

from app.api.endpoints import health, runs

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(runs.router, prefix="/api", tags=["runs"])
