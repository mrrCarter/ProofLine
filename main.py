import os
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.api import api_router

app = FastAPI(title="ProofLine API", version="0.1.0-skeleton")

app.include_router(api_router)

# Serve static UI if directory exists
static_dir = os.getenv("UI_STATIC_DIR", "static")
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        error = {**exc.detail, "requestId": exc.detail.get("requestId") or request_id}
    else:
        error = {
            "code": "HTTP_ERROR",
            "message": str(exc.detail),
            "details": None,
            "requestId": request_id,
        }
    return JSONResponse(status_code=exc.status_code, content={"error": error})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": exc.errors(),
                "requestId": request_id,
            }
        },
    )


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
