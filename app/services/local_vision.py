import hashlib
import io
import shutil
import subprocess
from functools import lru_cache
from typing import Any, Optional

from PIL import Image

from .format_signal import compute_region_readability_score, compute_warning_format_signal
from .preprocess import PreprocessConfig, PreprocessError, preprocess_image
from .vision_provider import BoundingBox, OCRResult, VisionProvider, VisionResponse


PROVIDER_NAME = "local"
ENGINE_NAME = "tesseract"


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
                    **_provider_metadata(),
                    "status": "preprocess_error",
                    "hash": artifact_hash,
                    "errorCode": exc.code,
                    "error": str(exc),
                    "details": exc.details,
                },
            )

        metadata: dict[str, Any] = {
            **_provider_metadata(),
            "hash": artifact_hash,
            "preprocess": preprocessed.metrics,
            "normalizedContentType": preprocessed.content_type,
            "normalizedWidth": preprocessed.width,
            "normalizedHeight": preprocessed.height,
        }

        if not _tesseract_available():
            return VisionResponse(
                results=[],
                readability_score=preprocessed.readability_score,
                metadata={
                    **metadata,
                    "status": "local_ocr_unavailable",
                    "reason": "pytesseract_module_or_tesseract_binary_missing",
                },
            )

        results = _run_tesseract(preprocessed.image_bytes)
        readability = compute_region_readability_score(preprocessed.readability_score, results)
        warning_format = compute_warning_format_signal(preprocessed.image_bytes, results)
        pipeline_context = {
            **warning_format.flat_context(),
            "warningFormat": warning_format.context_payload(),
            "readabilityScore": readability.score,
            "pipelineReadabilityScore": readability.score,
            "globalReadabilityScore": readability.global_score,
            "regionReadabilityScore": readability.region_score,
        }
        evidence_crops = [warning_format.warning_crop] if warning_format.warning_crop else []
        status = "local_success" if results else "local_no_text"
        if readability.score < self.preprocess_config.min_readability_score:
            status = "local_unreadable"
        return VisionResponse(
            results=results,
            readability_score=readability.score,
            metadata={
                **metadata,
                "status": status,
                "readability": readability.metadata_payload(),
                "readabilityFloor": self.preprocess_config.min_readability_score,
                "pipelineContext": pipeline_context,
                "warningFormat": warning_format.context_payload(),
                "evidenceCrops": evidence_crops,
            },
        )


def _provider_metadata() -> dict[str, Any]:
    metadata = {
        "provider": PROVIDER_NAME,
        "engine": ENGINE_NAME,
        "providerVersion": "local-tesseract",
    }
    tesseract_version = _tesseract_version()
    pytesseract_version = _pytesseract_version()
    if tesseract_version:
        metadata["engineVersion"] = tesseract_version
    if pytesseract_version:
        metadata["pytesseractVersion"] = pytesseract_version
    return metadata


def _tesseract_available() -> bool:
    if shutil.which("tesseract") is None:
        return False
    try:
        import pytesseract  # type: ignore[import-not-found,import-untyped]  # noqa: F401
    except Exception:
        return False
    return True


@lru_cache(maxsize=1)
def _tesseract_version() -> str | None:
    binary = shutil.which("tesseract")
    if binary is None:
        return None
    try:
        completed = subprocess.run(
            [binary, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first_line = ((completed.stdout or completed.stderr).splitlines() or [""])[0].strip()
    return first_line or None


@lru_cache(maxsize=1)
def _pytesseract_version() -> str | None:
    try:
        import pytesseract  # type: ignore[import-not-found,import-untyped]
    except Exception:
        return None
    return str(getattr(pytesseract, "__version__", "")).strip() or None


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
