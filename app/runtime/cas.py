# -*- coding: utf-8 -*-
"""Content-Addressable Artifact Store（蓝图 §6.9/§9.5 单机子集）。

- put_bytes/put_file：sha256 内容寻址，原子写（临时文件+fsync+rename）到
  data/cas/<digest>；DB（pi_cas_blobs）同内容去重（INSERT ON CONFLICT DO
  NOTHING），存在性以存储为准并校验 size；
- get：按 digest 读取（路径 = 存根目录 + sha256 前缀，不允许任意路径）；
- verify_digest：重读 blob 计算 sha256 与 digest 比对（防磁盘损坏/篡改）。
digest 格式统一 "sha256:<64hex>"。异常（大小不符/IO 错误）抛 CasError。
"""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from app.db import connect
from app.config import settings


class CasError(Exception):
    pass


def _digest_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def blob_path(digest: str) -> Path:
    if not (digest.startswith("sha256:") and len(digest) == 71
            and all(c in "0123456789abcdef" for c in digest[7:])):
        raise CasError(f"非法 digest: {digest}")
    return settings.cas_dir / digest[7:]


def put_bytes(data: bytes) -> str:
    """存字节；返回 digest（同内容幂等去重，不同内容绝不误判——评审 should-fix）。"""
    digest = _digest_of(data)
    target = blob_path(digest)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp-" + uuid.uuid4().hex[:8])
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
            _fsync_dir(target.parent)  # 评审 should-fix：rename 后 fsync 目录
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
    else:
        # 已存在必须重算校验：同尺寸损坏不得被当作幂等成功（评审 should-fix）
        actual_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual_hash != digest[7:]:
            raise CasError(f"CAS 已存在但内容不匹配: {digest}")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pi_cas_blobs (digest, size) VALUES (%s, %s) "
                "ON CONFLICT (digest) DO NOTHING", (digest, len(data)))
        conn.commit()
    return digest


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass  # 目录 fsync 非全部平台支持，尽力而为


def put_file(path: Path) -> str:
    """读文件内容存 CAS；返回 digest。"""
    data = path.read_bytes()
    return put_bytes(data)


def get(digest: str) -> bytes:
    """按 digest 读取 blob（路径由 digest 派生，杜绝任意路径）。"""
    target = blob_path(digest)
    if not target.is_file():
        raise CasError(f"blob 不存在: {digest}")
    return target.read_bytes()


def verify_digest(digest: str) -> bool:
    """重读 blob 并重算 sha256 校验（内容寻址完整性）。"""
    target = blob_path(digest)
    if not target.is_file():
        return False
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    return actual == digest[7:]