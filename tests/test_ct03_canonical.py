"""CT-03：集合类数组乱序 / 重复（手册 §12.2，蓝图 §12 canonicalSortKeys）。

- 乱序正向量：canonicalPayload / payloadDigest 与有序版本逐字节一致（无序语义）
- 重复拒绝：canonical 排序层（绕过 Schema uniqueItems 直接探测）拒绝重复元素
- 排序键缺失拒绝：by=key 模式元素缺少排序键即失败
- 双实现覆盖：Node verify_vectors_node.js 已对全部正向量（含乱序）重新递推校验
"""
import json
from pathlib import Path

import pytest

from app.contracts.codec import (
    ContractError,
    canonical_payload,
    load_digest_profile,
    load_schema,
    payload_digest,
)

ROOT = Path(__file__).resolve().parent.parent
VEC = ROOT / "contracts" / "test-vectors"


def _load(object_type):
    schema = load_schema(object_type, "2")
    profile = load_digest_profile(object_type, "2")
    vectors = json.loads((VEC / object_type / "v2" / "vectors.json").read_text(encoding="utf-8"))
    return schema, profile, vectors["vectors"]


def test_attempt_tools_unordered_digest_stable():
    """CT-03 attempt_contract：toolAllowlist 乱序与有序的 canonicalPayload/digest 一致。"""
    _, _, vectors = _load("attempt_contract")
    ordered = next(v for v in vectors if v["id"] == "pos-minimal")
    unordered = next(v for v in vectors if v["id"] == "pos-tools-unordered")
    assert set(ordered["object"]["toolAllowlist"]) == set(unordered["object"]["toolAllowlist"])
    assert ordered["object"]["toolAllowlist"] != unordered["object"]["toolAllowlist"], \
        "前置：两向量数组顺序必须确实不同"
    assert unordered["canonicalPayloadB64"] == ordered["canonicalPayloadB64"]
    assert unordered["payloadDigest"] == ordered["payloadDigest"]


def test_task_policy_refs_unordered_digest_stable():
    """CT-03 task_spec：policyTemplateRefs 按 templateRef 排序，乱序 digest 一致。"""
    schema, profile, vectors = _load("task_spec")
    a = next(v for v in vectors if v["id"] == "pos-policy-refs-a")
    b = next(v for v in vectors if v["id"] == "pos-policy-refs-b")
    assert [r["templateRef"] for r in a["object"]["policyTemplateRefs"]] == \
        [r["templateRef"] for r in reversed(b["object"]["policyTemplateRefs"])]
    assert a["payloadDigest"] == b["payloadDigest"], "乱序集合 digest 必须一致"
    # 实时重算校验（不依赖固化）
    assert payload_digest(a["object"], profile) == payload_digest(b["object"], profile)
    assert payload_digest(a["object"], profile) == a["payloadDigest"]


def test_canonical_layer_rejects_duplicates_by_value():
    """CT-03 重复拒绝（value 模式）：绕过 Schema 直接探测 canonical 层。"""
    schema, profile, vectors = _load("attempt_contract")
    obj = next(v for v in vectors if v["id"] == "pos-minimal")["object"]
    bad = json.loads(json.dumps(obj))
    bad["toolAllowlist"] = ["list_dir", "list_dir", "write_file"]
    with pytest.raises(ContractError, match="duplicate"):
        canonical_payload(bad, profile)


def test_canonical_layer_rejects_duplicates_by_key():
    """CT-03 重复拒绝（key 模式）：policyTemplateRefs 相同 templateRef。"""
    schema, profile, vectors = _load("task_spec")
    obj = next(v for v in vectors if v["id"] == "pos-policy-refs-a")["object"]
    bad = json.loads(json.dumps(obj))
    bad["policyTemplateRefs"] = [
        {"templateRef": "eval-tpl/001", "templateDigest": "sha256:" + "0" * 64},
        {"templateRef": "eval-tpl/001", "templateDigest": "sha256:" + "0" * 64},
    ]
    with pytest.raises(ContractError, match="duplicate"):
        canonical_payload(bad, profile)


def test_canonical_layer_rejects_missing_sort_key():
    """CT-03 排序键缺失（by=key 模式）：元素缺少 templateRef 即失败。"""
    schema, profile, vectors = _load("task_spec")
    obj = next(v for v in vectors if v["id"] == "pos-policy-refs-a")["object"]
    bad = json.loads(json.dumps(obj))
    bad["policyTemplateRefs"] = [
        {"templateDigest": "sha256:" + "0" * 64},
    ]
    with pytest.raises(ContractError, match="canonical sort key missing"):
        canonical_payload(bad, profile)


def test_unordered_vectors_schema_valid():
    """乱序向量对象本身必须 Schema 合法（集合乱序不等于非法）。"""
    for ot, vid in (("attempt_contract", "pos-tools-unordered"),
                    ("task_spec", "pos-policy-refs-b")):
        schema, _, vectors = _load(ot)
        v = next(x for x in vectors if x["id"] == vid)
        from jsonschema import Draft202012Validator
        assert Draft202012Validator(schema).is_valid(v["object"]), f"{ot}/{vid} 应合法"