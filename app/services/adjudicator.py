import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


ENABLED_ENV = "PROOFLINE_ADJUDICATOR_ENABLED"
ENDPOINT_ENV = "PROOFLINE_ADJUDICATOR_ENDPOINT"
TIMEOUT_ENV = "PROOFLINE_ADJUDICATOR_TIMEOUT_SECONDS"
FAILURE_THRESHOLD_ENV = "PROOFLINE_ADJUDICATOR_CIRCUIT_FAILURES"
COOLDOWN_ENV = "PROOFLINE_ADJUDICATOR_CIRCUIT_COOLDOWN_SECONDS"
PROVIDER_NAME = "vlm-adjudicator"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_COOLDOWN_SECONDS = 60.0
TRUTHY = {"1", "true", "yes", "on"}


@dataclass
class CircuitBreaker:
    failures: int = 0
    opened_at: float | None = None


_CIRCUIT = CircuitBreaker()


def adjudicator_enabled() -> bool:
    return os.getenv(ENABLED_ENV, "").strip().casefold() in TRUTHY


def reset_circuit() -> None:
    _CIRCUIT.failures = 0
    _CIRCUIT.opened_at = None


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(0.1, float(raw))
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _timeout_seconds() -> float:
    return _float_env(TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS)


def _failure_threshold() -> int:
    return _int_env(FAILURE_THRESHOLD_ENV, DEFAULT_FAILURE_THRESHOLD)


def _cooldown_seconds() -> float:
    return _float_env(COOLDOWN_ENV, DEFAULT_COOLDOWN_SECONDS)


def _circuit_open() -> bool:
    if _CIRCUIT.opened_at is None:
        return False
    if time.monotonic() - _CIRCUIT.opened_at >= _cooldown_seconds():
        reset_circuit()
        return False
    return True


def _record_failure() -> None:
    _CIRCUIT.failures += 1
    if _CIRCUIT.failures >= _failure_threshold():
        _CIRCUIT.opened_at = time.monotonic()


def _record_success() -> None:
    reset_circuit()


def _request_payload(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "runId": run.get("runId"),
        "requestId": run.get("requestId"),
        "artifactSha256": run.get("artifactSha256"),
        "rulePack": run.get("rulePack"),
        "verdict": run.get("verdict"),
        "findings": run.get("findings", []),
        "ocr": run.get("ocr", {}),
    }


def _post_json(endpoint: str, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"content-type": "application/json", "accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read()
    parsed = json.loads(body.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("adjudicator response must be a JSON object")
    return parsed


def _safe_advisory_decision(raw: Any) -> str:
    decision = str(raw or "NEEDS_REVIEW").strip().upper()
    if decision not in {"PASS", "FAIL", "NEEDS_REVIEW", "UNREADABLE"}:
        return "NEEDS_REVIEW"
    return decision


def _safe_error_code(exc: BaseException) -> str:
    return type(exc).__name__[:80]


async def advise_if_enabled(run: dict[str, Any]) -> dict[str, Any]:
    if not adjudicator_enabled():
        return {"status": "disabled", "provider": None, "decision": None}

    if _circuit_open():
        return {
            "status": "circuit_open",
            "provider": PROVIDER_NAME,
            "decision": "NEEDS_REVIEW",
            "reason": "circuit_breaker_open",
        }

    endpoint = os.getenv(ENDPOINT_ENV, "").strip()
    if not endpoint:
        return {
            "status": "unconfigured",
            "provider": PROVIDER_NAME,
            "decision": "NEEDS_REVIEW",
            "reason": f"{ENDPOINT_ENV}_missing",
        }

    timeout_seconds = _timeout_seconds()
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(_post_json, endpoint, _request_payload(run), timeout_seconds),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        _record_failure()
        return {
            "status": "timeout",
            "provider": PROVIDER_NAME,
            "decision": "NEEDS_REVIEW",
            "reason": "timeout",
            "timeoutSeconds": timeout_seconds,
        }
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
        _record_failure()
        return {
            "status": "error",
            "provider": PROVIDER_NAME,
            "decision": "NEEDS_REVIEW",
            "reason": "adapter_call_failed",
            "errorCode": _safe_error_code(exc),
        }

    _record_success()
    return {
        "status": "opinion",
        "provider": PROVIDER_NAME,
        "decision": "NEEDS_REVIEW",
        "advisoryDecision": _safe_advisory_decision(response.get("decision")),
        "rationale": str(response.get("rationale", "")),
    }
