# -*- coding: utf-8 -*-
"""GitStagingResult v2 契约：固化向量语义校验 + ID 派生性质测试。
- 正向量 4 个：verified_git_staging_result 必须为空（Schema+语义全过）；
- 负向量 5 个：verified 必须非空；
- ID 性质：expectedRef/epoch/opKey 变化必变 ID；stagedAt 不进 ID（幂等）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.runtime.gitstager import (
    git_staging_result_id,
    verified_git_staging_result,
)

ROOT = Path(__file__).resolve().parent.parent
VEC = (ROOT / "contracts" / "test-vectors" / "git_staging_result" / "v2"
       / "vectors.json")

BUNDLE_ID = "abcd1234abcd1234abcd1234abcd1234"
BUNDLE_DIGEST = "sha256:" + "5e" * 32
REPO_ID = "00aa11bb22cc33dd"
REF = "refs/heads/main"
APPLIED = {"algorithm": "sha1", "hex": "cd" * 20}


def _vectors() -> list[dict]:
    return json.loads(VEC.read_text(encoding="utf-8"))["vectors"]


def _pos(id_: str) -> dict:
    for v in _vectors():
        if v["id"] == id_:
            return v["object"]
    raise AssertionError(f"no vector {id_}")


@pytest.mark.parametrize("vid", ["pos-initial-staging", "pos-append-staging",
                                 "pos-distinct-opkey", "pos-signed"])
def test_positive_vectors_verified(vid):
    result = _pos(vid)
    assert verified_git_staging_result(result) == []


@pytest.mark.parametrize("vid", ["neg-expected-algorithm", "neg-missing-required",
                                 "neg-bad-ref-pattern", "neg-bad-opkey",
                                 "neg-unknown-field"])
def test_negative_vectors_verified_nonempty(vid):
    v = next(x for x in _vectors() if x["id"] == vid)
    assert v["expectedSchemaValid"] is False
    assert verified_git_staging_result(v["object"]) != []


def test_result_id_derivation_properties():
    """ID 派生：不可变前像任一变化必变 ID；stagedAt 不进 ID（幂等复用）。"""
    kw = dict(commit_bundle_id_=BUNDLE_ID, commit_bundle_digest=BUNDLE_DIGEST,
              repository_id=REPO_ID, candidate_ref=REF, expected=None,
              applied=APPLIED, git_staging_epoch=1, op_key="1122" * 8)
    base = git_staging_result_id(**kw)
    assert git_staging_result_id(**dict(kw, expected=APPLIED)) != base
    assert git_staging_result_id(**dict(kw, git_staging_epoch=2)) != base
    assert git_staging_result_id(**dict(kw, op_key="ff" * 16)) != base
    assert git_staging_result_id(**dict(kw, applied={
        "algorithm": "sha1", "hex": "ab" * 20})) != base
    assert git_staging_result_id(**kw) == base  # 同数据重试 ID 稳定


def test_positive_result_ids_unique():
    ids = [v["object"]["gitStagingResultId"] for v in _vectors()
           if v["kind"] == "positive"]
    assert len(ids) == len(set(ids))
    assert all(len(x) == 32 and all(c in "0123456789abcdef" for c in x)
               for x in ids)