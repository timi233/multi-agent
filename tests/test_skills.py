"""skill_bundle_snapshot v2 契约 + Skill 供应链闭环测试：
向量期望/digest 重算/verified 语义（重复 mountPath、tree==manifest、decisions
非空/self）/pos-signed 验签与 registry/确定性构建（SKILL-REPRO-001：
artifact 与 manifest digest 与构建记录无关——两次构建同输入必同摘要）/
审批四象（quorum/veto/槽位唯一/职责分离）//publish 闭环与 ACTIVE 指针/
快照 ID 派生含审批引用。"""
import base64
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.contracts.codec import (
    canonical_payload,
    load_digest_profile,
    load_schema,
    payload_digest,
    validate_profile_consistency,
)
from app.db import connect, execute, execute_one
from app.runtime import skills
from app.runtime.cas import get, put_bytes
from app.runtime.skills import (
    approval_quorum_reached,
    build_skill_bundle,
    build_snapshot,
    create_approval_proposal,
    publish_bundle,
    record_approval_decision,
    verified_skill_bundle_snapshot,
    verify_skill_bundle_signature,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads(
    (ROOT / "contracts" / "test-vectors" / "skill_bundle_snapshot" / "v2"
     / "vectors.json").read_text(encoding="utf-8"))

SCHEMA = load_schema("skill_bundle_snapshot", "2")
PROFILE = load_digest_profile("skill_bundle_snapshot", "2")
VALIDATOR = Draft202012Validator(SCHEMA)
TEST_SEED = bytes.fromhex("b09b" * 16)

_PKG_A = {
    "skillPackageId": "aaaa000000000001", "skillPackageVersionId": "aaaa000000000002",
    "packageName": "file-ops", "packageVersion": "1.0.0",
    "packageDigest": "sha256:" + "a1" * 32,
    "mountPath": "skills/file-ops", "entrypointPath": "SKILL.md",
}
_PKG_B = {
    "skillPackageId": "bbbb000000000001", "skillPackageVersionId": "bbbb000000000002",
    "packageName": "web-scrape", "packageVersion": "0.3.0",
    "packageDigest": "sha256:" + "b1" * 32,
    "mountPath": "skills/web-scrape", "entrypointPath": "INDEX.md",
}


def _vec(vid: str) -> dict:
    return next(x for x in VECTORS["vectors"] if x["id"] == vid)


def _base() -> dict:
    snap = skills.build_snapshot(
        bundle_name="core-utils", revision=1, packages=[_PKG_A],
        approval_set_id="aa11bb22cc33dd44",
        approval_set_digest="sha256:" + "d" * 64,
        approval_decision_ids=["1111111111111111", "2222222222222222"],
        build={"artifact_digest": "sha256:" + "e1" * 32,
               "manifest_digest": "sha256:" + "e2" * 32})
    return snap


def test_profile_consistency_ok():
    assert validate_profile_consistency(SCHEMA, PROFILE) == []


def test_vectors_schema_expectations():
    for v in VECTORS["vectors"]:
        errors = list(VALIDATOR.iter_errors(v["object"]))
        assert (not errors) == v["expectedSchemaValid"], (
            f"{v['id']}: 期望 schemaValid={v['expectedSchemaValid']} "
            f"实际={not errors} {[e.message for e in errors][:1]}")


def test_positive_digests_recomputable():
    seen = 0
    for v in VECTORS["vectors"]:
        if v["kind"] != "positive":
            continue
        canon = canonical_payload(v["object"], PROFILE)
        assert base64.b64encode(canon).decode() == v["canonicalPayloadB64"], v["id"]
        assert payload_digest(v["object"], PROFILE) == v["payloadDigest"], v["id"]
        seen += 1
    assert seen == 4


def test_verified_accepts_positive_vectors():
    for v in VECTORS["vectors"]:
        if v["kind"] == "positive":
            assert verified_skill_bundle_snapshot(v["object"]) == [], v["id"]


def test_verified_rejects_semantic_tamper():
    snap = _base()
    assert verified_skill_bundle_snapshot(snap) == []

    # 同 mountPath 不同内容（uniqueItems 无法拒）→ 语义拒
    dup = dict(snap)
    dup["packageVersions"] = [snap["packageVersions"][0],
                              dict(snap["packageVersions"][0], packageVersion="9.9")]
    assert any("重复 mountPath" in p for p in verified_skill_bundle_snapshot(dup))

    # expectedMountedSkillTreeDigest != manifest → 拒
    bad_tree = dict(snap, expectedMountedSkillTreeDigest="sha256:" + "f" * 64)
    assert any("bundleManifestDigest" in p for p in verified_skill_bundle_snapshot(bad_tree))

    # 空 Decision 集 → 拒
    no_dec = dict(snap, approvalDecisionIds=[])
    assert any("approvalDecisionIds" in p for p in verified_skill_bundle_snapshot(no_dec))

    # self-digest 篡改 → 拒
    bad_self = dict(snap, payloadDigest="sha256:" + "9" * 64)
    assert any("payloadDigest self" in p for p in verified_skill_bundle_snapshot(bad_self))


def test_pos_signed_envelope_identity_and_signature():
    from app.contracts.codec import build_signature_envelope, jcs
    v = _vec("pos-signed")
    obj = v["object"]
    registry = json.loads((ROOT / "deploy" / "keys" / "keys.lock.json")
                          .read_text(encoding="utf-8"))
    reg = registry["keys"]["sk-bundle-vector"]
    assert obj["signature"]["keyId"] == "sk-bundle-vector"
    assert obj["signature"]["issuer"] == reg["issuer"] == "skill-builder-test"
    assert "skill_bundle_snapshots" in reg["allowedObjectTypes"]
    key = Ed25519PrivateKey.from_private_bytes(TEST_SEED)
    pub_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    assert hashlib.sha256(pub_der).hexdigest() == reg["pubFingerprintSha256"]
    from app.contracts.codec import SIGNATURE_ENVELOPE_KEYS
    meta = {k: obj["signature"][k] for k in SIGNATURE_ENVELOPE_KEYS if k in obj["signature"]}
    _, sig_in, _ = build_signature_envelope(obj, SCHEMA, PROFILE, meta)
    key.public_key().verify(base64.b64decode(obj["signature"]["value"]), sig_in)
    with pytest.raises(Exception):
        key.public_key().verify(base64.b64decode(obj["signature"]["value"]),
                                b"tampered")


def test_positive_snapshot_ids_unique():
    ids = [v["object"]["skillBundleSnapshotId"]
           for v in VECTORS["vectors"] if v["kind"] == "positive"]
    assert len(ids) == len(set(ids))


# ---------- G4 Skill 供应链闭环 ----------

def _seed_package(name="file-ops", version="1.0.0", text="## file-ops skill\n"
                                                          "helps manage files",
                  source_dir="file-ops") -> dict:
    digest = put_bytes(text.encode())
    pid = hashlib.sha256((name + version).encode()).hexdigest()[:16]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pi_skill_packages (skill_package_id, name, version, "
                "description, source_dir, package_digest) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (pid, name, version, "seed", source_dir, digest))
    return {"skillPackageId": pid,
            "skillPackageVersionId": hashlib.sha256((name + version + "v").encode())
            .hexdigest()[:16],
            "packageName": name, "packageVersion": version,
            "packageDigest": digest, "mountPath": f"skills/{name}",
            "entrypointPath": "SKILL.md"}


def test_build_deterministic_repro():
    """SKILL-REPRO-001 子集：同输入两次构建，artifact/manifest digest 必同。"""
    pkg = _seed_package()
    b1 = build_skill_bundle([pkg], "repro")
    b2 = build_skill_bundle([pkg], "repro")
    assert b1["artifact_digest"] == b2["artifact_digest"]
    assert b1["manifest_digest"] == b2["manifest_digest"]
    blob = get(b1["artifact_digest"])
    assert blob.startswith(b"skills/file-ops/SKILL.md") or b"file-ops" in blob


def test_approval_quorum_and_veto():
    pkg = _seed_package()
    build = build_skill_bundle([pkg], "approve")
    with connect() as conn:
        proposal = create_approval_proposal(
            conn, subject_id=build["artifact_digest"], subject_digest=build["manifest_digest"],
            bundle_name="approve")
        record_approval_decision(conn, proposal_id=proposal,
                                 approval_role="FUNCTION_APPROVER",
                                 approver_identity="alice", approved=True)
        assert approval_quorum_reached(conn, proposal) is False
        conn.commit()
    # 同一角色二次（改决定）必须拒绝（UNIQUE 槽位），独立事务避免 rollback 污染
    with pytest.raises(ValueError):
        with connect() as conn:
            record_approval_decision(conn, proposal_id=proposal,
                                     approval_role="FUNCTION_APPROVER",
                                     approver_identity="alice2", approved=True)
    with connect() as conn:
        record_approval_decision(conn, proposal_id=proposal,
                                 approval_role="SECURITY_APPROVER",
                                 approver_identity="bob", approved=True)
        assert approval_quorum_reached(conn, proposal) is True
        conn.commit()

    # veto 路径：新提案任一决策 approved=False → 直接 REJECTED，发布禁止
    pkg2 = _seed_package(name="veto-skill", version="2.0.0")
    build2 = build_skill_bundle([pkg2], "veto")
    with connect() as conn:
        p2 = create_approval_proposal(conn, subject_id=build2["artifact_digest"],
                                      subject_digest=build2["manifest_digest"],
                                      bundle_name="veto")
        record_approval_decision(conn, proposal_id=p2,
                                 approval_role="SECURITY_APPROVER",
                                 approver_identity="bob", approved=False)
        assert approval_quorum_reached(conn, p2) is False
        with pytest.raises(ValueError):
            publish_bundle(conn, proposal_id=p2)
        conn.rollback()


def test_approval_identity_separation():
    """评审 block-3：同一身份不能填充两个必需审批槽（四象职责分离）。"""
    pkg = _seed_package(name="identity-split", version="1.0.0")
    build = build_skill_bundle([pkg], "identity-split")
    with connect() as conn:
        prop = create_approval_proposal(conn, subject_id=build["artifact_digest"],
                                        subject_digest=build["manifest_digest"],
                                        bundle_name="identity-split")
        record_approval_decision(conn, proposal_id=prop,
                                 approval_role="FUNCTION_APPROVER",
                                 approver_identity="mallory", approved=True)
        with pytest.raises(ValueError, match="职责分离"):
            record_approval_decision(conn, proposal_id=prop,
                                     approval_role="SECURITY_APPROVER",
                                     approver_identity="mallory", approved=True)
        conn.rollback()


def test_publish_binds_build_inputs():
    """评审 block-1：发布内容唯一取自提案 build_inputs（不可自传内容绕过）。"""
    pkg = _seed_package(name="bind-check", version="1.0.0")
    build = build_skill_bundle([pkg], "bind-check")
    with connect() as conn:
        prop = create_approval_proposal(conn, subject_id=build["artifact_digest"],
                                        subject_digest=build["manifest_digest"],
                                        bundle_name="bind-check", build_inputs=[pkg])
        record_approval_decision(conn, proposal_id=prop,
                                 approval_role="FUNCTION_APPROVER",
                                 approver_identity="alice", approved=True)
        record_approval_decision(conn, proposal_id=prop,
                                 approval_role="SECURITY_APPROVER",
                                 approver_identity="bob", approved=True)
        assert approval_quorum_reached(conn, prop) is True
        snap = publish_bundle(conn, proposal_id=prop)
        conn.commit()
    assert snap["bundleName"] == "bind-check"
    assert snap["packageVersions"][0]["packageDigest"] == pkg["packageDigest"]
    assert snap["bundleArtifactDigest"] == build["artifact_digest"]


def test_publish_closed_loop():
    """构建→提案→双决策→quorum→发布：签名 Snapshot 落库 + ACTIVE 指针。"""
    pkg = _seed_package(name="closed-loop", version="1.0.0")
    build = build_skill_bundle([pkg], "closed-loop")
    with connect() as conn:
        prop = create_approval_proposal(conn, subject_id=build["artifact_digest"],
                                        subject_digest=build["manifest_digest"],
                                        bundle_name="closed-loop",
                                        build_inputs=[pkg])
        record_approval_decision(conn, proposal_id=prop,
                                 approval_role="FUNCTION_APPROVER",
                                 approver_identity="alice", approved=True)
        record_approval_decision(conn, proposal_id=prop,
                                 approval_role="SECURITY_APPROVER",
                                 approver_identity="bob", approved=True)
        assert approval_quorum_reached(conn, prop) is True
        snap = publish_bundle(conn, proposal_id=prop)
        conn.commit()

    assert verified_skill_bundle_snapshot(snap) == []
    assert verify_skill_bundle_signature(snap) is True
    row = execute_one("SELECT state FROM pi_skill_publication_pointers WHERE env_scope='local'")
    assert row["state"] == "ACTIVE"
    snap_row = execute_one(
        "SELECT bundle_revision, artifact_digest FROM pi_skill_bundle_snapshots "
        "WHERE snapshot_id=%s", (snap["skillBundleSnapshotId"],))
    assert snap_row["bundle_revision"] == 1
    assert snap_row["artifact_digest"] == snap["bundleArtifactDigest"]


def test_snapshot_id_changes_with_approval_ref():
    """审批引用变化必变快照 ID（防审批集与内容错配）。"""
    pkg = _seed_package(name="id-approval", version="1.0.0")
    build = build_skill_bundle([pkg], "id-approval")
    s1 = build_snapshot(bundle_name="id-approval", revision=1, packages=[pkg],
                        approval_set_id="aaa1000000000001",
                        approval_set_digest="sha256:" + "1" * 64,
                        approval_decision_ids=["1111111111111111"], build=build)
    s2 = build_snapshot(bundle_name="id-approval", revision=1, packages=[pkg],
                        approval_set_id="aaa1000000000002",
                        approval_set_digest="sha256:" + "2" * 64,
                        approval_decision_ids=["2222222222222222"], build=build)
    assert s1["skillBundleSnapshotId"] != s2["skillBundleSnapshotId"]


def test_publish_rejects_digest_mismatch():
    """评审 block-2：提案 subject 摘要与 build_inputs 重建摘要不一致必须拒绝。"""
    pkg = _seed_package(name="mismatch-check", version="1.0.0")
    build = build_skill_bundle([pkg], "mismatch-check")
    other_digest = "sha256:" + "9" * 64  # 伪造的批准摘要（≠ 真实 build 摘要）
    with connect() as conn:
        prop = create_approval_proposal(
            conn, subject_id=other_digest, subject_digest=other_digest,
            bundle_name="mismatch-check", build_inputs=[pkg])
        record_approval_decision(conn, proposal_id=prop,
                                 approval_role="FUNCTION_APPROVER",
                                 approver_identity="alice", approved=True)
        record_approval_decision(conn, proposal_id=prop,
                                 approval_role="SECURITY_APPROVER",
                                 approver_identity="bob", approved=True)
        assert approval_quorum_reached(conn, prop) is True
        with pytest.raises(ValueError, match="批准摘要"):
            publish_bundle(conn, proposal_id=prop)
        conn.rollback()


def test_api_closed_loop_and_worker_injection():
    """API 闭环：注册包→构建→双决策→发布→GET 查询 + worker skill 注入。"""
    from fastapi.testclient import TestClient
    from app.main import create_app

    client = TestClient(create_app(enable_worker=False))
    with client:
        r = client.post("/api/v1/skills/packages",
                        json={"name": "api-file-ops", "version": "1.2.0",
                              "source_dir": "file-ops", "description": "api demo"})
        assert r.status_code == 200, r.text
        r2 = client.post("/api/v1/skills/bundles:build",
                         json={"bundle_name": "api-core", "package_source_dirs": ["file-ops"]})
        assert r2.status_code == 200, r2.text
        proposal = r2.json()["proposalId"]
        assert r2.json()["artifactDigest"].startswith("sha256:")

        d1 = client.post(f"/api/v1/skills/proposals/{proposal}/decisions",
                         json={"approval_role": "FUNCTION_APPROVER",
                               "approver_identity": "alice", "approved": True})
        assert d1.status_code == 200 and d1.json()["quorumReached"] is False
        d2 = client.post(f"/api/v1/skills/proposals/{proposal}/decisions",
                         json={"approval_role": "SECURITY_APPROVER",
                               "approver_identity": "bob", "approved": True})
        assert d2.json()["quorumReached"] is True
        adv = client.post("/api/v1/skills/publications:advance",
                          json={"proposal_id": proposal})
        assert adv.status_code == 200, adv.text
        snap_id = adv.json()["snapshotId"]

        pub = client.get("/api/v1/skills/publication").json()
        assert pub["snapshot_id"] == snap_id and pub["state"] == "ACTIVE"
        assert pub["packages"][0]["name"] == "api-file-ops"
        bundles = client.get("/api/v1/skills/bundles").json()
        assert bundles[0]["snapshot_id"] == snap_id

    # worker 注入：已发布 skill 说明出现在 attempt prompt 前缀
    task = _insert_task(prompt="do something")
    _workspace_for_skill_task(task)
    got = {}

    def fake(task, **kw):
        got["prompt"] = task["prompt"]
        return True, "done", None

    _run_task(task, fake)
    assert "[已发布技能]" in got["prompt"]
    assert "api-file-ops@1.2.0" in got["prompt"]


def _workspace_for_skill_task(task) -> None:
    from pathlib import Path
    from app.config import settings
    p = Path(settings.workspaces_dir) / task["workspace"]
    p.mkdir(parents=True, exist_ok=True)
    (p / "out.txt").write_text("x")


def _insert_task(prompt="hello") -> dict:
    import uuid
    tid = uuid.uuid4().hex[:16]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
                "VALUES (%s,'t',%s,'w','RUNNING') RETURNING *",
                (tid, prompt))
            return cur.fetchone()


def _run_task(task: dict, fake_run_attempt):
    import app.worker as worker_mod
    from app.runtime import agent as agent_mod
    backup = agent_mod.run_attempt
    agent_mod.run_attempt = fake_run_attempt
    try:
        conn = connect()
        try:
            worker_mod._run_task(conn, task["id"])
        finally:
            conn.close()
    finally:
        agent_mod.run_attempt = backup