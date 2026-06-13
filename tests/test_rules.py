from app.api.endpoints.runs import _sanitize_rule_context
from app.core.constants import GOVERNMENT_WARNING_TEXT
from app.schemas.finding import FindingStatus
from app.services.rules import RuleEngine


def _ocr(text: str, confidence: float = 0.98):
    return [{"text": text, "confidence": confidence, "bbox": [[0, 0], [100, 0], [100, 20], [0, 20]]}]


def _ocr_many(items):
    return [
        {"text": text, "confidence": confidence, "bbox": [[0, 0], [100, 0], [100, 20], [0, 20]]}
        for text, confidence in items
    ]


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


def test_class_type_match_passes():
    engine = RuleEngine()
    findings = engine.evaluate(
        _ocr("KENTUCKY STRAIGHT BOURBON WHISKY"),
        {"classType": "Kentucky Straight Bourbon Whisky"},
    )
    finding = _finding(findings, "CLASS_TYPE_MATCH")

    assert finding.status == FindingStatus.PASS
    assert finding.expected["normalized"] == "kentucky straight bourbon whisky"


def test_class_type_match_handles_joined_beverage_class_tokens():
    engine = RuleEngine()
    findings = engine.evaluate(
        _ocr("OLD FORESTER KENTUCKY STRAIGHT BOURBONWHISKY 43% ALC/VOL 750ML"),
        {"classType": "Bourbon Whisky"},
    )
    finding = _finding(findings, "CLASS_TYPE_MATCH")

    assert finding.status == FindingStatus.PASS
    assert "bourbon whisky" in finding.observed["normalized"]
    assert finding.expected["threshold"] == 0.93


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


def test_proof_only_equivalent_passes_against_abv():
    engine = RuleEngine()
    findings = engine.evaluate(_ocr("90 Proof"), {"abv": "45% ABV"})
    finding = _finding(findings, "ALCOHOL_CONTENT_MATCH")

    assert finding.status == FindingStatus.PASS
    assert finding.expected["abv"] == 45
    assert finding.observed["abv"] == 45


def test_abv_mismatch_fails():
    engine = RuleEngine()
    findings = engine.evaluate(_ocr("40% Alc/Vol"), {"abv": 45})
    finding = _finding(findings, "ALCOHOL_CONTENT_MATCH")

    assert finding.status == FindingStatus.FAIL
    assert finding.observed["deltaAbv"] == 5


def test_net_contents_unit_equivalent_passes():
    engine = RuleEngine()
    findings = engine.evaluate(_ocr("75 cL"), {"netContents": "750 mL"})
    finding = _finding(findings, "NET_CONTENTS_MATCH")

    assert finding.status == FindingStatus.PASS
    assert finding.expected["ml"] == 750
    assert finding.observed["ml"] == 750


def test_import_missing_origin_fails():
    engine = RuleEngine()
    findings = engine.evaluate(_ocr("Imported whisky"), {"imported": True, "countryOfOrigin": "Scotland"})
    finding = _finding(findings, "COUNTRY_OF_ORIGIN_IF_IMPORT")

    assert finding.status == FindingStatus.FAIL
    assert finding.observed["countryPresent"] is False
    assert finding.observed["blockedByReadability"] is False


def test_import_origin_unreadable_does_not_render_hard_fail():
    engine = RuleEngine()
    findings = engine.evaluate(
        _ocr("CAVIT PINOT GRIGIO IMPORTED BY PALM BAY HE RISK OF BIRTH DEFECTS", 0.96),
        {"origin": "Imported", "countryOfOrigin": "Italy", "readabilityScore": 0.57},
    )
    finding = _finding(findings, "COUNTRY_OF_ORIGIN_IF_IMPORT")

    assert finding.status == FindingStatus.UNREADABLE
    assert finding.observed["countryPresent"] is False
    assert finding.observed["blockedByReadability"] is True
    assert engine.aggregate_verdict(findings) == "UNREADABLE"


def test_ui_origin_imported_alias_reaches_country_rule():
    engine = RuleEngine()
    findings = engine.evaluate(_ocr("Product of Scotland"), {"origin": "Imported", "countryOfOrigin": "Scotland"})
    finding = _finding(findings, "COUNTRY_OF_ORIGIN_IF_IMPORT")

    assert finding.status == FindingStatus.PASS
    assert finding.expected["imported"] is True


def test_name_address_present_passes():
    engine = RuleEngine()
    findings = engine.evaluate(
        _ocr("Bottled by Old Forester Distilling Co. Louisville KY"),
        {"applicantName": "Old Forester Distilling Co", "city": "Louisville", "state": "KY"},
    )
    finding = _finding(findings, "NAME_ADDRESS_PRESENT")

    assert finding.status == FindingStatus.PASS
    assert finding.observed["missing"] == []


def test_name_address_missing_fails():
    engine = RuleEngine()
    findings = engine.evaluate(
        _ocr("Bottled by Unknown Co."),
        {"applicantName": "Old Forester Distilling Co", "city": "Louisville", "state": "KY"},
    )
    finding = _finding(findings, "NAME_ADDRESS_PRESENT")

    assert finding.status == FindingStatus.FAIL
    assert finding.observed["missing"] == ["name", "city", "state"]


def test_name_address_missing_application_data_needs_review():
    engine = RuleEngine()
    findings = engine.evaluate(_ocr("Bottled by Old Forester Distilling Co. Louisville KY"), {})
    finding = _finding(findings, "NAME_ADDRESS_PRESENT")

    assert finding.status == FindingStatus.NEEDS_REVIEW
    assert finding.expected["reason"] == "mandatory-rule-context-missing"


def test_low_readability_routes_unreadable():
    engine = RuleEngine()
    findings = engine.evaluate(_ocr("blurry text", confidence=0.4), {"readabilityScore": 0.4})
    finding = _finding(findings, "IMAGE_READABILITY")

    assert finding.status == FindingStatus.UNREADABLE
    assert engine.aggregate_verdict(findings) == "UNREADABLE"


def test_global_readability_without_warning_anchor_routes_unreadable():
    engine = RuleEngine()
    findings = engine.evaluate(
        _ocr("CAVIT PINOT GRIGIO IMPORTED BY PALM BAY HE RISK OF BIRTH DEFECTS AND MAY CAUSE HEALTH", 0.96),
        {"readabilityScore": 0.7},
    )
    finding = _finding(findings, "IMAGE_READABILITY")

    assert finding.status == FindingStatus.UNREADABLE
    assert finding.observed["warningAnchorVisible"] is False
    assert finding.observed["warningFragmentVisible"] is True
    assert engine.aggregate_verdict(findings) == "UNREADABLE"


def test_low_confidence_without_warning_anchor_routes_unreadable():
    engine = RuleEngine()
    findings = engine.evaluate(
        _ocr("CAV PINOT GRIC VALDA PE NOMINAZIONE ORIGIN", 0.7),
        {"readabilityScore": 0.7},
    )
    finding = _finding(findings, "IMAGE_READABILITY")

    assert finding.status == FindingStatus.UNREADABLE
    assert finding.observed["warningAnchorVisible"] is False
    assert finding.observed["warningFragmentVisible"] is False
    assert engine.aggregate_verdict(findings) == "UNREADABLE"


def test_high_confidence_missing_warning_anchor_remains_fail():
    engine = RuleEngine()
    findings = engine.evaluate(
        _ocr("OLD FORESTER BOURBON WHISKY 43% ALC/VOL 750 mL", 0.97),
        {"readabilityScore": 0.97},
    )
    finding = _finding(findings, "IMAGE_READABILITY")

    assert finding.status == FindingStatus.PASS
    assert _finding(findings, "GOVERNMENT_WARNING_PRESENT").status == FindingStatus.FAIL
    assert _finding(findings, "GOVERNMENT_WARNING_EXACT_TEXT").status == FindingStatus.FAIL
    assert engine.aggregate_verdict(findings) == "FAIL"


def test_unreadable_warning_evidence_does_not_render_hard_warning_fail():
    engine = RuleEngine()
    findings = engine.evaluate(
        _ocr("CAVIT PINOT GRIGIO IMPORTED BY PALM BAY HE RISK OF BIRTH DEFECTS", 0.96),
        {"readabilityScore": 0.57},
    )
    warning_present = _finding(findings, "GOVERNMENT_WARNING_PRESENT")
    warning_text = _finding(findings, "GOVERNMENT_WARNING_EXACT_TEXT")
    readability = _finding(findings, "IMAGE_READABILITY")

    assert warning_present.status == FindingStatus.UNREADABLE
    assert warning_present.observed["blockedByReadability"] is True
    assert warning_text.status == FindingStatus.UNREADABLE
    assert warning_text.observed["blockedByReadability"] is True
    assert readability.status == FindingStatus.UNREADABLE
    assert engine.aggregate_verdict(findings) == "UNREADABLE"


def test_unreadable_field_evidence_does_not_render_hard_field_failures():
    engine = RuleEngine()
    findings = engine.evaluate(
        _ocr("CAVIT PINOT GRI IMPORTED BY PALM BAY HE RISK OF BIRTH DEFECTS", 0.96),
        {
            "classType": "Pinot Grigio",
            "abv": "12.5% ABV",
            "netContents": "750 mL",
            "readabilityScore": 0.57,
        },
    )

    for rule_id in ("CLASS_TYPE_MATCH", "ALCOHOL_CONTENT_MATCH", "NET_CONTENTS_MATCH"):
        finding = _finding(findings, rule_id)
        assert finding.status == FindingStatus.UNREADABLE
        assert finding.observed["blockedByReadability"] is True

    assert engine.aggregate_verdict(findings) == "UNREADABLE"


def test_readable_missing_numeric_fields_still_fail():
    engine = RuleEngine()
    findings = engine.evaluate(
        _ocr("OLD FORESTER KENTUCKY STRAIGHT BOURBON WHISKY", 0.97),
        {"abv": "43% ABV", "netContents": "750 mL", "readabilityScore": 0.97},
    )

    assert _finding(findings, "ALCOHOL_CONTENT_MATCH").status == FindingStatus.FAIL
    assert _finding(findings, "NET_CONTENTS_MATCH").status == FindingStatus.FAIL
    assert engine.aggregate_verdict(findings) == "FAIL"


def test_low_global_readability_with_required_anchors_routes_review_not_unreadable():
    engine = RuleEngine()
    findings = engine.evaluate(
        _ocr_many(
            [
                (GOVERNMENT_WARNING_TEXT, 0.96),
                ("Bottled by Old Forester Distilling Co. Louisville KY", 0.95),
            ]
        ),
        {
            "applicantName": "Old Forester Distilling Co",
            "city": "Louisville",
            "state": "KY",
            "readabilityScore": 0.4,
        },
    )
    finding = _finding(findings, "IMAGE_READABILITY")

    assert finding.status == FindingStatus.NEEDS_REVIEW
    assert finding.observed["requiredAnchorsVisible"] is True
    assert engine.aggregate_verdict(findings) == "NEEDS_REVIEW"


def test_warning_format_ignores_legacy_caller_supplied_signal():
    engine = RuleEngine()
    findings = engine.evaluate(
        _ocr(GOVERNMENT_WARNING_TEXT),
        {
            "warningBoldSignal": "unlikely",
            "warningBoldConfidence": 0.8,
            "computedWarningBoldSignal": "unlikely",
            "computedWarningBoldConfidence": 0.8,
            "warningFormat": {"boldSignal": "unlikely", "boldConfidence": 0.8},
        },
    )
    finding = _finding(findings, "GOVERNMENT_WARNING_FORMAT_SIGNAL")

    assert finding.status == FindingStatus.PASS
    assert finding.observed["boldSignal"] == "indeterminate"
    assert finding.observed["legacyCallerSignalIgnored"] is True
    assert finding.observed["passWithCaveat"] is True


def test_warning_format_computed_unlikely_needs_review_not_fail():
    engine = RuleEngine()
    findings = engine.evaluate(
        _ocr(GOVERNMENT_WARNING_TEXT),
        {"_pipelineComputed": {"warningFormat": {"boldSignal": "unlikely", "boldConfidence": 0.8}}},
    )
    finding = _finding(findings, "GOVERNMENT_WARNING_FORMAT_SIGNAL")

    assert finding.status == FindingStatus.NEEDS_REVIEW
    assert finding.observed["signalSource"] == "pipeline_computed"
    assert engine.aggregate_verdict(findings) == "NEEDS_REVIEW"


def test_warning_format_computed_likely_passes():
    engine = RuleEngine()
    findings = engine.evaluate(
        _ocr(GOVERNMENT_WARNING_TEXT),
        {"pipelineContext": {"warningFormat": {"boldSignal": "likely", "boldConfidence": 0.91}}},
    )
    finding = _finding(findings, "GOVERNMENT_WARNING_FORMAT_SIGNAL")

    assert finding.status == FindingStatus.PASS
    assert finding.observed["signalSource"] == "pipeline_computed"
    assert finding.observed["passWithCaveat"] is False


def test_warning_format_test_override_requires_test_env(monkeypatch):
    engine = RuleEngine()
    context = {
        "testOnlyComputedFormatSignal": True,
        "test_override_warningBoldSignal": "unlikely",
        "test_override_warningBoldConfidence": 0.92,
    }

    monkeypatch.setenv("PROOFLINE_ENV", "production")
    production_findings = engine.evaluate(_ocr(GOVERNMENT_WARNING_TEXT, confidence=0.95), context)
    production_finding = _finding(production_findings, "GOVERNMENT_WARNING_FORMAT_SIGNAL")

    assert production_finding.status == FindingStatus.PASS
    assert production_finding.observed["signalSource"] == "not_computed"
    assert production_finding.observed["passWithCaveat"] is True

    monkeypatch.setenv("PROOFLINE_ENV", "test")
    test_findings = engine.evaluate(_ocr(GOVERNMENT_WARNING_TEXT, confidence=0.95), context)
    test_finding = _finding(test_findings, "GOVERNMENT_WARNING_FORMAT_SIGNAL")

    assert test_finding.status == FindingStatus.NEEDS_REVIEW
    assert test_finding.observed["signalSource"] == "test_override"


def test_rule_context_sanitizer_strips_user_computed_and_test_override_keys():
    context = _sanitize_rule_context(
        {
            "brandName": "Old Forester",
            "_pipelineComputed": {"warningFormat": {"boldSignal": "likely"}},
            "pipelineComputed": {"warningFormat": {"boldSignal": "likely"}},
            "pipelineContext": {"warningFormat": {"boldSignal": "likely"}},
            "testOnlyComputedFormatSignal": True,
            "test_override_warningBoldSignal": "likely",
            "testOverrideWarningBoldConfidence": 0.99,
        }
    )

    assert context == {"brandName": "Old Forester"}


def test_warning_format_indeterminate_passes_with_caveat_on_text_caps_and_confidence():
    engine = RuleEngine()
    findings = engine.evaluate(_ocr(GOVERNMENT_WARNING_TEXT, confidence=0.95), {})
    finding = _finding(findings, "GOVERNMENT_WARNING_FORMAT_SIGNAL")

    assert finding.status == FindingStatus.PASS
    assert finding.observed["boldSignal"] == "indeterminate"
    assert finding.observed["passWithCaveat"] is True
    assert "passes with caveat" in finding.explanation


def test_warning_format_indeterminate_needs_review_when_confidence_low():
    engine = RuleEngine()
    findings = engine.evaluate(_ocr(GOVERNMENT_WARNING_TEXT, confidence=0.82), {})
    finding = _finding(findings, "GOVERNMENT_WARNING_FORMAT_SIGNAL")

    assert finding.status == FindingStatus.NEEDS_REVIEW
    assert finding.observed["passWithCaveat"] is False


def test_wine_and_malt_rule_packs_load():
    wine = RuleEngine("rules/wine-v1.yaml")
    malt = RuleEngine("rules/malt-v1.yaml")

    assert wine.rule_pack_ref == "wine-v1@1.0.0"
    assert malt.rule_pack_ref == "malt-v1@1.0.0"
    assert "GOVERNMENT_WARNING_EXACT_TEXT" in wine.rules_by_id
    assert "NET_CONTENTS_MATCH" in malt.rules_by_id
