import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.services.preprocess import PreprocessConfig, preprocess_image


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_full_pipeline_white_label_fixtures_clear_readability_floor():
    cases = json.loads((FIXTURE_DIR / "full_pipeline_image_cases.json").read_text(encoding="utf-8"))
    floor = PreprocessConfig().min_readability_score

    for case in cases:
        image_bytes = (FIXTURE_DIR / str(case["imagePath"])).read_bytes()
        result = preprocess_image(image_bytes)

        assert result.readability_score >= floor, case["fixtureId"]
        assert result.metrics["documentLikeWhite"] is True


def test_blank_white_image_stays_below_readability_floor():
    image = Image.new("RGB", (800, 400), "white")
    payload = io.BytesIO()
    image.save(payload, format="PNG")

    result = preprocess_image(payload.getvalue())

    assert result.readability_score < PreprocessConfig().min_readability_score
    assert result.metrics["documentLikeWhite"] is False
    assert result.metrics["glarePenalty"] > 0
    assert result.metrics["deskewApplied"] is False


def test_preprocess_applies_bounded_deskew_to_skewed_label_text():
    image = _synthetic_label_image().rotate(
        5.0,
        resample=Image.Resampling.BICUBIC,
        expand=True,
        fillcolor="white",
    )
    payload = io.BytesIO()
    image.save(payload, format="PNG")

    result = preprocess_image(payload.getvalue())

    assert result.metrics["deskewApplied"] is True
    assert -6.5 <= result.metrics["deskewAngleDegrees"] <= -3.5
    assert result.metrics["deskewConfidence"] >= PreprocessConfig().deskew_min_confidence
    assert max(result.width, result.height) <= PreprocessConfig().target_long_edge


def test_preprocess_leaves_already_level_label_unrotated():
    payload = io.BytesIO()
    _synthetic_label_image().save(payload, format="PNG")

    result = preprocess_image(payload.getvalue())

    assert result.metrics["deskewApplied"] is False
    assert abs(result.metrics["deskewAngleDegrees"]) < PreprocessConfig().deskew_min_abs_degrees


def _synthetic_label_image() -> Image.Image:
    image = Image.new("RGB", (900, 520), "white")
    draw = ImageDraw.Draw(image)
    font = _label_font()
    lines = [
        "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL,",
        "WOMEN SHOULD NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY.",
        "CONSUMPTION OF ALCOHOLIC BEVERAGES IMPAIRS YOUR ABILITY",
        "TO DRIVE A CAR OR OPERATE MACHINERY.",
        "45% ALC/VOL    90 PROOF    750 ML",
        "PRODUCED BY SAMPLE DISTILLING CO.",
    ]
    for index, line in enumerate(lines):
        draw.text((50, 52 + (index * 58)), line, fill="black", font=font)
    return image


def _label_font() -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", 25)
    except OSError:
        return ImageFont.load_default()
