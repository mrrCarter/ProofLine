import asyncio
import json

from app.services import adjudicator


def test_adjudicator_disabled_never_calls_adapter(monkeypatch):
    monkeypatch.delenv(adjudicator.ENABLED_ENV, raising=False)
    monkeypatch.setattr(
        adjudicator,
        "_post_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("adapter should not be called")),
    )

    result = asyncio.run(adjudicator.advise_if_enabled({"runId": "run-disabled"}))

    assert result == {"status": "disabled", "provider": None, "decision": None}


def test_adjudicator_enabled_without_endpoint_is_local_needs_review(monkeypatch):
    adjudicator.reset_circuit()
    monkeypatch.setenv(adjudicator.ENABLED_ENV, "true")
    monkeypatch.delenv(adjudicator.ENDPOINT_ENV, raising=False)

    result = asyncio.run(adjudicator.advise_if_enabled({"runId": "run-unconfigured"}))

    assert result["status"] == "unconfigured"
    assert result["provider"] == adjudicator.PROVIDER_NAME
    assert result["decision"] == "NEEDS_REVIEW"


def test_adjudicator_circuit_opens_after_failures(monkeypatch):
    adjudicator.reset_circuit()
    monkeypatch.setenv(adjudicator.ENABLED_ENV, "true")
    monkeypatch.setenv(adjudicator.ENDPOINT_ENV, "http://127.0.0.1:9/adjudicate")
    monkeypatch.setenv(adjudicator.FAILURE_THRESHOLD_ENV, "1")
    monkeypatch.setattr(
        adjudicator,
        "_post_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("network denied")),
    )

    failed = asyncio.run(adjudicator.advise_if_enabled({"runId": "run-failed"}))
    opened = asyncio.run(adjudicator.advise_if_enabled({"runId": "run-open"}))

    assert failed["status"] == "error"
    assert opened["status"] == "circuit_open"
    assert opened["decision"] == "NEEDS_REVIEW"


def test_adjudicator_error_payload_does_not_leak_endpoint(monkeypatch):
    adjudicator.reset_circuit()
    endpoint = "http://secret.internal.example/adjudicate"
    monkeypatch.setenv(adjudicator.ENABLED_ENV, "true")
    monkeypatch.setenv(adjudicator.ENDPOINT_ENV, endpoint)
    monkeypatch.setattr(
        adjudicator,
        "_post_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(f"failed to reach {endpoint}")),
    )

    result = asyncio.run(adjudicator.advise_if_enabled({"runId": "run-sanitized"}))

    assert result["status"] == "error"
    assert result["reason"] == "adapter_call_failed"
    assert result["errorCode"] == "OSError"
    assert endpoint not in json.dumps(result)
