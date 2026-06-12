from fastapi import APIRouter

from app.api.endpoints import batches, extract, health, runs

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(runs.router, prefix="/api", tags=["runs"])
api_router.include_router(batches.router, prefix="/api", tags=["batches"])
api_router.include_router(extract.router, prefix="/api", tags=["extract"])
