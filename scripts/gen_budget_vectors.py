# -*- coding: utf-8 -*-
"""生成 budget_grant v2 契约向量（正/负）到 contracts/test-vectors/budget_grant/v2/。

覆盖：空 journal（无消费）、链式 journal 全生命周期（RESERVED→SENT→SETTLED）、
FAILED 释放、UNKNOWN 保守占额、pos-signed（§9.4 信封自洽回填 payloadDigest）；
负向量：状态枚举外、entryDigest 非 hex、未知字段、缺必填。

链式 digest 用 app.runtime.budget._entry_digest 逐条真实计算（previousEntryDigest
= 前条 entryDigest，首条 = pi-budget-root-v1）——向量即守法快照，可由
BudgetDomain.verified_budget_grant 复核。环境变量 PI_VEC_OUT 可覆盖输出目录。
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
from app.runtime.budget import ROOT_DIGEST, _entry_digest  # noqa: E402
from app.security import keys as node_keys  # noqa: E402

OUT_DEFAULT = ROOT / "contracts" / "test-vectors" / "budget_grant" / "v2"
OUT = Path(os.environ.get("PI_VEC_OUT", OUT_DEFAULT))

SCHEMA = load_schema("budget_grant", "2")
PROFILE = load_digest_profile("budget_grant", "2")
VALIDATOR = Draft202012Validator(SCHEMA)

GRANT_ID = "2222222222222222"
INV = "1111111111111111"


def _chain(specs: list[tuple]) -> list[dict]:
    """按 _entry_digest 逐条生成真实链（previous=前条 digest，首条=根锚）。"""
    out: list[dict] = []
    prev = ROOT_DIGEST
    for seq, (etype, req, reserved, actual) in enumerate(specs, start=1):
        digest = _entry_digest(prev, GRANT_ID, INV, etype, reserved, actual, req)
        out.append({
            "seq": seq, "entryType": etype, "invocationId": INV,
            "requestDigest": req, "reservedTokens": reserved,
            "actualTokens": actual, "previousEntryDigest": prev,
            "entryDigest": digest,
        })
        prev = digest
    return out


CHAIN_FULL = _chain([
    ("RESERVED", "a" * 32, 4096, None),
    ("SENT", None, 0, None),
    ("SETTLED", None, 4096, 1250),  # 与运行时一致：SETTLED.reserved=原预留（完整释放占额），actual 单独计 consumed
])
CHAIN_FAILED = _chain([
    ("RESERVED", "a" * 32, 4096, None),
    ("FAILED", None, 4096, None),
])
CHAIN_UNKNOWN = _chain([
    ("RESERVED", "a" * 32, 4096, None),
    ("UNKNOWN", None, 0, None),
])

BASE = {
    "contractId": "a" * 32,
    "contractVersion": "2",
    "workloadIdentity": "pi.gate",
    "grantId": GRANT_ID,
    "taskId": "0123456789abcdef",
    "attemptId": "3333333333333333",
    "totalBudgetTokens": 200000,
    "consumedTokens": 0,
    "status": "ACTIVE",
    "journal": CHAIN_FULL,
    "createdAt": "2026-09-05T08:00:00Z",
}

CASES = [
    ("pos-minimal", True, "合法对象：新建 Grant 尚未消费（空 journal、consumed=0）",
     {"journal": [], "consumedTokens": 0, "status": "ACTIVE"}, None),
    ("pos-chain-settled", True, "全生命周期消费中（SETTLED 已累计 consumed=1250，Grant 仍 ACTIVE）",
     {"journal": CHAIN_FULL, "consumedTokens": 1250, "status": "ACTIVE"}, None),
    ("pos-failed-release", True, "FAILED 释放预留（任务失败后 grant 终结 SETTLED，无实耗）",
     {"journal": CHAIN_FAILED, "consumedTokens": 0, "status": "SETTLED"}, None),
    ("pos-unknown", True, "UNKNOWN 保守占额（结果不确定不累计 consumed；未耗尽仍 ACTIVE——生产无 EXHAUSTED 写入路径）",
     {"journal": CHAIN_UNKNOWN, "consumedTokens": 0, "status": "ACTIVE"}, None),
    ("neg-status-enum", False, "status 枚举外必须拒绝", {"status": "DONE"}, "enum"),
    ("neg-broken-entry-digest", False, "entryDigest 非 64hex 必须拒绝",
     {"journal": [dict(CHAIN_FULL[0], entryDigest="zz")]}, "pattern"),
    ("neg-unknown-field", False, "未知字段必须拒绝", {"bogusField": "x"}, "unknown field"),
    ("neg-missing-entry-required", False, "journal 条目缺 entryDigest 必须拒绝",
     {"journal": [{"seq": 1, "entryType": "RESERVED",
                   "invocationId": INV, "reservedTokens": 1,
                   "previousEntryDigest": ROOT_DIGEST}]}, "required"),
    ("neg-negative-total", False, "totalBudgetTokens 负数必须拒绝",
     {"totalBudgetTokens": -1}, "minimum"),
    ("pos-signed", True, "含 §9.4 签名信封（对象自洽回填 payloadDigest）",
     {"consumedTokens": 1250, "signature": {
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
                # 真实 Ed25519 签名（评审 warn-fix：非占位；用 Runtime 密钥）
                entry["object"]["signature"]["value"] = node_keys.sign(sig_in)
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