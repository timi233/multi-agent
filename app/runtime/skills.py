# -*- coding: utf-8 -*-
"""SkillBundleSnapshot 契约语义校验（蓝图 §10.4 单机子集）与 Skill 供应链服务。

verified_skill_bundle_snapshot(snap) -> list[str]：先 Schema（形状）再语义：
- packageVersions 按 mountPath 键全局唯一（同 mountPath 不同内容拒收）；
- bundleArtifactDigest / bundleManifestDigest / expectedMountedSkillTreeDigest
  均为 sha256 格式（Schema 已约束）；expectedMountedSkillTreeDigest 单机语义
  恒等于 bundleManifestDigest（子集实现：文件树摘要即挂载树摘要），不一致拒；
- approvalDecisionIds 非空（四象审批链单机必含至少一条 Decision）；
- self-digest 兜底重算。
digest 重算异常（ContractError）收敛为问题列表。

构建/审批/发布闭环（G4-3）：build_bundle → create_approval_proposal →
record_approval_decision（双槽位 UNIQUE(proposal_id, role)，veto 直接拒绝）→
approval_quorum_reached → publish_bundle（最终签名 Snapshot + ACTIVE 指针）。
"""
from __future__ import annotations

import datetime
import hashlib
import io
import json
import tarfile
from pathlib import Path

from app.contracts.codec import (
    ContractError,
    build_signature_envelope,
    jcs,
    load_digest_profile,
    load_schema,
    payload_digest,
    validate,
)
from app.security import keys as node_keys

_MOUNT_POLICY = {
    "mountMode": "READ_ONLY", "runtimeDiscoveryMode": "STATIC_INDEX_ONLY",
    "runtimeMutationAllowed": False, "runtimeInstallAllowed": False,
    "networkRequired": False, "executableBinaryAllowed": False,
    "mcpAutoInstallAllowed": False,
}


def _semantic_checks(snap: dict) -> list[str]:
    problems: list[str] = []
    packages = snap.get("packageVersions") or []
    mounts = [p.get("mountPath") for p in packages]
    if len(mounts) != len(set(mounts)):
        problems.append("packageVersions 存在重复 mountPath（同挂载点不同内容）")
    if snap.get("expectedMountedSkillTreeDigest") != snap.get("bundleManifestDigest"):
        problems.append(
            "expectedMountedSkillTreeDigest 必须等于 bundleManifestDigest"
            "（单机子集：挂载树摘要即文件树摘要）")
    if not snap.get("approvalDecisionIds"):
        problems.append("approvalDecisionIds 非空（审批四象链单机必含 Decision）")
    try:
        recomputed = payload_digest(snap, load_digest_profile(
            "skill_bundle_snapshot", "2"))
    except ContractError as exc:
        problems.append(f"payloadDigest 无法重算: {exc}")
        recomputed = None
    if recomputed is not None and snap.get("payloadDigest") != recomputed:
        problems.append(
            f"payloadDigest self 不一致: object={snap.get('payloadDigest')} "
            f"recomputed={recomputed}")
    return problems


def verified_skill_bundle_snapshot(snap: dict) -> list[str]:
    schema_problems = validate(snap, load_schema("skill_bundle_snapshot", "2"))
    if schema_problems:
        return schema_problems
    return _semantic_checks(snap)


def _snap_id(*, bundle_name: str, revision: int, packages: list[dict],
             approval_set_id: str, approval_set_digest: str) -> str:
    blob = jcs({
        "bundleName": bundle_name, "bundleRevision": revision,
        "packages": sorted(packages, key=lambda p: p["mountPath"]),
        "compilerVersion": "0.1.0",
        "approvalSetId": approval_set_id, "approvalSetDigest": approval_set_digest,
        "builtAt": _BUILT_AT,
    })
    return hashlib.sha256(blob).hexdigest()[:32]


_BUILT_AT = "2026-09-06T12:00:00Z"


def build_skill_bundle(packages: list[dict], bundle_name: str) -> dict:
    """构造 SkillBundleSnapshot（内容/摘要真实来自 packageDigest 对应 CAS
    blob 与确定性 tar）。调用方负责 packages 元数据来自 pi_skill_packages。"""
    from app.runtime.cas import get, put_bytes

    entries = []
    buf = io.BytesIO()
    buf.name = "bundle.tar"
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for pkg in sorted(packages, key=lambda p: p["mountPath"]):
            data = get(pkg["packageDigest"])  # CAS 字节身份
            info = tarfile.TarInfo(name=pkg["mountPath"] + "/SKILL.md")
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0  # 确定性：固定 mtime
            tar.addfile(info, io.BytesIO(data))
            entries.append({"path": pkg["mountPath"] + "/SKILL.md",
                            "digest": pkg["packageDigest"], "size": len(data)})
    blob = buf.getvalue()
    artifact_digest = put_bytes(blob)
    manifest_digest = "sha256:" + hashlib.sha256(
        jcs({"entries": sorted(entries, key=lambda e: e["path"])})).hexdigest()
    return {"artifact_digest": artifact_digest, "manifest_digest": manifest_digest,
            "entries": entries}


def build_snapshot(*, bundle_name: str, revision: int, packages: list[dict],
                   approval_set_id: str, approval_set_digest: str,
                   approval_decision_ids: list[str],
                   build: dict) -> dict:
    """构造并签名 SkillBundleSnapshot；落库由 publish_bundle 完成。"""
    env = {
        "contractVersion": "2",
        "workloadIdentity": "pi.skill-builder",
        "skillBundleSnapshotId": _snap_id(
            bundle_name=bundle_name, revision=revision, packages=packages,
            approval_set_id=approval_set_id, approval_set_digest=approval_set_digest),
        "bundleName": bundle_name, "bundleRevision": revision,
        "packageVersions": sorted(packages, key=lambda p: p["mountPath"]),
        "compilerId": "pi.skill-builder", "compilerVersion": "0.1.0",
        "bundleArtifactDigest": build["artifact_digest"],
        "bundleManifestDigest": build["manifest_digest"],
        "expectedMountedSkillTreeDigest": build["manifest_digest"],
        "runtimeMountPolicy": dict(_MOUNT_POLICY),
        "approvalSetId": approval_set_id,
        "approvalSetDigest": approval_set_digest,
        "approvalDecisionIds": sorted(approval_decision_ids),
        "builtAt": "2026-09-06T12:00:00Z",
        "payloadDigest": "sha256:" + "0" * 64,
    }
    env["payloadDigest"] = payload_digest(env, load_digest_profile(
        "skill_bundle_snapshot", "2"))
    now = datetime.datetime.now(datetime.timezone.utc)
    meta = {
        "objectType": "skill_bundle_snapshot", "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": node_keys.key_id(),
        "issuer": "pi.skill-builder", "issuerWorkloadIdentity": "pi.skill-builder",
        "audience": "pi.platform", "controlPlaneEpoch": 0,
        "signedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    env["signature"] = {**meta, "payloadDigest": "sha256:" + "0" * 64,
                        "value": node_keys.sign(b"placeholder")}
    _env, sig_in, _ = build_signature_envelope(
        env, load_schema("skill_bundle_snapshot", "2"),
        load_digest_profile("skill_bundle_snapshot", "2"), meta)
    env["signature"] = {**_env, "value": node_keys.sign(sig_in)}
    return env


def verify_skill_bundle_signature(snap: dict) -> bool:
    """验签：元数据绑定 builder 身份 + 三方 digest 一致 + Ed25519 验签。"""
    from app.contracts.codec import SIGNATURE_ENVELOPE_KEYS
    try:
        sig = snap["signature"]
        if sig.get("objectType") != "skill_bundle_snapshot" or sig.get("schemaVersion") != "2":
            return False
        if sig.get("keyId") != node_keys.key_id():
            return False
        if sig.get("issuer") != "pi.skill-builder" or \
                sig.get("issuerWorkloadIdentity") != "pi.skill-builder":
            return False
        if sig.get("audience") != "pi.platform":
            return False
        recomputed = payload_digest(snap, load_digest_profile("skill_bundle_snapshot", "2"))
        if sig.get("payloadDigest") != recomputed or snap.get("payloadDigest") != recomputed:
            return False
        meta = {k: sig[k] for k in SIGNATURE_ENVELOPE_KEYS if k in sig}
        _env, sig_in, _ = build_signature_envelope(
            snap, load_schema("skill_bundle_snapshot", "2"),
            load_digest_profile("skill_bundle_snapshot", "2"), meta)
        return node_keys.verify(sig_in, sig["value"])
    except Exception:
        return False


def _uuid16() -> str:
    import uuid
    return uuid.uuid4().hex[:16]


def register_package(conn, *, name: str, version: str, source_dir: str,
                     description: str | None = None) -> dict:
    """从 skills_dir/<source_dir>/SKILL.md 注册包（entrypoint 单文件，披露）。"""
    from app.config import settings
    from app.runtime.cas import put_bytes
    entrypoint = (settings.skills_dir / source_dir / "SKILL.md").resolve()
    if not entrypoint.is_file() or not entrypoint.is_relative_to(
            settings.skills_dir.resolve()):
        # 评审 block-4：is_relative_to 而非字符串前缀（防 skills-evil sibling 绕过）
        raise ValueError(f"skills 源目录不存在或越界: {source_dir}")
    digest = put_bytes(entrypoint.read_bytes())
    package_id = _uuid16()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pi_skill_packages (skill_package_id, name, version, "
            "description, source_dir, package_digest) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING skill_package_id",
            (package_id, name, version, description, source_dir, digest))
    return {"skill_package_id": package_id, "name": name, "version": version,
            "package_digest": digest}


def resolve_package_refs(conn, source_dirs: list[str]) -> list[dict]:
    """source_dirs → packageVersions 引用（从 pi_skill_packages 读取）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT skill_package_id, name, version, package_digest, source_dir, "
            "entrypoint_path FROM pi_skill_packages ORDER BY source_dir")
        rows = cur.fetchall()
    by_dir = {r["source_dir"]: r for r in rows}
    out = []
    for sd in source_dirs:
        r = by_dir.get(sd)
        if r is None:
            raise ValueError(f"未注册的 skill 包: {sd}")
        out.append({
            "skillPackageId": r["skill_package_id"],
            "skillPackageVersionId": _uuid16(),
            "packageName": r["name"], "packageVersion": r["version"],
            "packageDigest": r["package_digest"], "mountPath": f"skills/{r['name']}",
            "entrypointPath": r["entrypoint_path"] or "SKILL.md",
        })
    return out


def active_bundle_summary(conn) -> list[dict] | None:
    """当前 'local' ACTIVE 指针的 bundle 包说明（worker 注入 attempt 用）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT p.state, s.envelope FROM pi_skill_publication_pointers p "
            "JOIN pi_skill_bundle_snapshots s ON s.snapshot_id=p.snapshot_id "
            "WHERE p.env_scope='local' AND p.state='ACTIVE'")
        row = cur.fetchone()
    if row is None:
        return None
    return [{"name": p["packageName"], "version": p["packageVersion"],
             "mountPath": p["mountPath"]}
            for p in row["envelope"]["packageVersions"]]


def create_approval_proposal(conn, *, subject_id: str, subject_digest: str,
                             bundle_name: str, build_inputs: list[dict] | None = None) -> str:
    proposal_id = _uuid16()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pi_approval_proposals (proposal_id, subject_type, "
            "subject_id, subject_digest, bundle_name, build_inputs) "
            "VALUES (%s,'SKILL_BUNDLE',%s,%s,%s,%s::jsonb) RETURNING proposal_id",
            (proposal_id, subject_id, subject_digest, bundle_name,
             json.dumps(build_inputs or [])))
    return proposal_id


def record_approval_decision(conn, *, proposal_id: str, approval_role: str,
                             approver_identity: str, approved: bool) -> dict:
    """写独立 Decision（UNIQUE(proposal_id, approval_role)，veto 直接拒绝提案；
    评审 block-3：双槽必须不同 approver 身份——同一身份不能填充两个必需槽；
    should-2：SAVEPOINT 隔离唯一冲突，不污染调用方事务）。"""
    from psycopg.errors import UniqueViolation
    if approval_role not in ("FUNCTION_APPROVER", "SECURITY_APPROVER"):
        raise ValueError(f"未知审批角色: {approval_role}")
    decision_id = _uuid16()
    with conn.cursor() as cur:
        # 评审 block：锁提案行序列化四象身份检查（防并发同身份填双槽），
        # UNIQUE(proposal_id, approver_identity) 兜底
        cur.execute("SELECT proposal_id FROM pi_approval_proposals "
                    "WHERE proposal_id=%s FOR UPDATE", (proposal_id,))
        if cur.fetchone() is None:
            raise ValueError(f"提案不存在: {proposal_id}")
        cur.execute(
            "SELECT approval_role, approver_identity FROM pi_approval_decisions "
            "WHERE proposal_id=%s AND approval_role <> %s", (proposal_id, approval_role))
        other = cur.fetchone()
        if other is not None and other["approver_identity"] == approver_identity:
            raise ValueError(
                f"四象职责分离：{approver_identity} 已填充另一必需审批槽")
        cur.execute("SAVEPOINT sp_decision")
        try:
            cur.execute(
                "INSERT INTO pi_approval_decisions (decision_id, proposal_id, "
                "approval_role, approver_identity, approved) "
                "VALUES (%s,%s,%s,%s,%s) RETURNING decision_id",
                (decision_id, proposal_id, approval_role, approver_identity, approved))
        except UniqueViolation:
            cur.execute("ROLLBACK TO SAVEPOINT sp_decision")
            raise ValueError(
                f"{approval_role} 槽位已被占（决策不可覆盖，改决定必须新提案）")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT sp_decision")
            raise
        else:
            cur.execute("RELEASE SAVEPOINT sp_decision")
    if not approved:  # veto：直接拒绝，不可再填另一槽
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pi_approval_proposals SET status='REJECTED' "
                "WHERE proposal_id=%s", (proposal_id,))
    return {"decision_id": decision_id, "proposal_id": proposal_id,
            "approval_role": approval_role, "approved": approved}


def approval_quorum_reached(conn, proposal_id: str) -> bool:
    """四象：双必需角色齐全且全 approved 才 QUORUM_REACHED。
    评审 block-2：锁提案行序列化 quorum 计算（并发 final 决策不产生
    永久 PENDING），状态迁移原子（WHERE status='PENDING'）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM pi_approval_proposals WHERE proposal_id=%s "
            "FOR UPDATE", (proposal_id,))
        row = cur.fetchone()
        if row is None or row["status"] == "REJECTED":
            return False
        cur.execute(
            "SELECT approval_role, approved FROM pi_approval_decisions "
            "WHERE proposal_id=%s", (proposal_id,))
        decisions = cur.fetchall()
    roles = {d["approval_role"] for d in decisions}
    if roles != {"FUNCTION_APPROVER", "SECURITY_APPROVER"}:
        return False  # quorum 不足（缺角色）
    if any(not d["approved"] for d in decisions):
        return False
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE pi_approval_proposals SET status='QUORUM_REACHED' "
            "WHERE proposal_id=%s AND status='PENDING'", (proposal_id,))
    return True


def approval_set_digest(conn, proposal_id: str) -> str:
    """ApprovalSet digest：决策集 canonical 排序摘要（重算完整 Decision 集；
    不含时间戳——JCS 不支持 datetime，且时间不入确定性摘要）。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT decision_id, approval_role, approver_identity, approved "
            "FROM pi_approval_decisions WHERE proposal_id=%s",
            (proposal_id,))
        rows = [dict(r) for r in cur.fetchall()]
    blob = jcs({"decisions": sorted(rows, key=lambda r: r["decision_id"])})
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def publish_bundle(conn, *, proposal_id: str, revision: int | None = None) -> dict:
    """审批通过后构造最终签名 Snapshot 并落库 + publication pointer ACTIVE。

    评审 block-1：内容来源唯一取自提案 build_inputs（锁定提案行读），
    拒绝调用方自传 packages/build/bundleName 绕过审批内容；revision 同事务
    （bundle_name, bundle_revision）分配并原子落库（约束兜底冲突重试）。
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, bundle_name, build_inputs, subject_id, subject_digest "
            "FROM pi_approval_proposals WHERE proposal_id=%s FOR UPDATE",
            (proposal_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"提案不存在: {proposal_id}")
        if row["status"] != "QUORUM_REACHED":
            raise ValueError("提案未达 quorum，禁止发布")
        bundle_name = row["bundle_name"]
        packages = row["build_inputs"] or []
        if not packages:
            raise ValueError("提案缺少 build_inputs，禁止发布")
    build = build_skill_bundle(packages, bundle_name)
    # 评审 block-2：重建摘要必须等于被审批的 subject_id/subject_digest
    if build["artifact_digest"] != row["subject_id"] or \
            build["manifest_digest"] != row["subject_digest"]:
        raise ValueError(
            "构建摘要与审批内容不一致（build_inputs 与批准摘要错配），禁止发布")
    if revision is None:
        with conn.cursor() as cur:
            # 评审 should-2：advisory lock 按 bundle 串行 revision 分配
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (bundle_name,))
            cur.execute(
                "SELECT COALESCE(MAX(bundle_revision),0)+1 AS next_revision FROM "
                "pi_skill_bundle_snapshots WHERE bundle_name=%s", (bundle_name,))
            revision = cur.fetchone()["next_revision"]
    with conn.cursor() as cur:
        cur.execute("SELECT decision_id FROM pi_approval_decisions "
                    "WHERE proposal_id=%s", (proposal_id,))
        decision_ids = [r["decision_id"] for r in cur.fetchall()]
    as_digest = approval_set_digest(conn, proposal_id)
    snap = build_snapshot(
        bundle_name=bundle_name, revision=revision, packages=packages,
        approval_set_id=proposal_id, approval_set_digest=as_digest,
        approval_decision_ids=decision_ids, build=build)
    problems = verified_skill_bundle_snapshot(snap)
    if problems:
        raise ValueError("Snapshot 未通过契约语义校验: " + "; ".join(problems[:3]))
    if not verify_skill_bundle_signature(snap):
        raise ValueError("Snapshot Ed25519 验签失败")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pi_skill_bundle_snapshots "
            "(snapshot_id, bundle_name, bundle_revision, approval_proposal_id, "
            "artifact_digest, manifest_digest, package_versions, envelope) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
            (snap["skillBundleSnapshotId"], bundle_name, revision, proposal_id,
             snap["bundleArtifactDigest"], snap["bundleManifestDigest"],
             json.dumps(snap["packageVersions"]), json.dumps(snap)))
        cur.execute(
            "INSERT INTO pi_skill_publication_pointers (env_scope, snapshot_id) "
            "VALUES ('local',%s) "
            "ON CONFLICT (env_scope) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id, "
            "state='ACTIVE', row_version=pi_skill_publication_pointers.row_version+1, "
            "updated_at=now()", (snap["skillBundleSnapshotId"],))
    return snap