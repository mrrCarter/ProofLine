import hashlib
import io
import os
import re
import time
from functools import lru_cache
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image, ImageOps

from .format_signal import compute_region_readability_score, compute_warning_format_signal
from .preprocess import PreprocessConfig, PreprocessError, preprocess_image
from .vision_provider import BoundingBox, OCRResult, VisionProvider, VisionResponse


PROVIDER_NAME = "rapidocr"
ENGINE_NAME = "rapidocr-onnxruntime"
REQUIRED_MODEL_FILES = (
    "ch_PP-OCRv4_det_mobile.onnx",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    "ch_PP-OCRv4_rec_mobile.onnx",
)
REQUIRED_MODEL_SHA256 = {
    "ch_PP-OCRv4_det_mobile.onnx": "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
    "ch_PP-OCRv4_rec_mobile.onnx": "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b",
}
RAPID_ROI_CROPS = (
    {
        "cropId": "abv-right-wide",
        "ratios": (0.5167, 0.5782, 0.6720, 0.6675),
        "scale": 4.0,
        "mode": "grayscale",
    },
)


class RapidOCRVisionProvider(VisionProvider):
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
            "runtimeNetwork": False,
            "runtimeDownloadDisabled": True,
        }

        availability = _rapidocr_availability()
        if not availability["available"]:
            return VisionResponse(
                results=[],
                readability_score=preprocessed.readability_score,
                metadata={
                    **metadata,
                    "status": "rapidocr_unavailable",
                    "reason": availability["reason"],
                    "missingModelFiles": availability.get("missingModelFiles", []),
                    "invalidModelFiles": availability.get("invalidModelFiles", []),
                },
            )
        metadata["offlineModelSource"] = availability["modelSource"]
        metadata["offlineModels"] = availability["models"]

        try:
            image = Image.open(io.BytesIO(preprocessed.image_bytes)).convert("RGB")
            image.load()
        except OSError as exc:
            return VisionResponse(
                results=[],
                readability_score=preprocessed.readability_score,
                metadata={
                    **metadata,
                    "status": "rapidocr_image_decode_failed",
                    "reason": str(exc),
                },
            )

        roi_image = _decode_original_image(image_bytes) or image

        started = time.monotonic()
        deadline = started + _ocr_wall_budget_seconds()
        results, rapid_metadata = _run_rapidocr_image(image)
        if _has_plausible_alcohol_percent(results):
            roi_results = []
            roi_metadata = {
                "enabled": True,
                "trigger": "plausible_alcohol_percent_already_detected",
                "addedTokenCount": 0,
                "passes": [],
                "budgetExhausted": False,
            }
        else:
            roi_results, roi_metadata = _run_roi_passes(roi_image, deadline=deadline)
        if roi_results:
            results = [*roi_results, *results]

        readability = compute_region_readability_score(preprocessed.readability_score, results)
        warning_format = compute_warning_format_signal(preprocessed.image_bytes, results)
        warning_format_payload = warning_format.context_payload()
        pipeline_context = {
            "readabilityScore": readability.score,
            "pipelineReadabilityScore": readability.score,
            "globalReadabilityScore": readability.global_score,
            "regionReadabilityScore": readability.region_score,
        }
        if _include_warning_format_for_rules(warning_format_payload):
            pipeline_context.update(warning_format.flat_context())
            pipeline_context["warningFormat"] = warning_format_payload
        else:
            pipeline_context["warningFormatOmittedReason"] = warning_format_payload["reason"]
        evidence_crops = [warning_format.warning_crop] if warning_format.warning_crop else []
        status = "rapidocr_success" if results else "rapidocr_no_text"
        if readability.score < self.preprocess_config.min_readability_score:
            status = "rapidocr_unreadable"

        return VisionResponse(
            results=results,
            readability_score=readability.score,
            metadata={
                **metadata,
                "status": status,
                "readability": readability.metadata_payload(),
                "readabilityFloor": self.preprocess_config.min_readability_score,
                "rapidOcr": rapid_metadata,
                "roiOcr": roi_metadata,
                "pipelineContext": pipeline_context,
                "warningFormat": warning_format_payload,
                "evidenceCrops": evidence_crops,
                "elapsedMs": int((time.monotonic() - started) * 1000),
            },
        )


def _ocr_wall_budget_seconds() -> float:
    raw = os.getenv("PROOFLINE_RAPIDOCR_WALL_BUDGET_SECONDS")
    if raw:
        try:
            return max(0.5, float(raw))
        except ValueError:
            pass
    return 4.0


def _provider_metadata() -> dict[str, Any]:
    metadata = {
        "provider": PROVIDER_NAME,
        "engine": ENGINE_NAME,
        "providerVersion": _package_version("rapidocr"),
    }
    onnx_version = _package_version("onnxruntime")
    if onnx_version != "unknown":
        metadata["onnxruntimeVersion"] = onnx_version
    return metadata


def _package_version(package: str) -> str:
    try:
        return importlib_metadata.version(package)
    except importlib_metadata.PackageNotFoundError:
        return "unknown"


def _include_warning_format_for_rules(warning_format: dict[str, Any]) -> bool:
    return not (
        warning_format.get("boldSignal") == "indeterminate"
        and float(warning_format.get("boldConfidence") or 0.0) <= 0.0
        and not warning_format.get("warningBbox")
    )


def _decode_original_image(image_bytes: bytes) -> Image.Image | None:
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        image.load()
    except OSError:
        return None
    return image


@lru_cache(maxsize=1)
def _rapidocr_availability() -> dict[str, Any]:
    try:
        model_files = _rapidocr_model_files()
        missing = [name for name, path in model_files.items() if not path.is_file()]
        if missing:
            return {
                "available": False,
                "reason": "packaged_model_files_missing",
                "missingModelFiles": missing,
            }

        models = _model_file_metadata(model_files)
        invalid = [
            model["name"]
            for model in models
            if model["sha256"] != REQUIRED_MODEL_SHA256[model["name"]]
        ]
        if invalid:
            return {
                "available": False,
                "reason": "packaged_model_file_hash_mismatch",
                "invalidModelFiles": invalid,
                "models": models,
            }

        import onnxruntime  # type: ignore[import-not-found]  # noqa: F401
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {
        "available": True,
        "reason": "ok",
        "modelSource": "rapidocr_wheel_explicit_paths",
        "models": models,
    }


def _rapidocr_model_files() -> dict[str, Path]:
    import rapidocr  # type: ignore[import-not-found]

    model_dir = Path(rapidocr.__file__).resolve().parent / "models"
    return {name: model_dir / name for name in REQUIRED_MODEL_FILES}


def _model_file_metadata(model_files: dict[str, Path]) -> list[dict[str, Any]]:
    metadata = []
    for name in REQUIRED_MODEL_FILES:
        path = model_files[name]
        metadata.append(
            {
                "name": name,
                "source": "rapidocr_wheel",
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return metadata


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _rapidocr_engine():
    from rapidocr import RapidOCR  # type: ignore[import-not-found]

    model_files = _rapidocr_model_files()
    return RapidOCR(
        params={
            "Global.log_level": "warning",
            "Det.model_path": str(model_files["ch_PP-OCRv4_det_mobile.onnx"]),
            "Cls.model_path": str(model_files["ch_ppocr_mobile_v2.0_cls_mobile.onnx"]),
            "Rec.model_path": str(model_files["ch_PP-OCRv4_rec_mobile.onnx"]),
        }
    )


def _run_rapidocr_image(
    image: Image.Image,
    *,
    offset: tuple[float, float] = (0.0, 0.0),
    scale: float = 1.0,
) -> tuple[list[OCRResult], dict[str, Any]]:
    started = time.monotonic()
    engine = _rapidocr_engine()
    output = engine(np.asarray(image.convert("RGB")))
    elapsed_ms = int((time.monotonic() - started) * 1000)
    results = _output_to_results(output, offset=offset, scale=scale)
    return results, {
        "engine": ENGINE_NAME,
        "runtimeDownloadDisabled": True,
        "modelSource": "rapidocr_wheel_explicit_paths",
        "elapsedMs": elapsed_ms,
        "tokenCount": len(results),
    }


def _run_roi_passes(
    image: Image.Image,
    *,
    deadline: float,
) -> tuple[list[OCRResult], dict[str, Any]]:
    collected: list[OCRResult] = []
    passes: list[dict[str, Any]] = []
    budget_exhausted = False

    for crop_config in RAPID_ROI_CROPS:
        if time.monotonic() >= deadline:
            budget_exhausted = True
            break
        crop_id = str(crop_config["cropId"])
        ratios = crop_config["ratios"]
        scale = float(crop_config["scale"])
        crop_box = _ratio_box(ratios, image.size)
        enhanced = _enhance_roi_crop(image.crop(crop_box), scale)
        crop_results, crop_metadata = _run_rapidocr_image(
            enhanced,
            offset=(float(crop_box[0]), float(crop_box[1])),
            scale=scale,
        )
        collected.extend(crop_results)
        passes.append(
            {
                "cropId": crop_id,
                "bbox": _box_vertices(crop_box),
                "scale": scale,
                "mode": crop_config["mode"],
                "elapsedMs": crop_metadata["elapsedMs"],
                "tokenCount": len(crop_results),
                "texts": [item.text for item in crop_results],
            }
        )

    return collected, {
        "enabled": True,
        "addedTokenCount": len(collected),
        "passes": passes,
        "budgetExhausted": budget_exhausted,
    }


def _has_plausible_alcohol_percent(results: list[OCRResult]) -> bool:
    text = " ".join(item.text for item in results if item.text)
    normalized = re.sub(r"\s+", "", text.upper())
    for match in re.finditer(r"(\d{1,3}(?:\.\d+)?)%(?:ALC|ABV|BYVOL|VOL)", normalized):
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        if 0.0 < value <= 100.0:
            return True
    return False


def _output_to_results(
    output: Any,
    *,
    offset: tuple[float, float],
    scale: float,
) -> list[OCRResult]:
    boxes = getattr(output, "boxes", None)
    texts = getattr(output, "txts", None) or ()
    scores = getattr(output, "scores", None) or ()
    if boxes is None or not texts:
        return []

    results: list[OCRResult] = []
    for idx, text in enumerate(texts):
        clean = str(text).strip()
        if not clean:
            continue
        confidence = _score_at(scores, idx)
        try:
            vertices = [
                [(float(point[0]) / scale) + offset[0], (float(point[1]) / scale) + offset[1]]
                for point in boxes[idx]
            ]
        except (IndexError, TypeError, ValueError):
            continue
        results.append(
            OCRResult(
                text=clean,
                confidence=confidence,
                bbox=BoundingBox(vertices=vertices),
            )
        )
    return results


def _score_at(scores: Any, idx: int) -> float:
    try:
        score = float(scores[idx])
    except (IndexError, TypeError, ValueError):
        score = 0.0
    return max(0.0, min(1.0, score))


def _enhance_roi_crop(crop: Image.Image, scale: float) -> Image.Image:
    grayscale = ImageOps.grayscale(crop)
    target_w = max(1, int(round(grayscale.width * scale)))
    target_h = max(1, int(round(grayscale.height * scale)))
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    return grayscale.resize((target_w, target_h), resampling).convert("RGB")


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


RapidVisionProvider = RapidOCRVisionProvider
