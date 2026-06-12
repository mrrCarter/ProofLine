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
    assert suggestions["brandName"]["value"] == "OLD FORESTER"
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
    assert suggestions["brandName"]["value"] == "CAVIT PINOT GRIGIO"
    assert suggestions["classType"]["value"] == "CAVIT PINOT GRIGIO"
    assert suggestions["netContents"]["value"] == "750 mL"
    assert suggestions["origin"]["value"] == "Imported"
    assert suggestions["countryOfOrigin"]["value"] == "Italy"


def test_extract_rejects_invalid_file_type():
    client = _client()

    response = client.post(
        "/api/extract",
        files={"image": ("label.txt", b"not an image", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_FILE_TYPE"
