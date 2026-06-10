import asyncio
import io

from PIL import Image, ImageDraw

from app.services import local_vision
from app.services.format_signal import compute_region_readability_score, compute_warning_format_signal
from app.services.local_vision import LocalVisionProvider
from app.services.preprocess import PreprocessResult
from app.services.vision_provider import BoundingBox, OCRResult


def _bbox(left: int, top: int, right: int, bottom: int) -> dict:
    return {"vertices": [[left, top], [right, top], [right, bottom], [left, bottom]]}


def _ocr(text: str, left: int, top: int, right: int, bottom: int) -> dict:
    return {"text": text, "confidence": 0.96, "bbox": _bbox(left, top, right, bottom)}


def _image_bytes(warning_width: int, body_width: int, warning_height: int = 32) -> bytes:
    image = Image.new("RGB", (420, 180), "white")
    draw = ImageDraw.Draw(image)
    for x in range(22, 160, 24):
        draw.line((x, 22, x, 42), fill="black", width=body_width)
    for x in range(24, 184, 28):
        draw.line((x, 86, x, 86 + warning_height), fill="black", width=warning_width)
    for x in range(198, 350, 28):
        draw.line((x, 86, x, 86 + warning_height), fill="black", width=warning_width)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _ocr_items() -> list[dict]:
    return [
        _ocr("BOURBON", 20, 20, 165, 44),
        _ocr("GOVERNMENT", 20, 82, 190, 122),
        _ocr("WARNING:", 194, 82, 360, 122),
        _ocr("contains", 24, 130, 145, 150),
    ]


def test_warning_format_signal_likely_for_thicker_warning_strokes():
    result = compute_warning_format_signal(_image_bytes(warning_width=5, body_width=1), _ocr_items())

    assert result.bold_signal == "likely"
    assert result.bold_confidence >= 0.62
    assert result.stroke_ratio is not None
    assert result.stroke_ratio >= 1.15
    assert result.warning_bbox is not None
    assert result.warning_crop is not None
    assert result.warning_crop["contentType"] == "image/png"
    assert result.context_payload()["boldSignal"] == "likely"
    assert result.flat_context()["pipelineWarningBoldSignal"] == "likely"


def test_warning_format_signal_unlikely_for_thin_warning_strokes():
    result = compute_warning_format_signal(_image_bytes(warning_width=1, body_width=5), _ocr_items())

    assert result.bold_signal == "unlikely"
    assert result.bold_confidence >= 0.62
    assert result.stroke_ratio is not None
    assert result.stroke_ratio <= 0.90


def test_warning_format_signal_indeterminate_without_warning_prefix():
    result = compute_warning_format_signal(
        _image_bytes(warning_width=5, body_width=1),
        [_ocr("BOURBON", 20, 20, 165, 44)],
    )

    assert result.bold_signal == "indeterminate"
    assert result.bold_confidence == 0.0
    assert result.reason == "government_warning_prefix_not_located"
    assert result.warning_crop is None


def test_region_readability_uses_ocr_text_regions_over_background_score():
    result = compute_region_readability_score(0.31, _ocr_items())

    assert result.score > 0.9
    assert result.global_score == 0.31
    assert result.region_score is not None
    assert result.token_count == 4
    assert result.reason == "region_weighted_ocr_confidence"


def test_region_readability_keeps_global_score_without_text_regions():
    result = compute_region_readability_score(0.31, [])

    assert result.score == 0.31
    assert result.region_score is None
    assert result.token_count == 0
    assert result.reason == "no_ocr_text_regions"


def test_local_provider_emits_pipeline_context_and_crop(monkeypatch):
    image_bytes = _image_bytes(warning_width=5, body_width=1)
    ocr_results = [
        OCRResult(text=item["text"], confidence=item["confidence"], bbox=BoundingBox(vertices=item["bbox"]["vertices"]))
        for item in _ocr_items()
    ]

    monkeypatch.setattr(local_vision, "_tesseract_available", lambda: True)
    monkeypatch.setattr(local_vision, "_run_tesseract", lambda _: ocr_results)
    monkeypatch.setattr(
        local_vision,
        "preprocess_image",
        lambda *_: PreprocessResult(
            image_bytes=image_bytes,
            content_type="image/png",
            width=420,
            height=180,
            readability_score=0.97,
            metrics={"readabilityScore": 0.97},
        ),
    )

    response = asyncio.run(LocalVisionProvider().process_image(image_bytes, artifact_hash="synthetic"))

    assert response.metadata["warningFormat"]["boldSignal"] == "likely"
    assert response.metadata["pipelineContext"]["pipelineWarningBoldSignal"] == "likely"
    assert response.metadata["pipelineContext"]["warningFormat"]["boldSignal"] == "likely"
    assert response.metadata["pipelineContext"]["readabilityScore"] == 0.97
    assert response.metadata["evidenceCrops"][0]["ruleId"] == "GOVERNMENT_WARNING_FORMAT_SIGNAL"


def test_local_provider_lifts_low_global_readability_when_text_regions_are_legible(monkeypatch):
    image_bytes = _image_bytes(warning_width=5, body_width=1)
    ocr_results = [
        OCRResult(text=item["text"], confidence=item["confidence"], bbox=BoundingBox(vertices=item["bbox"]["vertices"]))
        for item in _ocr_items()
    ]

    monkeypatch.setattr(local_vision, "_tesseract_available", lambda: True)
    monkeypatch.setattr(local_vision, "_run_tesseract", lambda _: ocr_results)
    monkeypatch.setattr(
        local_vision,
        "preprocess_image",
        lambda *_: PreprocessResult(
            image_bytes=image_bytes,
            content_type="image/png",
            width=420,
            height=180,
            readability_score=0.31,
            metrics={"readabilityScore": 0.31},
        ),
    )

    response = asyncio.run(LocalVisionProvider().process_image(image_bytes, artifact_hash="synthetic"))

    assert response.metadata["status"] == "local_success"
    assert response.readability_score > 0.9
    assert response.metadata["readability"]["globalScore"] == 0.31
    assert response.metadata["readability"]["reason"] == "region_weighted_ocr_confidence"
    assert response.metadata["pipelineContext"]["readabilityScore"] == response.readability_score
