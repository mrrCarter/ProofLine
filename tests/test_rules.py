from app.core.constants import GOVERNMENT_WARNING_TEXT
from app.schemas.finding import FindingStatus
from app.services.rules import RuleEngine


def _ocr(text: str, confidence: float = 0.98):
    return [{"text": text, "confidence": confidence, "bbox": [[0, 0], [100, 0], [100, 20], [0, 20]]}]


def _finding(findings, rule_id):
    return next(finding for finding in findings if finding.ruleId == rule_id)


def test_rule_pack_metadata():
    engine = RuleEngine()

    assert engine.rule_pack_ref == "spirits-v1@1.0.0"
    assert "BRAND_NAME_MATCH" in engine.rules_by_id
    assert "GOVERNMENT_WARNING_EXACT_TEXT" in engine.rules_by_id


def test_brand_case_and_apostrophe_equivalent_passes():
    engine = RuleEngine()
    findings = engine.evaluate(_ocr("STONE'S THROW"), {"brandName": "Stone's Throw", "ocr_provider": "mock"})
    finding = _finding(findings, "BRAND_NAME_MATCH")

    assert finding.status == FindingStatus.PASS
    assert finding.expected["normalized"] == "stones throw"
    assert finding.observed["normalized"] == "stones throw"
    assert finding.confidence == 1.0


def test_brand_material_mismatch_fails():
    engine = RuleEngine()
    findings = engine.evaluate(_ocr("OTHER WHISKEY"), {"brandName": "Old Forester"})
    finding = _finding(findings, "BRAND_NAME_MATCH")

    assert finding.status == FindingStatus.FAIL
    assert finding.expected["threshold"] == 0.93
    assert finding.confidence < 0.93


def test_warning_exact_text_passes_with_wrapped_lines():
    engine = RuleEngine()
    wrapped = GOVERNMENT_WARNING_TEXT.replace("birth defects. ", "birth defects.\n")
    findings = engine.evaluate(_ocr(wrapped), {})

    assert _finding(findings, "GOVERNMENT_WARNING_PRESENT").status == FindingStatus.PASS
    assert _finding(findings, "GOVERNMENT_WARNING_EXACT_TEXT").status == FindingStatus.PASS


def test_warning_title_case_prefix_fails_exact_text():
    engine = RuleEngine()
    title_case = GOVERNMENT_WARNING_TEXT.replace("GOVERNMENT WARNING:", "Government Warning:")
    findings = engine.evaluate(_ocr(title_case, confidence=0.99), {})
    finding = _finding(findings, "GOVERNMENT_WARNING_EXACT_TEXT")

    assert _finding(findings, "GOVERNMENT_WARNING_PRESENT").status == FindingStatus.PASS
    assert finding.status == FindingStatus.FAIL
    assert finding.observed["prefix"] == "Government Warning:"
    assert finding.observed["prefixCaseOk"] is False


def test_warning_title_case_low_confidence_needs_review():
    engine = RuleEngine()
    title_case = GOVERNMENT_WARNING_TEXT.replace("GOVERNMENT WARNING:", "Government Warning:")
    findings = engine.evaluate(_ocr(title_case, confidence=0.82), {})
    finding = _finding(findings, "GOVERNMENT_WARNING_EXACT_TEXT")

    assert finding.status == FindingStatus.NEEDS_REVIEW


def test_aggregate_verdict_fails_on_high_deterministic_failure():
    engine = RuleEngine()
    findings = engine.evaluate(_ocr("Government Warning: wrong case"), {"brandName": "Other Brand"})

    assert engine.aggregate_verdict(findings) == "FAIL"
