import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from app.api.endpoints import runs as run_endpoint
from app.schemas.error import ErrorResponse


router = APIRouter()

FIXTURE_ROOT = run_endpoint.PROJECT_ROOT / "tests" / "fixtures"
FIXTURE_CASES_PATH = FIXTURE_ROOT / "full_pipeline_image_cases.json"
RULE_EVAL_CASES_PATH = FIXTURE_ROOT / "rule_eval_cases.json"
ARTIFACT_ROOT = Path(os.getenv("PROOFLINE_ARTIFACT_DIR", str(run_endpoint.PROJECT_ROOT / "artifacts")))
APP_DATA_EXCLUDE_KEYS = {
    "readabilityScore",
    "testOnlyComputedFormatSignal",
}
APP_DATA_EXCLUDE_PREFIXES = ("test_override_", "testOverride")


@lru_cache(maxsize=1)
def _rule_eval_cases_by_id() -> dict[str, dict[str, Any]]:
    with RULE_EVAL_CASES_PATH.open("r", encoding="utf-8") as handle:
        cases = json.load(handle)
    return {str(case["fixtureId"]): case for case in cases}


@lru_cache(maxsize=1)
def _fixture_image_cases() -> list[dict[str, Any]]:
    with FIXTURE_CASES_PATH.open("r", encoding="utf-8") as handle:
        cases = json.load(handle)
    return [case for case in cases if isinstance(case, dict)]


def application_data_for_fixture(fixture_id: str) -> dict[str, Any]:
    case = _rule_eval_cases_by_id().get(fixture_id)
    context = dict(case.get("context", {})) if case else {}
    return {
        key: value
        for key, value in context.items()
        if key not in APP_DATA_EXCLUDE_KEYS
        and not any(str(key).startswith(prefix) for prefix in APP_DATA_EXCLUDE_PREFIXES)
    }


def expected_verdict_for_fixture(fixture_id: str) -> str | None:
    case = _rule_eval_cases_by_id().get(fixture_id)
    verdict = case.get("expectedVerdict") if case else None
    return str(verdict) if verdict else None


def _fixture_image_path(fixture_id: str) -> Path | None:
    for case in _fixture_image_cases():
        if case.get("fixtureId") == fixture_id:
            image_path = FIXTURE_ROOT / str(case.get("imagePath", ""))
            return image_path
    return None


def _mime_type_for(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


def _public_fixture(case: dict[str, Any]) -> dict[str, Any]:
    fixture_id = str(case["fixtureId"])
    image_path = str(case["imagePath"])
    return {
        "fixtureId": fixture_id,
        "imagePath": image_path,
        "imageUrl": f"/api/fixtures/{fixture_id}/image",
        "expectedVerdict": expected_verdict_for_fixture(fixture_id),
        "applicationData": application_data_for_fixture(fixture_id),
    }


@router.get("/fixtures", response_model=dict)
async def list_fixtures():
    return {"fixtures": [_public_fixture(case) for case in _fixture_image_cases()]}


@router.get(
    "/fixtures/{fixture_id}/image",
    responses={404: {"model": ErrorResponse}},
)
async def get_fixture_image(request: Request, fixture_id: str):
    image_path = _fixture_image_path(fixture_id)
    if image_path is None or not image_path.is_file():
        run_endpoint._raise_error(
            404,
            "FIXTURE_NOT_FOUND",
            "Fixture image not found",
            run_endpoint._request_id(request, None),
            {"fixtureId": fixture_id},
        )
    return FileResponse(image_path, media_type=_mime_type_for(image_path), filename=image_path.name)


@router.get(
    "/artifacts/crops/{run_id}/{filename:path}",
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def get_crop_artifact(request: Request, run_id: str, filename: str):
    clean_filename = Path(filename).name
    if not clean_filename or clean_filename != filename:
        run_endpoint._raise_error(
            400,
            "INVALID_CROP_URI",
            "Crop filename must not contain path separators",
            run_endpoint._request_id(request, None),
            {"filename": filename},
        )

    artifact_root = ARTIFACT_ROOT.resolve()
    crop_path = (artifact_root / "crops" / run_id / clean_filename).resolve()
    try:
        crop_path.relative_to(artifact_root)
    except ValueError:
        run_endpoint._raise_error(
            400,
            "INVALID_CROP_URI",
            "Crop path escapes the artifact root",
            run_endpoint._request_id(request, None),
            {"filename": filename},
        )

    if not crop_path.is_file():
        run_endpoint._raise_error(
            404,
            "CROP_NOT_FOUND",
            "Crop artifact not found",
            run_endpoint._request_id(request, None),
            {"runId": run_id, "filename": clean_filename},
        )
    return FileResponse(crop_path, media_type=_mime_type_for(crop_path), filename=clean_filename)
