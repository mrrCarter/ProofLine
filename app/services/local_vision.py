import hashlib
import io
import os
import re
import shutil
import subprocess
import time
from functools import lru_cache
from typing import Any, Optional

from PIL import Image, ImageFilter, ImageOps

from .format_signal import compute_region_readability_score, compute_warning_format_signal
from .preprocess import PreprocessConfig, PreprocessError, preprocess_image
from .vision_provider import BoundingBox, OCRResult, VisionProvider, VisionResponse


PROVIDER_NAME = "local"
ENGINE_NAME = "tesseract"

# Wall-clock budget (seconds) for the whole local-OCR stage, measured from the
# start of the global Tesseract pass. The warning-ROI retry passes (which only
# run on images where the global pass did not find the warning, e.g. a curved
# glare-lit phone photo) are skipped once this deadline is hit, so a real photo
# can never blow Law 1 (5s p95). The ROI passes do not rescue a genuinely
# unreadable warning, so capping them costs nothing on hard images — the verdict
# stays an honest UNREADABLE rather than a slow partial-confident guess.
def _ocr_wall_budget_seconds() -> float:
    raw = os.getenv("PROOFLINE_OCR_WALL_BUDGET_SECONDS")
    if raw:
        try:
            return max(0.5, float(raw))
        except ValueError:
            pass
    return 3.0
LABEL_ROI_CROPS = (
    {
        "cropId": "main-label-center",
        "ratios": (0.18, 0.12, 0.86, 0.64),
        "config": "--oem 1 --psm 6",
        "scale": 2.2,
    },
    {
        "cropId": "lower-label-center",
        "ratios": (0.12, 0.50, 0.90, 0.88),
        "config": "--oem 1 --psm 6",
        "scale": 2.5,
    },
    {
        "cropId": "warning-band",
        "ratios": (0.16, 0.61, 0.88, 0.78),
        "config": "--oem 1 --psm 6",
        "scale": 3.0,
    },
    {
        "cropId": "lower-label-wide",
        "ratios": (0.00, 0.56, 1.00, 0.98),
        "config": "--oem 1 --psm 4",
        "scale": 2.5,
    },
)


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

        ocr_started = time.monotonic()
        results = _run_tesseract(preprocessed.image_bytes)
        roi_deadline = ocr_started + _ocr_wall_budget_seconds()
        roi_results, roi_metadata = _run_warning_roi_passes(
            preprocessed.image_bytes, results, deadline=roi_deadline
        )
        if roi_results:
            results = [*results, *roi_results]
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
                "roiOcr": roi_metadata,
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
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image.load()
    return _run_tesseract_image(image)


def _run_tesseract_image(
    image: Image.Image,
    *,
    config: str | None = None,
    offset: tuple[float, float] = (0.0, 0.0),
    scale: float = 1.0,
) -> list[OCRResult]:
    import pytesseract  # type: ignore[import-not-found,import-untyped]

    data = pytesseract.image_to_data(
        image,
        config=config or "",
        output_type=pytesseract.Output.DICT,
    )
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

        left = (float(data["left"][idx]) / scale) + offset[0]
        top = (float(data["top"][idx]) / scale) + offset[1]
        width = float(data["width"][idx]) / scale
        height = float(data["height"][idx]) / scale
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


def _run_warning_roi_passes(
    image_bytes: bytes,
    full_results: list[OCRResult],
    *,
    deadline: float | None = None,
) -> tuple[list[OCRResult], dict[str, Any]]:
    if _has_warning_prefix(full_results):
        return [], {
            "enabled": True,
            "trigger": "warning_prefix_already_detected",
            "addedTokenCount": 0,
            "passes": [],
        }

    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        image.load()
    except OSError:
        return [], {
            "enabled": True,
            "trigger": "warning_prefix_missing",
            "status": "image_decode_failed",
            "addedTokenCount": 0,
            "passes": [],
        }

    collected: list[OCRResult] = []
    passes: list[dict[str, Any]] = []
    budget_exhausted = False
    for crop_config in LABEL_ROI_CROPS:
        # Stop spending OCR time once the wall-clock budget is hit: a real phone
        # photo must not blow Law 1, and these passes do not recover a genuinely
        # unreadable warning anyway (the verdict stays an honest UNREADABLE).
        if deadline is not None and time.monotonic() >= deadline:
            budget_exhausted = True
            break
        crop_id = str(crop_config["cropId"])
        ratios = crop_config["ratios"]
        config = str(crop_config["config"])
        scale = float(crop_config["scale"])
        crop_box = _ratio_box(ratios, image.size)
        crop = image.crop(crop_box)
        enhanced = _enhance_ocr_crop(crop, scale)
        crop_results = _run_tesseract_image(
            enhanced,
            config=config,
            offset=(float(crop_box[0]), float(crop_box[1])),
            scale=scale,
        )
        passes.append(
            {
                "cropId": crop_id,
                "bbox": _box_vertices(crop_box),
                "config": config,
                "scale": scale,
                "tokenCount": len(crop_results),
                "warningPrefixDetected": _has_warning_prefix(crop_results),
            }
        )
        collected.extend(crop_results)

    return collected, {
        "enabled": True,
        "trigger": "warning_prefix_missing",
        "addedTokenCount": len(collected),
        "passes": passes,
        "warningPrefixRecovered": _has_warning_prefix(collected),
        "budgetExhausted": budget_exhausted,
    }


# Cap the upscaled ROI-crop long edge. The crops were upscaling to ~3000px, which
# made each Tesseract pass ~1s and blew Law 1 on real photos. Capping keeps the
# same set of passes (so the verdict stays stable — all crops still run) but each
# pass is fast; the smaller upscale is still well above Tesseract's legibility
# floor for any text it could actually read.
ROI_CROP_MAX_LONG_EDGE = 700


def _enhance_ocr_crop(crop: Image.Image, scale: float) -> Image.Image:
    grayscale = ImageOps.grayscale(crop)
    enhanced = ImageOps.autocontrast(grayscale)
    target_w = max(1.0, enhanced.width * scale)
    target_h = max(1.0, enhanced.height * scale)
    long_edge = max(target_w, target_h)
    if long_edge > ROI_CROP_MAX_LONG_EDGE:
        factor = ROI_CROP_MAX_LONG_EDGE / long_edge
        target_w *= factor
        target_h *= factor
    width = max(1, int(round(target_w)))
    height = max(1, int(round(target_h)))
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    enhanced = enhanced.resize((width, height), resampling)
    enhanced = enhanced.filter(ImageFilter.UnsharpMask(radius=1.2, percent=150, threshold=3))
    return enhanced.convert("RGB")


def _has_warning_prefix(results: list[OCRResult]) -> bool:
    text = " ".join(item.text for item in results if item.text)
    return re.search(r"\bgovernment\s+warning\s*:", text, re.IGNORECASE) is not None


def _ratio_box(
    ratios: tuple[float, float, float, float],
    size: tuple[int, int],
) -> tuple[int, int, int, int]:
    width, height = size
    left = max(0, min(width - 1, int(round(width * ratios[0]))))
    top = max(0, min(height - 1, int(round(height * ratios[1]))))
    right = max(left + 1, min(width, int(round(width * ratios[2]))))
    bottom = max(top + 1, min(height, int(round(height * ratios[3]))))
    return (left, top, right, bottom)


def _box_vertices(box: tuple[int, int, int, int]) -> list[list[float]]:
    left, top, right, bottom = box
    return [
        [float(left), float(top)],
        [float(right), float(top)],
        [float(right), float(bottom)],
        [float(left), float(bottom)],
    ]
