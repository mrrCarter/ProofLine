import asyncio
import hashlib
import json
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, NoReturn, Optional

from fastapi import APIRouter, BackgroundTasks, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.fsm import RuntimeState
from app.schemas.error import ErrorResponse, error_detail
from app.services.adjudicator import advise_if_enabled
from app.services.factory import get_vision_provider
from app.services.receipts import public_key_payload, sign_run_receipt, verify_signed_receipt
from app.services.rules import RuleEngine

router = APIRouter()

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
PROJECT_ROOT = Path(__file__).resolve().parents[3]
RULE_PACKS_BY_COMMODITY = {
    "spirits": PROJECT_ROOT / "rules" / "spirits-v1.yaml",
    "wine": PROJECT_ROOT / "rules" / "wine-v1.yaml",
    "malt": PROJECT_ROOT / "rules" / "malt-v1.yaml",
    "malt beverage": PROJECT_ROOT / "rules" / "malt-v1.yaml",
}
TERMINAL_STATES = {
    RuntimeState.PASS,
    RuntimeState.FAIL,
    RuntimeState.NEEDS_REVIEW,
    RuntimeState.UNREADABLE,
    RuntimeState.ERROR,
}
EVENT_STREAM_MAX_IDLE_POLLS = 1200
EVENT_STREAM_INITIAL_BACKOFF_SECONDS = 0.05
EVENT_STREAM_MAX_BACKOFF_SECONDS = 0.25

# In-memory storage is deliberately scoped to the prototype slice.
runs: dict[str, dict[str, Any]] = {}
receipts: dict[str, dict[str, Any]] = {}
result_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
RESERVED_RULE_CONTEXT_KEYS = {
    "_pipelineComputed",
    "pipelineComputed",
    "pipelineContext",
    "testOnlyComputedFormatSignal",
    "test_only_computed_format_signal",
}
TEST_OVERRIDE_KEY_PREFIXES = ("test_override_", "testOverride")


def _sanitize_rule_context(application_data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in application_data.items()
        if isinstance(key, str)
        and key not in RESERVED_RULE_CONTEXT_KEYS
        and not key.startswith(TEST_OVERRIDE_KEY_PREFIXES)
    }


def _request_id(request: Request, form_request_id: Optional[str]) -> str:
    return form_request_id or request.headers.get("x-request-id") or str(uuid.uuid4())


def _raise_error(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    details: Any = None,
) -> NoReturn:
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
    heic_brands = {b"heic", b"heif", b"heix", b"hevc", b"hevx", b"mif1"}
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


def _normalize_application_data(parsed: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(parsed)
    origin = normalized.get("origin")
    if isinstance(origin, str) and origin.strip():
        origin_value = origin.strip()
        origin_key = origin_value.casefold()
        domestic_values = {"domestic", "united states", "usa", "us", "u.s.", "u.s.a."}
        if origin_key in domestic_values:
            normalized.setdefault("imported", False)
        elif "import" in origin_key:
            normalized.setdefault("imported", True)
            if ":" in origin_value:
                country = origin_value.split(":", 1)[1].strip()
                if country:
                    normalized.setdefault("countryOfOrigin", country)
        else:
            normalized.setdefault("imported", True)
            normalized.setdefault("countryOfOrigin", origin_value)
    return normalized


def _application_brand(application_data: dict[str, Any]) -> Optional[str]:
    for key in ("brandName", "brand_name", "brand", "applicantBrandName"):
        value = application_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _commodity(application_data: dict[str, Any]) -> str:
    for key in ("commodity", "labelType", "label_type", "productType", "product_type"):
        value = application_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    return "spirits"


@lru_cache(maxsize=8)
def _rule_engine_for_commodity(commodity: str) -> RuleEngine:
    rule_pack_path = RULE_PACKS_BY_COMMODITY.get(commodity, RULE_PACKS_BY_COMMODITY["spirits"])
    return RuleEngine(rule_pack_path)


def _rule_engine_for(application_data: dict[str, Any]) -> RuleEngine:
    return _rule_engine_for_commodity(_commodity(application_data))


def _append_event(run: dict[str, Any], event: str, data: dict[str, Any]) -> None:
    run["events"].append({"event": event, "data": data})


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _state_value(state: Any) -> str:
    if isinstance(state, RuntimeState):
        return state.value
    return str(state)


def _next_event_stream_backoff(current: float) -> float:
    if EVENT_STREAM_MAX_BACKOFF_SECONDS <= 0:
        return 0.0
    return min(EVENT_STREAM_MAX_BACKOFF_SECONDS, max(EVENT_STREAM_INITIAL_BACKOFF_SECONDS, current * 2))


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    return value.dict()


def _finding_payload(finding: Any) -> dict[str, Any]:
    return _model_dump(finding)


def _terminal_state(run: dict[str, Any]) -> bool:
    return run["state"] in TERMINAL_STATES


def _receipt_ref(run_id: str) -> str:
    return f"/api/receipts/{run_id}"


def _store_receipt(run: dict[str, Any]) -> dict[str, Any]:
    receipt = sign_run_receipt(run)
    receipts[run["runId"]] = receipt
    run["receiptId"] = run["runId"]
    run["receiptRef"] = _receipt_ref(run["runId"])
    return receipt


def _application_data_hash(application_data: dict[str, Any]) -> str:
    canonical = json.dumps(
        application_data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cache_key_for(
    artifact_hash: str,
    rule_pack: str,
    application_data: dict[str, Any],
) -> tuple[str, str, str]:
    return (artifact_hash, rule_pack, _application_data_hash(application_data))


def _cache_key(run: dict[str, Any]) -> tuple[str, str, str]:
    return _cache_key_for(run["artifactSha256"], run["rulePack"], run["applicationData"])


def _cache_result(run: dict[str, Any]) -> None:
    result_cache[_cache_key(run)] = {
        "verdict": run["verdict"],
        "findings": run["findings"],
        "rulePack": run["rulePack"],
        "providers": run.get("providers", {}),
    }


def _complete_cached_run(run: dict[str, Any], cached: dict[str, Any]) -> None:
    run["state"] = RuntimeState(cached["verdict"])
    run["verdict"] = cached["verdict"]
    run["findings"] = cached["findings"]
    run["rulePack"] = cached["rulePack"]
    run["providers"] = cached.get("providers", {})
    run["latencyMs"] = int((time.monotonic() - run["startedAtMonotonic"]) * 1000)
    run["timings"] = {"cacheMs": run["latencyMs"], "totalMs": run["latencyMs"]}
    _store_receipt(run)
    _append_event(
        run,
        "run.completed",
        {
            "runId": run["runId"],
            "status": run["verdict"],
            "verdict": run["verdict"],
            "latencyMs": run["latencyMs"],
            "receiptId": run["receiptId"],
            "cacheHit": True,
        },
    )


async def _run_skeleton_pipeline(run_id: str) -> None:
    run = runs.get(run_id)
    if run is None or _terminal_state(run):
        return

    try:
        await _execute_skeleton_pipeline(run)
    except Exception as exc:
        started = run["startedAtMonotonic"]
        run["state"] = RuntimeState.ERROR
        run["verdict"] = RuntimeState.ERROR.value
        run["latencyMs"] = int((time.monotonic() - started) * 1000)
        run["timings"]["totalMs"] = run["latencyMs"]
        _append_event(
            run,
            "run.completed",
            {
                "runId": run["runId"],
                "status": RuntimeState.ERROR.value,
                "verdict": RuntimeState.ERROR.value,
                "latencyMs": run["latencyMs"],
                "receiptId": None,
                "error": {
                    "code": "PIPELINE_ERROR",
                    "message": str(exc),
                },
            },
        )


async def _execute_skeleton_pipeline(run: dict[str, Any]) -> None:
    started = run["startedAtMonotonic"]

    preprocess_started = time.monotonic()
    await asyncio.sleep(0.05)
    run["state"] = RuntimeState.PREPROCESSED
    run["timings"]["preprocessMs"] = int((time.monotonic() - preprocess_started) * 1000)
    _append_event(
        run,
        "preprocess.completed",
        {"runId": run["runId"], "latencyMs": run["timings"]["preprocessMs"]},
    )

    ocr_started = time.monotonic()
    provider = get_vision_provider()
    ocr = await provider.process_image(run["imageBytes"], artifact_hash=run["artifactSha256"])
    run["timings"]["ocrMs"] = int((time.monotonic() - ocr_started) * 1000)
    run["state"] = RuntimeState.EXTRACTED
    run["ocr"] = _model_dump(ocr)
    provider_name = ocr.metadata.get("provider", "mock")
    run["providers"] = {"ocr": provider_name, "adjudicator": None}
    _append_event(
        run,
        "ocr.completed",
        {
            "runId": run["runId"],
            "provider": provider_name,
            "confidence": round(max((item.confidence for item in ocr.results), default=0.0), 3),
            "latencyMs": run["timings"]["ocrMs"],
        },
    )

    rules_started = time.monotonic()
    brand = _application_brand(run["applicationData"])
    rule_context = _sanitize_rule_context(run["applicationData"])
    pipeline_context = ocr.metadata.get("pipelineContext")
    if isinstance(pipeline_context, dict):
        rule_context["pipelineContext"] = pipeline_context
    rule_context["ocr_provider"] = provider_name
    rule_engine = _rule_engine_for(rule_context)
    rule_result = rule_engine.evaluate_with_verdict(
        [_model_dump(item) for item in ocr.results],
        rule_context,
    )
    finding_payloads = [_finding_payload(finding) for finding in rule_result["findings"]]

    run["findings"] = finding_payloads
    run["rulePack"] = rule_result["rulePack"]
    run["state"] = RuntimeState.RULED
    run["timings"]["rulesMs"] = int((time.monotonic() - rules_started) * 1000)
    _append_event(
        run,
        "field.extracted",
        {"runId": run["runId"], "field": "brandName", "value": brand},
    )
    for finding_payload in finding_payloads:
        _append_event(
            run,
            "rule.evaluated",
            {
                "runId": run["runId"],
                "ruleId": finding_payload["ruleId"],
                "status": finding_payload["status"],
                "latencyMs": run["timings"]["rulesMs"],
            },
        )

    verdict = RuntimeState(rule_result["verdict"])
    if verdict == RuntimeState.NEEDS_REVIEW:
        advice = await advise_if_enabled(run)
        if advice["status"] != "disabled":
            adjudicator_provider = advice.get("provider")
            run["providers"]["adjudicator"] = adjudicator_provider
            run["adjudication"] = advice
            run["state"] = RuntimeState.ESCALATED
            _append_event(
                run,
                "run.escalated",
                {"runId": run["runId"], "reason": advice.get("status"), "provider": adjudicator_provider},
            )
            _append_event(
                run,
                "agent.spawned",
                {"runId": run["runId"], "role": "vlm_adjudicator", "reason": advice.get("status")},
            )
            _append_event(
                run,
                "agent.opinion",
                {
                    "runId": run["runId"],
                    "decision": advice.get("advisoryDecision", advice.get("decision")),
                    "rationale": advice.get("rationale", advice.get("reason", "")),
                },
            )

    run["state"] = verdict
    run["verdict"] = verdict.value
    run["latencyMs"] = int((time.monotonic() - started) * 1000)
    run["timings"]["totalMs"] = run["latencyMs"]
    _store_receipt(run)
    _cache_result(run)
    _append_event(
        run,
        "run.completed",
        {
            "runId": run["runId"],
            "status": verdict.value,
            "verdict": verdict.value,
            "latencyMs": run["latencyMs"],
            "receiptId": run["receiptId"],
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
        "rulePack": run.get("rulePack"),
        "latencyMs": run.get("latencyMs"),
        "timings": run.get("timings", {}),
        "receiptRef": run.get("receiptRef"),
    }


@router.post(
    "/runs",
    response_model=dict,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
)
async def create_run(
    request: Request,
    background_tasks: BackgroundTasks,
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
    if detected_type is None:
        _raise_error(
            400,
            "INVALID_FILE_TYPE",
            "Upload bytes must be a supported jpeg, png, webp, heic, heif, or pdf artifact",
            rid,
            {"contentType": image.content_type, "detectedType": detected_type},
        )

    parsed_application_data = _normalize_application_data(_parse_application_data(application_data, rid))
    run_id = str(uuid.uuid4())
    artifact_hash = hashlib.sha256(image_bytes).hexdigest()
    rule_pack_ref = _rule_engine_for(parsed_application_data).rule_pack_ref
    runs[run_id] = {
        "runId": run_id,
        "requestId": rid,
        "artifactSha256": artifact_hash,
        "applicationData": parsed_application_data,
        "imageBytes": image_bytes,
        "contentType": detected_type,
        "state": RuntimeState.RECEIVED,
        "events": [],
        "timings": {},
        "rulePack": rule_pack_ref,
        "startedAtMonotonic": time.monotonic(),
    }
    _append_event(runs[run_id], "run.created", {"runId": run_id, "artifactSha256": artifact_hash})
    cached = result_cache.get(_cache_key_for(artifact_hash, rule_pack_ref, parsed_application_data))
    if cached:
        _complete_cached_run(runs[run_id], cached)
        return {
            "runId": run_id,
            "requestId": rid,
            "eventsUrl": f"/api/runs/{run_id}/events",
            "receiptUrl": _receipt_ref(run_id),
            "cacheHit": True,
        }

    background_tasks.add_task(_run_skeleton_pipeline, run_id)

    return {
        "runId": run_id,
        "requestId": rid,
        "eventsUrl": f"/api/runs/{run_id}/events",
        "cacheHit": False,
    }


@router.get("/receipts/pubkey", response_model=dict)
async def get_receipt_public_key():
    return public_key_payload()


@router.post("/receipts/verify", response_model=dict)
async def verify_receipt(receipt: dict[str, Any]):
    return verify_signed_receipt(receipt)


@router.get("/receipts/{run_id}", response_model=dict, responses={404: {"model": ErrorResponse}})
async def get_receipt(request: Request, run_id: str):
    receipt = receipts.get(run_id)
    if receipt is None:
        _raise_error(
            404,
            "RECEIPT_NOT_FOUND",
            "Receipt not found",
            _request_id(request, None),
            {"runId": run_id},
        )
    return receipt


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
        idle_polls = 0
        backoff_seconds = EVENT_STREAM_INITIAL_BACKOFF_SECONDS
        while idle_polls < EVENT_STREAM_MAX_IDLE_POLLS:
            emitted = False
            while sent < len(run["events"]):
                event = run["events"][sent]
                sent += 1
                emitted = True
                yield _sse(event["event"], event["data"])
            if emitted:
                idle_polls = 0
                backoff_seconds = EVENT_STREAM_INITIAL_BACKOFF_SECONDS

            if _terminal_state(run):
                return

            idle_polls += 1
            await asyncio.sleep(min(backoff_seconds, EVENT_STREAM_MAX_BACKOFF_SECONDS))
            backoff_seconds = _next_event_stream_backoff(backoff_seconds)

        yield _sse(
            "run.stream.timeout",
            {
                "runId": run_id,
                "state": _state_value(run["state"]),
                "maxAttempts": EVENT_STREAM_MAX_IDLE_POLLS,
            },
        )

    return StreamingResponse(event_generator(), media_type="text/event-stream")
