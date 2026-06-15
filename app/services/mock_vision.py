import hashlib
import json
import os
import zipfile
from pathlib import Path
from typing import Any, Optional

from app.core.constants import GOVERNMENT_WARNING_TEXT

from .vision_provider import VisionProvider, VisionResponse, OCRResult, BoundingBox


MOCK_ENGINE_VERSION = "mock-ocr-fixture@1.0.0"


class MockVisionProvider(VisionProvider):
    def __init__(self, fixture_path: Optional[str] = None):
        if fixture_path is None:
            # Default path relative to this file
            base_dir = os.path.dirname(os.path.abspath(__file__))
            fixture_path = os.path.join(base_dir, "fixtures", "mock_ocr.json")

        self.fixture_path = fixture_path
        self.fixtures = self._load_fixtures()

    def _load_fixtures(self) -> dict[str, Any]:
        if os.path.exists(self.fixture_path):
            with open(self.fixture_path, "r", encoding="utf-8") as f:
                fixtures = json.load(f)
            self._augment_generated_fixtures(fixtures)
            return fixtures
        return {}

    def _augment_generated_fixtures(self, fixtures: dict[str, Any]) -> None:
        if not isinstance(fixtures.get("fixtures"), dict):
            return

        project_root = Path(__file__).resolve().parents[2]
        fixture_root = project_root / "tests" / "fixtures"
        rule_cases_path = fixture_root / "rule_eval_cases.json"
        image_cases_path = fixture_root / "full_pipeline_image_cases.json"
        batch_zip_path = fixture_root / "batch_mixed_50.zip"
        if not rule_cases_path.is_file():
            return

        rule_cases = json.loads(rule_cases_path.read_text(encoding="utf-8"))
        rule_cases_by_id = {str(case["fixtureId"]): case for case in rule_cases}
        generated_key_by_id: dict[str, str] = {}
        for fixture_id, case in rule_cases_by_id.items():
            generated_key = f"generated:{fixture_id}"
            generated_key_by_id[fixture_id] = generated_key
            fixtures["fixtures"][generated_key] = self._generated_fixture(fixture_id, case)

        hashes = fixtures.setdefault("hashes", {})
        if image_cases_path.is_file():
            image_cases = json.loads(image_cases_path.read_text(encoding="utf-8"))
            for image_case in image_cases:
                fixture_id = str(image_case.get("fixtureId", ""))
                generated_key = generated_key_by_id.get(fixture_id)
                image_path = fixture_root / str(image_case.get("imagePath", ""))
                if generated_key and image_path.is_file():
                    hashes[hashlib.sha256(image_path.read_bytes()).hexdigest()] = generated_key

        if batch_zip_path.is_file():
            with zipfile.ZipFile(batch_zip_path) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    fixture_id = self._fixture_id_from_demo_filename(member.filename)
                    generated_key = generated_key_by_id.get(fixture_id or "")
                    if generated_key:
                        hashes[hashlib.sha256(archive.read(member)).hexdigest()] = generated_key

    def _generated_fixture(self, fixture_id: str, case: dict[str, Any]) -> dict[str, Any]:
        context = case.get("context", {})
        metadata = {
            "fixtureId": fixture_id,
            "labelType": self._label_type_for_case(case),
            "sampleName": fixture_id,
            "source": "tests/fixtures/rule_eval_cases.json",
        }
        pipeline_context = self._pipeline_context_for_case(context)
        if pipeline_context:
            metadata["pipelineContext"] = pipeline_context
        return {
            "metadata": metadata,
            "readability_score": float(context.get("readabilityScore", 0.96)),
            "results": [
                {
                    "text": self._expand_fixture_text(str(item.get("text", ""))),
                    "confidence": float(item.get("confidence", 0.0)),
                    "bbox": {"vertices": self._synthetic_bbox(index)},
                }
                for index, item in enumerate(case.get("ocr", []))
            ],
        }

    def _pipeline_context_for_case(self, context: dict[str, Any]) -> dict[str, Any]:
        warning_format: dict[str, Any] = {}
        mappings = {
            "test_override_warningBoldSignal": "boldSignal",
            "test_override_warningBoldConfidence": "boldConfidence",
            "test_override_warningSizeSignal": "sizeSignal",
            "test_override_warningSizeRatio": "sizeRatio",
        }
        for source_key, target_key in mappings.items():
            if source_key in context:
                warning_format[target_key] = context[source_key]
        return {"warningFormat": warning_format} if warning_format else {}

    def _expand_fixture_text(self, text: str) -> str:
        if text == "__GOVERNMENT_WARNING__":
            return GOVERNMENT_WARNING_TEXT
        if text == "__GOVERNMENT_WARNING_TITLE_CASE__":
            return GOVERNMENT_WARNING_TEXT.replace("GOVERNMENT WARNING:", "Government Warning:")
        return text

    def _synthetic_bbox(self, index: int) -> list[list[int]]:
        y = 40 + index * 36
        return [[40, y], [860, y], [860, y + 24], [40, y + 24]]

    def _label_type_for_case(self, case: dict[str, Any]) -> str:
        rule_pack = str(case.get("rulePackPath", "")).casefold()
        if "wine" in rule_pack:
            return "wine"
        if "malt" in rule_pack:
            return "malt"
        return "spirits"

    def _fixture_id_from_demo_filename(self, filename: str) -> Optional[str]:
        stem = Path(filename).stem
        parts = stem.split("_", 1)
        if len(parts) == 2 and parts[0].isdigit():
            stem = parts[1]
        if "_v" in stem:
            candidate, version = stem.rsplit("_v", 1)
            if version.isdigit():
                stem = candidate
        return stem or None

    def _fixture_for_hash(self, artifact_hash: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
        if "fixtures" not in self.fixtures:
            fixture = self.fixtures.get(artifact_hash)
            return artifact_hash, fixture if isinstance(fixture, dict) else None

        fixture_id = self.fixtures.get("hashes", {}).get(artifact_hash)
        # HTTP uploads always pass the real sha256; aliases keep direct mock/unit calls readable.
        fixture_id = fixture_id or self.fixtures.get("aliases", {}).get(artifact_hash)
        if not fixture_id:
            return None, None

        fixture = self.fixtures.get("fixtures", {}).get(fixture_id)
        return fixture_id, fixture if isinstance(fixture, dict) else None

    async def process_image(
        self,
        image_bytes: bytes,
        artifact_hash: Optional[str] = None,
    ) -> VisionResponse:
        """
        Returns deterministic OCR results from a mock fixture based on the artifact hash.
        """
        if not artifact_hash:
            artifact_hash = hashlib.sha256(image_bytes).hexdigest()

        fixture_id, fixture = self._fixture_for_hash(artifact_hash)

        if not fixture:
            return VisionResponse(
                results=[
                    OCRResult(
                        text="MOCK OCR TEXT",
                        confidence=0.5,
                        bbox=BoundingBox(vertices=[[0, 0], [10, 0], [10, 10], [0, 10]]),
                    )
                ],
                readability_score=0.5,
                metadata={
                    "provider": "mock",
                    "engine": "mock",
                    "providerVersion": MOCK_ENGINE_VERSION,
                    "status": "mock_unknown_hash",
                    "hash": artifact_hash,
                },
            )

        results = [
            OCRResult(
                text=r["text"],
                confidence=r["confidence"],
                bbox=BoundingBox(vertices=r["bbox"]["vertices"]),
            )
            for r in fixture["results"]
        ]

        metadata = {
            "provider": "mock",
            "engine": "mock",
            "providerVersion": MOCK_ENGINE_VERSION,
            "status": "mock_success",
            "hash": artifact_hash,
            "fixtureId": fixture_id or artifact_hash,
        }
        metadata.update(fixture.get("metadata", {}))

        return VisionResponse(
            results=results,
            readability_score=fixture["readability_score"],
            metadata=metadata,
        )
