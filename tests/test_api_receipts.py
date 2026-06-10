import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from main import app


TERMINAL_STATES = {"PASS", "FAIL", "NEEDS_REVIEW", "UNREADABLE", "ERROR"}


def _client() -> TestClient:
    return TestClient(app)


def _post_bourbon(client: TestClient) -> dict:
    image = Path("app/services/fixtures/pass_bourbon.png").read_bytes()
    response = client.post(
        "/api/runs",
        files={"image": ("pass_bourbon.png", image, "image/png")},
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
    return response.json()


def _terminal_run(client: TestClient, run_id: str) -> dict:
    for _ in range(20):
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        body = response.json()
        if body["state"] in TERMINAL_STATES:
            return body
        time.sleep(0.05)
    raise AssertionError("run did not reach a terminal state")


def test_receipt_download_and_verify_round_trip():
    client = _client()
    created = _post_bourbon(client)
    run = _terminal_run(client, created["runId"])

    receipt_response = client.get(run["receiptRef"])
    assert receipt_response.status_code == 200
    receipt = receipt_response.json()

    assert receipt["receiptVersion"] == "1"
    assert receipt["runId"] == run["runId"]
    assert receipt["requestId"] == run["requestId"]
    assert receipt["artifactSha256"] == run["artifactSha256"]
    assert receipt["rulePack"] == run["rulePack"]
    assert receipt["verdict"] == run["verdict"]
    assert receipt["findings"] == run["findings"]
    assert receipt["signature"].startswith("ed25519:")

    pubkey = client.get("/api/receipts/pubkey")
    assert pubkey.status_code == 200
    assert pubkey.json()["algorithm"] == "Ed25519"

    verified = client.post("/api/receipts/verify", json=receipt)
    assert verified.status_code == 200
    assert verified.json()["valid"] is True

    tampered = {**receipt, "verdict": "PASS" if receipt["verdict"] != "PASS" else "FAIL"}
    rejected = client.post("/api/receipts/verify", json=tampered)
    assert rejected.status_code == 200
    assert rejected.json()["valid"] is False


def test_artifact_rulepack_cache_returns_signed_receipt_for_new_run():
    client = _client()
    first = _post_bourbon(client)
    first_run = _terminal_run(client, first["runId"])

    second = _post_bourbon(client)
    assert second["cacheHit"] is True
    second_run = _terminal_run(client, second["runId"])

    assert second_run["runId"] != first_run["runId"]
    assert second_run["artifactSha256"] == first_run["artifactSha256"]
    assert second_run["rulePack"] == first_run["rulePack"]
    assert second_run["verdict"] == first_run["verdict"]
    assert second_run["findings"] == first_run["findings"]
    assert second_run["receiptRef"] == f"/api/receipts/{second_run['runId']}"

    second_receipt = client.get(second_run["receiptRef"]).json()
    assert second_receipt["runId"] == second_run["runId"]
    assert client.post("/api/receipts/verify", json=second_receipt).json()["valid"] is True
