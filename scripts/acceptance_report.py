# -*- coding: utf-8 -*-
"""验收基准报告（③ 验收基准与故障注入）：按蓝图/手册验收条目生成结构化对照。

输出 JSON（默认 stdout；--json 覆盖输出到文件）：每条目含 id / label /
phase / status（PASS|PARTIAL|NOT_IMPLEMENTED）/ evidence（测试或脚本名）/ note。
如实声明：本平台为"单机分节点简化 MVP"（用户确认边界），Phase 0 正式门槛
（手册 §18.2：GT + RT-01~06 全 PASS）尚未达到——report.phase0.declarable 为
false 并给出缺失项；绝不虚报。

运行：.venv/bin/python scripts/acceptance_report.py [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.acceptance_gates import (  # noqa: E402,F401
    fault_scenario_count,
    load_gate_run,
    positive_scenario_count,
)

ENTRIES: list[dict] = [
    # ---------- 契约层（Phase 0）----------
    {"id": "CT-01", "label": "canonicalPayload 主实现与独立参考实现逐字节一致",
     "phase": "Phase 0", "status": "PASS",
     "evidence": ["scripts/verify_vectors_node.js（Python vs Node，15 正向量 0 不一致）",
                  "tests/test_reproducibility.py（generator tmp 重跑逐字节一致）"],
     "note": "双实现精神达成（Python 主 + Node 参考）；蓝图原句为 Go 实现，本平台以 Node 独立实现替代（简化披露）"},
    {"id": "CT-02", "label": "Schema 正/负向量完整且可复现",
     "phase": "Phase 0", "status": "PASS",
     "evidence": ["contracts/test-vectors/*（attempt/task_spec/event/budget_grant）",
                  "tests/test_contracts.py / test_protocol_vectors.py / test_budget_contract.py"],
     "note": ""},
    {"id": "CT-03", "label": "canonical 排序（集合数组先排序/拒绝重复；有序语义互斥）",
     "phase": "Phase 0", "status": "PASS",
     "evidence": ["tests/test_ct03_canonical.py", "tests/test_reproducibility.py（orderedArrays 互斥）"],
     "note": "兼容性边界见 README（schemaVersion=2 下既有 digest 失效，已重生成向量）"},
    {"id": "CT-04", "label": "必须签名对象正/反向量 + 真实签名可验签",
     "phase": "Phase 0", "status": "PARTIAL",
     "evidence": ["scripts/gen_signature_vectors.py", "scripts/gen_budget_vectors.py（pos-signed 真实 Ed25519）",
                  "scripts/gen_execution_plan_vectors.py（pos-signed 真实 Ed25519）",
                  "scripts/gen_terminal_envelope_vectors.py（pos-signed 真实 Ed25519）",
                  "scripts/gen_skill_bundle_vectors.py（pos-signed 真实 Ed25519）",
                  "tests/test_runtime_capabilities.py / test_budget_contract.py / test_execution_plan_contract.py / test_terminal_envelope_contract.py / test_skills.py（验签真/伪）"],
     "note": "已覆盖 8 类对象（attempt_contract/task_spec/event_envelope/budget_grant/runtime_capability_report/execution_plan_snapshot/attempt_terminal_envelope/skill_bundle_snapshot）；"
             "蓝图 §9.4 完整清单（node_state/execution_lease/route_attestation/commit_intent 等）未覆盖——如实 PARTIAL"},
    {"id": "CT-08", "label": "事件信封 if/then 约束（Attempt/Run/Artifact/Evidence/Budget vs Task）",
     "phase": "Phase 0", "status": "PASS",
     "evidence": ["scripts/gen_protocol_vectors.py（event 8 向量）", "tests/test_protocol_vectors.py"],
     "note": ""},
    # ---------- 状态机 ----------
    {"id": "SM-xx", "label": "状态机白名单 + 跨事务收敛/防悬挂",
     "phase": "Phase 0", "status": "PARTIAL",
     "evidence": ["tests/test_sm_model.py（11 项）", "tests/test_security.py（cancel/abort 收敛）",
                  "tests/test_run_state.py（Run 白名单/db 推进）", "tests/test_orchestrator.py（G1b 编排）"],
     "note": "Task/Attempt/Run 白名单与跨事务检查已覆盖：Run 主行进路径 CREATED→READY→EXECUTING→OUTPUT_STAGED→VERIFYING→VERIFIED + FAILED/BUDGET_EXHAUSTED/CANCELLED（G1b 落地，pi_runs + api /tasks/{id}/runs）；"
             "SM-01 剩余缺口为 CandidateStagingOperation 对象（随 G5 Git 侧）；"
             "QUEUED->RUNNING 跨事务事件仍为 SM-08 warnings（count_warnings==1 锁定）——如实 PARTIAL"},
    # ---------- Gateway 预算 ----------
    {"id": "GW-03", "label": "调用前 RESERVED/SENT 持久化预留；崩溃不恢复余额",
     "phase": "Phase 1", "status": "PARTIAL",
     "evidence": ["tests/test_budget.py", "tests/test_fault_injection.py（crash-after-reserve / sent-after-kill）"],
     "note": "持久化预留与占额保留已验；kill 为**同进程崩溃语义模拟**（commit 后不再操作 + 恢复路径），"
             "非真实 kill/restart 进程注入；DISPATCH_INTENT 独立阶段与无盲目重发未系统验证——如实 PARTIAL"},
    {"id": "GW-04", "label": "Journal 链式 + 计数锚点 + 对账（删行/篡改检出）",
     "phase": "Phase 1", "status": "PASS",
     "evidence": ["tests/test_budget.py（reconcile/verify_chain/tamper/truncation）",
                  "tests/test_budget_contract.py（verified_budget_grant）"],
     "note": "威胁边界：非协同损坏；恶意协同篡改不在模型内（README 披露）"},
    {"id": "GW-06", "label": "同 invocationId 不同 requestDigest 拒绝",
     "phase": "Phase 1", "status": "PASS",
     "evidence": ["tests/test_budget.py::test_duplicate_invocation_digest_mismatch"],
     "note": ""},
    {"id": "GW-07", "label": "预算上限 100% 阻断 + BUDGET_EXHAUSTED 结构化事件",
     "phase": "Phase 1", "status": "PASS",
     "evidence": ["tests/test_budget.py（0 预算 E2E 不触达 LLM）"],
     "note": ""},
    {"id": "GW-09", "label": "每次调用生成 RouteAttestation（覆盖 100%）",
     "phase": "Phase 1", "status": "PARTIAL",
     "evidence": ["app/runtime/budget.py（每物理请求独立 invocation + Journal 事实）", "tests/test_budget.py"],
     "note": "Journal 事实（invocation/usage）已生成，但 RouteAttestation 完整契约证明未实现——"
             "不可替代，如实 PARTIAL"},
    {"id": "GW-01", "label": "非 Node Proxy 身份调用 InvokeModel 拒绝",
     "phase": "Phase 1", "status": "NOT_IMPLEMENTED",
     "evidence": [], "note": "身份绑定未实现（简化差距）"},
    {"id": "GW-02", "label": "别名解析后路由不一致停止调用",
     "phase": "Phase 1", "status": "NOT_IMPLEMENTED",
     "evidence": [], "note": "单一 cliproxy 路由、无多 provider 别名"},
    {"id": "GW-05", "label": "Gateway 代次排他（BUDGET_GRANT_OWNER_MISMATCH）",
     "phase": "Phase 1", "status": "NOT_IMPLEMENTED",
     "evidence": [], "note": "单实例单代次"},
    {"id": "GW-08", "label": "撤销 checkpoint 新鲜度",
     "phase": "Phase 1", "status": "NOT_IMPLEMENTED",
     "evidence": [], "note": ""},
    {"id": "GW-10", "label": "热路径不查询 PostgreSQL",
     "phase": "Phase 1", "status": "NOT_IMPLEMENTED",
     "evidence": [], "note": "每次预算操作一次 PG 往返（单实例可接受，README 披露）"},
    # ---------- Runtime ----------
    {"id": "RT-BASELINE", "label": "Runtime 能力报告（签名可验证事实基线 + API）",
     "phase": "Phase 0", "status": "PASS",
     "evidence": ["app/runtime/capabilities.py", "GET /api/v1/runtime/capabilities",
                  "tests/test_runtime_capabilities.py（真签名/缓存/contractId 语义）"],
     "note": ""},
    {"id": "RT-01", "label": "无 TCP/UDS/DNS/继承网络 FD 的沙箱启动",
     "phase": "Phase 0", "status": "NOT_IMPLEMENTED",
     "evidence": [], "note": "无沙箱（目录级限额）；以 RT-04 knownGap 披露"},
    {"id": "RT-02", "label": "provider 调用映射为 ModelCallIntent（流式/压缩/重试全经管道）",
     "phase": "Phase 0", "status": "NOT_IMPLEMENTED",
     "evidence": [], "note": "直连 OpenAI 兼容 HTTP"},
    {"id": "RT-03", "label": "畸形/超大/乱序协议稳定 NO_VERDICT",
     "phase": "Phase 0", "status": "NOT_IMPLEMENTED",
     "evidence": [], "note": ""},
    {"id": "RT-04", "label": "仓库工具子进程控制管道/ptrace/proc 旁路全部阻止",
     "phase": "Phase 0", "status": "PARTIAL",
     "evidence": ["tests/test_security.py（路径/symlink/环境清洗防护）", "tests/test_sandbox.py（G2 沙箱硬化）"],
     "note": "G2 已落地：命令 deny list（特权/系统变更/全局包管理/网络外联客户端）+ setuid 拒绝 + 命令长度上限 + 超时整组终止 + 最小环境白名单 + READ_ONLY 只读工具集；"
             "沙箱级进程/网络隔离（netns/cgroup）未实现（networkEnabledForTools=true、networkIsolation=none-host-network 如实）——PARTIAL 而非 PASS"},
    {"id": "RT-05", "label": "恢复/消息/计划任务/扩展发现/Skill 装载禁用边界",
     "phase": "Phase 0", "status": "NOT_IMPLEMENTED",
     "evidence": [], "note": "相关设施不存在即不加载"},
    {"id": "RT-06", "label": "真实模型调用→工具→调用→完成的完整链路证据（含用量）",
     "phase": "Phase 0", "status": "PARTIAL",
     "evidence": ["E2E 冒烟（真实 LLM 链路可跑）", "预算 Journal 含 usage 结算"],
     "note": "RouteAttestation 证据形态未达"},
    # ---------- Git / 供应链 ----------
    {"id": "GT-xx", "label": "Git Provider 能力报告 / CAS 读回",
     "phase": "Phase 0", "status": "NOT_IMPLEMENTED",
     "evidence": [], "note": "无 Git Provider（用户简化边界：单机本地文件操作）"},
]

PHASE0_MISSING = [e["id"] for e in ENTRIES
                  if e["phase"] == "Phase 0" and e["status"] != "PASS"]


def _gate_status(gate: str) -> dict:
    """机器可验证（评审 block-6）：只信运行 artifact——缺失 NOT_RUN、失败 FAIL。"""
    run = load_gate_run(gate)
    if run is None:
        return {"measured": None, "status": "NOT_RUN",
                "evidence": [f"contracts/acceptance/gates/run.{gate}.json（缺失）"],
                "note": "运行 artifact 缺失：以 scripts/acceptance_report.py --gates auto 生成（现场跑对应 pytest 文件）。"}
    if not run["ok"]:
        return {"measured": run["measured"], "status": "FAIL",
                "evidence": [f"contracts/acceptance/gates/run.{gate}.json",
                             run.get("testFile", "")],
                "note": f"运行失败 rc={run['rc']}：" + (run.get("stderrTail", "")[-200:])}
    return {"measured": run["measured"], "status": "PASS",
            "evidence": [f"contracts/acceptance/gates/run.{gate}.json",
                         run.get("testFile", "")],
            "note": f"现场运行通过（pytest rc=0），measured={run['measured']}。"}


def build_report(gates_mode: str = "cache") -> dict:
    phase0_missing = sorted(set(PHASE0_MISSING))
    positive, _negative = positive_scenario_count()
    faults = fault_scenario_count()
    g_fault = _gate_status("fault")
    g_o3 = _gate_status("out_of_order")
    g_crash = _gate_status("crash")
    return {
        "report": "pi-platform acceptance (③ 验收基准)",
        "generatedAt": None,  # 复现性：不含时间戳（由调用方决定是否覆盖）
        "phase": {"0": "CONTRACT_VALIDATED（正式门槛）", "1": "PROTOTYPE_VERIFIED（受控 PoC）"},
        "phase0": {
            "declarable": not phase0_missing,
            "missing": phase0_missing,
            "note": "手册 §18.2：Phase 0 全 PASS（GT+RT-01~06）才可声明 CONTRACT_VALIDATED；"
                    "当前如实标注：未达（GT 未实现、RT-01/02/03/04/05 未达、RT-06 部分）。"
                    "平台实际可运行（E2E 冒烟、真实 LLM 链路），但正式门槛未越。",
        },
        "hardGates": {
            "note": "附录 A 单机可行子集硬指标（G6）。八小时并发/七天稳定性为时长类指标："
                    "提供可运行脚本（scripts/acceptance_longrun.py --hours 8 / --days 7，"
                    "真实 8h/7d 由 CI 或使用者按需执行），本轮实测短时长冒烟；其余四项为"
                    "自动化断言并给出实测数字。任何指标未达即不得称 100%（如实）。",
            "gates": {
                "faultScenarios": {
                    "target": 200, "measured": g_fault["measured"],
                    "status": g_fault["status"],
                    "evidence": g_fault["evidence"],
                    "note": g_fault["note"]},
                "outOfOrderEvents": {
                    "target": 10000, "measured": g_o3["measured"],
                    "status": g_o3["status"],
                    "evidence": g_o3["evidence"],
                    "note": g_o3["note"]},
                "crashCycles": {
                    "target": 100, "measured": g_crash["measured"],
                    "status": g_crash["status"],
                    "evidence": g_crash["evidence"],
                    "note": g_crash["note"]},
                "positiveScenarios": {
                    "target": 219, "measured": positive,
                    "status": "PASS" if positive >= 219 else "FAIL",
                    "evidence": ["tests/test_gate_positive_audit.py（allowlist 审计）",
                                 "tests/test_gate_positive.py（正向链路批量用例）"],
                    "note": f"allowlist 正向链路场景计数 {positive}（≥219）；"
                            "仅统计显式白名单文件，新增无关测试不抬高数字。"},
                "concurrentLongrun": {
                    "target": "8h 并发稳定性（可运行）",
                    "measured": "脚本就绪（scripts/acceptance_longrun.py --hours 8）；"
                                "每次运行的实测（轮数/交付/事件收敛/健康检查/ok）"
                                "见当次运行 JSON，报告不固化陈旧数字",
                    "status": "SCRIPT_READY",
                    "evidence": ["scripts/acceptance_longrun.py（并发 stage_commit+"
                                 "幂等复用+读回强校验+事件乱序重放+健康检查"
                                 "（计数单调/归档==ref/cat-file），输出 JSON 退出码即断言）"],
                    "note": "真实 8h 由执行方按 CI/排程运行（长时指标如实；"
                            "归档存在但 repo 缺失视为健康检查失败）。"},
                "stability7d": {
                    "target": "7d 连续稳定（可运行）",
                    "measured": "脚本就绪（--days 7）；本机未实测满 7 天",
                    "status": "SCRIPT_READY",
                    "evidence": ["scripts/acceptance_longrun.py（--days 7）"],
                    "note": "泄露/漂移面：每个 task 独立 deliveries repo + DB 计数"
                            "单调一致由健康检查断言；长时间实测需挂机，如实披露。"},
            },
        },
        "entries": ENTRIES,
        "summary": {
            "total": len(ENTRIES),
            "pass": sum(1 for e in ENTRIES if e["status"] == "PASS"),
            "partial": sum(1 for e in ENTRIES if e["status"] == "PARTIAL"),
            "not_implemented": sum(1 for e in ENTRIES if e["status"] == "NOT_IMPLEMENTED"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Pi 平台验收基准报告")
    parser.add_argument("--json", metavar="FILE", default=None,
                        help="写入 JSON 文件（默认 stdout）")
    parser.add_argument("--gates", choices=("auto", "cache"), default="auto",
                        help="auto：三项可运行门禁（故障矩阵/10k 乱序/100 崩溃）"
                             "artifact 缺失或失败时现场重跑生成（约 1 分钟）；"
                             "cache：只读已固化的 run.<gate>.json，缺失标记 NOT_RUN")
    args = parser.parse_args()
    if args.gates == "auto":
        from scripts.acceptance_gates import run_gate
        # 评审 block-6 复评：auto 必须**始终现场运行**（不比对旧 artifact），
        # 防止代码回归后陈旧 PASS 被永久复用；artifact 每次都由本次运行覆盖。
        for gate in ("fault", "out_of_order", "crash"):
            print(f"[gates] 运行 {gate} ...", flush=True)
            run_gate(gate)
    report = build_report(gates_mode=args.gates)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        print(f"验收报告 -> {out}")
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    s = report["summary"]
    print(f"\n汇总：{s['total']} 条目 = PASS {s['pass']} / PARTIAL {s['partial']} "
          f"/ NOT_IMPLEMENTED {s['not_implemented']}")
    print(f"Phase 0 可声明 CONTRACT_VALIDATED: "
          f"{'是' if report['phase0']['declarable'] else '否'} "
          f"（缺失: {report['phase0']['missing']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())