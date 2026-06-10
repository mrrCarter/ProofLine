import asyncio

from app.services import local_vision
from app.services.local_vision import LocalVisionProvider
from app.services.mock_vision import MOCK_ENGINE_VERSION, MockVisionProvider


def test_mock_provider_reports_engine_version_for_unknown_hash():
    response = asyncio.run(MockVisionProvider().process_image(b"not-a-known-fixture", artifact_hash="missing"))

    assert response.metadata["provider"] == "mock"
    assert response.metadata["engine"] == "mock"
    assert response.metadata["providerVersion"] == MOCK_ENGINE_VERSION
    assert response.metadata["status"] == "mock_unknown_hash"


def test_local_provider_reports_engine_versions_on_preprocess_error(monkeypatch):
    monkeypatch.setattr(local_vision, "_tesseract_version", lambda: "tesseract 5.5.0")
    monkeypatch.setattr(local_vision, "_pytesseract_version", lambda: "0.3.13")

    response = asyncio.run(LocalVisionProvider().process_image(b"", artifact_hash="empty"))

    assert response.metadata["provider"] == "local"
    assert response.metadata["engine"] == "tesseract"
    assert response.metadata["providerVersion"] == "local-tesseract"
    assert response.metadata["engineVersion"] == "tesseract 5.5.0"
    assert response.metadata["pytesseractVersion"] == "0.3.13"
    assert response.metadata["status"] == "preprocess_error"
    assert response.metadata["errorCode"] == "EMPTY_IMAGE"
