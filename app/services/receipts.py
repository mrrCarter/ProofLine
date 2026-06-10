import base64
import json
import os
from datetime import datetime, timezone
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey


RECEIPT_VERSION = "1"
SIGNATURE_PREFIX = "ed25519:"
SIGNING_KEY_ENV = "PROOFLINE_ED25519_SEED_B64"
PUBLIC_KEY_ID_ENV = "PROOFLINE_PUBLIC_KEY_ID"


def _load_signing_key() -> SigningKey:
    encoded = os.getenv(SIGNING_KEY_ENV) or os.getenv("PROOFLINE_ED25519_PRIVATE_KEY_B64")
    if not encoded:
        return SigningKey.generate()

    key_bytes = base64.b64decode(encoded)
    if len(key_bytes) not in {32, 64}:
        raise ValueError(f"{SIGNING_KEY_ENV} must decode to 32-byte seed or 64-byte private key")
    return SigningKey(key_bytes[:32])


_SIGNING_KEY = _load_signing_key()
_VERIFY_KEY = _SIGNING_KEY.verify_key
_PUBLIC_KEY_ID = os.getenv(PUBLIC_KEY_ID_ENV, "proofline-dev-ephemeral")


def canonical_receipt_bytes(receipt: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in receipt.items() if key != "signature"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def public_key_payload() -> dict[str, str]:
    return {
        "keyId": _PUBLIC_KEY_ID,
        "algorithm": "Ed25519",
        "publicKey": _b64(bytes(_VERIFY_KEY)),
    }


def sign_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    receipt = {
        **payload,
        "receiptVersion": payload.get("receiptVersion", RECEIPT_VERSION),
        "publicKeyId": payload.get("publicKeyId", _PUBLIC_KEY_ID),
    }
    signature = _SIGNING_KEY.sign(canonical_receipt_bytes(receipt)).signature
    return {**receipt, "signature": f"{SIGNATURE_PREFIX}{_b64(signature)}"}


def verify_signed_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    signature = receipt.get("signature")
    if not isinstance(signature, str) or not signature.startswith(SIGNATURE_PREFIX):
        return {"valid": False, "error": "missing_or_invalid_signature"}

    try:
        signature_bytes = base64.b64decode(signature.removeprefix(SIGNATURE_PREFIX))
        VerifyKey(bytes(_VERIFY_KEY)).verify(canonical_receipt_bytes(receipt), signature_bytes)
    except (BadSignatureError, ValueError, TypeError):
        return {"valid": False, "error": "signature_verification_failed"}

    return {
        "valid": True,
        "runId": receipt.get("runId"),
        "artifactSha256": receipt.get("artifactSha256"),
        "publicKeyId": receipt.get("publicKeyId"),
    }


def build_receipt_payload(run: dict[str, Any]) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "receiptVersion": RECEIPT_VERSION,
        "runId": run["runId"],
        "requestId": run["requestId"],
        "artifactSha256": run["artifactSha256"],
        "rulePack": run.get("rulePack"),
        "providers": run.get("providers", {"ocr": None, "adjudicator": None}),
        "verdict": run.get("verdict"),
        "findings": run.get("findings", []),
        "timings": {
            "totalMs": run.get("latencyMs"),
            "stages": run.get("timings", {}),
        },
        "createdAt": created_at,
    }


def sign_run_receipt(run: dict[str, Any]) -> dict[str, Any]:
    return sign_receipt(build_receipt_payload(run))
