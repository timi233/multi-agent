"""契约计算主实现（Phase 0，蓝图 §9.4 / 手册 §12）。

canonical 编码约定（与独立参考实现 scripts/ref_impl/jcs.js 一致，逐字节比对）：
- 对象键按 Unicode 码点升序（本项目 schema 键为 ASCII，即字节序）
- 字符串最小 JSON 转义；**非 ASCII 一律转义**：BMP 码点 \\uXXXX；
  非 BMP（>0xFFFF）编码为 UTF-16 代理对 \\uD8xx\\uDCxx（与 Node 实现一致）
- 控制字符 0x00-0x1F、\\u2028/\\u2029 转义为 \\uXXXX；\\" 与 \\\\
- 数字仅允许安全整数（|n| <= 2^53-1，跨语言 IEEE-754 无损）
- 输出无空白分隔（separators=(',', ':')）
- 数组不重排（顺序语义由 schema 声明）

签名输入（蓝图 §9.4 域分离）：JCS（{signatureContext, ...}）除 canonicalPayload 外，
信封元数据（objectType/schemaVersion/keyId/issuer/workloadIdentity/audience/
controlPlaneEpoch/signedAt/payloadDigest）一并受签名保护。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

CONTRACTS_DIR = Path(__file__).resolve().parent.parent.parent / "contracts"
SCHEMA_DIR = CONTRACTS_DIR / "jsonschema"

# 跨语言安全整数上界（IEEE-754 无损）
MAX_SAFE_INTEGER = 2**53 - 1
MIN_SAFE_INTEGER = -(2**53 - 1)


class ContractError(Exception):
    pass


# ---------- RFC 8785-lite 序列化 ----------

_CTRL_ESCAPE = re.compile(r'[\x00-\x1f\\"\u2028\u2029]')
_ESCAPE_MAP = {
    '"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n",
    "\r": "\\r", "\t": "\\t",
}


def _escape(s: str) -> str:
    out: list[str] = []
    for ch in s:
        cp = ord(ch)
        if ch in _ESCAPE_MAP:
            out.append(_ESCAPE_MAP[ch])
        elif cp < 0x20 or cp in (0x2028, 0x2029):
            out.append(f"\\u{cp:04x}")
        elif cp >= 0x80:  # 非 ASCII 一律转义（BMP 直转；非 BMP 编码 UTF-16 代理对）
            if cp <= 0xFFFF:
                out.append(f"\\u{cp:04x}")
            else:
                v = cp - 0x10000
                hi = 0xD800 + (v >> 10)
                lo = 0xDC00 + (v & 0x3FF)
                out.append(f"\\u{hi:04x}\\u{lo:04x}")
        else:
            out.append(ch)
    return "".join(out)


def jcs(obj) -> bytes:
    """RFC 8785-lite：确定性 JSON 字节（键码点序、统一转义、无空白）。"""
    return _serialize(obj).encode("utf-8")


def _serialize(obj) -> str:
    if obj is None:
        return "null"
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    if isinstance(obj, int):
        if obj < MIN_SAFE_INTEGER or obj > MAX_SAFE_INTEGER:
            raise ContractError(
                f"integer out of cross-language safe range (±2^53-1): {obj}"
            )
        return str(obj)
    if isinstance(obj, float):
        raise ContractError("floating numbers are not allowed in canonical payload")
    if isinstance(obj, str):
        return '"' + _escape(obj) + '"'
    if isinstance(obj, list):
        return "[" + ",".join(_serialize(x) for x in obj) + "]"
    if isinstance(obj, dict):
        items = []
        for k in sorted(obj.keys()):  # 码点序（本项目键为 ASCII）
            if not isinstance(k, str):
                raise ContractError("object keys must be strings")
            items.append('"' + _escape(k) + '":' + _serialize(obj[k]))
        return "{" + ",".join(items) + "}"
    raise ContractError(f"unsupported value type: {type(obj).__name__}")


# ---------- Schema 校验 / canonicalPayload / digest ----------

def validate(obj: dict, schema: dict) -> list[str]:
    """Schema 校验，返回错误消息列表（空列表=合法）。"""
    validator = Draft202012Validator(schema)
    return [str(e.message) for e in validator.iter_errors(obj)]


def _resolve_pointer(obj, pointer: str):
    parts = pointer.lstrip("/").split("/") if pointer.startswith("/") else []
    cur = obj
    for part in parts:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise ContractError(f"pointer not found: {pointer}")
    return cur


def load_schema(object_type: str, schema_version: str) -> dict:
    path = SCHEMA_DIR / f"{object_type}.v{schema_version}.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    if not schema.get("title"):
        raise ContractError(f"schema not found: {object_type} v{schema_version}")
    return schema


def load_digest_profile(object_type: str, schema_version: str) -> dict:
    path = SCHEMA_DIR / f"{object_type}.v{schema_version}.digestprofile.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    if profile.get("objectType") != object_type:
        raise ContractError("digest profile objectType mismatch")
    return profile


def validate_profile_consistency(schema: dict, profile: dict) -> list[str]:
    """校验 DigestProfile 与 Schema 一致（评审 fix-9）：optionalImmutablePointers
    必须在 Schema properties 中真实存在且非 required（父对象 required 不含叶名）。"""
    problems: list[str] = []

    def schema_at(pointer: str):
        node = schema
        for part in pointer.lstrip("/").split("/"):
            props = node.get("properties") if isinstance(node, dict) else None
            if not isinstance(props, dict) or part not in props:
                return None
            node = props[part]
        return node

    def parent_schema(pointer: str):
        parts = pointer.lstrip("/").split("/")
        leaf = parts[-1]
        node = schema
        for part in parts[:-1]:
            props = node.get("properties") if isinstance(node, dict) else None
            if not isinstance(props, dict) or part not in props:
                return None
            node = props[part]
        return node, leaf

    for p in profile.get("optionalImmutablePointers") or []:
        if schema_at(p) is None:
            problems.append(f"optional pointer 在 Schema 中不存在: {p}")
            continue
        parent, leaf = parent_schema(p)
        req = parent.get("required") if isinstance(parent, dict) else None
        if isinstance(req, list) and leaf in req:
            problems.append(f"optional pointer 实为 Schema 必需字段（不可选）: {p}")
    # duplicateConsistencyPointers 防漂移（评审 nit-3）：对象指针必须存在于
    # Schema；信封键必须属于 §9.4 信封键集合——防止拼写错误静默关闭绑定。
    dup = profile.get("duplicateConsistencyPointers") or {}
    for obj_ptr, env_key in dup.items():
        if schema_at(obj_ptr) is None:
            problems.append(f"duplicateConsistencyPointers 对象指针在 Schema 中不存在: {obj_ptr}")
        if env_key not in SIGNATURE_ENVELOPE_KEYS:
            problems.append(
                f"duplicateConsistencyPointers 信封键不在 §9.4 键集中: {env_key}")
    # 蓝图 §12：JCS 不替数组排序——投影数组字段必须声明"有序语义"或
    # canonicalSortKey：orderedArrays（有序，JCS 不重排）或 canonicalSortKeys
    # （集合，先排序后求 digest）。by 仅允许 value|key；排序声明必须指向投影字段。
    sorts = profile.get("canonicalSortKeys") or {}
    ordered = set(profile.get("orderedArrays") or [])
    allowed_by = ("value", "key")
    for p, spec in sorts.items():
        if p in ordered:
            problems.append(
                f"数组 {p} 同时声明于 canonicalSortKeys 与 orderedArrays（互斥）")
        if p not in (profile.get("immutablePayloadPointers") or []):
            problems.append(
                f"canonicalSortKeys 指针 {p} 不在 immutablePayloadPointers 中")
        if spec.get("by") not in allowed_by:
            problems.append(
                f"canonicalSortKeys {p} by 仅允许 {'|'.join(allowed_by)}，"
                f"实际 {spec.get('by')!r}")
    for p in profile.get("immutablePayloadPointers") or []:
        node = schema_at(p)
        if isinstance(node, dict) and node.get("type") == "array":
            if p in ordered:
                continue
            spec = sorts.get(p)
            if spec is None:
                problems.append(
                    f"集合数组 {p} 必须声明 canonicalSortKeys 或 orderedArrays（蓝图 §12）")
            elif spec.get("by") == "key" and not spec.get("key"):
                problems.append(f"canonicalSortKeys {p} by=key 必须提供 key")
    return problems


def _canonical_sort_key(elem, spec: dict) -> bytes:
    """canonicalSortKeys 排序键（蓝图 §12：集合数组按稳定键/值字节序排序）。

    by=value（标量数组）：对元素整体 JCS 编码；by=key（对象数组）：对
    elems[key] JCS 编码（key 缺失或非对象元素即失败，排序键必须存在）。"""
    if spec.get("by") == "key":
        key = spec["key"]
        if not isinstance(elem, dict) or key not in elem:
            raise ContractError(
                f"canonical sort key missing: {key!r} in {elem!r}")
        return jcs(elem[key])
    return jcs(elem)


def _canonical_normalize_array(arr: list, spec: dict) -> list:
    """集合数组规范化：按排序键字节序稳定排序，相邻排序键相等即重复拒绝。"""
    keyed = [(_canonical_sort_key(e, spec), e) for e in arr]
    keyed.sort(key=lambda t: t[0])
    out: list = []
    prev = None
    for sk, elem in keyed:
        if prev is not None and sk == prev:
            raise ContractError("canonical sort detected duplicate element")
        prev = sk
        out.append(elem)
    return out


def canonical_payload(obj: dict, profile: dict) -> bytes:
    """按 DigestProfile.immutablePayloadPointers 白名单投影生成 canonicalPayload 字节。

    仅 `optionalImmutablePointers` 显式声明的可选指针允许缺失（跳过）；
    其余指针缺失（含必需字段缺失、Profile 拼写错误）fail closed 抛错，
    避免投影静默缩小签名覆盖范围。
    声明于 `canonicalSortKeys` 的集合数组在投影时先按规范排序并拒绝重复
    （蓝图 §12：JCS 不替数组排序——集合数组必须先 canonical sort）。"""
    pointers = profile.get("immutablePayloadPointers") or []
    optional = set(profile.get("optionalImmutablePointers") or [])
    sorts = profile.get("canonicalSortKeys") or {}
    projected: dict = {}
    for pointer in pointers:
        key = pointer.lstrip("/")
        try:
            value = _resolve_pointer(obj, pointer)
        except ContractError:
            if pointer in optional:
                continue
            raise ContractError(
                f"projection pointer missing and not optional: {pointer}")
        spec = sorts.get(pointer)
        if spec and isinstance(value, list):
            value = _canonical_normalize_array(value, spec)
        projected[key] = value
    return jcs(projected)


def payload_digest(obj: dict, profile: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_payload(obj, profile)).hexdigest()


def verified_payload_digest(obj: dict, schema: dict, profile: dict) -> str:
    """原子路径：Schema 校验 → 白名单投影 → digest。未知字段 fail closed（评审 fix-3）。"""
    errors = validate(obj, schema)
    if errors:
        raise ContractError(f"SCHEMA_INVALID: {errors[:3]}")
    return payload_digest(obj, profile)


def signature_input(
    canonical_payload_bytes: bytes,
    envelope: dict,
) -> bytes:
    """签名输入（蓝图 §9.4 域分离）：JCS({signatureContext, signatureAlgorithm,
    keyId, issuer, issuerWorkloadIdentity, audience, objectType, schemaVersion,
    payloadDigest, controlPlaneEpoch, signedAt})。

    canonicalPayload 通过 payloadDigest 引入（不在签名正文内展开，与蓝图
    "digest 是 canonicalPayload 的语义化别名"一致）；audience 无适用对象时
    显式 null。验证方必须以校验过的信封重建输入；篡改任一信封字段即不匹配。
    第一个参数保留为向后兼容占位（签名正文不展开业务字节）。
    """
    required = (
        "objectType", "schemaVersion", "signatureAlgorithm", "keyId", "issuer",
        "issuerWorkloadIdentity", "audience", "controlPlaneEpoch", "signedAt",
        "payloadDigest",
    )
    missing = [k for k in required if k not in envelope]
    if missing:
        raise ContractError(f"signature envelope missing fields: {missing}")
    ctx = {
        "signatureContext": "pi.contract.signature.v1",
        **{k: envelope[k] for k in required},
    }
    return jcs(ctx)


# 蓝图 §9.4 签名信封键（不含 value 与 payloadDigest——payloadDigest 由原子构造重算填充）
SIGNATURE_ENVELOPE_KEYS = (
    "objectType", "schemaVersion", "signatureAlgorithm", "keyId", "issuer",
    "issuerWorkloadIdentity", "audience", "controlPlaneEpoch", "signedAt",
)


def validate_signature_envelope(env: dict) -> None:
    """校验组装后的签名信封字段（评审 nit-1）：值域/类型的 Schema 级约束。"""
    if env.get("signatureAlgorithm") != "Ed25519":
        raise ContractError(f"signatureAlgorithm must be Ed25519")
    for key, label in (("keyId", "keyId"), ("issuer", "issuer"),
                       ("issuerWorkloadIdentity", "issuerWorkloadIdentity"),
                       ("objectType", "objectType")):
        if not isinstance(env.get(key), str) or not env[key]:
            raise ContractError(f"signature envelope {label} must be non-empty string")
    if env.get("audience") is not None and not isinstance(env.get("audience"), str):
        raise ContractError("signature envelope audience must be string or null")
    if not isinstance(env.get("schemaVersion"), (str, int)):
        raise ContractError("signature envelope schemaVersion invalid")
    if isinstance(env.get("controlPlaneEpoch"), bool) or \
            not isinstance(env.get("controlPlaneEpoch"), int) or env["controlPlaneEpoch"] < 0:
        raise ContractError("signature envelope controlPlaneEpoch must be int >= 0")
    if not isinstance(env.get("signedAt"), str) or \
            not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T", env.get("signedAt", "")):
        raise ContractError("signature envelope signedAt must be ISO timestamp")
    if not isinstance(env.get("payloadDigest"), str) or \
            not re.match(r"^sha256:[a-f0-9]{64}$", env.get("payloadDigest", "")):
        raise ContractError("signature envelope payloadDigest invalid")


def build_signature_envelope(obj: dict, schema: dict, profile: dict,
                             meta: dict) -> tuple[dict, bytes, str]:
    """原子签名信封构造（fail closed，评审 fix-1/fix-8/nit-1）：
    Profile 一致性 → Schema 校验 → 重算 payloadDigest → 组装蓝图 §9.4
    十字段信封 → 信封字段校验 → 签名输入。

    强制约束：
    - meta.objectType / meta.schemaVersion 必须等于 profile 的对象类型/版本
      （防止为错误对象域构造签名）；
    - 若 profile 声明 selfDigestPointer（对象顶层含 payloadDigest 派生字段，
      如事件信封），其值必须等于重算结果（防止自指 digest 冲突/不一致对象被签）；
    - 组装后的信封字段必须满足值域约束（signatureAlgorithm、audience 等）。
    信封内的 payloadDigest 必然来自本对象重算，杜绝伪造 digest 签名。
    返回 (envelope, signature_input_bytes, payload_digest_str)。"""
    problems = validate_profile_consistency(schema, profile)
    if problems:
        raise ContractError(
            f"profile/schema inconsistent: {'; '.join(problems[:3])}")
    if meta.get("objectType") != profile.get("objectType"):
        raise ContractError(
            f"signature envelope objectType mismatch: "
            f"{meta.get('objectType')} != {profile.get('objectType')}")
    if meta.get("schemaVersion") != str(profile.get("schemaVersion")):
        raise ContractError(
            f"signature envelope schemaVersion mismatch: "
            f"{meta.get('schemaVersion')} != {profile.get('schemaVersion')}")
    digest = verified_payload_digest(obj, schema, profile)
    self_ptr = profile.get("selfDigestPointer")
    if self_ptr:
        try:
            self_digest = _resolve_pointer(obj, self_ptr)
        except ContractError:
            raise ContractError(
                f"selfDigestPointer {self_ptr} missing in object")
        if self_digest != digest:
            raise ContractError(
                f"self-digest mismatch at {self_ptr}: object={self_digest}, "
                f"recomputed={digest}")
    missing = [k for k in SIGNATURE_ENVELOPE_KEYS if k not in meta]
    if missing:
        raise ContractError(f"signature envelope meta missing fields: {missing}")
    envelope = {k: meta[k] for k in SIGNATURE_ENVELOPE_KEYS}
    envelope["payloadDigest"] = digest
    # 重复安全字段绑定（评审 nit-2）：对象内与签名信封共有的字段必须一致
    # （如事件信封顶层 controlPlaneEpoch == 签名信封 controlPlaneEpoch）
    dup = profile.get("duplicateConsistencyPointers") or {}
    for obj_ptr, env_key in dup.items():
        try:
            obj_val = _resolve_pointer(obj, obj_ptr)
        except ContractError:
            continue  # 对象无该可选字段则跳过
        if obj_val != envelope.get(env_key):
            raise ContractError(
                f"duplicate field mismatch: object {obj_ptr}={obj_val} != "
                f"signature envelope {env_key}={envelope.get(env_key)}")
    validate_signature_envelope(envelope)
    return envelope, signature_input(b"", envelope), digest