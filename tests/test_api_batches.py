import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.endpoints import batches as batch_endpoint
from app.api.endpoints import runs as run_endpoint
from main import app


TERMINAL_BATCH_STATES = {"COMPLETED"}


def _client() -> TestClient:
    return TestClient(app)


def _minimal_png() -> bytes:
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c6360000002000100ffff03000006000557bfab9d"
        "0000000049454e44ae426082"
    )


def _terminal_batch(client: TestClient, batch_id: str) -> dict:
    for _ in range(40):
        response = client.get(f"/api/batches/{batch_id}")
        assert response.status_code == 200
        body = response.json()
        if body["state"] in TERMINAL_BATCH_STATES:
            return body
        time.sleep(0.05)
    raise AssertionError("batch did not complete")


def setup_function() -> None:
    batch_endpoint.batches.clear()
    run_endpoint.runs.clear()
    run_endpoint.receipts.clear()
    run_endpoint.result_cache.clear()


def test_batch_multi_upload_isolates_bad_label_and_exports_csv():
    client = _client()
    response = client.post(
        "/api/batches",
        files=[
            ("files", ("label-a.png", _minimal_png(), "image/png")),
            ("files", ("not-a-label.txt", b"plain text", "text/plain")),
        ],
        data={"application_data": json.dumps({"brandName": "MOCK"})},
    )
    assert response.status_code == 200
    created = response.json()

    batch = _terminal_batch(client, created["batchId"])

    assert batch["counts"]["ERROR"] == 1
    assert sum(batch["counts"].values()) == 2
    completed_items = [item for item in batch["items"] if item["state"] != "ERROR"]
    assert len(completed_items) == 1
    assert completed_items[0]["runId"] in run_endpoint.runs
    assert completed_items[0]["receiptRef"] == f"/api/receipts/{completed_items[0]['runId']}"

    csv_response = client.get(batch["exportUrl"])
    assert csv_response.status_code == 200
    assert "label-a.png" in csv_response.text
    assert "not-a-label.txt" in csv_response.text
    assert "INVALID_FILE_TYPE" in csv_response.text


def test_batch_events_stream_to_completion():
    client = _client()
    response = client.post(
        "/api/batches",
        files=[("files", ("label-a.png", _minimal_png(), "image/png"))],
        data={"application_data": json.dumps({"brandName": "MOCK"})},
    )
    assert response.status_code == 200
    batch_id = response.json()["batchId"]
    _terminal_batch(client, batch_id)

    events = client.get(f"/api/batches/{batch_id}/events")

    assert events.status_code == 200
    assert "event: batch.created" in events.text
    assert "event: batch.item.completed" in events.text
    assert "event: batch.completed" in events.text


def test_batch_accepts_zip_upload():
    client = _client()
    fixture = Path("app/services/fixtures/pass_bourbon.png").read_bytes()
    import io
    import zipfile

    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("pass_bourbon.png", fixture)

    response = client.post(
        "/api/batches",
        files=[("files", ("labels.zip", archive_bytes.getvalue(), "application/zip"))],
        data={
            "application_data": json.dumps(
                {
                    "brandName": "Old Forester",
                    "classType": "Kentucky Straight Bourbon Whisky",
                    "abv": "43% ABV",
                    "imported": False,
                }
            )
        },
    )
    assert response.status_code == 200
    batch = _terminal_batch(client, response.json()["batchId"])

    assert batch["counts"].get("ERROR", 0) == 0
    assert sum(batch["counts"].values()) == 1
    assert batch["items"][0]["runId"] in run_endpoint.runs
    assert batch["items"][0]["receiptRef"] == f"/api/receipts/{batch['items'][0]['runId']}"
