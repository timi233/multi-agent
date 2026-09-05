"""平台身份密钥：Runtime 节点 Ed25519 签名密钥（进场信号：真实签名而非占位）。

- 私钥持久化于 data/keys/runtime_ed25519.pem（data/ 已排除 git，权限 600）；
- keyId = sha256(公钥 DER)[:16]，跨重启稳定（供签名信封与未来 Gateway 身份绑定）；
- signature(value) = base64(Ed25519(私钥, signature_input))；
- verify() 供测试与验签方使用。
"""
from __future__ import annotations

import base64
import hashlib
import os
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from ..config import BASE_DIR

KEYS_DIR = BASE_DIR / "data" / "keys"
PRIVATE_KEY_PATH = KEYS_DIR / "runtime_ed25519.pem"


def _load() -> Ed25519PrivateKey:
    return serialization.load_pem_private_key(
        PRIVATE_KEY_PATH.read_bytes(), password=None)


def _load_or_create() -> Ed25519PrivateKey:
    """原子创建（评审）：同目录临时文件完整写入并 fsync，再以 `os.link`
    原子发布——读者只可能看到完整文件（link 在目标已存在时失败，竞争方
    回退读取既有完整密钥），杜绝读到半写文件或互相覆盖。"""
    if PRIVATE_KEY_PATH.exists():
        return _load()
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    tmp = KEYS_DIR / f".runtime_ed25519.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.link(tmp, PRIVATE_KEY_PATH)  # 原子发布完整文件（已存在则失败）
    except FileExistsError:
        pass  # 竞争失败：既有完整密钥为准
    finally:
        tmp.unlink(missing_ok=True)
    return _load()


def _private_key() -> Ed25519PrivateKey:
    return _load_or_create()


def public_key_der() -> bytes:
    return _private_key().public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def key_id() -> str:
    """公钥指纹（sha256(DER) 前 16 字节 hex）：跨重启稳定。"""
    return hashlib.sha256(public_key_der()).hexdigest()[:16]


def sign(data: bytes) -> str:
    """对 signature_input 字节签名，返回 base64。"""
    return base64.b64encode(_private_key().sign(data)).decode("ascii")


def verify(data: bytes, signature_b64: str) -> bool:
    """用持久化公钥验签（供测试与验签方）。"""
    try:
        sig = base64.b64decode(signature_b64)
        _private_key().public_key().verify(sig, data)
        return True
    except Exception:
        return False


def public_key_pem() -> str:
    """导出的公钥 PEM（对外发布/验证用；本 repo 不跟踪密钥）。"""
    return _private_key().public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")