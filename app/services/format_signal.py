import base64
import hashlib
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median
from typing import Any, Sequence

from PIL import Image, ImageFilter, ImageOps


WARNING_SIGNAL_LIKELY_RATIO = 1.15
WARNING_SIGNAL_UNLIKELY_RATIO = 0.90
WARNING_SIZE_TOO_SMALL_RATIO = 0.72


@dataclass(frozen=True)
class RegionReadability:
    score: float
    global_score: float
    region_score: float | None
    token_count: int
    reason: str

    def metadata_payload(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "globalScore": self.global_score,
            "regionScore": self.region_score,
            "tokenCount": self.token_count,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class WarningFormatSignal:
    bold_signal: str
    bold_confidence: float
    size_signal: str
    size_ratio: float | None
    stroke_ratio: float | None
    warning_bbox: list[list[float]] | None
    warning_crop: dict[str, Any] | None
    reason: str

    def context_payload(self) -> dict[str, Any]:
        return {
            "boldSignal": self.bold_signal,
            "boldConfidence": self.bold_confidence,
            "sizeSignal": self.size_signal,
            "sizeRatio": self.size_ratio,
            "strokeRatio": self.stroke_ratio,
            "warningBbox": self.warning_bbox,
            "source": "pipeline_computed",
            "reason": self.reason,
        }

    def flat_context(self) -> dict[str, Any]:
        return {
            "pipelineWarningBoldSignal": self.bold_signal,
            "pipelineWarningBoldConfidence": self.bold_confidence,
            "pipelineWarningSizeSignal": self.size_signal,
            "pipelineWarningSizeRatio": self.size_ratio,
        }


def compute_region_readability_score(global_score: float, ocr_results: Sequence[Any]) -> RegionReadability:
    """Score readability from detected text regions so background does not dominate phone photos."""
    items = [_item_payload(item) for item in ocr_results]
    weighted_confidence = 0.0
    total_weight = 0.0
    token_count = 0
    for item in items:
        text = str(item.get("text", "")).strip()
        confidence = _as_confidence(item.get("confidence"))
        box = _bbox_tuple(item.get("bbox"))
        if len(text) < 2 or confidence is None or box is None:
            continue
        area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
        weight = area ** 0.5
        weighted_confidence += confidence * weight
        total_weight += weight
        token_count += 1

    global_score = _clamp(global_score)
    if total_weight <= 0.0:
        return RegionReadability(
            score=global_score,
            global_score=global_score,
            region_score=None,
            token_count=0,
            reason="no_ocr_text_regions",
        )

    region_score = _clamp(weighted_confidence / total_weight)
    if region_score > global_score:
        score = region_score
        reason = "region_weighted_ocr_confidence"
    else:
        score = global_score
        reason = "global_readability_dominates"

    return RegionReadability(
        score=round(score, 3),
        global_score=round(global_score, 3),
        region_score=round(region_score, 3),
        token_count=token_count,
        reason=reason,
    )


def compute_warning_format_signal(image_bytes: bytes, ocr_results: Sequence[Any]) -> WarningFormatSignal:
    """Estimate warning bold/size signal from OCR token bboxes and the preprocessed image."""
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        image.load()
    except OSError:
        return _indeterminate("preprocessed_image_decode_failed")

    items = [_item_payload(item) for item in ocr_results]
    warning_indices = _warning_prefix_indices(items)
    if not warning_indices:
        return _indeterminate("government_warning_prefix_not_located")

    warning_boxes = [_bbox_tuple(items[index]["bbox"]) for index in warning_indices if items[index].get("bbox")]
    warning_boxes = [box for box in warning_boxes if box is not None]
    if not warning_boxes:
        return _indeterminate("government_warning_prefix_has_no_bbox")

    warning_box = _pad_box(_union_boxes(warning_boxes), image.size, pad=4)
    warning_scores = [_stroke_score(image, box) for box in warning_boxes]
    warning_scores = [score for score in warning_scores if score is not None]
    if not warning_scores:
        return _indeterminate("warning_stroke_not_measurable", warning_box)

    warning_heights = [_height(box) for box in warning_boxes]
    body_boxes = _body_reference_boxes(items, set(warning_indices), warning_box)
    body_scores = [_stroke_score(image, box) for box in body_boxes]
    body_scores = [score for score in body_scores if score is not None]
    if not body_scores:
        return _indeterminate("body_reference_stroke_not_measurable", warning_box)

    body_heights = [_height(box) for box in body_boxes if _height(box) > 0]
    warning_score = float(median(warning_scores))
    body_score = float(median(body_scores))
    stroke_ratio = warning_score / body_score if body_score > 0 else None
    size_ratio = (
        float(median(warning_heights)) / float(median(body_heights))
        if body_heights and median(body_heights) > 0
        else None
    )

    if stroke_ratio is None:
        bold_signal = "indeterminate"
        bold_confidence = 0.45
        reason = "stroke_ratio_unavailable"
    elif stroke_ratio >= WARNING_SIGNAL_LIKELY_RATIO:
        bold_signal = "likely"
        bold_confidence = _clamp(0.62 + min(0.33, (stroke_ratio - WARNING_SIGNAL_LIKELY_RATIO) * 0.45))
        reason = "warning_strokes_thicker_than_body_reference"
    elif stroke_ratio <= WARNING_SIGNAL_UNLIKELY_RATIO:
        bold_signal = "unlikely"
        bold_confidence = _clamp(0.62 + min(0.33, (WARNING_SIGNAL_UNLIKELY_RATIO - stroke_ratio) * 0.55))
        reason = "warning_strokes_not_thicker_than_body_reference"
    else:
        bold_signal = "indeterminate"
        bold_confidence = 0.55
        reason = "warning_stroke_ratio_near_body_reference"

    size_signal = "indeterminate"
    if size_ratio is not None:
        size_signal = "too_small" if size_ratio < WARNING_SIZE_TOO_SMALL_RATIO else "likely"
        if size_signal == "too_small" and bold_signal == "likely":
            bold_signal = "unlikely"
            bold_confidence = max(bold_confidence, 0.68)
            reason = "warning_size_ratio_too_small"

    crop = crop_candidate(image, warning_box, "GOVERNMENT_WARNING_FORMAT_SIGNAL", "warning-format")
    return WarningFormatSignal(
        bold_signal=bold_signal,
        bold_confidence=round(bold_confidence, 3),
        size_signal=size_signal,
        size_ratio=round(size_ratio, 3) if size_ratio is not None else None,
        stroke_ratio=round(stroke_ratio, 3) if stroke_ratio is not None else None,
        warning_bbox=_box_vertices(warning_box),
        warning_crop=crop,
        reason=reason,
    )


def crop_candidate(
    image: Image.Image,
    box: tuple[float, float, float, float],
    rule_id: str,
    crop_id: str,
) -> dict[str, Any]:
    crop_box = _pad_box(box, image.size, pad=8)
    crop = image.crop(_int_box(crop_box))
    output = io.BytesIO()
    crop.save(output, format="PNG", optimize=True)
    data = output.getvalue()
    return {
        "cropId": crop_id,
        "ruleId": rule_id,
        "contentType": "image/png",
        "bbox": _box_vertices(crop_box),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytesBase64": base64.b64encode(data).decode("ascii"),
    }


def _indeterminate(reason: str, warning_box: tuple[float, float, float, float] | None = None) -> WarningFormatSignal:
    return WarningFormatSignal(
        bold_signal="indeterminate",
        bold_confidence=0.0,
        size_signal="indeterminate",
        size_ratio=None,
        stroke_ratio=None,
        warning_bbox=_box_vertices(warning_box) if warning_box else None,
        warning_crop=None,
        reason=reason,
    )


def _item_payload(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        item = item.model_dump()
    elif hasattr(item, "dict"):
        item = item.dict()
    if not isinstance(item, dict):
        return {"text": "", "confidence": 0.0, "bbox": None}
    return item


def _warning_prefix_indices(items: Sequence[dict[str, Any]]) -> list[int]:
    tokens = [_normalize_token(str(item.get("text", ""))) for item in items]
    for index, token in enumerate(tokens):
        if token != "government":
            continue
        for next_index in range(index + 1, min(index + 4, len(tokens))):
            if tokens[next_index] == "warning":
                return [index, next_index]
    return []


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _as_confidence(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return _clamp(float(value))


def _body_reference_boxes(
    items: Sequence[dict[str, Any]],
    warning_indices: set[int],
    warning_box: tuple[float, float, float, float],
) -> list[tuple[float, float, float, float]]:
    boxes: list[tuple[float, float, float, float]] = []
    warning_height = max(1.0, _height(warning_box))
    for index, item in enumerate(items):
        if index in warning_indices:
            continue
        text = str(item.get("text", "")).strip()
        if len(text) < 2:
            continue
        box = _bbox_tuple(item.get("bbox"))
        if box is None:
            continue
        height = _height(box)
        if height <= 0 or height > warning_height * 3.0:
            continue
        boxes.append(box)
    return boxes[:24]


def _stroke_score(image: Image.Image, box: tuple[float, float, float, float]) -> float | None:
    left, top, right, bottom = _int_box(_pad_box(box, image.size, pad=2))
    if right - left < 3 or bottom - top < 3:
        return None
    crop = ImageOps.grayscale(image.crop((left, top, right, bottom)))
    threshold = _otsu_threshold(crop)
    mask = crop.point(lambda pixel: 255 if pixel <= threshold else 0, mode="L")
    ink = _ink_fraction(mask)
    if ink <= 0.002:
        return None
    eroded_once = mask.filter(ImageFilter.MinFilter(3))
    eroded_twice = eroded_once.filter(ImageFilter.MinFilter(3))
    survival_once = _ink_fraction(eroded_once) / ink
    survival_twice = _ink_fraction(eroded_twice) / ink
    return 1.0 + (2.8 * survival_once) + (1.2 * survival_twice)


def _ink_fraction(mask: Image.Image) -> float:
    histogram = mask.histogram()
    white_pixels = histogram[255] if len(histogram) > 255 else 0
    return white_pixels / max(1, mask.width * mask.height)


def _otsu_threshold(image: Image.Image) -> int:
    histogram = image.histogram()
    total = sum(histogram)
    if total <= 0:
        return 128
    sum_total = sum(index * count for index, count in enumerate(histogram))
    sum_background = 0.0
    weight_background = 0
    best_threshold = 128
    best_variance = -1.0
    for threshold, count in enumerate(histogram):
        weight_background += count
        if weight_background == 0:
            continue
        weight_foreground = total - weight_background
        if weight_foreground == 0:
            break
        sum_background += threshold * count
        mean_background = sum_background / weight_background
        mean_foreground = (sum_total - sum_background) / weight_foreground
        variance = weight_background * weight_foreground * (mean_background - mean_foreground) ** 2
        if variance > best_variance:
            best_variance = variance
            best_threshold = threshold
    return max(32, min(224, best_threshold))


def _bbox_tuple(value: Any) -> tuple[float, float, float, float] | None:
    vertices = value.get("vertices") if isinstance(value, dict) else value
    if not isinstance(vertices, list) or not vertices:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for vertex in vertices:
        if not isinstance(vertex, Iterable):
            continue
        pair = list(vertex)
        if len(pair) < 2:
            continue
        try:
            xs.append(float(pair[0]))
            ys.append(float(pair[1]))
        except (TypeError, ValueError):
            continue
    if not xs or not ys:
        return None
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def _union_boxes(boxes: Sequence[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _pad_box(
    box: tuple[float, float, float, float],
    size: tuple[int, int],
    pad: float,
) -> tuple[float, float, float, float]:
    width, height = size
    return (
        max(0.0, box[0] - pad),
        max(0.0, box[1] - pad),
        min(float(width), box[2] + pad),
        min(float(height), box[3] + pad),
    )


def _box_vertices(box: tuple[float, float, float, float] | None) -> list[list[float]] | None:
    if box is None:
        return None
    left, top, right, bottom = box
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def _int_box(box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    return (int(left), int(top), max(int(right), int(left) + 1), max(int(bottom), int(top) + 1))


def _height(box: tuple[float, float, float, float]) -> float:
    return max(0.0, box[3] - box[1])


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
