import io
import warnings
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat


@dataclass(frozen=True)
class PreprocessConfig:
    max_pixels: int = 20_000_000
    max_dimension: int = 6_000
    target_long_edge: int = 1_600
    min_readability_score: float = 0.35
    pdf_page_cap: int = 2


@dataclass(frozen=True)
class PreprocessResult:
    image_bytes: bytes
    content_type: str
    width: int
    height: int
    readability_score: float
    metrics: dict[str, Any]


class PreprocessError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def preprocess_image(
    image_bytes: bytes,
    config: PreprocessConfig | None = None,
) -> PreprocessResult:
    cfg = config or PreprocessConfig()
    if not image_bytes:
        raise PreprocessError("EMPTY_IMAGE", "Image payload is empty")
    if image_bytes.startswith(b"%PDF"):
        raise PreprocessError(
            "PDF_PREPROCESS_NOT_AVAILABLE",
            "PDF rasterization is not available in this container image yet",
            {"pageCap": cfg.pdf_page_cap},
        )

    image = _open_raster_image(image_bytes, cfg)
    original_format = (image.format or "PNG").lower()
    original_size = image.size

    image = ImageOps.exif_transpose(image)
    image = image.convert("RGB")
    _enforce_size_limits(image, cfg)

    image = _resize_for_ocr(image, cfg)
    score, metrics = _readability_metrics(image)

    image = ImageOps.autocontrast(image)
    image = ImageEnhance.Contrast(image).enhance(1.15)
    image = ImageEnhance.Sharpness(image).enhance(1.08)
    metrics.update(
        {
            "originalWidth": original_size[0],
            "originalHeight": original_size[1],
            "normalizedWidth": image.width,
            "normalizedHeight": image.height,
            "format": original_format,
            "maxPixels": cfg.max_pixels,
            "maxDimension": cfg.max_dimension,
            "targetLongEdge": cfg.target_long_edge,
        }
    )

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)

    return PreprocessResult(
        image_bytes=output.getvalue(),
        content_type="image/png",
        width=image.width,
        height=image.height,
        readability_score=score,
        metrics=metrics,
    )


def _open_raster_image(image_bytes: bytes, config: PreprocessConfig) -> Image.Image:
    Image.MAX_IMAGE_PIXELS = config.max_pixels
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            image = Image.open(io.BytesIO(image_bytes))
            image.load()
            return image
        except Image.DecompressionBombWarning as exc:
            raise PreprocessError(
                "IMAGE_TOO_LARGE",
                "Image exceeds safe pixel budget",
                {"maxPixels": config.max_pixels},
            ) from exc
        except Image.DecompressionBombError as exc:
            raise PreprocessError(
                "IMAGE_TOO_LARGE",
                "Image exceeds safe pixel budget",
                {"maxPixels": config.max_pixels},
            ) from exc
        except OSError as exc:
            raise PreprocessError(
                "UNSUPPORTED_IMAGE",
                "Image could not be decoded by the local preprocess path",
            ) from exc


def _enforce_size_limits(image: Image.Image, config: PreprocessConfig) -> None:
    width, height = image.size
    if width <= 0 or height <= 0:
        raise PreprocessError("INVALID_DIMENSIONS", "Image dimensions are invalid")
    if width * height > config.max_pixels:
        raise PreprocessError(
            "IMAGE_TOO_LARGE",
            "Image exceeds safe pixel budget",
            {"width": width, "height": height, "maxPixels": config.max_pixels},
        )
    if max(width, height) > config.max_dimension:
        raise PreprocessError(
            "IMAGE_TOO_LARGE",
            "Image exceeds safe dimension budget",
            {"width": width, "height": height, "maxDimension": config.max_dimension},
        )


def _resize_for_ocr(image: Image.Image, config: PreprocessConfig) -> Image.Image:
    long_edge = max(image.size)
    if long_edge <= config.target_long_edge:
        return image
    scale = config.target_long_edge / float(long_edge)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def _readability_metrics(image: Image.Image) -> tuple[float, dict[str, Any]]:
    gray = ImageOps.grayscale(image)
    stat = ImageStat.Stat(gray)
    mean = float(stat.mean[0])
    stddev = float(stat.stddev[0])

    edge = gray.filter(ImageFilter.FIND_EDGES)
    edge_mean = float(ImageStat.Stat(edge).mean[0])

    histogram = gray.histogram()
    total = max(1, gray.width * gray.height)
    glare_fraction = sum(histogram[252:]) / total
    dark_fraction = sum(histogram[:4]) / total

    contrast_score = _clamp(stddev / 64.0)
    edge_score = _clamp(edge_mean / 24.0)
    exposure_score = _clamp(1.0 - (abs(mean - 128.0) / 128.0))
    document_like_white = contrast_score >= 0.55 and edge_score >= 0.20 and dark_fraction >= 0.015
    glare_penalty = 0.0 if document_like_white else min(0.30, glare_fraction * 1.5)
    dark_penalty = min(0.20, dark_fraction)

    score = _clamp(
        (0.45 * contrast_score)
        + (0.35 * edge_score)
        + (0.20 * exposure_score)
        - glare_penalty
        - dark_penalty
    )

    return score, {
        "brightnessMean": round(mean, 3),
        "contrastStddev": round(stddev, 3),
        "edgeMean": round(edge_mean, 3),
        "glareFraction": round(glare_fraction, 5),
        "darkFraction": round(dark_fraction, 5),
        "contrastScore": round(contrast_score, 3),
        "edgeScore": round(edge_score, 3),
        "exposureScore": round(exposure_score, 3),
        "documentLikeWhite": document_like_white,
        "glarePenalty": round(glare_penalty, 3),
        "readabilityScore": round(score, 3),
    }


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))
