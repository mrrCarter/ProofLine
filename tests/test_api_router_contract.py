from fastapi.testclient import TestClient

from app.api.api import api_router
from app.api.endpoints import batches as batch_endpoint
from app.api.endpoints import fixtures as fixture_endpoint
from app.api.endpoints import runs as run_endpoint
from main import app


CORE_CONTRACT_PATHS = {
    "/healthz",
    "/api/runs",
    "/api/runs/{run_id}",
    "/api/runs/{run_id}/events",
    "/api/receipts/pubkey",
    "/api/receipts/verify",
    "/api/receipts/{run_id}",
    "/api/batches",
    "/api/batches/{batch_id}",
    "/api/batches/{batch_id}/events",
    "/api/batches/{batch_id}/export.csv",
    "/api/batches/demo",
    "/api/fixtures",
    "/api/fixtures/{fixture_id}/image",
    "/api/artifacts/crops/{run_id}/{filename:path}",
}


def setup_function() -> None:
    batch_endpoint.batches.clear()
    run_endpoint.runs.clear()
    run_endpoint.receipts.clear()
    run_endpoint.result_cache.clear()


def test_api_router_registers_core_runtime_contract_paths():
    registered_paths = {route.path for route in api_router.routes}

    assert CORE_CONTRACT_PATHS <= registered_paths


def test_registered_missing_resource_boundaries_return_structured_404s():
    client = TestClient(app)

    cases = [
        ("/api/runs/missing-run", "RUN_NOT_FOUND"),
        ("/api/receipts/missing-run", "RECEIPT_NOT_FOUND"),
        ("/api/batches/missing-batch", "BATCH_NOT_FOUND"),
        ("/api/batches/missing-batch/export.csv", "BATCH_NOT_FOUND"),
        ("/api/fixtures/missing-fixture/image", "FIXTURE_NOT_FOUND"),
        ("/api/artifacts/crops/missing-run/missing.png", "CROP_NOT_FOUND"),
    ]
    for path, code in cases:
        response = client.get(path, headers={"x-request-id": "router-contract-test"})

        assert response.status_code == 404
        assert response.json()["error"]["code"] == code
        assert response.json()["error"]["requestId"] == "router-contract-test"


def test_fixtures_endpoint_serves_real_gallery_images_and_fields():
    client = TestClient(app)

    response = client.get("/api/fixtures")

    assert response.status_code == 200
    fixtures = response.json()["fixtures"]
    pass_bourbon = next(item for item in fixtures if item["fixtureId"] == "pass_bourbon")
    small_warning = next(item for item in fixtures if item["fixtureId"] == "warning_small_font_signal")

    assert pass_bourbon["expectedVerdict"] == "PASS"
    assert pass_bourbon["applicationData"]["brandName"] == "Old Forester"
    assert pass_bourbon["imageUrl"] == "/api/fixtures/pass_bourbon/image"
    assert "readabilityScore" not in pass_bourbon["applicationData"]
    assert not any(key.startswith("test_override_") for key in small_warning["applicationData"])

    image = client.get(pass_bourbon["imageUrl"])
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/png")
    assert image.content.startswith(b"\x89PNG")


def test_crop_artifact_endpoint_serves_only_artifact_root_files(tmp_path, monkeypatch):
    crop_dir = tmp_path / "crops" / "run-123"
    crop_dir.mkdir(parents=True)
    crop = crop_dir / "warning.png"
    crop.write_bytes(b"\x89PNG\r\n\x1a\ncrop")
    monkeypatch.setattr(fixture_endpoint, "ARTIFACT_ROOT", tmp_path)
    client = TestClient(app)

    response = client.get("/api/artifacts/crops/run-123/warning.png")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content.startswith(b"\x89PNG")

    rejected = client.get("/api/artifacts/crops/run-123/../warning.png")
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "INVALID_CROP_URI"
