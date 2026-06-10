import asyncio
import hashlib
import json
import re
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.fsm import RuntimeState
from app.schemas.error import ErrorResponse, error_detail
from app.services.factory import get_vision_provider

router = APIRouter()

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
SUPPORTED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
    "application/pdf",
}
TERMINAL_STATES = {
    RuntimeState.PASS,
    RuntimeState.FAIL,
    RuntimeState.NEEDS_REVIEW,
    RuntimeState.UNREADABLE,
    RuntimeState.ERROR,
}

# In-memory storage is deliberately scoped to the walking skeleton.
runs: dict[str, dict[str, Any]] = {}


def _request_id(request: Request, form_request_id: Optional[str]) -> str:
    return form_request_id or request.headers.get("x-request-id") or str(uuid.uuid4())


def _raise_error(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    details: Any = None,
) -> None:
    raise HTTPException(
        status_code=status_code,
        detail=error_detail(code, message, request_id, details),
    )


def _detect_upload_type(content: bytes) -> Optional[str]:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"%PDF"):
        return "application/pdf"
    heic_brands = {b"heic", b"heix", b"hevc", b"hevx", b"mif1"}
    if len(content) > 12 and content[4:8] == b"ftyp" and content[8:12] in heic_brands:
        return "image/heic"
    return None


def _parse_application_data(raw: str, request_id: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        _raise_error(
            400,
            "INVALID_APPLICATION_DATA",
            "application_data must be a JSON object",
            request_id,
            {"offset": exc.pos},
        )
    if not isinstance(parsed, dict):
        _raise_error(
            400,
            "INVALID_APPLICATION_DATA",
            "application_data must be a JSON object",
            request_id,
        )
    return parsed


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _application_brand(application_data: dict[str, Any]) -> Optional[str]:
    for key in ("brandName", "brand_name", "brand", "applicantBrandName"):
        value = application_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _append_event(run: dict[str, Any], event: str, data: dict[str, Any]) -> None:
    run["events"].append({"event": event, "data": data})


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value.dict()


async def _run_skeleton_pipeline(run: dict[str, Any]) -> None:
    if run["state"] in TERMINAL_STATES:
        return

    started = run["startedAtMonotonic"]
    await asyncio.sleep(0.05)
    run["state"] = RuntimeState.PREPROCESSED
    _append_event(run, "preprocess.completed", {"runId": run["runId"], "latencyMs": 50})

    provider = get_vision_provider()
    ocr = await provider.process_image(run["imageBytes"], artifact_hash=run["artifactSha256"])
    run["state"] = RuntimeState.EXTRACTED
    run["ocr"] = _model_dump(ocr)
    _append_event(
        run,
        "ocr.completed",
        {
            "runId": run["runId"],
            "provider": ocr.metadata.get("provider", "mock"),
            "confidence": round(max((item.confidence for item in ocr.results), default=0.0), 3),
            "latencyMs": 25,
        },
    )

    brand = _application_brand(run["applicationData"])
    all_text = " ".join(item.text for item in ocr.results)
    normalized_brand = _normalize_text(brand or "")
    normalized_label = _normalize_text(all_text)
    brand_pass = bool(normalized_brand and normalized_brand in normalized_label)
    finding = {
        "ruleId": "BRAND_NAME_MATCH",
        "severity": "HIGH",
        "status": "PASS" if brand_pass else "FAIL",
        "expected": {"raw": brand, "normalized": normalized_brand},
        "observed": {"raw": all_text, "normalized": normalized_label},
        "confidence": 0.95 if brand_pass else 0.8,
        "evidence": {
            "text": all_text,
            "bbox": ocr.results[0].bbox.vertices if ocr.results else None,
            "provider": "mock",
        },
        "explanation": (
            "Application brand was found in mock OCR text."
            if brand_pass
            else "Application brand was not found in mock OCR text."
        ),
        "remediation": None if brand_pass else "Confirm the application brand matches the label.",
    }

    run["findings"] = [finding]
    run["state"] = RuntimeState.RULED
    _append_event(
        run,
        "field.extracted",
        {"runId": run["runId"], "field": "brandName", "value": brand},
    )
    _append_event(
        run,
        "rule.evaluated",
        {"runId": run["runId"], "ruleId": "BRAND_NAME_MATCH", "status": finding["status"]},
    )

    verdict = RuntimeState.PASS if brand_pass else RuntimeState.FAIL
    run["state"] = verdict
    run["verdict"] = verdict.value
    run["latencyMs"] = int((time.monotonic() - started) * 1000)
    _append_event(
        run,
        "run.completed",
        {
            "runId": run["runId"],
            "status": verdict.value,
            "verdict": verdict.value,
            "latencyMs": run["latencyMs"],
            "receiptId": None,
        },
    )


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "runId": run["runId"],
        "requestId": run["requestId"],
        "artifactSha256": run["artifactSha256"],
        "state": run["state"].value if isinstance(run["state"], RuntimeState) else run["state"],
        "verdict": run.get("verdict"),
        "findings": run.get("findings", []),
        "latencyMs": run.get("latencyMs"),
        "receiptRef": None,
    }


@router.post(
    "/runs",
    response_model=dict,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
)
async def create_run(
    request: Request,
    image: UploadFile = File(...),
    application_data: str = Form(...),
    request_id: Optional[str] = Form(None),
):
    rid = _request_id(request, request_id)
    image_bytes = await image.read()
    if not image_bytes:
        _raise_error(400, "EMPTY_UPLOAD", "Upload file is empty", rid)
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        _raise_error(
            413,
            "UPLOAD_TOO_LARGE",
            "Upload exceeds the 15MB limit",
            rid,
            {"maxBytes": MAX_UPLOAD_BYTES},
        )

    detected_type = _detect_upload_type(image_bytes)
    if image.content_type not in SUPPORTED_CONTENT_TYPES or detected_type is None:
        _raise_error(
            400,
            "INVALID_FILE_TYPE",
            "Upload must be a supported jpeg, png, webp, heic, or pdf artifact",
            rid,
            {"contentType": image.content_type, "detectedType": detected_type},
        )

    parsed_application_data = _parse_application_data(application_data, rid)
    run_id = str(uuid.uuid4())
    artifact_hash = hashlib.sha256(image_bytes).hexdigest()
    runs[run_id] = {
        "runId": run_id,
        "requestId": rid,
        "artifactSha256": artifact_hash,
        "applicationData": parsed_application_data,
        "imageBytes": image_bytes,
        "contentType": detected_type,
        "state": RuntimeState.RECEIVED,
        "events": [],
        "startedAtMonotonic": time.monotonic(),
    }
    _append_event(runs[run_id], "run.created", {"runId": run_id, "artifactSha256": artifact_hash})

    return {"runId": run_id, "requestId": rid, "eventsUrl": f"/api/runs/{run_id}/events"}


@router.get("/runs/{run_id}", response_model=dict, responses={404: {"model": ErrorResponse}})
async def get_run(request: Request, run_id: str):
    run = runs.get(run_id)
    if run is None:
        _raise_error(
            404,
            "RUN_NOT_FOUND",
            "Run not found",
            _request_id(request, None),
            {"runId": run_id},
        )
    return _public_run(run)


@router.get("/runs/{run_id}/events")
async def get_run_events(request: Request, run_id: str):
    run = runs.get(run_id)
    if run is None:
        _raise_error(
            404,
            "RUN_NOT_FOUND",
            "Run not found",
            _request_id(request, None),
            {"runId": run_id},
        )

    async def event_generator():
        sent = 0
        for event in run["events"]:
            sent += 1
            yield _sse(event["event"], event["data"])

        await _run_skeleton_pipeline(run)
        for event in run["events"][sent:]:
            yield _sse(event["event"], event["data"])

    return StreamingResponse(event_generator(), media_type="text/event-stream")
