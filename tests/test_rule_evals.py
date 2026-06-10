import json
import math
import time
from pathlib import Path

import pytest

from app.core.constants import GOVERNMENT_WARNING_TEXT
from app.services.rules import RuleEngine


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "rule_eval_cases.json"
SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "rule_eval_expected.json"
LATENCY_BUDGET_MS = 4500.0
BATCH_SIZE = 50


def _load_cases() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _expand_ocr_items(items: list[dict]) -> list[dict]:
    title_case_warning = GOVERNMENT_WARNING_TEXT.replace("GOVERNMENT WARNING:", "Government Warning:")
    replacements = {
        "__GOVERNMENT_WARNING__": GOVERNMENT_WARNING_TEXT,
        "__GOVERNMENT_WARNING_TITLE_CASE__": title_case_warning,
    }
    return [
        {
            **item,
            "text": replacements.get(item["text"], item["text"]),
        }
        for item in items
    ]


def _evaluate_case(case: dict) -> dict:
    result = RuleEngine(case["rulePackPath"]).evaluate_with_verdict(
        _expand_ocr_items(case["ocr"]),
        dict(case["context"]),
    )
    findings = result["findings"]
    return {
        "fixtureId": case["fixtureId"],
        "rulePack": result["rulePack"],
        "verdict": result["verdict"],
        "findingIds": [finding.ruleId for finding in findings],
        "findingStatuses": {finding.ruleId: finding.status.value for finding in findings},
    }


def test_spec_10_rule_eval_snapshots():
    actual = [_evaluate_case(case) for case in _load_cases()]
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert actual == expected
    assert [item["fixtureId"] for item in actual] == [case["fixtureId"] for case in _load_cases()]
    for case, result in zip(_load_cases(), actual):
        assert result["verdict"] == case["expectedVerdict"]


@pytest.mark.latency
def test_rule_eval_latency_p95_under_budget():
    cases = _load_cases()
    durations_ms = []
    for index in range(BATCH_SIZE):
        case = cases[index % len(cases)]
        started = time.perf_counter()
        _evaluate_case(case)
        durations_ms.append((time.perf_counter() - started) * 1000)

    p95_index = math.ceil(len(durations_ms) * 0.95) - 1
    p95_ms = sorted(durations_ms)[p95_index]
    assert p95_ms <= LATENCY_BUDGET_MS, f"p95={p95_ms:.2f}ms budget={LATENCY_BUDGET_MS:.2f}ms"
