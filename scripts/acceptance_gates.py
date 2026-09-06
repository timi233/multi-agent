# -*- coding: utf-8 -*-
"""G6 附录 A 硬门槛度量（审计/报告共用，单一来源防数字漂移）。

- fault_scenario_count()：固化故障矩阵清单 contracts/acceptance/gates/
  fault_matrix.json（不可缩减输入，见 tests/test_gate_fault_matrix.py）；
- positive_scenario_count()：**allowlist 审计**——只统计显式列入正向链路
  承载名单的测试文件（评审 block-4：新增任意无关测试不得抬高数字），
  文件内再剔除可机检对抗标记（neg/raises/reject 等）；
- run_gates() / load_gate_runs()：三项可运行门禁（fault 矩阵 / 10k 乱序 /
  100 崩溃）由报告 CLI 现场跑对应 pytest 文件生成 run.<gate>.json artifact
  （ok/measured，机器可验证）；报告只读 artifact——缺失 NOT_RUN、失败 FAIL
  （评审 block-6：绝不硬编码 PASS）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATES_DIR = ROOT / "contracts" / "acceptance" / "gates"

NEGATIVE_MARKERS = (
    "neg", "raises", "negative", "reject", "crash", "tamper", "corrupt",
    "reject_", "vs_in_order", "foreign", "evil", "unknown-field",
    "missing-required", "self-digest", "signature.", "pattern.", "minimum.",
    "type.", "const.", "digest.", "invalid", "aborted", "blocked", "deny",
    "escap", "refuse", "forbidden", "broken", "mismatch", "not_found",
    "404", "409", "422", "bogus", "duplicate", "conflict", "exhausted",
    "kill", "stale", "recover", "dup", "unavailable",
    # 评审 block-4 复评补充：明确对抗/拒绝/未知任务语义（防入榜）
    "blocks", "fail_closed", "unknown_", "cancel_", "reject_exhaust",
)

# 正向链路承载文件 allowlist（regen 范围；评审 block-4 复评：计数改为
# **固化节点名单** positive_nodes.json——新增测试需显式 regen 才会入榜，
# 不能靠文件内新命名自动抬高数字）。
POSITIVE_ALLOWLIST = (
    "test_gate_positive.py", "test_gate_out_of_order.py",
    "test_api.py", "test_budget.py", "test_budget_contract.py",
    "test_skills.py", "test_smoke.py", "test_run_state.py",
    "test_sm_model.py", "test_evidence.py", "test_sandbox.py",
    "test_security.py", "test_tools.py", "test_orchestrator.py",
    "test_terminal_envelope_contract.py", "test_execution_plan_contract.py",
    "test_commit_bundle_contract.py", "test_contracts.py",
    "test_reproducibility.py", "test_runtime_capabilities.py",
    "test_gitstager.py",
)

POSITIVE_NODES_FILE = GATES_DIR / "positive_nodes.json"

# 人工审核拒绝名单（评审 block-4 终评：正向链路=成功路径/收敛/一致性/契约正例；
# 以下为人工审核剔除的防御/失败/拒绝/限额/异常处理路径——不论名字是否命中
# NEGATIVE_MARKERS 都不得入榜；新增此类测试亦须在此登记或由 regen 固有过滤捕获）。
MANUAL_REVIEW_DENY = {
    # budget：防御/拒绝/超额
    "test_agent_missing_usage_keeps_reservation",
    "test_chain_detects_reserved_row_deletion",
    "test_chain_detects_truncation",
    "test_reserve_exceeds_budget",
    "test_settle_actual_over_reserve",
    # contracts：边界强制/防御
    "test_integer_safe_range_enforced",
    # evidence：失败信封/快照防护
    "test_ingest_failure_envelope_allows_missing_evidence",
    "test_snapshot_skips_symlink_and_not_regular",
    "test_snapshot_workspace_limits",
    "test_worker_business_failure_ingests_failure_envelope",
    # orchestrator：失败/异常路径
    "test_platform_exception_converges_active_run",
    "test_step_failure_stops_later_steps",
    # run_state：失败映射
    "test_failure_mapping_from_executing",
    # sandbox：防护/限额（非成功路径）
    "test_command_length_limit",
    "test_denied_privileged_and_network_commands",
    "test_empty_and_bad_command",
    "test_git_local_readonly_allowed_network_denied",
    "test_grep_skips_outside_symlinks",
    "test_minimal_env_whitelist",
    "test_no_shell_metacharacter_execution",
    "test_tool_readonly_set_excludes_writers",
    # security：环境清洗/限额/失败隔离（防御性防护）
    "test_fail_isolated_skips_cancelled",
    "test_fail_isolated_unhit_converges_attempt",
    "test_run_command_cleans_env",
    "test_symlink_grep_skips_outside",
    "test_worker_capacity_limits_claim",
    # sm_model：死枚举审计（防御）
    "test_dead_enum_audit_attempt",
    # tools：错误参数/缺失文本/非零退出（失败路径）
    "test_bad_args_json",
    "test_edit_missing_old_text",
    "test_run_command_nonzero",
}

# 可运行门禁：文件 → measured 取值函数
RUNNABLE_GATES = {
    "fault": ("tests/test_gate_fault_matrix.py",
              lambda: fault_scenario_count()),
    "out_of_order": ("tests/test_gate_out_of_order.py", lambda: 10000),
    "crash": ("tests/test_gate_crash_loop.py", lambda: 100),
}


def fault_scenario_count() -> int:
    path = GATES_DIR / "fault_matrix.json"
    if not path.exists():
        return 0
    return int(json.loads(path.read_text(encoding="utf-8"))["scenarioCount"])


def _collected_nodes() -> list[str]:
    """pytest --collect-only 收集全部节点（含参数化展开 id，全名）。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
        cwd=ROOT, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        return []
    nodes = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or "::" not in line or line.startswith("=") or \
                line.startswith("["):
            continue
        nodes.append(line)
    return nodes


def regen_positive_nodes() -> int:
    """显式重新生成正向节点名单（人工审核 + 标记双闸）：
    - 仅 allowlist 文件内、不含可机检对抗标记的节点进入候选；
    - 再剔除 MANUAL_REVIEW_DENY（人工审核判定的防御/失败/拒绝路径）；
    - 结果入库。新增正向测试须显式 regen 才计入。"""
    nodes = _collected_nodes()
    positive = []
    for node in nodes:
        rel = node.split("::")[0]
        if not rel.startswith("tests/"):
            continue
        fname = rel.split("/")[-1]
        if fname not in POSITIVE_ALLOWLIST:
            continue
        case = node.split("::")[-1]
        if any(m in case for m in NEGATIVE_MARKERS):
            continue
        # 人工审核拒绝：参数化 id 可能与函数名并列，按“函数名”判定
        fn = case.split("[")[0]
        if fn in MANUAL_REVIEW_DENY:
            continue
        positive.append(node)
    GATES_DIR.mkdir(parents=True, exist_ok=True)
    POSITIVE_NODES_FILE.write_text(
        json.dumps({"generatedFrom": "scripts/acceptance_gates.regen_positive_nodes",
                    "count": len(positive),
                    "nodes": sorted(set(positive))},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return len(positive)


def positive_scenario_count() -> tuple[int, int]:
    """固化节点名单 ∩ 当前收集 = 正向计数（fail-closed）：
    - 名单节点缺失（已删除或参数 id 漂移）→ 返回负值显式失败；
    - 名单中混入 MANUAL_REVIEW_DENY 节点（手工/错误 regen）→ 返回负值，
      绝不把拒绝路径计入正向。"""
    if not POSITIVE_NODES_FILE.exists():
        return 0, 0
    listed = set(json.loads(POSITIVE_NODES_FILE.read_text(
        encoding="utf-8"))["nodes"])
    collected = set(_collected_nodes())
    missing = listed - collected
    if missing:  # 名单节点已消失：审计不一致，返回负值让调用方显式失败
        return -len(missing), len(listed)
    denied = [n for n in listed
              if n.split("::")[-1].split("[")[0] in MANUAL_REVIEW_DENY]
    if denied:  # 拒绝路径混入名单：fail-closed
        return -len(denied), len(listed)
    return len(listed & collected), len(collected) - len(listed & collected)


def run_gate(gate: str) -> dict:
    """现场跑一个可运行门禁 → 成功写 run.<gate>.json artifact。"""
    test_file, measured_fn = RUNNABLE_GATES[gate]
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", test_file, "-q"],
        cwd=ROOT, capture_output=True, text=True, timeout=600)
    result = {"ok": proc.returncode == 0,
              "measured": measured_fn(), "testFile": test_file,
              "rc": proc.returncode}
    if not result["ok"]:
        result["stderrTail"] = proc.stderr[-400:]
    GATES_DIR.mkdir(parents=True, exist_ok=True)
    (GATES_DIR / f"run.{gate}.json").write_text(
        json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def load_gate_run(gate: str) -> dict | None:
    path = GATES_DIR / f"run.{gate}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))