from pathlib import Path

from fastapi.testclient import TestClient

from app.api.endpoints import extract as extract_endpoint
from app.services.vision_provider import BoundingBox, OCRResult, VisionResponse
from main import app


MINIMAL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfab9d"
    "0000000049454e44ae426082"
)


def _client() -> TestClient:
    return TestClient(app)


def _suggestions_by_key(body: dict):
    suggested_fields = body["suggestedFields"]
    if isinstance(suggested_fields, dict):
        return suggested_fields
    return {item["key"]: item for item in suggested_fields}


def _statuses_by_key(body: dict):
    field_statuses = body["fieldStatuses"]
    if isinstance(field_statuses, dict):
        return field_statuses
    return {item["key"]: item for item in field_statuses}


def test_extract_returns_suggestions_from_mock_fixture():
    client = _client()
    image = Path("app/services/fixtures/pass_bourbon.png").read_bytes()

    response = client.post(
        "/api/extract",
        files={"image": ("pass_bourbon.png", image, "image/png")},
    )

    assert response.status_code == 200
    body = response.json()
    suggestions = _suggestions_by_key(body)

    assert body["provider"] == "mock"
    assert suggestions["commodity"]["value"] == "spirits"
    assert "brandName" not in suggestions
    assert suggestions["classType"]["value"] == "KENTUCKY STRAIGHT BOURBON WHISKY"
    assert suggestions["alcoholContent"]["value"] == "43% ALC/VOL"
    assert "netContents" not in suggestions

    statuses = _statuses_by_key(body)
    assert set(statuses) == set(extract_endpoint.FIELD_LABELS)
    assert body["expectedFields"] == body["fieldStatuses"]
    assert body["expectedFieldItems"] == body["fieldStatusItems"]
    assert statuses["classType"]["status"] == "detected"
    assert statuses["classType"]["confidence"] > 0
    assert statuses["brandName"]["status"] == "missing"
    assert statuses["brandName"]["value"] == ""
    assert statuses["brandName"]["confidence"] == 0.0


def test_extract_unknown_mock_hash_does_not_fabricate_fields():
    client = _client()

    response = client.post(
        "/api/extract",
        files={"image": ("tiny.png", MINIMAL_PNG, "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["suggestedFields"] == {}


def test_extract_parses_net_contents_and_origin_from_ocr(monkeypatch):
    class FakeProvider:
        async def process_image(self, image_bytes, artifact_hash=None):
            return VisionResponse(
                results=[
                    OCRResult(
                        text="CAVIT PINOT GRIGIO",
                        confidence=0.96,
                        bbox=BoundingBox(vertices=[[0, 0], [10, 0], [10, 10], [0, 10]]),
                    ),
                    OCRResult(
                        text="Product of Italy 750 mL",
                        confidence=0.93,
                        bbox=BoundingBox(vertices=[[0, 12], [10, 12], [10, 22], [0, 22]]),
                    ),
                ],
                readability_score=0.91,
                metadata={"provider": "fake", "status": "fake_success", "labelType": "wine"},
            )

    monkeypatch.setattr(extract_endpoint, "get_vision_provider", lambda: FakeProvider())
    client = _client()

    response = client.post(
        "/api/extract",
        files={"image": ("tiny.png", MINIMAL_PNG, "image/png")},
    )

    assert response.status_code == 200
    suggestions = _suggestions_by_key(response.json())

    assert suggestions["commodity"]["value"] == "wine"
    assert "brandName" not in suggestions
    assert suggestions["classType"]["value"] == "CAVIT PINOT GRIGIO"
    assert suggestions["netContents"]["value"] == "750 mL"
    assert suggestions["origin"]["value"] == "Imported"
    assert suggestions["countryOfOrigin"]["value"] == "Italy"

    statuses = _statuses_by_key(response.json())
    assert statuses["netContents"]["status"] == "detected"
    assert statuses["origin"]["status"] == "detected"
    assert statuses["producerName"]["status"] == "missing"

    body = response.json()
    assert body["rawText"] == "CAVIT PINOT GRIGIO Product of Italy 750 mL"
    assert body["rawOcrItems"] == [
        {
            "text": "CAVIT PINOT GRIGIO",
            "confidence": 0.96,
            "bbox": [[0, 0], [10, 0], [10, 10], [0, 10]],
        },
        {
            "text": "Product of Italy 750 mL",
            "confidence": 0.93,
            "bbox": [[0, 12], [10, 12], [10, 22], [0, 22]],
        },
    ]


def test_extract_omits_noisy_hard_bottle_guesses(monkeypatch):
    class FakeProvider:
        async def process_image(self, image_bytes, artifact_hash=None):
            return VisionResponse(
                results=[
                    OCRResult(
                        text="ORIGIN",
                        confidence=0.96,
                        bbox=BoundingBox(vertices=[[0, 0], [10, 0], [10, 10], [0, 10]]),
                    ),
                    OCRResult(
                        text="PINOT",
                        confidence=0.96,
                        bbox=BoundingBox(vertices=[[0, 12], [10, 12], [10, 22], [0, 22]]),
                    ),
                    OCRResult(
                        text="Product of ITALY wo",
                        confidence=0.94,
                        bbox=BoundingBox(vertices=[[0, 24], [10, 24], [10, 34], [0, 34]]),
                    ),
                    OCRResult(
                        text="Imported by CAVIT GOVERNMENT WARNING health problems",
                        confidence=0.92,
                        bbox=BoundingBox(vertices=[[0, 36], [10, 36], [10, 46], [0, 46]]),
                    ),
                    OCRResult(
                        text="12.5 750",
                        confidence=0.96,
                        bbox=BoundingBox(vertices=[[0, 48], [10, 48], [10, 58], [0, 58]]),
                    ),
                ],
                readability_score=0.57,
                metadata={"provider": "fake", "status": "fake_success"},
            )

    monkeypatch.setattr(extract_endpoint, "get_vision_provider", lambda: FakeProvider())
    client = _client()

    response = client.post(
        "/api/extract",
        files={"image": ("tiny.png", MINIMAL_PNG, "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["suggestedFields"] == {}


def test_extract_accepts_validated_hard_bottle_abv_without_net_contents_guess(monkeypatch):
    class FakeProvider:
        async def process_image(self, image_bytes, artifact_hash=None):
            return VisionResponse(
                results=[
                    OCRResult(
                        text="CAVIT PINOT GRIGIO",
                        confidence=0.96,
                        bbox=BoundingBox(vertices=[[0, 0], [10, 0], [10, 10], [0, 10]]),
                    ),
                    OCRResult(
                        text="ALC 12.5% BY VOL",
                        confidence=0.91,
                        bbox=BoundingBox(vertices=[[0, 12], [10, 12], [10, 22], [0, 22]]),
                    ),
                    OCRResult(
                        text="L25064 PRODUCT OF ITALY WO",
                        confidence=0.94,
                        bbox=BoundingBox(vertices=[[0, 24], [10, 24], [10, 34], [0, 34]]),
                    ),
                    OCRResult(
                        text="OTHESURGEON GENERAL",
                        confidence=0.9,
                        bbox=BoundingBox(vertices=[[0, 36], [10, 36], [10, 46], [0, 46]]),
                    ),
                ],
                readability_score=0.95,
                metadata={"provider": "rapid", "status": "rapid_success", "labelType": "wine"},
            )

    monkeypatch.setattr(extract_endpoint, "get_vision_provider", lambda: FakeProvider())
    client = _client()

    response = client.post(
        "/api/extract",
        files={"image": ("hard-bottle.png", MINIMAL_PNG, "image/png")},
    )

    assert response.status_code == 200
    suggestions = _suggestions_by_key(response.json())

    assert suggestions["alcoholContent"]["value"] == "12.5%"
    assert "netContents" not in suggestions

    statuses = _statuses_by_key(response.json())
    assert statuses["classType"]["status"] == "detected"
    assert statuses["alcoholContent"]["status"] == "detected"
    assert statuses["netContents"]["status"] == "unreadable"
    assert statuses["netContents"]["value"] == ""
    assert statuses["netContents"]["confidence"] == 0.0
    assert statuses["netContents"]["reason"] == "field-not-readable"


def test_extract_class_candidate_handles_joined_beverage_class_tokens(monkeypatch):
    class FakeProvider:
        async def process_image(self, image_bytes, artifact_hash=None):
            return VisionResponse(
                results=[
                    OCRResult(
                        text="KENTUCKY STRAIGHT BOURBONWHISKY",
                        confidence=0.97,
                        bbox=BoundingBox(vertices=[[0, 0], [10, 0], [10, 10], [0, 10]]),
                    ),
                ],
                readability_score=0.97,
                metadata={"provider": "rapid", "status": "rapid_success"},
            )

    monkeypatch.setattr(extract_endpoint, "get_vision_provider", lambda: FakeProvider())
    client = _client()

    response = client.post(
        "/api/extract",
        files={"image": ("bourbon.png", MINIMAL_PNG, "image/png")},
    )

    assert response.status_code == 200
    statuses = _statuses_by_key(response.json())

    assert statuses["classType"]["status"] == "detected"
    assert statuses["classType"]["value"] == "KENTUCKY STRAIGHT BOURBON WHISKY"
    assert statuses["classType"]["confidence"] == 0.97


def test_extract_marks_readable_absent_fields_missing_not_unreadable(monkeypatch):
    class FakeProvider:
        async def process_image(self, image_bytes, artifact_hash=None):
            return VisionResponse(
                results=[
                    OCRResult(
                        text="CAVIT PINOT GRIGIO",
                        confidence=0.96,
                        bbox=BoundingBox(vertices=[[0, 0], [10, 0], [10, 10], [0, 10]]),
                    ),
                    OCRResult(
                        text="ALC 12.5% BY VOL",
                        confidence=0.95,
                        bbox=BoundingBox(vertices=[[0, 12], [10, 12], [10, 22], [0, 22]]),
                    ),
                ],
                readability_score=0.96,
                metadata={"provider": "fake", "status": "fake_success", "labelType": "wine"},
            )

    monkeypatch.setattr(extract_endpoint, "get_vision_provider", lambda: FakeProvider())
    client = _client()

    response = client.post(
        "/api/extract",
        files={"image": ("readable-missing-net.png", MINIMAL_PNG, "image/png")},
    )

    assert response.status_code == 200
    statuses = _statuses_by_key(response.json())

    assert statuses["classType"]["status"] == "detected"
    assert statuses["alcoholContent"]["status"] == "detected"
    assert statuses["netContents"]["status"] == "missing"
    assert statuses["netContents"]["reason"] == "field-not-detected"


def test_extract_rejects_invalid_file_type():
    client = _client()

    response = client.post(
        "/api/extract",
        files={"image": ("label.txt", b"not an image", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_FILE_TYPE"
