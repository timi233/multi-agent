# -*- coding: utf-8 -*-
"""G6 附录 A 硬门槛④：219 正向链路场景审计（allowlist，可复现、防虚高）。

计数与验收报告共用 scripts/acceptance_gates.py（单一来源）：
- 只统计显式白名单文件（POSITIVE_ALLOWLIST：正向链路承载测试）中的用例
  （评审 block-4：新增任意无关测试不得抬高数字）；
- 白名单文件内再剔除可机检对抗标记（neg/raises/reject/tamper/crash…）；
- 断言正向场景数 ≥ 219（手册附录 A 门槛）。
运行：.venv/bin/python -m pytest tests/test_gate_positive_audit.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.acceptance_gates import (  # noqa: E402
    NEGATIVE_MARKERS,
    POSITIVE_ALLOWLIST,
    positive_scenario_count,
)

GATE = 219


def test_positive_scenario_count_at_least_219():
    positive, negative = positive_scenario_count()
    assert positive >= GATE, (
        f"allowlist 正向场景不足: {positive} < {GATE}（负向 {negative}）")
    print(f"\npositive_scenarios={positive} negative={negative}")


def test_allowlist_and_markers_effective():
    """防规则退化：白名单非空、负向标记非空、对抗用例确实被排除。"""
    import json
    assert POSITIVE_ALLOWLIST
    assert NEGATIVE_MARKERS
    _positive, negative = positive_scenario_count()
    assert negative >= 1  # 对抗用例确实存在并被正确归类


def test_positive_nodes_list_has_no_antagonistic_cases():
    """名单自洽（评审 block-4 终评）：固化名单**逐节点**不得混入任何
    负向标记或 MANUAL_REVIEW_DENY 拒绝路径（按函数名判定）。"""
    import json
    from scripts.acceptance_gates import MANUAL_REVIEW_DENY
    nodes = json.loads((ROOT / "contracts" / "acceptance" / "gates"
                        / "positive_nodes.json").read_text(
                            encoding="utf-8"))["nodes"]
    bad = [n for n in nodes
           if any(m in n.split("::")[-1] for m in NEGATIVE_MARKERS)]
    denied = [n for n in nodes
              if n.split("::")[-1].split("[")[0] in MANUAL_REVIEW_DENY]
    assert bad == [], f"名单混入对抗用例: {bad[:5]}"
    assert denied == [], f"名单混入人工审核拒绝路径: {denied[:5]}"