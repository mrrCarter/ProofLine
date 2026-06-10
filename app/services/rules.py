import re
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml
from rapidfuzz import fuzz

from app.core.constants import (
    GOVERNMENT_WARNING_RETRIEVED_ON,
    GOVERNMENT_WARNING_SOURCE_URL,
    GOVERNMENT_WARNING_TEXT,
)
from app.schemas.finding import Evidence, Finding, FindingSeverity, FindingStatus


BRAND_MATCH_THRESHOLD = 0.93
ABV_TOLERANCE = 0.05
NET_CONTENTS_TOLERANCE_ML = 1.0
READABILITY_FLOOR = 0.65
WARNING_FORMAT_SOURCE_URL = (
    "https://www.ecfr.gov/current/title-27/chapter-I/subchapter-A/part-16/"
    "subpart-C/section-16.22"
)


def normalize_label_text(text: str) -> str:
    """Normalize only case, apostrophe style, punctuation, and whitespace."""
    value = text.casefold().replace("’", "'").replace("`", "'").replace("´", "'")
    value = re.sub(r"['’]", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def collapse_statement_whitespace(text: str) -> str:
    return " ".join(text.split())


def _default_rule_pack_path() -> Path:
    return Path(__file__).resolve().parents[2] / "rules" / "spirits-v1.yaml"


def _bbox_from_item(item: Optional[dict[str, Any]]) -> Optional[list[list[float]]]:
    if item is None:
        return None
    bbox = item.get("bbox")
    if bbox is None:
        return None
    if isinstance(bbox, dict):
        vertices = bbox.get("vertices")
        return vertices if isinstance(vertices, list) else None
    return bbox if isinstance(bbox, list) else None


def _clamp(value: float) -> float:
    return max(0.0, min(value, 1.0))


def _confidence_from_items(items: Iterable[dict[str, Any]]) -> float:
    values = [
        float(item.get("confidence", 0.0))
        for item in items
        if isinstance(item.get("confidence"), (int, float))
    ]
    if not values:
        return 0.0
    return _clamp(sum(values) / len(values))


def _context_value(context: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = context.get(key)
        if value is not None and value != "":
            return value
    return None


def _mapping_value(value: Any, keys: tuple[str, ...]) -> Any:
    if not isinstance(value, dict):
        return None
    return _context_value(value, keys)


def _test_only_enabled(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().casefold() in {"1", "true", "yes"})


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "yes", "y", "1", "imported"}:
            return True
        if normalized in {"false", "no", "n", "0", "domestic"}:
            return False
    return None


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        if match:
            return float(match.group(0))
    return None


class RuleEngine:
    def __init__(self, rule_pack_path: str | Path | None = None):
        self.rule_pack_path = Path(rule_pack_path) if rule_pack_path else _default_rule_pack_path()
        with self.rule_pack_path.open("r", encoding="utf-8") as f:
            self.rule_pack = yaml.safe_load(f)
        self.rules = self.rule_pack.get("rules", [])
        self.rules_by_id = {rule["id"]: rule for rule in self.rules}
        self.rule_pack_id = self.rule_pack.get("id") or self.rule_pack.get("name")
        self.rule_pack_version = str(self.rule_pack.get("version", "0.0.0"))
        self.rule_pack_ref = f"{self.rule_pack_id}@{self.rule_pack_version}"

    def normalize(self, text: str) -> str:
        return normalize_label_text(text)

    def evaluate(self, ocr_results: list[dict[str, Any]], context: dict[str, Any]) -> list[Finding]:
        findings: list[Finding] = []

        brand_name = self._context_text(
            context,
            ("brandName", "brand_name", "brand", "applicantBrandName"),
        )
        if "BRAND_NAME_MATCH" in self.rules_by_id and brand_name:
            findings.append(
                self._evaluate_text_match(
                    "BRAND_NAME_MATCH",
                    brand_name,
                    ocr_results,
                    context,
                    label="Brand",
                )
            )

        class_type = self._context_text(
            context,
            ("classType", "class_type", "classAndType", "class_type_designation"),
        )
        if "CLASS_TYPE_MATCH" in self.rules_by_id and class_type:
            findings.append(
                self._evaluate_text_match(
                    "CLASS_TYPE_MATCH",
                    class_type,
                    ocr_results,
                    context,
                    label="Class/type",
                )
            )

        if "ALCOHOL_CONTENT_MATCH" in self.rules_by_id:
            alcohol = self._evaluate_alcohol_content(ocr_results, context)
            if alcohol:
                findings.append(alcohol)

        if "NET_CONTENTS_MATCH" in self.rules_by_id:
            net_contents = self._evaluate_net_contents(ocr_results, context)
            if net_contents:
                findings.append(net_contents)

        if "NAME_ADDRESS_PRESENT" in self.rules_by_id:
            name_address = self._evaluate_name_address(ocr_results, context)
            if name_address:
                findings.append(name_address)

        if "COUNTRY_OF_ORIGIN_IF_IMPORT" in self.rules_by_id:
            origin = self._evaluate_country_of_origin(ocr_results, context)
            if origin:
                findings.append(origin)

        if "GOVERNMENT_WARNING_PRESENT" in self.rules_by_id:
            findings.append(self._evaluate_warning_present(ocr_results, context))

        if "GOVERNMENT_WARNING_EXACT_TEXT" in self.rules_by_id:
            findings.append(self._evaluate_warning_exact_text(ocr_results, context))

        if "GOVERNMENT_WARNING_FORMAT_SIGNAL" in self.rules_by_id:
            findings.append(self._evaluate_warning_format(ocr_results, context))

        if "IMAGE_READABILITY" in self.rules_by_id:
            findings.append(self._evaluate_readability(ocr_results, context))

        return findings

    def aggregate_verdict(self, findings: list[Finding]) -> str:
        if any(finding.status == FindingStatus.UNREADABLE for finding in findings):
            return FindingStatus.UNREADABLE.value
        if any(
            finding.status == FindingStatus.FAIL
            and finding.severity in {FindingSeverity.HIGH, FindingSeverity.CRITICAL}
            for finding in findings
        ):
            return FindingStatus.FAIL.value
        if any(finding.status == FindingStatus.NEEDS_REVIEW for finding in findings):
            return FindingStatus.NEEDS_REVIEW.value
        if any(finding.status == FindingStatus.FAIL for finding in findings):
            return FindingStatus.FAIL.value
        return FindingStatus.PASS.value

    def evaluate_with_verdict(
        self,
        ocr_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        findings = self.evaluate(ocr_results, context)
        return {
            "rulePack": self.rule_pack_ref,
            "verdict": self.aggregate_verdict(findings),
            "findings": findings,
        }

    def _context_text(self, context: dict[str, Any], keys: tuple[str, ...]) -> Optional[str]:
        value = _context_value(context, keys)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _all_ocr_text(self, ocr_results: list[dict[str, Any]]) -> str:
        return " ".join(str(item.get("text", "")) for item in ocr_results if item.get("text"))

    def _warning_text_observation(self, ocr_results: list[dict[str, Any]]) -> dict[str, Any]:
        expected = collapse_statement_whitespace(GOVERNMENT_WARNING_TEXT)
        observed = collapse_statement_whitespace(self._all_ocr_text(ocr_results))
        prefix = re.search(r"\bgovernment\s+warning\s*:", observed, re.IGNORECASE)
        prefix_text = prefix.group(0) if prefix else None
        return {
            "expected": expected,
            "observed": observed,
            "exactPresent": expected in observed,
            "prefix": prefix_text,
            "prefixCaseOk": prefix_text == "GOVERNMENT WARNING:" if prefix_text else False,
            "caseOnlyMismatch": expected.casefold() in observed.casefold(),
        }

    def _anchor_token_confidence(self, ocr_results: list[dict[str, Any]], required_tokens: set[str]) -> Optional[float]:
        if not required_tokens:
            return None
        matched: dict[str, float] = {}
        for item in ocr_results:
            confidence = _as_float(item.get("confidence"))
            if confidence is None:
                continue
            for token in re.findall(r"[a-z0-9]+", self.normalize(str(item.get("text", "")))):
                if token in required_tokens:
                    matched[token] = max(matched.get(token, 0.0), _clamp(float(confidence)))
        if set(matched) != required_tokens:
            return None
        return min(matched.values())

    def _readability_anchor_visibility(
        self,
        ocr_results: list[dict[str, Any]],
        context: dict[str, Any],
        floor: float,
    ) -> dict[str, Any]:
        label_text = self.normalize(self._all_ocr_text(ocr_results))
        warning_confidence = self._anchor_token_confidence(ocr_results, {"government", "warning"})
        warning_visible = warning_confidence is not None and warning_confidence >= floor

        name = self._context_text(
            context,
            ("applicantName", "applicant_name", "bottlerName", "producerName", "name"),
        )
        city = self._context_text(context, ("city", "producerCity", "bottlerCity"))
        state = self._context_text(context, ("state", "producerState", "bottlerState"))
        name_address_values = [value for value in (name, city, state) if value]
        expected_name_address_present = bool(name_address_values) and all(
            self.normalize(value) in label_text for value in name_address_values
        )
        name_address_tokens: set[str] = set()
        for value in name_address_values:
            name_address_tokens.update(re.findall(r"[a-z0-9]+", self.normalize(value)))
        name_address_confidence = (
            self._anchor_token_confidence(ocr_results, name_address_tokens)
            if expected_name_address_present
            else None
        )
        name_address_visible = (
            expected_name_address_present
            and name_address_confidence is not None
            and name_address_confidence >= floor
        )

        return {
            "warningAnchorVisible": warning_visible,
            "warningAnchorConfidence": round(warning_confidence, 4) if warning_confidence is not None else None,
            "nameAddressAnchorVisible": name_address_visible,
            "nameAddressAnchorConfidence": (
                round(name_address_confidence, 4) if name_address_confidence is not None else None
            ),
            "requiredAnchorsVisible": warning_visible and name_address_visible,
        }

    def _computed_warning_format_signal(self, context: dict[str, Any]) -> dict[str, Any]:
        pipeline_context = (
            context.get("_pipelineComputed")
            or context.get("pipelineComputed")
            or context.get("pipelineContext")
        )
        format_context = _mapping_value(pipeline_context, ("warningFormat", "warning_format"))
        signal = _mapping_value(
            format_context,
            ("boldSignal", "warningBoldSignal", "bold_signal", "warning_bold_signal"),
        )
        confidence = _as_float(
            _mapping_value(
                format_context,
                ("boldConfidence", "warningBoldConfidence", "bold_confidence", "warning_bold_confidence"),
            )
        )
        size_signal = _mapping_value(format_context, ("sizeSignal", "warningSizeSignal", "size_signal"))
        size_ratio = _mapping_value(format_context, ("sizeRatio", "warningSizeRatio", "size_ratio"))
        source = "pipeline_computed"

        if signal is None and _test_only_enabled(context.get("testOnlyComputedFormatSignal")):
            signal = _context_value(context, ("test_override_warningBoldSignal", "testOverrideWarningBoldSignal"))
            confidence = _as_float(
                _context_value(context, ("test_override_warningBoldConfidence", "testOverrideWarningBoldConfidence"))
            )
            size_signal = _context_value(context, ("test_override_warningSizeSignal", "testOverrideWarningSizeSignal"))
            size_ratio = _context_value(context, ("test_override_warningSizeRatio", "testOverrideWarningSizeRatio"))
            source = "test_override"

        normalized = str(signal or "indeterminate").casefold()
        if normalized not in {"likely", "unlikely", "indeterminate"}:
            normalized = "indeterminate"
        caller_supplied_present = (
            _context_value(
                context,
                (
                    "warningBoldSignal",
                    "boldSignal",
                    "computedWarningBoldSignal",
                    "pipelineWarningBoldSignal",
                    "computed_warning_bold_signal",
                    "warningFormatBoldSignal",
                ),
            )
            is not None
            or isinstance(context.get("warningFormat"), dict)
            or isinstance(context.get("warning_format"), dict)
        )
        return {
            "signal": normalized,
            "confidence": confidence,
            "sizeSignal": size_signal or "indeterminate",
            "sizeRatio": size_ratio,
            "source": source if signal is not None else "not_computed",
            "legacyCallerSignalIgnored": caller_supplied_present and source != "test_override",
        }

    def _best_text_match(
        self,
        expected: str,
        ocr_results: list[dict[str, Any]],
    ) -> tuple[float, Optional[dict[str, Any]], str]:
        normalized_expected = self.normalize(expected)
        best_ratio = 0.0
        best_match: Optional[dict[str, Any]] = None
        best_normalized = ""

        for item in ocr_results:
            raw_text = str(item.get("text", ""))
            normalized_ocr = self.normalize(raw_text)
            ratio = fuzz.ratio(normalized_expected, normalized_ocr) / 100.0
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = item
                best_normalized = normalized_ocr

        full_text = self.normalize(self._all_ocr_text(ocr_results))
        if full_text:
            full_text_ratio = max(
                fuzz.partial_ratio(normalized_expected, full_text) / 100.0,
                fuzz.token_set_ratio(normalized_expected, full_text) / 100.0,
            )
            if full_text_ratio > best_ratio:
                best_ratio = full_text_ratio
                best_match = None
                best_normalized = full_text

        return best_ratio, best_match, best_normalized

    def _evaluate_text_match(
        self,
        rule_id: str,
        expected_text: str,
        ocr_results: list[dict[str, Any]],
        context: dict[str, Any],
        label: str,
    ) -> Finding:
        rule = self.rules_by_id[rule_id]
        threshold = float(rule.get("threshold", BRAND_MATCH_THRESHOLD))
        normalized_expected = self.normalize(expected_text)
        best_ratio, best_match, best_normalized = self._best_text_match(expected_text, ocr_results)
        status = FindingStatus.PASS if best_ratio >= threshold else FindingStatus.FAIL
        best_text = str(best_match.get("text", "")) if best_match else None

        return Finding(
            ruleId=rule_id,
            severity=FindingSeverity(rule.get("severity", "HIGH")),
            status=status,
            expected={"raw": expected_text, "normalized": normalized_expected, "threshold": threshold},
            observed={"raw": best_text, "normalized": best_normalized, "score": round(best_ratio, 4)},
            confidence=round(best_ratio, 4),
            evidence=Evidence(
                text=best_text,
                bbox=_bbox_from_item(best_match),
                provider=context.get("ocr_provider") or context.get("provider"),
            ),
            explanation=(
                f"{label} matched after bounded normalization."
                if status == FindingStatus.PASS
                else f"{label} did not meet the bounded fuzzy-match threshold."
            ),
            remediation=None if status == FindingStatus.PASS else f"Confirm the application {label.lower()} matches label text.",
        )

    def _expected_alcohol(self, context: dict[str, Any]) -> Optional[dict[str, Any]]:
        proof = _context_value(context, ("proof", "labelProof"))
        if proof is not None:
            value = _as_float(proof)
            if value is not None:
                return {"raw": proof, "abv": value / 2, "unit": "proof"}

        abv = _context_value(context, ("abv", "alcoholByVolume", "alcohol_by_volume", "alcoholContent"))
        if abv is not None:
            parsed = self._parse_alcohol(str(abv))
            if parsed:
                return parsed
            value = _as_float(abv)
            if value is not None:
                return {"raw": abv, "abv": value, "unit": "abv"}
        return None

    def _parse_alcohol(self, text: str) -> Optional[dict[str, Any]]:
        proof = re.search(r"(\d+(?:\.\d+)?)\s*proof\b", text, re.IGNORECASE)
        if proof:
            value = float(proof.group(1))
            return {"raw": proof.group(0), "abv": value / 2, "unit": "proof"}

        abv = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:%\s*)?(?:abv|alc\.?\s*/?\s*vol\.?|alcohol by volume|%)",
            text,
            re.IGNORECASE,
        )
        if abv:
            value = float(abv.group(1))
            return {"raw": abv.group(0), "abv": value, "unit": "abv"}
        return None

    def _evaluate_alcohol_content(
        self,
        ocr_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> Optional[Finding]:
        expected = self._expected_alcohol(context)
        if expected is None:
            return None
        rule = self.rules_by_id["ALCOHOL_CONTENT_MATCH"]
        tolerance = float(rule.get("toleranceAbv", ABV_TOLERANCE))
        observed = self._parse_alcohol(self._all_ocr_text(ocr_results))
        if observed is None:
            return self._missing_finding(
                "ALCOHOL_CONTENT_MATCH",
                rule,
                expected,
                "Alcohol content was not detected on the label.",
                "Add alcohol content as ABV or proof.",
                context,
            )

        delta = abs(float(expected["abv"]) - float(observed["abv"]))
        status = FindingStatus.PASS if delta <= tolerance else FindingStatus.FAIL
        return Finding(
            ruleId="ALCOHOL_CONTENT_MATCH",
            severity=FindingSeverity(rule.get("severity", "HIGH")),
            status=status,
            expected={**expected, "toleranceAbv": tolerance},
            observed={**observed, "deltaAbv": round(delta, 4)},
            confidence=1.0 if status == FindingStatus.PASS else _clamp(1.0 - min(delta / 10.0, 1.0)),
            evidence=Evidence(
                text=observed["raw"],
                provider=context.get("ocr_provider") or context.get("provider"),
            ),
            explanation=(
                "Alcohol content matched after ABV/proof conversion."
                if status == FindingStatus.PASS
                else "Alcohol content differed beyond the ABV tolerance."
            ),
            remediation=None if status == FindingStatus.PASS else "Correct the stated ABV/proof equivalence.",
        )

    def _expected_net_contents(self, context: dict[str, Any]) -> Optional[dict[str, Any]]:
        value = _context_value(context, ("netContents", "net_contents", "containerSize", "bottleSize"))
        return self._parse_net_contents(str(value)) if value is not None else None

    def _parse_net_contents(self, text: str) -> Optional[dict[str, Any]]:
        match = re.search(
            r"(\d+(?:\.\d+)?)\s*(ml|milliliters?|cl|centiliters?|l|liters?)\b",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        value = float(match.group(1))
        unit = match.group(2).casefold()
        multiplier = 1.0
        if unit in {"cl", "centiliter", "centiliters"}:
            multiplier = 10.0
        elif unit in {"l", "liter", "liters"}:
            multiplier = 1000.0
        return {"raw": match.group(0), "ml": value * multiplier, "unit": unit}

    def _evaluate_net_contents(
        self,
        ocr_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> Optional[Finding]:
        expected = self._expected_net_contents(context)
        if expected is None:
            return None
        rule = self.rules_by_id["NET_CONTENTS_MATCH"]
        tolerance = float(rule.get("toleranceMl", NET_CONTENTS_TOLERANCE_ML))
        observed = self._parse_net_contents(self._all_ocr_text(ocr_results))
        if observed is None:
            return self._missing_finding(
                "NET_CONTENTS_MATCH",
                rule,
                expected,
                "Net contents were not detected on the label.",
                "Add the required net contents statement.",
                context,
            )

        delta = abs(float(expected["ml"]) - float(observed["ml"]))
        status = FindingStatus.PASS if delta <= tolerance else FindingStatus.FAIL
        return Finding(
            ruleId="NET_CONTENTS_MATCH",
            severity=FindingSeverity(rule.get("severity", "HIGH")),
            status=status,
            expected={**expected, "toleranceMl": tolerance},
            observed={**observed, "deltaMl": round(delta, 4)},
            confidence=1.0 if status == FindingStatus.PASS else _clamp(1.0 - min(delta / 1000.0, 1.0)),
            evidence=Evidence(
                text=observed["raw"],
                provider=context.get("ocr_provider") or context.get("provider"),
            ),
            explanation=(
                "Net contents matched after unit normalization to milliliters."
                if status == FindingStatus.PASS
                else "Net contents differed beyond the milliliter tolerance."
            ),
            remediation=None if status == FindingStatus.PASS else "Correct the label net contents or application value.",
        )

    def _evaluate_name_address(
        self,
        ocr_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> Optional[Finding]:
        name = self._context_text(
            context,
            ("applicantName", "applicant_name", "bottlerName", "producerName", "name"),
        )
        city = self._context_text(context, ("city", "producerCity", "bottlerCity"))
        state = self._context_text(context, ("state", "producerState", "bottlerState"))
        rule = self.rules_by_id["NAME_ADDRESS_PRESENT"]
        label_text = self.normalize(self._all_ocr_text(ocr_results))
        if not any((name, city, state)):
            return Finding(
                ruleId="NAME_ADDRESS_PRESENT",
                severity=FindingSeverity(rule.get("severity", "HIGH")),
                status=FindingStatus.NEEDS_REVIEW,
                expected={
                    "requiredApplicationFields": ["applicantName", "city", "state"],
                    "reason": "mandatory-rule-context-missing",
                },
                observed={"normalizedLabelText": label_text[:500]},
                confidence=_confidence_from_items(ocr_results),
                evidence=Evidence(
                    text=self._all_ocr_text(ocr_results)[:500],
                    provider=context.get("ocr_provider") or context.get("provider"),
                ),
                explanation="Name/address rule could not be deterministically evaluated because application data is missing.",
                remediation="Provide producer/bottler name and city/state application fields.",
            )

        missing = []
        if name and self.normalize(name) not in label_text:
            missing.append("name")
        if city and self.normalize(city) not in label_text:
            missing.append("city")
        if state and self.normalize(state) not in label_text:
            missing.append("state")

        status = FindingStatus.PASS if not missing else FindingStatus.FAIL
        return Finding(
            ruleId="NAME_ADDRESS_PRESENT",
            severity=FindingSeverity(rule.get("severity", "HIGH")),
            status=status,
            expected={"name": name, "city": city, "state": state},
            observed={"normalizedLabelText": label_text[:500], "missing": missing},
            confidence=_confidence_from_items(ocr_results),
            evidence=Evidence(
                text=self._all_ocr_text(ocr_results)[:500],
                provider=context.get("ocr_provider") or context.get("provider"),
            ),
            explanation=(
                "Required producer/bottler name and address signals were present."
                if status == FindingStatus.PASS
                else "Required producer/bottler name or address signal was missing."
            ),
            remediation=None if status == FindingStatus.PASS else "Add the producer/bottler name and city/state.",
        )

    def _evaluate_country_of_origin(
        self,
        ocr_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> Optional[Finding]:
        imported = _as_bool(_context_value(context, ("imported", "isImported", "originType", "origin")))
        if imported is None:
            return None
        rule = self.rules_by_id["COUNTRY_OF_ORIGIN_IF_IMPORT"]
        country = self._context_text(context, ("countryOfOrigin", "country_of_origin", "originCountry"))
        label_text = self.normalize(self._all_ocr_text(ocr_results))

        if imported is False:
            return Finding(
                ruleId="COUNTRY_OF_ORIGIN_IF_IMPORT",
                severity=FindingSeverity(rule.get("severity", "HIGH")),
                status=FindingStatus.PASS,
                expected={"imported": False},
                observed={"required": False},
                confidence=1.0,
                evidence=None,
                explanation="Country of origin is not required for domestic context.",
                remediation=None,
            )

        if not country:
            return Finding(
                ruleId="COUNTRY_OF_ORIGIN_IF_IMPORT",
                severity=FindingSeverity(rule.get("severity", "HIGH")),
                status=FindingStatus.NEEDS_REVIEW,
                expected={"imported": True, "country": None},
                observed={"normalizedLabelText": label_text[:500]},
                confidence=_confidence_from_items(ocr_results),
                evidence=Evidence(text=self._all_ocr_text(ocr_results)[:500]),
                explanation="Imported product lacks a country-of-origin value in application data.",
                remediation="Provide country of origin for imported products.",
            )

        present = self.normalize(country) in label_text
        return Finding(
            ruleId="COUNTRY_OF_ORIGIN_IF_IMPORT",
            severity=FindingSeverity(rule.get("severity", "HIGH")),
            status=FindingStatus.PASS if present else FindingStatus.FAIL,
            expected={"imported": True, "country": country, "normalized": self.normalize(country)},
            observed={"normalizedLabelText": label_text[:500], "countryPresent": present},
            confidence=_confidence_from_items(ocr_results),
            evidence=Evidence(
                text=self._all_ocr_text(ocr_results)[:500],
                provider=context.get("ocr_provider") or context.get("provider"),
            ),
            explanation=(
                "Country of origin was present for imported product."
                if present
                else "Country of origin was not detected for imported product."
            ),
            remediation=None if present else "Add country of origin to the label.",
        )

    def _evaluate_warning_present(
        self,
        ocr_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> Finding:
        rule = self.rules_by_id["GOVERNMENT_WARNING_PRESENT"]
        all_text = collapse_statement_whitespace(self._all_ocr_text(ocr_results))
        anchor_present = re.search(r"\bgovernment\s+warning\s*:", all_text, re.IGNORECASE) is not None
        return Finding(
            ruleId="GOVERNMENT_WARNING_PRESENT",
            severity=FindingSeverity(rule.get("severity", "HIGH")),
            status=FindingStatus.PASS if anchor_present else FindingStatus.FAIL,
            expected={"anchor": "GOVERNMENT WARNING:"},
            observed={"text": all_text[:500], "anchorPresent": anchor_present},
            confidence=_confidence_from_items(ocr_results),
            evidence=Evidence(
                text=all_text[:500],
                provider=context.get("ocr_provider") or context.get("provider"),
            ),
            explanation=(
                "Government warning anchor was detected."
                if anchor_present
                else "Government warning anchor was not detected."
            ),
            remediation=None if anchor_present else "Add the required government warning statement.",
        )

    def _evaluate_warning_exact_text(
        self,
        ocr_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> Finding:
        rule = self.rules_by_id["GOVERNMENT_WARNING_EXACT_TEXT"]
        observation = self._warning_text_observation(ocr_results)
        expected = observation["expected"]
        observed = observation["observed"]
        exact_present = bool(observation["exactPresent"])
        prefix_text = observation["prefix"]
        prefix_case_ok = bool(observation["prefixCaseOk"])
        confidence = _confidence_from_items(ocr_results)
        case_only_mismatch = bool(observation["caseOnlyMismatch"])
        status = FindingStatus.PASS if exact_present else FindingStatus.FAIL
        if not exact_present and case_only_mismatch and not prefix_case_ok and confidence < 0.9:
            status = FindingStatus.NEEDS_REVIEW

        return Finding(
            ruleId="GOVERNMENT_WARNING_EXACT_TEXT",
            severity=FindingSeverity(rule.get("severity", "HIGH")),
            status=status,
            expected={
                "text": expected,
                "sourceUrl": GOVERNMENT_WARNING_SOURCE_URL,
                "retrievedOn": GOVERNMENT_WARNING_RETRIEVED_ON,
                "normalization": "whitespace-collapse-only",
            },
            observed={
                "text": observed[:1000],
                "prefix": prefix_text,
                "prefixCaseOk": prefix_case_ok,
            },
            confidence=confidence,
            evidence=Evidence(
                text=observed[:1000],
                provider=context.get("ocr_provider") or context.get("provider"),
            ),
            explanation=(
                "Government warning text matched the pinned eCFR statement after whitespace collapse."
                if exact_present
                else (
                    "Warning prefix capitalization is uncertain because OCR confidence is below 0.9."
                    if status == FindingStatus.NEEDS_REVIEW
                    else "Government warning text did not match the pinned eCFR statement exactly."
                )
            ),
            remediation=None if exact_present else "Use the exact 27 CFR 16.21 warning statement and uppercase prefix.",
        )

    def _evaluate_warning_format(
        self,
        ocr_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> Finding:
        rule = self.rules_by_id["GOVERNMENT_WARNING_FORMAT_SIGNAL"]
        signal_result = self._computed_warning_format_signal(context)
        signal = str(signal_result["signal"])
        confidence = signal_result["confidence"]
        if confidence is None:
            confidence = _confidence_from_items(ocr_results)
        confidence = _clamp(float(confidence))
        warning_text = self._warning_text_observation(ocr_results)
        fallback_pass = (
            signal == "indeterminate"
            and warning_text["exactPresent"]
            and warning_text["prefixCaseOk"]
            and confidence >= 0.9
        )
        status = (
            FindingStatus.PASS
            if signal == "likely" or fallback_pass
            else FindingStatus.NEEDS_REVIEW
        )
        if signal == "likely":
            explanation = "Pipeline-computed warning bold signal is likely compliant."
            remediation = None
        elif fallback_pass:
            explanation = (
                "Warning bold was not provable from the photo, but exact warning text, uppercase prefix, "
                "and high OCR confidence were verified; format passes with caveat."
            )
            remediation = None
        elif signal == "unlikely":
            explanation = "Pipeline-computed warning bold signal is unlikely; human review is required."
            remediation = "Review warning crop for bold type and relative size."
        else:
            explanation = (
                "Warning bold was not provable from the photo and text/prefix confidence did not satisfy "
                "the PASS-with-caveat policy."
            )
            remediation = "Upload a clearer label image or review warning crop for bold type and relative size."

        return Finding(
            ruleId="GOVERNMENT_WARNING_FORMAT_SIGNAL",
            severity=FindingSeverity(rule.get("severity", "MEDIUM")),
            status=status,
            expected={
                "prefix": "GOVERNMENT WARNING",
                "capitalLetters": True,
                "boldType": "likely",
                "sourceUrl": WARNING_FORMAT_SOURCE_URL,
                "note": "Photo evidence cannot prove physical type size without scale.",
                "fallbackPolicy": (
                    "If the computed bold signal is indeterminate, exact text + uppercase prefix + "
                    "OCR confidence >= 0.9 passes with caveat."
                ),
            },
            observed={
                "boldSignal": signal,
                "signalSource": signal_result["source"],
                "legacyCallerSignalIgnored": signal_result["legacyCallerSignalIgnored"],
                "sizeSignal": signal_result["sizeSignal"],
                "sizeRatio": signal_result["sizeRatio"],
                "exactTextPresent": warning_text["exactPresent"],
                "prefixCaseOk": warning_text["prefixCaseOk"],
                "passWithCaveat": fallback_pass,
            },
            confidence=confidence,
            evidence=Evidence(
                text=collapse_statement_whitespace(self._all_ocr_text(ocr_results))[:500],
                provider=context.get("ocr_provider") or context.get("provider"),
            ),
            explanation=explanation,
            remediation=remediation,
        )

    def _evaluate_readability(
        self,
        ocr_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> Finding:
        rule = self.rules_by_id["IMAGE_READABILITY"]
        floor = float(rule.get("floor", READABILITY_FLOOR))
        score = _as_float(_context_value(context, ("readabilityScore", "readability_score")))
        if score is None:
            score = _confidence_from_items(ocr_results)
        score = _clamp(float(score))
        anchor_visibility = self._readability_anchor_visibility(ocr_results, context, floor)
        status = FindingStatus.PASS
        if score < floor:
            status = FindingStatus.NEEDS_REVIEW if anchor_visibility["requiredAnchorsVisible"] else FindingStatus.UNREADABLE
        if status == FindingStatus.PASS:
            explanation = "Image readability is above the deterministic floor."
            remediation = None
        elif status == FindingStatus.NEEDS_REVIEW:
            explanation = (
                "Image readability is below the global deterministic floor, but required warning and "
                "name/address anchors were detected at token confidence above the floor; human review is required."
            )
            remediation = "Review the warning and name/address crops before accepting the label."
        else:
            explanation = "Image readability is below the deterministic floor and required content was not located."
            remediation = "Upload a clearer label image."
        return Finding(
            ruleId="IMAGE_READABILITY",
            severity=FindingSeverity(rule.get("severity", "HIGH")),
            status=status,
            expected={"minimumReadabilityScore": floor},
            observed={"readabilityScore": round(score, 4), **anchor_visibility},
            confidence=score,
            evidence=Evidence(
                text=collapse_statement_whitespace(self._all_ocr_text(ocr_results))[:500],
                provider=context.get("ocr_provider") or context.get("provider"),
            ),
            explanation=explanation,
            remediation=remediation,
        )

    def _missing_finding(
        self,
        rule_id: str,
        rule: dict[str, Any],
        expected: dict[str, Any],
        explanation: str,
        remediation: str,
        context: dict[str, Any],
    ) -> Finding:
        return Finding(
            ruleId=rule_id,
            severity=FindingSeverity(rule.get("severity", "HIGH")),
            status=FindingStatus.FAIL,
            expected=expected,
            observed=None,
            confidence=0.0,
            evidence=Evidence(provider=context.get("ocr_provider") or context.get("provider")),
            explanation=explanation,
            remediation=remediation,
        )
