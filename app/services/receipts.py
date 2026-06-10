import base64
import binascii
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
PUBLIC_KEY_REGISTRY_ENV = "PROOFLINE_RECEIPT_PUBLIC_KEYS_JSON"
PRODUCTION_ENVS = {"prod", "production"}


def _production_mode() -> bool:
    return os.getenv("PROOFLINE_ENV", "").strip().casefold() in PRODUCTION_ENVS


def _decode_b64(value: str, env_name: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except binascii.Error as exc:
        raise ValueError(f"{env_name} must be valid base64") from exc


def _load_signing_key() -> SigningKey:
    encoded = os.getenv(SIGNING_KEY_ENV) or os.getenv("PROOFLINE_ED25519_PRIVATE_KEY_B64")
    if not encoded:
        if _production_mode():
            raise RuntimeError(f"{SIGNING_KEY_ENV} is required when PROOFLINE_ENV=production")
        return SigningKey.generate()

    key_bytes = _decode_b64(encoded, SIGNING_KEY_ENV)
    if len(key_bytes) not in {32, 64}:
        raise ValueError(f"{SIGNING_KEY_ENV} must decode to 32-byte seed or 64-byte private key")
    return SigningKey(key_bytes[:32])


_SIGNING_KEY = _load_signing_key()
_VERIFY_KEY = _SIGNING_KEY.verify_key
_PUBLIC_KEY_ID = os.getenv(PUBLIC_KEY_ID_ENV, "proofline-dev-ephemeral")


def _decode_public_key(encoded: str, source: str) -> VerifyKey:
    key_bytes = _decode_b64(encoded, source)
    if len(key_bytes) != 32:
        raise ValueError(f"{source} must decode to a 32-byte Ed25519 public key")
    return VerifyKey(key_bytes)


def _load_public_key_registry(current_key_id: str, current_key: VerifyKey) -> dict[str, VerifyKey]:
    registry = {current_key_id: current_key}
    raw_registry = os.getenv(PUBLIC_KEY_REGISTRY_ENV)
    if not raw_registry:
        return registry

    try:
        configured = json.loads(raw_registry)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{PUBLIC_KEY_REGISTRY_ENV} must be valid JSON") from exc

    if not isinstance(configured, dict):
        raise ValueError(f"{PUBLIC_KEY_REGISTRY_ENV} must be a JSON object of keyId to publicKey")

    for key_id, encoded_key in configured.items():
        if not isinstance(key_id, str) or not key_id.strip():
            raise ValueError(f"{PUBLIC_KEY_REGISTRY_ENV} contains an invalid key id")
        if not isinstance(encoded_key, str):
            raise ValueError(f"{PUBLIC_KEY_REGISTRY_ENV}.{key_id} must be a base64 public key")
        registry[key_id] = _decode_public_key(encoded_key, f"{PUBLIC_KEY_REGISTRY_ENV}.{key_id}")

    return registry


_KEY_REGISTRY = _load_public_key_registry(_PUBLIC_KEY_ID, _VERIFY_KEY)


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

    public_key_id = receipt.get("publicKeyId")
    if not isinstance(public_key_id, str) or not public_key_id.strip():
        return {"valid": False, "error": "missing_public_key_id"}

    verify_key = _KEY_REGISTRY.get(public_key_id)
    if verify_key is None:
        return {"valid": False, "error": "unknown_public_key_id", "publicKeyId": public_key_id}

    try:
        signature_bytes = base64.b64decode(signature.removeprefix(SIGNATURE_PREFIX))
        verify_key.verify(canonical_receipt_bytes(receipt), signature_bytes)
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
