import asyncio
import csv
import hashlib
import io
import os
import time
import uuid
import zipfile
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, File, Request, UploadFile, Form
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.api.endpoints import runs as run_endpoint
from app.core.fsm import RuntimeState
from app.schemas.error import ErrorResponse


router = APIRouter()

MAX_BATCH_LABELS = 300
BATCH_WORKERS = max(1, min(4, int(os.getenv("PROOFLINE_BATCH_WORKERS", "2"))))
PROCESS_POOL = ProcessPoolExecutor(max_workers=BATCH_WORKERS)
BATCH_TERMINAL_STATES = {
    RuntimeState.PASS.value,
    RuntimeState.FAIL.value,
    RuntimeState.NEEDS_REVIEW.value,
    RuntimeState.UNREADABLE.value,
    RuntimeState.ERROR.value,
}

batches: dict[str, dict[str, Any]] = {}


def _append_batch_event(batch: dict[str, Any], event: str, data: dict[str, Any]) -> None:
    batch["events"].append({"event": event, "data": data})


def _batch_counts(batch: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in batch["items"]:
        state = str(item["state"])
        counts[state] = counts.get(state, 0) + 1
    return counts


def _public_batch(batch: dict[str, Any]) -> dict[str, Any]:
    return {
        "batchId": batch["batchId"],
        "requestId": batch["requestId"],
        "state": batch["state"],
        "counts": _batch_counts(batch),
        "items": [
            {
                "itemId": item["itemId"],
                "filename": item["filename"],
                "state": item["state"],
                "verdict": item.get("verdict"),
                "runId": item.get("runId"),
                "receiptRef": item.get("receiptRef"),
                "latencyMs": item.get("latencyMs"),
                "error": item.get("error"),
            }
            for item in batch["items"]
        ],
        "eventsUrl": f"/api/batches/{batch['batchId']}/events",
        "exportUrl": f"/api/batches/{batch['batchId']}/export.csv",
    }


def _create_error_item(filename: str, code: str, message: str) -> dict[str, Any]:
    return {
        "itemId": str(uuid.uuid4()),
        "filename": filename,
        "state": RuntimeState.ERROR.value,
        "error": {"code": code, "message": message},
    }


def _create_queued_item(
    filename: str,
    image_bytes: bytes,
    content_type: str,
    application_data: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    return {
        "itemId": str(uuid.uuid4()),
        "filename": filename,
        "state": "QUEUED",
        "requestId": request_id,
        "applicationData": application_data,
        "imageBytes": image_bytes,
        "contentType": content_type,
        "artifactSha256": hashlib.sha256(image_bytes).hexdigest(),
    }


def _csv_application_rows(content: bytes) -> dict[str, dict[str, Any]]:
    if not content:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    for row in reader:
        filename = (row.get("filename") or row.get("file") or "").strip()
        if not filename:
            continue
        fields = {key: value for key, value in row.items() if key not in {"filename", "file"} and value}
        rows[filename] = fields
    return rows


def _application_for_file(
    filename: str,
    base_application_data: dict[str, Any],
    csv_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {**base_application_data, **csv_rows.get(filename, {})}


def _uploads_from_zip(filename: str, archive_bytes: bytes) -> list[tuple[str, bytes]]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            uploads: list[tuple[str, bytes]] = []
            for member in archive.infolist():
                if member.is_dir():
                    continue
                uploads.append((member.filename, archive.read(member)))
            return uploads
    except zipfile.BadZipFile:
        return [(filename, archive_bytes)]


async def _read_uploads(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    uploads: list[tuple[str, bytes]] = []
    for upload in files:
        content = await upload.read()
        filename = upload.filename or "upload"
        if filename.lower().endswith(".zip") or upload.content_type in {
            "application/zip",
            "application/x-zip-compressed",
        }:
            uploads.extend(_uploads_from_zip(filename, content))
        else:
            uploads.append((filename, content))
    return uploads


def _execute_batch_run_sync(payload: dict[str, Any]) -> dict[str, Any]:
    run = {
        "runId": payload["runId"],
        "requestId": payload["requestId"],
        "artifactSha256": payload["artifactSha256"],
        "applicationData": payload["applicationData"],
        "imageBytes": payload["imageBytes"],
        "contentType": payload["contentType"],
        "state": RuntimeState.RECEIVED,
        "events": [],
        "timings": {},
        "rulePack": payload["rulePack"],
        "startedAtMonotonic": time.monotonic(),
    }
    run_endpoint._append_event(run, "run.created", {"runId": run["runId"], "artifactSha256": run["artifactSha256"]})
    asyncio.run(run_endpoint._execute_skeleton_pipeline(run))
    return {"run": run, "receipt": run_endpoint.receipts.get(run["runId"])}


async def _process_item(batch: dict[str, Any], item: dict[str, Any]) -> None:
    item["state"] = "RUNNING"
    _append_batch_event(
        batch,
        "batch.item.started",
        {"batchId": batch["batchId"], "itemId": item["itemId"], "filename": item["filename"]},
    )

    run_id = str(uuid.uuid4())
    rule_pack_ref = run_endpoint._rule_engine_for(item["applicationData"]).rule_pack_ref
    payload = {
        "runId": run_id,
        "requestId": item["requestId"],
        "artifactSha256": item["artifactSha256"],
        "applicationData": item["applicationData"],
        "imageBytes": item["imageBytes"],
        "contentType": item["contentType"],
        "rulePack": rule_pack_ref,
    }

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(PROCESS_POOL, _execute_batch_run_sync, payload)
        run = result["run"]
        run_endpoint.runs[run_id] = run
        run_endpoint._store_receipt(run)
        public_run = run_endpoint._public_run(run)
        item.update(
            {
                "state": public_run["state"],
                "verdict": public_run["verdict"],
                "runId": public_run["runId"],
                "receiptRef": public_run.get("receiptRef"),
                "latencyMs": public_run.get("latencyMs"),
            }
        )
        _append_batch_event(
            batch,
            "batch.item.completed",
            {
                "batchId": batch["batchId"],
                "itemId": item["itemId"],
                "runId": run_id,
                "status": item["state"],
                "verdict": item.get("verdict"),
            },
        )
    except Exception as exc:
        item["state"] = RuntimeState.ERROR.value
        item["error"] = {"code": "BATCH_ITEM_ERROR", "message": str(exc)}
        _append_batch_event(
            batch,
            "batch.item.failed",
            {"batchId": batch["batchId"], "itemId": item["itemId"], "error": item["error"]},
        )


async def _batch_worker(batch: dict[str, Any], queue: asyncio.Queue[dict[str, Any]]) -> None:
    while True:
        item = await queue.get()
        try:
            await _process_item(batch, item)
        finally:
            queue.task_done()


async def _process_batch(batch_id: str) -> None:
    batch = batches.get(batch_id)
    if batch is None:
        return
    batch["state"] = "RUNNING"
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    for item in batch["items"]:
        if item["state"] == "QUEUED":
            await queue.put(item)

    workers = [asyncio.create_task(_batch_worker(batch, queue)) for _ in range(min(BATCH_WORKERS, queue.qsize() or 1))]
    await queue.join()
    for worker in workers:
        worker.cancel()
    await asyncio.gather(*workers, return_exceptions=True)

    batch["state"] = "COMPLETED"
    _append_batch_event(
        batch,
        "batch.completed",
        {"batchId": batch_id, "counts": _batch_counts(batch)},
    )


@router.post(
    "/batches",
    response_model=dict,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
)
async def create_batch(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    application_data: str = Form("{}"),
    fields_csv: Optional[UploadFile] = File(None),
    request_id: Optional[str] = Form(None),
):
    rid = run_endpoint._request_id(request, request_id)
    base_application_data = run_endpoint._normalize_application_data(
        run_endpoint._parse_application_data(application_data, rid)
    )
    csv_rows = _csv_application_rows(await fields_csv.read()) if fields_csv is not None else {}
    uploads = await _read_uploads(files)
    if not uploads:
        run_endpoint._raise_error(400, "EMPTY_BATCH", "Batch contains no files", rid)
    if len(uploads) > MAX_BATCH_LABELS:
        run_endpoint._raise_error(
            413,
            "BATCH_TOO_LARGE",
            "Batch exceeds the 300-label prototype limit",
            rid,
            {"maxLabels": MAX_BATCH_LABELS},
        )

    batch_id = str(uuid.uuid4())
    items: list[dict[str, Any]] = []
    for filename, image_bytes in uploads:
        detected_type = run_endpoint._detect_upload_type(image_bytes)
        if detected_type is None:
            items.append(
                _create_error_item(
                    filename,
                    "INVALID_FILE_TYPE",
                    "Upload bytes must be a supported jpeg, png, webp, heic, heif, or pdf artifact",
                )
            )
            continue
        items.append(
            _create_queued_item(
                filename,
                image_bytes,
                detected_type,
                _application_for_file(filename, base_application_data, csv_rows),
                f"{rid}:{filename}",
            )
        )

    batches[batch_id] = {
        "batchId": batch_id,
        "requestId": rid,
        "state": "QUEUED",
        "items": items,
        "events": [],
        "createdAtMonotonic": time.monotonic(),
    }
    _append_batch_event(batches[batch_id], "batch.created", {"batchId": batch_id, "count": len(items)})
    for item in items:
        event_name = "batch.item.queued" if item["state"] == "QUEUED" else "batch.item.failed"
        _append_batch_event(
            batches[batch_id],
            event_name,
            {"batchId": batch_id, "itemId": item["itemId"], "filename": item["filename"], "state": item["state"]},
        )

    if any(item["state"] == "QUEUED" for item in items):
        background_tasks.add_task(_process_batch, batch_id)
    else:
        batches[batch_id]["state"] = "COMPLETED"
        _append_batch_event(batches[batch_id], "batch.completed", {"batchId": batch_id, "counts": _batch_counts(batches[batch_id])})

    return _public_batch(batches[batch_id])


@router.get("/batches/{batch_id}", response_model=dict, responses={404: {"model": ErrorResponse}})
async def get_batch(request: Request, batch_id: str):
    batch = batches.get(batch_id)
    if batch is None:
        run_endpoint._raise_error(
            404,
            "BATCH_NOT_FOUND",
            "Batch not found",
            run_endpoint._request_id(request, None),
            {"batchId": batch_id},
        )
    return _public_batch(batch)


@router.get("/batches/{batch_id}/events")
async def get_batch_events(request: Request, batch_id: str):
    batch = batches.get(batch_id)
    if batch is None:
        run_endpoint._raise_error(
            404,
            "BATCH_NOT_FOUND",
            "Batch not found",
            run_endpoint._request_id(request, None),
            {"batchId": batch_id},
        )

    async def event_generator():
        sent = 0
        while True:
            while sent < len(batch["events"]):
                event = batch["events"][sent]
                sent += 1
                yield run_endpoint._sse(event["event"], event["data"])
            if batch["state"] == "COMPLETED":
                return
            await asyncio.sleep(0.05)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/batches/{batch_id}/export.csv", responses={404: {"model": ErrorResponse}})
async def export_batch_csv(request: Request, batch_id: str):
    batch = batches.get(batch_id)
    if batch is None:
        run_endpoint._raise_error(
            404,
            "BATCH_NOT_FOUND",
            "Batch not found",
            run_endpoint._request_id(request, None),
            {"batchId": batch_id},
        )

    output = io.StringIO()
    fieldnames = [
        "batchId",
        "itemId",
        "filename",
        "state",
        "verdict",
        "runId",
        "receiptRef",
        "latencyMs",
        "errorCode",
        "errorMessage",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for item in batch["items"]:
        error = item.get("error") or {}
        writer.writerow(
            {
                "batchId": batch_id,
                "itemId": item["itemId"],
                "filename": item["filename"],
                "state": item["state"],
                "verdict": item.get("verdict"),
                "runId": item.get("runId"),
                "receiptRef": item.get("receiptRef"),
                "latencyMs": item.get("latencyMs"),
                "errorCode": error.get("code"),
                "errorMessage": error.get("message"),
            }
        )

    return PlainTextResponse(
        output.getvalue(),
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="proofline-{batch_id}.csv"'},
    )
