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
                ],
                readability_score=0.82,
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


def test_extract_rejects_invalid_file_type():
    client = _client()

    response = client.post(
        "/api/extract",
        files={"image": ("label.txt", b"not an image", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_FILE_TYPE"
