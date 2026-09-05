#!/usr/bin/env python3
"""生成并固化 attempt_contract v2 测试向量（手册 §12.2 CT-01~CT-04）。

输出：contracts/test-vectors/attempt_contract/v2/vectors.json
每用例如下结构：
  {id, kind: positive|negative, schemaValid: bool, note,
   object, canonicalPayloadB64, payloadDigest, signatureInputB64,
   invalidReason?}
"""
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jsonschema import Draft202012Validator

from app.contracts.codec import canonical_payload, load_digest_profile, load_schema, payload_digest

OUT = Path(__file__).resolve().parent.parent / "contracts" / "test-vectors" / "attempt_contract" / "v2"
SCHEMA = load_schema("attempt_contract", "2")
PROFILE = load_digest_profile("attempt_contract", "2")
VALIDATOR = Draft202012Validator(SCHEMA)

BASE = {
    "contractId": "a" * 32,
    "contractVersion": "2",
    "workloadIdentity": "pi.attempt",
    "taskId": "0123456789abcdef",
    "attemptNumber": 1,
    "sourceBundleDigest": "sha256:" + "b" * 64,
    "model": {"provider": "cliproxy-local", "name": "deepseek-v4-flash", "thinking": "low"},
    "resourceLimits": {"maxTurns": 40, "commandTimeoutSeconds": 60, "maxOutputBytes": 65536},
    "workspaceConstraints": {"root": "workspaces/task-0123456789abcdef"},
    "toolAllowlist": ["list_dir", "write_file", "run_command", "finish"],
    "createdAt": "2026-09-05T08:00:00Z",
    "expiresAt": "2026-09-05T09:00:00Z",
}

CASES = [
    ("pos-minimal", True, "合法最小对象（无 signature）", dict(), None),
    ("pos-signed", True, "合法对象含 Ed25519 签名信封", {"signature": {
        "algorithm": "Ed25519", "keyId": "sk-attempt", "issuer": "attempt-service",
        "signedAt": "2026-09-05T08:01:00Z", "value": "c" * 128}}, None),
    ("neg-unknown-field", False, "未知字段必须拒绝", {"bogusField": "x"}, "unknown field"),
    ("neg-enum-thinking", False, "枚举外 thinking 必须拒绝", {"model": {"provider": "cliproxy-local", "name": "m", "thinking": "ultra"}}, "enum"),
    ("neg-max-turns-over", False, "超上限 maxTurns=999 必须拒绝", {"resourceLimits": {"maxTurns": 999, "commandTimeoutSeconds": 60, "maxOutputBytes": 65536}}, "maximum"),
    ("neg-dup-tool", False, "toolAllowlist 重复必须拒绝", {"toolAllowlist": ["write_file", "write_file"]}, "uniqueItems"),
    ("neg-bad-digest", False, "非 sha256:64hex 的 sourceBundleDigest 必须拒绝", {"sourceBundleDigest": "md5:zz"}, "pattern"),
    ("neg-missing-required", False, "缺必需字段 expiresAt 必须拒绝", {"expiresAt": None}, "required"),
]


def main() -> int:
    vectors = []
    for cid, valid, note, mutate, invalid_reason in CASES:
        obj = json.loads(json.dumps(BASE))
        if mutate:
            if isinstance(mutate, dict) and cid == "neg-missing-required":
                obj.pop("expiresAt")
            else:
                obj.update(mutate)
        errors = sorted(VALIDATOR.iter_errors(obj), key=lambda e: str(e.path))
        schema_valid = not errors
        entry = {
            "id": cid, "kind": "positive" if valid else "negative",
            "expectedSchemaValid": valid, "note": note,
            "object": obj,
            "calculateOnLoad": True,
        }
        if valid:
            entry["canonicalPayloadB64"] = base64.b64encode(canonical_payload(obj, PROFILE)).decode()
            entry["payloadDigest"] = payload_digest(obj, PROFILE)
            entry["signatureInputB64"] = entry["canonicalPayloadB64"]
        else:
            entry["invalidReason"] = invalid_reason
        if schema_valid != valid:
            msgs = [e.message for e in errors][:2]
            print(f"[!!] {cid}: 期望 schemaValid={valid} 实际={schema_valid} {msgs}")
        vectors.append(entry)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "vectors.json").write_text(
        json.dumps({"objectType": "attempt_contract", "schemaVersion": "2",
                    "canonicalEncoding": "RFC8785-JCS-lite", "vectors": vectors},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"写 {len(vectors)} 个向量 -> {OUT / 'vectors.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())