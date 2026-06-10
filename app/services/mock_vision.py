import hashlib
import json
import os
from typing import Any, Optional

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
                return json.load(f)
        return {}

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
