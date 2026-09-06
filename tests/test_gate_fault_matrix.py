# -*- coding: utf-8 -*-
"""G6 附录 A 硬门槛①：200+ 故障场景矩阵（固化清单驱动、真实拒绝断言）。

方法（诚实可复现）：对 9 个已契约对象的**正向量基座**，按对象自身
Schema 驱动生成故障变体（type 破坏 / pattern 破坏 / const 破坏 / minimum
破坏 / 未知字段 / 缺必填 / 自 digest 破坏 / 签名信封破坏），每个变体一个
命名场景 <object>.<fault>.<field>；生成时预检“该变体确实被 Schema/语义
拒绝”，只保留真实拒绝的场景并**固化**为不可缩减清单
contracts/acceptance/gates/fault_matrix.json（随仓库入库）。

测试以**固化清单为唯一输入**逐项强制拒绝（评审 block-1）：若实现回归
开始接受某类故障，清单场景拒绝断言立即失败——不允许静默缩减/剔除以
凑数。生成器变更需显式重新生成清单（见 README G6 节）。

生成：PI_GATE_REGEN=1 .venv/bin/python -m pytest tests/test_gate_fault_matrix.py -q
运行：PI_PG_DB=pi_platform_test .venv/bin/python -m pytest tests/test_gate_fault_matrix.py -q
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from app.contracts.codec import load_schema, validate
from app.runtime.budget import BudgetDomain
from app.runtime.gitstager import (
    verified_commit_bundle,
    verified_git_staging_result,
    verify_commit_bundle_signature,
    verify_git_staging_result_signature,
)
from app.runtime.plans import verified_execution_plan
from app.runtime.skills import (
    verified_skill_bundle_snapshot,
    verify_skill_bundle_signature,
)
from app.runtime.terminal import (
    verified_terminal_envelope,
    verify_terminal_signature,
)

ROOT = Path(__file__).resolve().parent.parent
VEC = ROOT / "contracts" / "test-vectors"
GATES_DIR = ROOT / "contracts" / "acceptance" / "gates"
MANIFEST = GATES_DIR / "fault_matrix.json"

# 9 个已契约对象（正向量基座 + verified/validate + 可选验签路径）
OBJS: dict[str, tuple[str, object, object]] = {
    "attempt_contract": ("attempt_contract",
                         lambda o: validate(o, load_schema("attempt_contract", "2")),
                         None),
    "task_spec": ("task_spec",
                  lambda o: validate(o, load_schema("task_spec", "2")), None),
    "event_envelope": ("event_envelope",
                       lambda o: validate(o, load_schema("event_envelope", "2")),
                       None),
    "budget_grant": ("budget_grant",
                     lambda o: BudgetDomain.verified_budget_grant(o), None),
    "execution_plan_snapshot": ("execution_plan_snapshot",
                                lambda o: verified_execution_plan(o), None),
    "attempt_terminal_envelope": (
        "attempt_terminal_envelope", lambda o: verified_terminal_envelope(o),
        lambda o: verify_terminal_signature(o)),
    "skill_bundle_snapshot": (
        "skill_bundle_snapshot", lambda o: verified_skill_bundle_snapshot(o),
        lambda o: verify_skill_bundle_signature(o)),
    "commit_bundle": ("commit_bundle", lambda o: verified_commit_bundle(o),
                      lambda o: verify_commit_bundle_signature(o)),
    "git_staging_result": ("git_staging_result",
                           lambda o: verified_git_staging_result(o),
                           lambda o: verify_git_staging_result_signature(o)),
}


def _base(name: str) -> dict:
    vectors = json.loads((VEC / name / "v2" / "vectors.json").read_text(
        encoding="utf-8"))["vectors"]
    return deepcopy(next(v for v in vectors if v["kind"] == "positive")["object"])


def _leaf_paths(node: dict, prefix: str = "") -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for k, v in node.items():
        path = f"{prefix}/{k}"
        if isinstance(v, dict):
            out.append((path, v))
            out.extend(_leaf_paths(v, path))
        elif isinstance(v, list):
            out.append((path, v))
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    out.extend(_leaf_paths(item, f"{path}/{i}"))
                else:
                    out.append((f"{path}/{i}", item))
        else:
            out.append((path, v))
    return out


def _set_path(obj: dict, path: str, value: object) -> dict:
    o = deepcopy(obj)
    cur = o
    parts = [p for p in path.split("/") if p]
    for p in parts[:-1]:
        cur = cur[int(p)] if p.isdigit() else cur[p]
    last = parts[-1]
    if last.isdigit():
        cur[int(last)] = value
    else:
        cur[last] = value
    return o


def _schema_prop(name: str, field: str) -> dict:
    s = load_schema(name, "2")
    return (s.get("properties") or {}).get(field) or {}


def _build_candidates() -> list[dict]:
    """Schema 驱动生成候选故障场景（含 tampered 值，供清单固化）。"""
    cases: list[dict] = []
    for name, (_vec, _verify, _vsig) in OBJS.items():
        base = _base(name)
        schema = load_schema(name, "2")
        required = set(schema.get("required") or [])
        for path, val in _leaf_paths(base):
            if "/signature" in path or path == "/payloadDigest":
                continue
            if isinstance(val, str):
                cases.append({"id": f"{name}.type.{path.lstrip('/')}",
                              "fault": "type", "field": path, "tampered": 123})
            elif isinstance(val, int):
                cases.append({"id": f"{name}.type.{path.lstrip('/')}",
                              "fault": "type", "field": path,
                              "tampered": "not-an-int"})
            elif isinstance(val, (dict, list)):
                cases.append({"id": f"{name}.type.{path.lstrip('/')}",
                              "fault": "type", "field": path,
                              "tampered": "boom"})
        for path, val in _leaf_paths(base):
            fname = path.rstrip("/").split("/")[-1]
            prop = _schema_prop(name, fname)
            if prop.get("pattern") and isinstance(val, str):
                cases.append({"id": f"{name}.pattern.{path.lstrip('/')}",
                              "fault": "pattern", "field": path,
                              "tampered": "!" * max(3, len(val))})
            if isinstance(val, str) and val.startswith("sha256:") and \
                    prop.get("pattern"):
                cases.append({"id": f"{name}.digest.{path.lstrip('/')}",
                              "fault": "digest-pattern", "field": path,
                              "tampered": val.replace("sha256:", "sha256:zz")})
        for path, val in _leaf_paths(base):
            fname = path.rstrip("/").split("/")[-1]
            prop = _schema_prop(name, fname)
            if prop.get("const") and isinstance(val, str):
                cases.append({"id": f"{name}.const.{path.lstrip('/')}",
                              "fault": "const", "field": path,
                              "tampered": "violated"})
            if prop.get("type") == "integer" and \
                    prop.get("minimum") is not None and isinstance(val, int):
                cases.append({"id": f"{name}.minimum.{path.lstrip('/')}",
                              "fault": "minimum", "field": path,
                              "tampered": prop["minimum"] - 1})
        cases.append({"id": f"{name}.unknown-field", "fault": "unknown",
                      "field": "(root)", "tampered": {"__evil__": True}})
        if required:
            drop = next((f for f in sorted(required)
                         if f not in ("signature", "payloadDigest")), None)
            if drop:
                cases.append({"id": f"{name}.missing-required.{drop}",
                              "fault": "missing", "field": f"/{drop}",
                              "tampered": None})
        if "payloadDigest" in base:
            cases.append({"id": f"{name}.self-digest", "fault": "self-digest",
                          "field": "/payloadDigest",
                          "tampered": "sha256:" + "0" * 64})
        if "signature" in base:
            for sf, bad in (("value", "A" * 86 + "=="), ("issuer", "evil"),
                            ("keyId", "sk-evil"), ("objectType", "other"),
                            ("payloadDigest", "sha256:" + "0" * 64)):
                cases.append({"id": f"{name}.signature.{sf}",
                              "fault": f"signature-{sf}",
                              "field": f"/signature/{sf}", "tampered": bad})
    return cases


def _reject(name: str, tampered: dict) -> bool:
    _vec, verify, verify_sig = OBJS[name]
    if verify(tampered):
        return True
    if verify_sig is not None:
        return verify_sig(tampered) is False
    return False


def _tampered_of(name: str, s: dict) -> dict:
    """按固化场景精确重建被改对象（评审 block-1：清单为不可缩减输入）。"""
    base = _base(name)
    if s["fault"] == "unknown":
        return {**base, "__evil__": True}
    if s["fault"] == "missing":
        o = deepcopy(base)
        o.pop(s["field"].lstrip("/"))
        return o
    return _set_path(base, s["field"], s["tampered"])


def _load_manifest() -> list[dict]:
    assert MANIFEST.exists(), (
        f"清单缺失 {MANIFEST}——请先以 PI_GATE_REGEN=1 生成并提交（生成器会"
        "预检只保留真实拒绝场景；清单为不可缩减输入）")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["scenarios"]


MANIFEST_CASES = _load_manifest()
assert len(MANIFEST_CASES) >= 200, f"固化故障场景不足: {len(MANIFEST_CASES)}"


@pytest.mark.parametrize("s", MANIFEST_CASES, ids=[c["id"] for c in MANIFEST_CASES])
def test_fault_scenario_rejected(s: dict):
    """固化清单每个场景必须真实拒绝——回归接受即失败（不可静默缩减）。"""
    name = s["id"].split(".")[0]
    assert _reject(name, _tampered_of(name, s)), (
        f"清单场景 {s['id']} 未被拒绝（实现回归：故障不再被拒绝）")


def test_manifest_generation_mode():
    """PI_GATE_REGEN=1 时重新生成可缩减后的真实拒绝清单（仅显式触发）。"""
    if os.environ.get("PI_GATE_REGEN") != "1":
        return  # 默认不生成：清单是不可缩减的固定输入
    candidates = _build_candidates()
    real = [c for c in candidates
            if _reject(c["id"].split(".")[0], _tampered_of(c["id"].split(".")[0], c))]
    assert len(real) >= 200
    GATES_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {"objectType": "acceptance-fault-matrix",
                "generatedFrom": "tests/test_gate_fault_matrix.py（Schema 驱动 + 预检剔除无约束变体）",
                "scenarioCount": len(real),
                "objects": sorted({c["id"].split(".")[0] for c in real}),
                "scenarios": [{k: c[k] for k in ("id", "fault", "field", "tampered")}
                              for c in real]}
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"\nregen scenarios={len(real)} -> {MANIFEST}")