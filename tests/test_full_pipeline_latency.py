import asyncio
import hashlib
import json
import math
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.api.endpoints import runs as run_endpoint
from app.core.fsm import RuntimeState
from app.services.local_vision import LocalVisionProvider
from app.services.preprocess import PreprocessConfig


FIXTURE_DIR = Path(__file__).parent / "fixtures"
IMAGE_CASES_PATH = FIXTURE_DIR / "full_pipeline_image_cases.json"
RULE_CASES_PATH = FIXTURE_DIR / "rule_eval_cases.json"
LATENCY_BUDGET_MS = 4500.0
FULL_PIPELINE_FIXTURE_IDS = [
    "pass_bourbon",
    "brand_case_equivalent",
    "brand_material_mismatch",
    "abv_mismatch",
    "proof_only_equivalent",
    "net_contents_unit_equiv",
    "warning_missing",
    "warning_title_case",
    "warning_small_font_signal",
    "import_missing_origin",
]


def _require_tesseract() -> None:
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract binary is unavailable on this host; Dockerfile installs tesseract-ocr")
    pytest.importorskip("pytesseract")


def _load_cases() -> list[dict[str, Any]]:
    rule_cases = json.loads(RULE_CASES_PATH.read_text(encoding="utf-8"))
    image_cases = json.loads(IMAGE_CASES_PATH.read_text(encoding="utf-8"))
    by_id = {case["fixtureId"]: case for case in rule_cases}
    cases: list[dict[str, Any]] = []
    for image_case in image_cases:
        fixture_id = str(image_case["fixtureId"])
        case = dict(by_id[fixture_id])
        case["imagePath"] = image_case["imagePath"]
        cases.append(case)
    return cases


def _commodity_for_case(case: dict[str, Any]) -> str:
    rule_pack_path = str(case["rulePackPath"])
    if "wine-v1" in rule_pack_path:
        return "wine"
    if "malt-v1" in rule_pack_path:
        return "malt"
    return "spirits"


def _application_data(case: dict[str, Any]) -> dict[str, Any]:
    return {**case["context"], "commodity": _commodity_for_case(case)}


async def _execute_case(case: dict[str, Any]) -> tuple[dict[str, Any], float]:
    image_bytes = (FIXTURE_DIR / str(case["imagePath"])).read_bytes()
    started = time.perf_counter()
    run: dict[str, Any] = {
        "runId": str(uuid.uuid4()),
        "requestId": f"latency-{case['fixtureId']}",
        "artifactSha256": hashlib.sha256(image_bytes).hexdigest(),
        "applicationData": _application_data(case),
        "imageBytes": image_bytes,
        "contentType": "image/png",
        "state": RuntimeState.RECEIVED,
        "events": [],
        "timings": {},
        "rulePack": None,
        "startedAtMonotonic": time.monotonic(),
    }
    await run_endpoint._execute_skeleton_pipeline(run)
    return run, (time.perf_counter() - started) * 1000.0


def test_full_pipeline_image_fixture_set_has_spec_10_cases():
    cases = _load_cases()
    fixture_ids = [case["fixtureId"] for case in cases]

    assert len(cases) == 10
    assert fixture_ids == FULL_PIPELINE_FIXTURE_IDS
    for case in cases:
        image_path = FIXTURE_DIR / str(case["imagePath"])
        assert image_path.exists(), f"missing full-pipeline image fixture: {image_path}"


@pytest.mark.latency
def test_full_pipeline_tesseract_latency_p95_under_budget(monkeypatch: pytest.MonkeyPatch):
    _require_tesseract()
    monkeypatch.setenv("VISION_PROVIDER", "local")
    monkeypatch.setattr(
        run_endpoint,
        "get_vision_provider",
        lambda: LocalVisionProvider(PreprocessConfig(min_readability_score=0.0)),
    )
    run_endpoint.runs.clear()
    run_endpoint.receipts.clear()
    run_endpoint.result_cache.clear()

    durations_ms: list[float] = []
    completed: list[dict[str, Any]] = []
    for case in _load_cases():
        run, duration_ms = asyncio.run(_execute_case(case))
        durations_ms.append(duration_ms)
        completed.append(run)

        # Exact verdict semantics are covered by deterministic OCR snapshots.
        # This gate proves real Tesseract executes end-to-end within budget.
        assert run["state"] in {
            RuntimeState.PASS,
            RuntimeState.FAIL,
            RuntimeState.NEEDS_REVIEW,
            RuntimeState.UNREADABLE,
        }
        assert run["verdict"] == run["state"].value
        assert run["rulePack"].startswith(_commodity_for_case(case))
        assert run["providers"]["ocr"] == "local"
        assert run["ocr"]["metadata"]["status"] == "local_success"
        assert run["ocr"]["results"]
        assert run["findings"]
        assert run["timings"]["preprocessMs"] >= 0
        assert run["timings"]["ocrMs"] >= 0
        assert run["timings"]["rulesMs"] >= 0
        assert run["timings"]["totalMs"] <= LATENCY_BUDGET_MS
        receipt = run_endpoint.receipts[run["runId"]]
        assert receipt["runId"] == run["runId"]
        assert receipt["verdict"] == run["verdict"]
        assert receipt["providers"]["ocr"] == "local"
        assert receipt["timings"]["stages"]["ocrMs"] == run["timings"]["ocrMs"]
        assert receipt["signature"].startswith("ed25519:")

    p95_index = math.ceil(len(durations_ms) * 0.95) - 1
    p95_ms = sorted(durations_ms)[p95_index]
    p50_ms = sorted(durations_ms)[len(durations_ms) // 2]
    assert p95_ms <= LATENCY_BUDGET_MS, (
        f"full-pipeline p50={p50_ms:.2f}ms p95={p95_ms:.2f}ms "
        f"budget={LATENCY_BUDGET_MS:.2f}ms cases={len(completed)}"
    )
