"""验收基准报告测试（③）：结构完整性 + 不虚报约束（PASS 必须带证据、Phase 0
门槛如实声明）。"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.acceptance_report import build_report  # noqa: E402

STATUSES = {"PASS", "PARTIAL", "NOT_IMPLEMENTED"}


def test_report_structure():
    rep = build_report()
    assert set(rep) >= {"phase0", "entries", "summary"}
    assert rep["summary"]["total"] == len(rep["entries"])
    for e in rep["entries"]:
        assert set(e) >= {"id", "label", "phase", "status", "evidence", "note"}
        assert e["status"] in STATUSES
        assert e["phase"] in ("Phase 0", "Phase 1")


def test_pass_requires_evidence():
    """不虚报：PASS 条目必须给出可定位证据（测试/脚本/契约向量/API/源码名）。"""
    rep = build_report()
    for e in rep["entries"]:
        if e["status"] == "PASS":
            assert e["evidence"], f"{e['id']} 标 PASS 但无证据"
            for ev in e["evidence"]:
                assert any(marker in ev for marker in (
                    "tests/", "scripts/", "contracts/", "app/", "GET /")), \
                    f"{e['id']} 证据不可定位: {ev}"


def test_phase0_threshold_honest():
    """Phase 0 正式门槛（手册 §18.2：GT + RT-01~06）未达须如实：declarable=False，
    且缺失列表包含 GT 与 RT-01~05。"""
    rep = build_report()
    assert rep["phase0"]["declarable"] is False
    missing = set(rep["phase0"]["missing"])
    assert "GT-xx" in missing
    assert {"RT-01", "RT-02", "RT-03", "RT-04", "RT-05"} <= missing
    assert "RT-06" in missing  # PARTIAL 亦计入缺失


def test_core_ids_present():
    rep = build_report()
    ids = {e["id"] for e in rep["entries"]}
    assert {"CT-01", "CT-02", "CT-03", "CT-04", "CT-08", "SM-xx",
            "GW-03", "GW-04", "GW-06", "GW-07", "GW-09", "GW-10",
            "RT-BASELINE", "RT-06", "GT-xx"} <= ids


def test_cli_runs_and_writes_json():
    """脚本 CLI：--json 输出可解析且结构与 build_report 一致。"""
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "acceptance.json"
    proc = subprocess.run(
        [sys.executable, "scripts/acceptance_report.py", "--json", str(tmp)],
        cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(tmp.read_text(encoding="utf-8"))
    assert data["phase0"]["declarable"] is False
    assert data["summary"]["total"] == build_report()["summary"]["total"]