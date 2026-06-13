import asyncio
import sys
from types import SimpleNamespace

import pytest

from app.services.factory import get_vision_provider
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


@pytest.mark.parametrize("provider_name", ["rapid", "rapidocr", "rapid-ocr"])
def test_factory_selects_rapid_provider_aliases(monkeypatch, provider_name):
    class FakeRapidVisionProvider:
        pass

    monkeypatch.setenv("VISION_PROVIDER", provider_name)
    monkeypatch.setitem(
        sys.modules,
        "app.services.rapid_vision",
        SimpleNamespace(RapidVisionProvider=FakeRapidVisionProvider),
    )

    assert isinstance(get_vision_provider(), FakeRapidVisionProvider)
