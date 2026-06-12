import hashlib
import re
from typing import Any, Optional

from fastapi import APIRouter, File, Request, UploadFile

from app.api.endpoints.runs import MAX_UPLOAD_BYTES, _detect_upload_type, _raise_error
from app.services.factory import get_vision_provider
from app.services.rules import READABILITY_CONFIDENT_DECISION_FLOOR, RuleEngine


router = APIRouter()

FIELD_LABELS = {
    "commodity": "Product type",
    "brandName": "Brand name",
    "classType": "Class / type",
    "alcoholContent": "Alcohol content",
    "netContents": "Net contents",
    "origin": "Origin status",
    "countryOfOrigin": "Country of origin",
    "producerName": "Producer / bottler",
    "producerCity": "Producer city",
    "producerState": "Producer state",
}
CLASS_KEYWORDS = {
    "ale",
    "beer",
    "bourbon",
    "brandy",
    "gin",
    "grigio",
    "lager",
    "malt",
    "pinot",
    "rum",
    "stout",
    "tequila",
    "vodka",
    "whiskey",
    "whisky",
    "wine",
}
COUNTRY_NAMES = {
    "argentina": "Argentina",
    "australia": "Australia",
    "canada": "Canada",
    "chile": "Chile",
    "france": "France",
    "germany": "Germany",
    "italy": "Italy",
    "mexico": "Mexico",
    "new zealand": "New Zealand",
    "portugal": "Portugal",
    "south africa": "South Africa",
    "spain": "Spain",
    "united states": "United States",
    "usa": "United States",
}
NOISE_MARKERS = {
    "birth defects",
    "contains sulfites",
    "government warning",
    "health problems",
    "impairs",
    "pregnancy",
    "surgeon general",
    "warning",
}
COUNTRY_PATTERN = re.compile(
    r"\b(?:product\s+of|imported\s+from|produce\s+of)\s+([A-Z][A-Za-z .'-]{2,40})",
    re.IGNORECASE,
)
PRODUCER_PATTERN = re.compile(
    r"\b(?:produced\s+&\s+bottled\s+by|produced\s+and\s+bottled\s+by|bottled\s+by|imported\s+by)\s+(.{3,90})",
    re.IGNORECASE,
)


@router.post(
    "/extract",
    response_model=dict,
)
async def extract_fields(
    request: Request,
    image: UploadFile = File(...),
):
    request_id = request.headers.get("x-request-id") or ""
    image_bytes = await image.read()
    if not image_bytes:
        _raise_error(400, "EMPTY_UPLOAD", "Upload file is empty", request_id)
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        _raise_error(
            413,
            "UPLOAD_TOO_LARGE",
            "Upload exceeds the 15MB limit",
            request_id,
            {"maxBytes": MAX_UPLOAD_BYTES},
        )

    detected_type = _detect_upload_type(image_bytes)
    if detected_type is None:
        _raise_error(
            400,
            "INVALID_FILE_TYPE",
            "Upload bytes must be a supported jpeg, png, webp, heic, heif, or pdf artifact",
            request_id,
            {"contentType": image.content_type, "detectedType": detected_type},
        )

    artifact_hash = hashlib.sha256(image_bytes).hexdigest()
    provider = get_vision_provider()
    ocr = await provider.process_image(image_bytes, artifact_hash=artifact_hash)
    provider_name = str(ocr.metadata.get("provider", "unknown"))
    suggestion_items = _suggest_fields(
        [{"text": item.text, "confidence": item.confidence} for item in ocr.results],
        ocr.metadata,
        ocr.readability_score,
    )
    suggested_fields = {item["key"]: item for item in suggestion_items}
    return {
        "artifactSha256": artifact_hash,
        "provider": provider_name,
        "readabilityScore": round(float(ocr.readability_score), 4),
        "suggestedFields": suggested_fields,
        "suggestedFieldItems": suggestion_items,
    }


def _suggest_fields(
    ocr_items: list[dict[str, Any]],
    metadata: dict[str, Any],
    readability_score: float,
) -> list[dict[str, Any]]:
    status = str(metadata.get("status", ""))
    if status == "mock_unknown_hash":
        return []

    engine = RuleEngine()
    all_text = " ".join(_text(item) for item in ocr_items if _text(item))
    suggestions: dict[str, dict[str, Any]] = {}

    label_type = _clean_value(metadata.get("labelType"))
    if label_type:
        _put(suggestions, "commodity", label_type, 0.8, "ocr-metadata")

    brand = _brand_candidate(ocr_items, readability_score)
    if brand:
        _put(suggestions, "brandName", brand[0], brand[1], "ocr")

    class_type = _class_candidate(ocr_items, readability_score)
    if class_type:
        _put(suggestions, "classType", class_type[0], class_type[1], "ocr")

    alcohol = _alcohol_candidate(all_text) or engine._parse_alcohol(all_text)
    if alcohol:
        _put(suggestions, "alcoholContent", str(alcohol["raw"]), 0.9, "ocr")

    net_contents = engine._parse_net_contents(all_text)
    if net_contents:
        _put(suggestions, "netContents", str(net_contents["raw"]), 0.9, "ocr")

    country = _country_candidate(ocr_items, readability_score)
    if country:
        _put(suggestions, "origin", "Imported", 0.8, "ocr")
        _put(suggestions, "countryOfOrigin", country, 0.8, "ocr")

    producer = _producer_candidate(ocr_items, readability_score)
    if producer:
        _put(suggestions, "producerName", producer[0], producer[1], "ocr")

    return list(suggestions.values())


def _put(
    suggestions: dict[str, dict[str, Any]],
    key: str,
    value: str,
    confidence: float,
    source: str,
) -> None:
    value = value.strip(" ,.;:")
    if not value:
        return
    suggestions[key] = {
        "key": key,
        "label": FIELD_LABELS[key],
        "value": value,
        "confidence": max(0.0, min(1.0, confidence)),
        "source": source,
    }


def _text(item: dict[str, Any]) -> str:
    value = item.get("text")
    return value.strip() if isinstance(value, str) else ""


def _confidence(item: dict[str, Any]) -> float:
    value = item.get("confidence")
    return float(value) if isinstance(value, (int, float)) else 0.0


def _effective_confidence(item: dict[str, Any], readability_score: float) -> float:
    return min(_confidence(item), float(readability_score))


def _clean_value(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _alpha_tokens(text: str) -> list[str]:
    return re.findall(r"[a-z]+", text.casefold())


def _has_noise(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in NOISE_MARKERS)


def _brand_candidate(
    ocr_items: list[dict[str, Any]],
    readability_score: float,
) -> Optional[tuple[str, float]]:
    for item in sorted(ocr_items, key=lambda value: _effective_confidence(value, readability_score), reverse=True):
        text = _text(item)
        confidence = _effective_confidence(item, readability_score)
        lowered = text.casefold()
        tokens = _alpha_tokens(text)
        if confidence < READABILITY_CONFIDENT_DECISION_FLOOR:
            continue
        if len(tokens) < 2 or _has_noise(text):
            continue
        if any(marker in lowered for marker in (" alc", "proof", " ml", " cl", "product of", "imported by")):
            continue
        if 2 <= len(text) <= 60:
            return text, confidence
    return None


def _class_candidate(
    ocr_items: list[dict[str, Any]],
    readability_score: float,
) -> Optional[tuple[str, float]]:
    for item in sorted(ocr_items, key=lambda value: _effective_confidence(value, readability_score), reverse=True):
        text = _text(item)
        tokens = set(re.findall(r"[a-z0-9]+", text.casefold()))
        if len(_alpha_tokens(text)) < 2 or _has_noise(text):
            continue
        confidence = _effective_confidence(item, readability_score)
        if confidence >= READABILITY_CONFIDENT_DECISION_FLOOR and tokens & CLASS_KEYWORDS:
            return text, confidence
    return None


def _country_candidate(
    ocr_items: list[dict[str, Any]],
    readability_score: float,
) -> Optional[str]:
    for item in sorted(ocr_items, key=lambda value: _effective_confidence(value, readability_score), reverse=True):
        if _effective_confidence(item, readability_score) < 0.75:
            continue
        match = COUNTRY_PATTERN.search(_text(item))
        if not match:
            continue
        country = " ".join(match.group(1).strip(" ,.;:").casefold().split())
        if country in COUNTRY_NAMES:
            return COUNTRY_NAMES[country]
    return None


def _producer_candidate(
    ocr_items: list[dict[str, Any]],
    readability_score: float,
) -> Optional[tuple[str, float]]:
    for item in sorted(ocr_items, key=lambda value: _effective_confidence(value, readability_score), reverse=True):
        confidence = _effective_confidence(item, readability_score)
        if confidence < READABILITY_CONFIDENT_DECISION_FLOOR:
            continue
        text = _text(item)
        if _has_noise(text):
            continue
        match = PRODUCER_PATTERN.search(text)
        if not match:
            continue
        producer = match.group(1).split("  ", 1)[0].strip(" ,.;:")
        if 2 <= len(_alpha_tokens(producer)) <= 10:
            return producer, confidence
    return None


def _alcohol_candidate(text: str) -> Optional[dict[str, Any]]:
    abv = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:%\s*)?(?:abv|alc\.?\s*/?\s*vol\.?|alcohol by volume|%)",
        text,
        re.IGNORECASE,
    )
    if abv:
        return {"raw": abv.group(0), "abv": float(abv.group(1)), "unit": "abv"}
    return None
