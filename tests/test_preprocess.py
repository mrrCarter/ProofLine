import io
import json
from pathlib import Path

from PIL import Image

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
