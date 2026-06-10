from typing import Any, Optional

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None
    requestId: Optional[str] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


def error_detail(code: str, message: str, request_id: str, details: Optional[Any] = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "details": details,
        "requestId": request_id,
    }
