"""契约计算主实现（Phase 0，蓝图 §9.4 / 手册 §12）。

canonical 编码约定（与独立参考实现保持一致，逐字节比对）：
- 对象键按 Unicode 码点升序（本项目 schema 键为 ASCII，即字节序）
- 字符串使用最小 JSON 转义并强制 ensure_ascii（非 ASCII 一律 \\uXXXX，含 \\u2028/\\u2029）
- 数字仅允许整数（schema 层规避浮点 JCS 规范化差异）
- 输出无空白分隔（separators=(',', ':')）
- 数组不重排（顺序语义由 schema 声明）
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

CONTRACTS_DIR = Path(__file__).resolve().parent.parent.parent / "contracts"
SCHEMA_DIR = CONTRACTS_DIR / "jsonschema"


class ContractError(Exception):
    pass


# ---------- RFC 8785-lite 序列化 ----------

_ESCAPE_RE = re.compile(r'[\x00-\x1f\\"\u2028\u2029]')
_ESCAPE_MAP = {
    '"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n",
    "\r": "\\r", "\t": "\\t",
}


def _escape(s: str) -> str:
    def repl(m: re.Match) -> str:
        ch = m.group(0)
        if ch in _ESCAPE_MAP:
            return _ESCAPE_MAP[ch]
        return f"\\u{ord(ch):04x}"

    return _ESCAPE_RE.sub(repl, s)


def jcs(obj) -> bytes:
    """RFC 8785-lite：确定性 JSON 字节（键码点序、最小转义+ensure_ascii、无空白）。"""
    return _serialize(obj).encode("utf-8")


def _serialize(obj) -> str:
    if obj is None:
        return "null"
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    if isinstance(obj, int):
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


# ---------- canonicalPayload / digest ----------

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


def canonical_payload(obj: dict, profile: dict) -> bytes:
    """按 DigestProfile.immutablePayloadPointers 白名单投影生成 canonicalPayload 字节。"""
    pointers = profile.get("immutablePayloadPointers") or []
    projected: dict = {}
    for pointer in pointers:
        key = pointer.lstrip("/")
        projected[key] = _resolve_pointer(obj, pointer)
    return jcs(projected)


def payload_digest(obj: dict, profile: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_payload(obj, profile)).hexdigest()


def signature_input(obj: dict, profile: dict) -> bytes:
    """签名输入 = canonicalPayload 字节（蓝图：digest 是对 canonicalPayload 的 sha256，
    签名对象为 digest 语义化别名的原始字节串）。"""
    return canonical_payload(obj, profile)


# ---------- Schema / DigestProfile 装载 ----------

def load_schema(object_type: str, schema_version: str) -> dict:
    path = SCHEMA_DIR / f"{object_type}.v{schema_version}.schema.json"
    schemas = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(schemas, dict) and schemas.get("title"):
        return schemas
    raise ContractError(f"schema not found: {object_type} v{schema_version}")


def load_digest_profile(object_type: str, schema_version: str) -> dict:
    path = SCHEMA_DIR / f"{object_type}.v{schema_version}.digestprofile.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    if profile.get("objectType") != object_type:
        raise ContractError("digest profile objectType mismatch")
    return profile