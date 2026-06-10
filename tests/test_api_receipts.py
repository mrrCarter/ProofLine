import base64
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from app.api.endpoints import runs as run_endpoint
from app.services import receipts as receipt_service
from main import app


TERMINAL_STATES = {"PASS", "FAIL", "NEEDS_REVIEW", "UNREADABLE", "ERROR"}


def _client() -> TestClient:
    return TestClient(app)


def setup_function() -> None:
    run_endpoint.runs.clear()
    run_endpoint.receipts.clear()
    run_endpoint.result_cache.clear()


def _bourbon_application_data(overrides: dict | None = None) -> dict:
    application_data = {
        "brandName": "Old Forester",
        "classType": "Kentucky Straight Bourbon Whisky",
        "abv": "43% ABV",
        "imported": False,
    }
    if overrides:
        application_data.update(overrides)
    return application_data


def _post_bourbon(client: TestClient, overrides: dict | None = None) -> dict:
    image = Path("app/services/fixtures/pass_bourbon.png").read_bytes()
    response = client.post(
        "/api/runs",
        files={"image": ("pass_bourbon.png", image, "image/png")},
        data={"application_data": json.dumps(_bourbon_application_data(overrides))},
    )
    assert response.status_code == 200
    return response.json()


def _post_minimal_png(client: TestClient, application_data: dict) -> dict:
    minimal_png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c6360000002000100ffff03000006000557bfab9d"
        "0000000049454e44ae426082"
    )
    response = client.post(
        "/api/runs",
        files={"image": ("tiny.png", minimal_png, "image/png")},
        data={"application_data": json.dumps(application_data)},
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


def _signed_receipt_for(signing_key: SigningKey, public_key_id: str) -> dict:
    receipt = {
        "receiptVersion": "1",
        "runId": "run-legacy",
        "requestId": "request-legacy",
        "artifactSha256": "0" * 64,
        "rulePack": "spirits-v1@1.0.0",
        "providers": {"ocr": "legacy", "adjudicator": None},
        "verdict": "PASS",
        "findings": [],
        "timings": {"totalMs": 1, "stages": {}},
        "createdAt": "2026-06-10T00:00:00Z",
        "publicKeyId": public_key_id,
    }
    signature = signing_key.sign(receipt_service.canonical_receipt_bytes(receipt)).signature
    return {**receipt, "signature": f"ed25519:{base64.b64encode(signature).decode('ascii')}"}


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


def test_production_requires_configured_signing_seed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PROOFLINE_ENV", "production")
    monkeypatch.delenv(receipt_service.SIGNING_KEY_ENV, raising=False)
    monkeypatch.delenv("PROOFLINE_ED25519_PRIVATE_KEY_B64", raising=False)

    with pytest.raises(RuntimeError, match=receipt_service.SIGNING_KEY_ENV):
        receipt_service._load_signing_key()


def test_verify_uses_public_key_id_registry(monkeypatch: pytest.MonkeyPatch):
    legacy_key_id = "legacy-test-key"
    legacy_signing_key = SigningKey.generate()
    legacy_receipt = _signed_receipt_for(legacy_signing_key, legacy_key_id)

    unknown = receipt_service.verify_signed_receipt(legacy_receipt)
    assert unknown == {
        "valid": False,
        "error": "unknown_public_key_id",
        "publicKeyId": legacy_key_id,
    }

    monkeypatch.setitem(receipt_service._KEY_REGISTRY, legacy_key_id, legacy_signing_key.verify_key)
    verified = receipt_service.verify_signed_receipt(legacy_receipt)

    assert verified["valid"] is True
    assert verified["publicKeyId"] == legacy_key_id


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


def test_result_cache_key_includes_normalized_application_data():
    client = _client()
    first = _post_bourbon(client)
    first_run = _terminal_run(client, first["runId"])

    second = _post_bourbon(client, {"brandName": "Other Whiskey"})
    assert second["cacheHit"] is False
    second_run = _terminal_run(client, second["runId"])

    assert second_run["runId"] != first_run["runId"]
    assert second_run["artifactSha256"] == first_run["artifactSha256"]
    assert second_run["rulePack"] == first_run["rulePack"]

    first_findings = {finding["ruleId"]: finding for finding in first_run["findings"]}
    second_findings = {finding["ruleId"]: finding for finding in second_run["findings"]}
    assert first_findings["BRAND_NAME_MATCH"]["status"] == "PASS"
    assert second_findings["BRAND_NAME_MATCH"]["status"] == "FAIL"
    assert second_run["findings"] != first_run["findings"]


def test_ui_origin_field_is_normalized_for_country_rule():
    client = _client()
    created = _post_minimal_png(client, {"brandName": "MOCK", "origin": "Imported"})
    run = _terminal_run(client, created["runId"])
    findings = {finding["ruleId"]: finding for finding in run["findings"]}

    assert "COUNTRY_OF_ORIGIN_IF_IMPORT" in findings
    assert findings["COUNTRY_OF_ORIGIN_IF_IMPORT"]["status"] == "NEEDS_REVIEW"
    assert findings["COUNTRY_OF_ORIGIN_IF_IMPORT"]["expected"]["imported"] is True


def test_commodity_selects_rule_pack():
    client = _client()
    created = _post_bourbon(client)
    default_run = _terminal_run(client, created["runId"])

    wine_created = _post_minimal_png(client, {"brandName": "MOCK", "commodity": "wine"})
    wine_run = _terminal_run(client, wine_created["runId"])

    assert default_run["rulePack"] == "spirits-v1@1.0.0"
    assert wine_run["rulePack"] == "wine-v1@1.0.0"
