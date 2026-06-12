"""Executable blind-spot gates (VERIFY-01, mythos RULING 1).

These encode the invariants whose absence produced F1 (a clean label could never
PASS through the real UI) and the test-override injection hole (a user could force
PASS). See docs/BLIND_SPOT_MATRIX.md. The governing rule:

    A fixture may never supply a value the production pipeline is responsible for
    deriving, and the user path must never honour such a value.
"""
import json
import os
from pathlib import Path

from app.api.endpoints.runs import _sanitize_rule_context
from app.schemas.finding import FindingStatus
from app.services.rules import RuleEngine, _test_only_enabled

_FIXTURES = Path(__file__).parent / "fixtures"

# Values the production pipeline derives from the image — a real UI user cannot
# send these, so no rule-eval case may inject them (that is how F1 stayed hidden).
_DERIVED_SIGNAL_KEYS = {
    "warningBoldSignal",
    "boldSignal",
    "warning_bold_signal",
    "warningSizeSignal",
    "sizeSignal",
}


def _ocr(text: str, confidence: float = 0.98):
    return [{"text": text, "confidence": confidence, "bbox": [[0, 0], [100, 0], [100, 20], [0, 20]]}]


def _finding(findings, rule_id):
    return next(f for f in findings if f.ruleId == rule_id)


# --- BS guard: the user path strips injection keys -------------------------------

def test_user_context_strips_injection_keys():
    """A real upload cannot inject the pipeline signal or the test override."""
    hostile = {
        "brandName": "Stone's Throw",
        "_pipelineComputed": {"warningFormat": {"boldSignal": "likely"}},
        "pipelineContext": {"warningFormat": {"boldSignal": "likely"}},
        "testOnlyComputedFormatSignal": True,
        "test_override_warningBoldSignal": "likely",
        "testOverrideWarningSizeSignal": "ok",
    }
    clean = _sanitize_rule_context(hostile)
    assert clean["brandName"] == "Stone's Throw"  # legitimate field survives
    for forbidden in (
        "_pipelineComputed",
        "pipelineContext",
        "testOnlyComputedFormatSignal",
        "test_override_warningBoldSignal",
        "testOverrideWarningSizeSignal",
    ):
        assert forbidden not in clean, f"{forbidden} must be stripped from user context"


# --- BS guard: the test override is env-gated off in production -------------------

def test_test_override_is_env_gated_off_in_production(monkeypatch):
    """_test_only_enabled must refuse the override unless PROOFLINE_ENV is a test env."""
    monkeypatch.setenv("PROOFLINE_ENV", "production")
    assert _test_only_enabled(True) is False
    assert _test_only_enabled("true") is False
    monkeypatch.delenv("PROOFLINE_ENV", raising=False)
    assert _test_only_enabled(True) is False  # default (unset) is not a test env


# --- BS guard: the rule-eval fixtures are not rigged -----------------------------

def test_rule_eval_cases_do_not_inject_derived_signals():
    """rule_eval_cases.json must not hand the engine the bold/size answer."""
    cases = json.loads((_FIXTURES / "rule_eval_cases.json").read_text())
    if isinstance(cases, dict):
        cases = [c for group in cases.values() for c in group]
    for case in cases:
        ctx = case.get("context", {})
        leaked = _DERIVED_SIGNAL_KEYS & set(ctx)
        assert not leaked, f"case {case.get('fixtureId')} injects derived signals: {leaked}"


# --- BS1 contract: PASS is reachable when the signal says the warning is bold ----

def test_format_signal_can_pass_when_pipeline_reports_likely():
    """The verdict gate must be reachable: a computed 'likely' bold signal -> PASS.

    This is the rule-level contract proving the FORMAT_SIGNAL rule CAN pass (the
    exact thing F1 made impossible). The full-pipeline PASS on a real conspicuous
    label is verified separately on the deployed build.
    """
    engine = RuleEngine()
    context = {
        "brandName": "Stone's Throw",
        "ocr_provider": "mock",
        # As runs.py threads it after OCR — the PIPELINE-computed signal, not a
        # caller-supplied answer the user could send.
        "pipelineContext": {
            "warningFormat": {
                "boldSignal": "likely",
                "sizeSignal": "ok",
                "sizeRatio": 1.2,
                "signalSource": "pipeline_computed",
            }
        },
    }
    findings = engine.evaluate(_ocr("GOVERNMENT WARNING"), context)
    fmt = _finding(findings, "GOVERNMENT_WARNING_FORMAT_SIGNAL")
    assert fmt.status == FindingStatus.PASS, (
        "a pipeline-computed 'likely' bold signal must let FORMAT_SIGNAL PASS — "
        "otherwise no clean label can ever pass (F1)"
    )
