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


def test_expected_status_map():
    """逐 ID 机器可验证门槛映射（评审 should-fix：精确绑定防漂移）——子集实现
    不得升格为完整手册条目 PASS。"""
    expected = {
        "CT-01": "PASS", "CT-02": "PASS", "CT-03": "PASS", "CT-04": "PARTIAL",
        "CT-08": "PASS", "SM-xx": "PARTIAL",
        "GW-03": "PARTIAL", "GW-04": "PASS", "GW-06": "PASS", "GW-07": "PASS",
        "GW-09": "PARTIAL",
        "GW-01": "NOT_IMPLEMENTED", "GW-02": "NOT_IMPLEMENTED",
        "GW-05": "NOT_IMPLEMENTED", "GW-08": "NOT_IMPLEMENTED",
        "GW-10": "NOT_IMPLEMENTED",
        "RT-BASELINE": "PASS",
        "RT-01": "NOT_IMPLEMENTED", "RT-02": "NOT_IMPLEMENTED",
        "RT-03": "NOT_IMPLEMENTED", "RT-04": "NOT_IMPLEMENTED",
        "RT-05": "NOT_IMPLEMENTED", "RT-06": "PARTIAL",
        "GT-xx": "NOT_IMPLEMENTED",
    }
    by_id = {e["id"]: e["status"] for e in build_report()["entries"]}
    for cid, want in expected.items():
        assert by_id.get(cid) == want, f"{cid}: 期望 {want} 实际 {by_id.get(cid)}"


def test_evidence_paths_exist():
    """证据路径必须真实存在（评审 should-fix：机器可验证，不依赖文字）。"""
    import re

    token_re = re.compile(r"(tests|scripts|app|contracts)/[A-Za-z0-9_./\-*]+")
    for e in build_report()["entries"]:
        if not e["status"] == "PASS":
            continue
        for ev in e["evidence"]:
            hits = []
            if ev.startswith("GET /"):
                # API 端点证据：host 模块存在 + 路由文本存在（兼容 prefix 拆分）（评审 nit）
                api_txt = (ROOT / "app" / "control" / "api.py").read_text(
                    encoding="utf-8")
                route = ev.replace("GET ", "").split("?")[0].strip("（）() ")
                rel = route
                if route.startswith("/api/v1"):
                    rel = route[len("/api/v1"):]
                assert (ROOT / "app" / "control" / "api.py").exists(), \
                    f"{e['id']} 缺失 host 模块 app/control/api.py"
                assert rel in api_txt, f"{e['id']} 路由未见: {route}"
                hits.append(f"GET {route}")
                continue
            for m in token_re.finditer(ev):
                tok = m.group(0).split("（")[0].rstrip(".")
                if "*" in tok:  # glob：校验前缀目录存在
                    base = tok.split("*")[0]
                    assert (ROOT / base).exists(), \
                        f"{e['id']} 证据 glob 前缀不存在: {base}"
                    hits.append(base)
                else:  # 具体文件：必须完整存在（评审 nit：不回退父目录）
                    assert (ROOT / tok).exists(), \
                        f"{e['id']} 证据路径不存在: {tok}"
                    hits.append(tok)
            assert hits, f"{e['id']} 证据无真实路径: {ev}"


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