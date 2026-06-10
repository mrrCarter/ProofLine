import os

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
async def health_check():
    return {
        "status": "ok",
        "buildSha": os.getenv("BUILD_SHA", "dev"),
        "rulePacks": ["spirits-v1@1.0.0"],
        "ocrProvider": os.getenv("VISION_PROVIDER", "mock"),
        "outboundRequired": False,
    }
