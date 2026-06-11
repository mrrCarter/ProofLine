from fastapi.testclient import TestClient

from app.api.api import api_router
from app.api.endpoints import batches as batch_endpoint
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
    ]
    for path, code in cases:
        response = client.get(path, headers={"x-request-id": "router-contract-test"})

        assert response.status_code == 404
        assert response.json()["error"]["code"] == code
        assert response.json()["error"]["requestId"] == "router-contract-test"
