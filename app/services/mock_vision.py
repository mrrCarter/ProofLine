import json
import hashlib
import os
from typing import Dict, Any, Optional

from .vision_provider import VisionProvider, VisionResponse, OCRResult, BoundingBox


class MockVisionProvider(VisionProvider):
    def __init__(self, fixture_path: Optional[str] = None):
        if fixture_path is None:
            # Default path relative to this file
            base_dir = os.path.dirname(os.path.abspath(__file__))
            fixture_path = os.path.join(base_dir, "fixtures", "mock_ocr.json")

        self.fixture_path = fixture_path
        self.fixtures = self._load_fixtures()

    def _load_fixtures(self) -> Dict[str, Any]:
        if os.path.exists(self.fixture_path):
            with open(self.fixture_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

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

        fixture = self.fixtures.get(artifact_hash)

        if not fixture:
            # Fallback: if artifact_hash is a known fixture name, use its hash
            # This is useful for testing with names like "pass_bourbon"
            name_to_hash = {
                "pass_bourbon": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
            }
            if artifact_hash in name_to_hash:
                fixture = self.fixtures.get(name_to_hash[artifact_hash])

        if not fixture:
            # Generic fallback for unknown hashes
            return VisionResponse(
                results=[
                    OCRResult(
                        text="MOCK OCR TEXT",
                        confidence=0.5,
                        bbox=BoundingBox(vertices=[[0, 0], [10, 0], [10, 10], [0, 10]])
                    )
                ],
                readability_score=0.5,
                metadata={"status": "mock_unknown_hash", "hash": artifact_hash}
            )

        results = [
            OCRResult(
                text=r["text"],
                confidence=r["confidence"],
                bbox=BoundingBox(vertices=r["bbox"]["vertices"]),
            )
            for r in fixture["results"]
        ]

        return VisionResponse(
            results=results,
            readability_score=fixture["readability_score"],
            metadata=fixture.get(
                "metadata",
                {"status": "mock_success", "hash": artifact_hash, "provider": "mock"},
            ),
        )
