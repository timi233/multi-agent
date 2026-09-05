# -*- coding: utf-8 -*-
"""生成 budget_grant v2 契约向量（正/负）到 contracts/test-vectors/budget_grant/v2/。

覆盖：空 journal（无消费）、链式 journal 全生命周期（RESERVED→SENT→SETTLED）、
FAILED 释放、UNKNOWN 保守占额、pos-signed（§9.4 信封自洽回填 payloadDigest）；
负向量：状态枚举外、entryDigest 非 hex、未知字段、缺必填。
环境变量 PI_VEC_OUT 可覆盖输出目录（可复现性测试用）。
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator  # noqa: E402

from app.contracts.codec import (  # noqa: E402
    SIGNATURE_ENVELOPE_KEYS,
    build_signature_envelope,
    canonical_payload,
    load_digest_profile,
    load_schema,
    payload_digest,
)

OUT_DEFAULT = ROOT / "contracts" / "test-vectors" / "budget_grant" / "v2"
OUT = Path(os.environ.get("PI_VEC_OUT", OUT_DEFAULT))

SCHEMA = load_schema("budget_grant", "2")
PROFILE = load_digest_profile("budget_grant", "2")
VALIDATOR = Draft202012Validator(SCHEMA)

_JOURNAL_CHAIN = [
    {
        "seq": 1, "entryType": "RESERVED", "invocationId": "1111111111111111",
        "requestDigest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "reservedTokens": 4096,
        "previousEntryDigest": "pi-budget-root-v1",
        "entryDigest": "a" * 64,
    },
    {
        "seq": 2, "entryType": "SENT", "invocationId": "1111111111111111",
        "requestDigest": None, "reservedTokens": 0,
        "previousEntryDigest": "a" * 64,
        "entryDigest": "b" * 64,
    },
    {
        "seq": 3, "entryType": "SETTLED", "invocationId": "1111111111111111",
        "requestDigest": None, "reservedTokens": 4096, "actualTokens": 1250,
        "previousEntryDigest": "b" * 64,
        "entryDigest": "c" * 64,
    },
]

BASE = {
    "contractId": "a" * 32,
    "contractVersion": "2",
    "workloadIdentity": "pi.gate",
    "grantId": "2222222222222222",
    "taskId": "0123456789abcdef",
    "attemptId": "3333333333333333",
    "totalBudgetTokens": 200000,
    "consumedTokens": 1250,
    "status": "ACTIVE",
    "journal": _JOURNAL_CHAIN,
    "createdAt": "2026-09-05T08:00:00Z",
}

CASES = [
    ("pos-minimal", True, "合法对象：新建 Grant 尚无消费（空 journal）",
     {"journal": []}, None),
    ("pos-chain-settled", True, "链式 journal 全生命周期（RESERVED→SENT→SETTLED）",
     None, None),
    ("pos-failed-release", True, "FAILED 释放预留（条目 reservedTokens=原预留）",
     {"status": "SETTLED", "consumedTokens": 0,
      "journal": [dict(_JOURNAL_CHAIN[0], seq=1),
                  {"seq": 2, "entryType": "FAILED", "invocationId": "1111111111111111",
                   "requestDigest": None, "reservedTokens": 4096,
                   "previousEntryDigest": "a" * 64, "entryDigest": "b" * 64}]}, None),
    ("pos-unknown", True, "UNKNOWN 保守占额（Provider 结果不确定）",
     {"status": "EXHAUSTED", "journal": [dict(_JOURNAL_CHAIN[0], seq=1),
      {"seq": 2, "entryType": "UNKNOWN", "invocationId": "1111111111111111",
       "requestDigest": None, "reservedTokens": 0,
       "previousEntryDigest": "a" * 64, "entryDigest": "d" * 64}]}, None),
    ("neg-status-enum", False, "status 枚举外必须拒绝", {"status": "DONE"}, "enum"),
    ("neg-broken-entry-digest", False, "entryDigest 非 64hex 必须拒绝",
     {"journal": [dict(_JOURNAL_CHAIN[0], entryDigest="zz")]}, "pattern"),
    ("neg-unknown-field", False, "未知字段必须拒绝", {"bogusField": "x"}, "unknown field"),
    ("neg-missing-entry-required", False, "journal 条目缺 entryDigest 必须拒绝",
     {"journal": [{"seq": 1, "entryType": "RESERVED",
                   "invocationId": "1111111111111111", "reservedTokens": 1,
                   "previousEntryDigest": "pi-budget-root-v1"}]}, "required"),
    ("neg-negative-total", False, "totalBudgetTokens 负数必须拒绝",
     {"totalBudgetTokens": -1}, "minimum"),
    ("pos-signed", True, "含 §9.4 签名信封（对象自洽回填 payloadDigest）",
     {"signature": {
         "signatureAlgorithm": "Ed25519", "keyId": "sk-gate", "issuer": "gateway-service",
         "issuerWorkloadIdentity": "pi.gate", "audience": "pi.platform",
         "objectType": "budget_grant", "schemaVersion": "2",
         "payloadDigest": "sha256:" + "0" * 64,
         "controlPlaneEpoch": 1, "signedAt": "2026-09-05T08:01:00Z",
         "value": "c" * 86 + "=="}}, None),
]


def main() -> int:
    vectors = []
    for cid, valid, note, mutate, invalid_reason in CASES:
        obj = json.loads(json.dumps(BASE))
        if mutate:
            obj.update(mutate)
        errors = sorted(VALIDATOR.iter_errors(obj), key=lambda e: str(e.path))
        schema_valid = not errors
        entry = {
            "id": cid, "kind": "positive" if valid else "negative",
            "expectedSchemaValid": valid, "note": note,
            "expectedError": invalid_reason, "object": obj,
            "calculateOnLoad": True,
        }
        if valid:
            entry["canonicalPayloadB64"] = base64.b64encode(
                canonical_payload(obj, PROFILE)).decode()
            entry["payloadDigest"] = payload_digest(obj, PROFILE)
            if "signature" in obj:
                entry["object"]["signature"]["payloadDigest"] = entry["payloadDigest"]
                env, sig_in, _ = build_signature_envelope(
                    obj, SCHEMA, PROFILE,
                    {k: obj["signature"][k] for k in SIGNATURE_ENVELOPE_KEYS
                     if k in obj["signature"]})
                entry["signatureInputB64"] = base64.b64encode(sig_in).decode()
            else:
                entry["signatureInputB64"] = None
        else:
            entry["invalidReason"] = invalid_reason
        if schema_valid != valid:
            print(f"[!!] {cid}: 期望 schemaValid={valid} 实际={schema_valid} "
                  f"{[e.message for e in errors][:2]}")
        vectors.append(entry)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "vectors.json").write_text(
        json.dumps({"objectType": "budget_grant", "schemaVersion": "2",
                    "canonicalEncoding": "RFC8785-JCS-lite", "vectors": vectors},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"写 {len(vectors)} 个向量 -> {OUT / 'vectors.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())