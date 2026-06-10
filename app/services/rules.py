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


def _bbox_from_item(item: dict[str, Any]) -> Optional[list[list[float]]]:
    bbox = item.get("bbox")
    if bbox is None:
        return None
    if isinstance(bbox, dict):
        vertices = bbox.get("vertices")
        return vertices if isinstance(vertices, list) else None
    return bbox if isinstance(bbox, list) else None


def _confidence_from_items(items: Iterable[dict[str, Any]]) -> float:
    values = [
        float(item.get("confidence", 0.0))
        for item in items
        if isinstance(item.get("confidence"), (int, float))
    ]
    if not values:
        return 0.0
    return max(0.0, min(sum(values) / len(values), 1.0))


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
        findings = []

        if "BRAND_NAME_MATCH" in self.rules_by_id:
            brand_name = self._context_brand(context)
            if brand_name:
                findings.append(self._evaluate_brand_name(ocr_results, context, brand_name))

        if "GOVERNMENT_WARNING_PRESENT" in self.rules_by_id:
            findings.append(self._evaluate_warning_present(ocr_results, context))

        if "GOVERNMENT_WARNING_EXACT_TEXT" in self.rules_by_id:
            findings.append(self._evaluate_warning_exact_text(ocr_results, context))

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

    def _context_brand(self, context: dict[str, Any]) -> Optional[str]:
        for key in ("brandName", "brand_name", "brand", "applicantBrandName"):
            value = context.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _evaluate_brand_name(
        self,
        ocr_results: list[dict[str, Any]],
        context: dict[str, Any],
        brand_name: str,
    ) -> Finding:
        rule = self.rules_by_id["BRAND_NAME_MATCH"]
        threshold = float(rule.get("threshold", BRAND_MATCH_THRESHOLD))
        provider = context.get("ocr_provider") or context.get("provider")
        normalized_brand = self.normalize(brand_name)
        best_ratio = 0.0
        best_match: Optional[dict[str, Any]] = None
        best_normalized = ""

        for item in ocr_results:
            raw_text = str(item.get("text", ""))
            normalized_ocr = self.normalize(raw_text)
            ratio = fuzz.ratio(normalized_brand, normalized_ocr) / 100.0
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = item
                best_normalized = normalized_ocr

        status = FindingStatus.PASS if best_ratio >= threshold else FindingStatus.FAIL
        best_text = str(best_match.get("text", "")) if best_match else None
        return Finding(
            ruleId="BRAND_NAME_MATCH",
            severity=FindingSeverity(rule.get("severity", "HIGH")),
            status=status,
            expected={"raw": brand_name, "normalized": normalized_brand, "threshold": threshold},
            observed={"raw": best_text, "normalized": best_normalized, "score": round(best_ratio, 4)},
            confidence=round(best_ratio, 4),
            evidence=Evidence(
                text=best_text,
                bbox=_bbox_from_item(best_match) if best_match else None,
                provider=provider,
            ),
            explanation=(
                "Brand matched after bounded normalization."
                if status == FindingStatus.PASS
                else "Brand did not meet the bounded fuzzy-match threshold."
            ),
            remediation=None if status == FindingStatus.PASS else "Confirm application brand matches label text.",
        )

    def _all_ocr_text(self, ocr_results: list[dict[str, Any]]) -> str:
        return " ".join(str(item.get("text", "")) for item in ocr_results if item.get("text"))

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
        expected = collapse_statement_whitespace(GOVERNMENT_WARNING_TEXT)
        observed = collapse_statement_whitespace(self._all_ocr_text(ocr_results))
        exact_present = expected in observed
        prefix = re.search(r"\bgovernment\s+warning\s*:", observed, re.IGNORECASE)
        prefix_text = prefix.group(0) if prefix else None
        prefix_case_ok = prefix_text == "GOVERNMENT WARNING:" if prefix_text else False
        confidence = _confidence_from_items(ocr_results)
        case_only_mismatch = expected.casefold() in observed.casefold()
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
