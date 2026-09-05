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


def canonical_payload(obj: dict, profile: dict) -> bytes:
    """按 DigestProfile.immutablePayloadPointers 白名单投影生成 canonicalPayload 字节。
    可选指针在对象中缺失时跳过（投影只含实际存在的白名单字段）。"""
    pointers = profile.get("immutablePayloadPointers") or []
    projected: dict = {}
    for pointer in pointers:
        key = pointer.lstrip("/")
        try:
            projected[key] = _resolve_pointer(obj, pointer)
        except ContractError:
            continue
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