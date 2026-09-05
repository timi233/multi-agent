#!/usr/bin/env python3
"""生成 task_spec / event_envelope 契约向量的第二批（Phase 0 第二版）。

复用 app.contracts.codec 主实现；输出 contracts/test-vectors/<object>/v2/vectors.json。
运行: .venv/bin/python scripts/gen_protocol_vectors.py
"""
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.contracts.codec import (  # noqa: E402
    load_digest_profile,
    load_schema,
    payload_digest,
    validate,
)

OUT = ROOT / "contracts" / "test-vectors"

D64 = "sha256:" + "0" * 64
TID = "0123456789abcdef"


def build(object_type):
    schema = load_schema(object_type, "2")
    profile = load_digest_profile(object_type, "2")

    if object_type == "task_spec":
        base = {
            "schemaVersion": "2",
            "taskId": TID,
            "specVersion": "1",
            "userIdentity": {"subjectId": "user-042", "displayName": "测试用户"},
            "title": "整理周报",
            "prompt": "读取 workspace 内 weekly.md，汇总为三要点。",
            "workspace": {"root": "workspaces/t001", "writable": True},
            "requestedResources": {
                "model": {"provider": "cliproxy-local", "name": "deepseek-v4-flash", "thinking": "low"},
                "maxTurns": 8,
            },
            "policyTemplateRefs": [{"templateRef": "eval-tpl/001", "templateDigest": D64}],
            "createdAt": "2026-09-05T08:00:00Z",
        }
        cases = [
            ("pos-minimal", True, "合法最小任务规格（无可选字段）", {}, None),
            ("pos-full", True, "全字段（含 displayName/thinking/maxTokens/expiresAt）",
             {"userIdentity": {"subjectId": "user-042", "displayName": "张三"},
              "requestedResources": {"model": {"provider": "cliproxy-local", "name": "m", "thinking": "high"},
                                     "maxTurns": 12, "maxTokens": 4000},
              "expiresAt": "2026-09-06T08:00:00Z"}, None),
            ("neg-unknown-field", False, "未知字段必须拒绝", {"bogusField": "x"}, "bogusField"),
            ("neg-model-provider", False, "枚举外 provider 必须拒绝",
             {"requestedResources": {"model": {"provider": "openai", "name": "m"}, "maxTurns": 1}}, "provider"),
            ("neg-max-turns-zero", False, "maxTurns<1 必须拒绝",
             {"requestedResources": {"model": {"provider": "cliproxy-local", "name": "m"}, "maxTurns": 0}}, "maxTurns"),
            ("neg-missing-prompt", False, "缺必需 prompt 必须拒绝",
             {"prompt": None}, "prompt"),
            ("neg-bad-template-digest", False, "templateDigest 必须是 sha256: 格式",
             {"policyTemplateRefs": [{"templateRef": "t", "templateDigest": "md5:zz"}]}, "templateDigest"),
            ("neg-policy-duplicate", False, "policyTemplateRefs 重复引用必须拒绝",
             {"policyTemplateRefs": [{"templateRef": "a", "templateDigest": D64},
                                     {"templateRef": "a", "templateDigest": D64}]}, "uniqueItems"),
        ]
    else:  # event_envelope
        base = {
            "schemaVersion": "2",
            "eventId": "0199a00193d17abc8d11001122334455",
            "eventType": "TaskSubmitted.v2",
            "occurredAt": "2026-09-05T08:00:00.123456Z",
            "producer": {"serviceName": "task-api", "instanceId": "api-01"},
            "trace": {"traceId": "8f2b4fd756e3417fa2821d79b04f9231",
                      "spanId": "6f2b4fd756e3417f", "taskId": TID},
            "aggregate": {"type": "Task", "id": TID, "version": 1},
            "controlPlaneEpoch": 42,
            "operationIdempotencyKey": f"task-submit:{TID}",
            "payloadDigest": D64,
            "payload": {"taskSpecDigest": D64},
        }
        cases = [
            ("pos-task-event", True, "Task 作用域事件：仅 taskId，无 runId/attemptId", {}, None),
            ("pos-attempt-event", True, "Attempt 作用域事件：要求 taskId+runId+attemptId（CT-08）",
             {"eventType": "AttemptClaimed.v2",
              "trace": {"traceId": "8f2b4fd756e3417fa2821d79b04f9231", "taskId": TID,
                        "runId": "1111111111111111", "attemptId": "2222222222222222"},
              "aggregate": {"type": "Attempt", "id": "3333333333333333", "version": 1},
              "operationIdempotencyKey": f"attempt-claim:{TID}:1"}, None),
            ("neg-unknown-field", False, "未知字段必须拒绝", {"bogus": 1}, "bogus"),
            ("neg-task-missing-trace-task", False, "trace 必填 taskId",
             {"trace": {"spanId": "6f2b4fd756e3417f"}}, "taskId"),
            ("neg-attempt-missing-run", False, "Attempt 事件缺 runId 必须拒绝（CT-08）",
             {"eventType": "AttemptClaimed.v2", "trace": {"taskId": TID, "attemptId": "2222222222222222"}}, "runId"),
            ("neg-attempt-missing-attempt", False, "Attempt 事件缺 attemptId 必须拒绝（CT-08）",
             {"eventType": "AttemptRunning.v2", "trace": {"taskId": TID, "runId": "1111111111111111"}}, "attemptId"),
            ("neg-bad-event-type", False, "eventType 命名非法必须拒绝",
             {"eventType": "artifact published"}, "eventType"),
            ("neg-bad-payload-digest", False, "payloadDigest 非 sha256 格式必须拒绝",
             {"payloadDigest": "md5:abc"}, "payloadDigest"),
        ]

    def apply(mut: dict):
        obj = json.loads(json.dumps(base))
        for k, v in mut.items():
            if v is None:
                obj.pop(k, None)
            else:
                obj[k] = v
        return obj

    vectors = []
    for vid, valid, note, mut, kind_note in cases:
        obj = apply(mut)
        errs = validate(obj, schema)
        digest = payload_digest(obj, profile) if not errs else None
        vectors.append({
            "id": vid, "kind": "positive" if valid else "negative", "note": note,
            "schemaValid": not errs,
            "expectedError": kind_note,
            "schemaErrors": errs[:2],
            "object": obj,
        })
        if not errs:
            vectors[-1]["canonicalPayloadB64"] = base64.b64encode(
                _canonical(obj, profile)).decode()
            vectors[-1]["payloadDigest"] = digest

    out_dir = OUT / object_type / "v2"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "vectors.json").write_text(
        json.dumps({"objectType": object_type, "schemaVersion": "2",
                    "vectors": vectors}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    ok = sum(1 for v in vectors if v["schemaValid"] == (v["kind"] == "positive"))
    print(f"{object_type}: {len(vectors)} 向量，断言匹配 {ok}/{len(vectors)}")


def _canonical(obj, profile):
    from app.contracts.codec import canonical_payload
    return canonical_payload(obj, profile)


if __name__ == "__main__":
    for ot in ("task_spec", "event_envelope"):
        build(ot)