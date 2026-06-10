import hashlib
import io
import shutil
from typing import Any, Optional

from PIL import Image

from .preprocess import PreprocessConfig, PreprocessError, preprocess_image
from .vision_provider import BoundingBox, OCRResult, VisionProvider, VisionResponse


class LocalVisionProvider(VisionProvider):
    def __init__(self, preprocess_config: PreprocessConfig | None = None):
        self.preprocess_config = preprocess_config or PreprocessConfig()

    async def process_image(
        self,
        image_bytes: bytes,
        artifact_hash: Optional[str] = None,
    ) -> VisionResponse:
        if not artifact_hash:
            artifact_hash = hashlib.sha256(image_bytes).hexdigest()

        try:
            preprocessed = preprocess_image(image_bytes, self.preprocess_config)
        except PreprocessError as exc:
            return VisionResponse(
                results=[],
                readability_score=0.0,
                metadata={
                    "provider": "local",
                    "status": "preprocess_error",
                    "hash": artifact_hash,
                    "errorCode": exc.code,
                    "error": str(exc),
                    "details": exc.details,
                },
            )

        metadata: dict[str, Any] = {
            "provider": "local",
            "hash": artifact_hash,
            "preprocess": preprocessed.metrics,
            "normalizedContentType": preprocessed.content_type,
            "normalizedWidth": preprocessed.width,
            "normalizedHeight": preprocessed.height,
        }

        if preprocessed.readability_score < self.preprocess_config.min_readability_score:
            return VisionResponse(
                results=[],
                readability_score=preprocessed.readability_score,
                metadata={
                    **metadata,
                    "status": "local_unreadable",
                    "reason": "readability_score_below_floor",
                    "floor": self.preprocess_config.min_readability_score,
                },
            )

        if not _tesseract_available():
            return VisionResponse(
                results=[],
                readability_score=preprocessed.readability_score,
                metadata={
                    **metadata,
                    "status": "local_ocr_unavailable",
                    "engine": "tesseract",
                    "reason": "pytesseract_module_or_tesseract_binary_missing",
                },
            )

        results = _run_tesseract(preprocessed.image_bytes)
        return VisionResponse(
            results=results,
            readability_score=preprocessed.readability_score,
            metadata={
                **metadata,
                "status": "local_success" if results else "local_no_text",
                "engine": "tesseract",
            },
        )


def _tesseract_available() -> bool:
    if shutil.which("tesseract") is None:
        return False
    try:
        import pytesseract  # type: ignore[import-not-found,import-untyped]  # noqa: F401
    except Exception:
        return False
    return True


def _run_tesseract(image_bytes: bytes) -> list[OCRResult]:
    import pytesseract  # type: ignore[import-not-found,import-untyped]

    image = Image.open(io.BytesIO(image_bytes))
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    results: list[OCRResult] = []
    for idx, text in enumerate(data.get("text", [])):
        clean = text.strip()
        if not clean:
            continue
        try:
            confidence = float(data["conf"][idx]) / 100.0
        except (KeyError, TypeError, ValueError):
            confidence = 0.0
        if confidence < 0:
            continue

        left = float(data["left"][idx])
        top = float(data["top"][idx])
        width = float(data["width"][idx])
        height = float(data["height"][idx])
        results.append(
            OCRResult(
                text=clean,
                confidence=max(0.0, min(1.0, confidence)),
                bbox=BoundingBox(
                    vertices=[
                        [left, top],
                        [left + width, top],
                        [left + width, top + height],
                        [left, top + height],
                    ]
                ),
            )
        )
    return results
