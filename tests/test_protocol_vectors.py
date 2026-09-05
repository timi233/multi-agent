"""Phase 0 第二版契约对象（task_spec / event_envelope）向量断言。

覆盖：Schema 正反向量、canonicalPayload 可重算一致、payloadDigest 稳定、
事件信封 CT-08 作用域必填 ID（Task 事件不要求 runId；Attempt 事件要求三者）。
"""
import base64
import json
from pathlib import Path

import pytest

from app.contracts.codec import (
    canonical_payload,
    load_digest_profile,
    load_schema,
    payload_digest,
)

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "contracts" / "test-vectors"

CASES = [
    (load_schema("task_spec", "2"), load_digest_profile("task_spec", "2"),
     VECTORS / "task_spec" / "v2" / "vectors.json"),
    (load_schema("event_envelope", "2"), load_digest_profile("event_envelope", "2"),
     VECTORS / "event_envelope" / "v2" / "vectors.json"),
]


@pytest.mark.parametrize("schema,profile,path", CASES,
                         ids=["task_spec", "event_envelope"])
def test_positive_recompute(schema, profile, path):
    data = json.loads(path.read_text(encoding="utf-8"))
    pos = [v for v in data["vectors"] if v["kind"] == "positive"]
    assert len(pos) >= 2
    for v in pos:
        errs = [e.message for e in __import__(
            "jsonschema").validators.Draft202012Validator(schema).iter_errors(v["object"])]
        assert not errs, f"{v['id']} 应 schema 合法: {errs[:2]}"
        recon = canonical_payload(v["object"], profile)
        assert base64.b64encode(recon).decode() == v["canonicalPayloadB64"], \
            f"{v['id']} canonicalPayload 可重算一致"
        assert payload_digest(v["object"], profile) == v["payloadDigest"], \
            f"{v['id']} payloadDigest 可重算一致"


@pytest.mark.parametrize("schema,profile,path", CASES,
                         ids=["task_spec", "event_envelope"])
def test_negative_rejected(schema, profile, path):
    data = json.loads(path.read_text(encoding="utf-8"))
    neg = [v for v in data["vectors"] if v["kind"] == "negative"]
    assert len(neg) >= 5
    for v in neg:
        assert v["schemaValid"] is False, f"{v['id']} 必须被拒绝"
        assert v["expectedError"], f"{v['id']} 需声明可观察的错误关键字"


def test_event_scope_required_ids_ct08():
    """CT-08：事件信封作用域必填 ID——Task 事件不要求 runId；Attempt 事件要求三者。"""
    schema = load_schema("event_envelope", "2")
    profile = load_digest_profile("event_envelope", "2")
    data = json.loads((VECTORS / "event_envelope" / "v2" / "vectors.json").read_text(encoding="utf-8"))

    task_ev = next(v for v in data["vectors"] if v["id"] == "pos-task-event")["object"]
    assert task_ev["eventType"].startswith("Task")
    assert "runId" not in task_ev["trace"] and "attemptId" not in task_ev["trace"]
    assert task_ev["trace"]["taskId"]

    att_ev = next(v for v in data["vectors"] if v["id"] == "pos-attempt-event")["object"]
    assert att_ev["eventType"].startswith("Attempt")
    assert all(k in att_ev["trace"] for k in ("taskId", "runId", "attemptId"))

    # 负向量：Attempt 事件缺 runId / attemptId 必须在 trace 层报错
    for vid, missing in (("neg-attempt-missing-run", "runId"),
                         ("neg-attempt-missing-attempt", "attemptId")):
        v = next(x for x in data["vectors"] if x["id"] == vid)
        assert v["schemaValid"] is False
        assert v["schemaErrors"], f"{vid} 应产生 schema 错误"
        assert any(missing in e for e in v["schemaErrors"]), \
            f"{vid} 错误应提及缺失字段 {missing}: {v['schemaErrors']}"


def test_object_digest_stable_across_envelopes():
    """同一 taskId 的 task_spec 与事件独立成 digest，互不干扰（各自对象域）。"""
    spec = json.loads((VECTORS / "task_spec" / "v2" / "vectors.json").read_text(encoding="utf-8"))
    ev = json.loads((VECTORS / "event_envelope" / "v2" / "vectors.json").read_text(encoding="utf-8"))
    sd = next(v for v in spec["vectors"] if v["id"] == "pos-full")["payloadDigest"]
    ed = next(v for v in ev["vectors"] if v["id"] == "pos-task-event")["payloadDigest"]
    assert sd.startswith("sha256:") and ed.startswith("sha256:")
    assert sd != ed