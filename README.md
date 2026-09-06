# Pi 多 Agent 平台 —— 单机 MVP

单机（非分布式）最小可用版本：提交任务后，**Python agent 通过 LLM 推理 + 文件工具在隔离工作区内操作本地文件**，结果与事件全程落库可查。

- 依据：《环境搭建与验证手册 v1.3.2》/《架构与实现蓝图 v1.3.2》（**有意裁剪**，见「与蓝图的关系」）
- 状态：实验配置 MVP，已通过一次真实端到端任务（E2E-001：agent 创建 `hello.txt`/`calc.py`、运行 `python3 calc.py`、写入 `sum.txt=55`）

## 架构

```
用户（Web 127.0.0.1:8990 或 CLI）
  → Task API（FastAPI, POST /api/v1/tasks）
  → pi_tasks 状态机（QUEUED→RUNNING→SUCCESS|FAILED|CANCELLED，PostgreSQL）
  → Worker（线程池 + FOR UPDATE SKIP LOCKED 领取）
  → Runtime：agent 主循环（LLM ↔ tools）
      ├─ gateway：OpenAI 兼容客户端 → cliproxy（127.0.0.1:8317/v1）
      ├─ tools：list_dir/read_file/write_file/edit_file/find_files/grep/run_command/finish
      └─ 安全边界：一切路径 resolve 后必须落在任务工作区（workspaces/task-<id>/）内
  → 事件（pi_events，AGENT_TURN/TOOL_CALL/TOOL_RESULT/...）+ 产物（工作区文件）
```

## 快速开始

前置：S1 基础设施（见 `deploy/`）已运行（PostgreSQL `127.0.0.1:15432` 库 `pi_platform`）；`cliproxy` 可用。

```bash
# 1) 一次性：venv、依赖、数据库迁移
./scripts/run.sh setup
./scripts/run.sh migrate

# 2) 启动服务（Web + API + 后台 worker）
./scripts/run.sh serve 8990
# 本机访问 http://127.0.0.1:8990 （默认仅监听 127.0.0.1；如需 LAN 访问可自定义
# --host，但服务无认证——勿暴露公网/不可信网络）

# 3) 或 CLI 提交任务
./scripts/run.sh submit "在工作区创建 demo.py 并运行，输出写入 out.txt" --title demo
./scripts/run.sh status <task_id>
./scripts/run.sh list
```

## API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/tasks` | 创建任务（body: `{title, prompt, model?}`） |
| GET | `/api/v1/tasks` / `/api/v1/tasks/{id}` | 列表 / 详情 |
| POST | `/api/v1/tasks/{id}/cancel` | 取消（QUEUED/RUNNING） |
| GET | `/api/v1/tasks/{id}/events` | 事件流（traceId、工具调用、结果） |
| GET | `/api/v1/tasks/{id}/workspace`、`.../workspace/file?path=` | 只读浏览工作区产物 |
| GET | `/healthz` | 健康检查 |

## 配置（可环境变量覆盖）

| 变量 | 默认 | 说明 |
|---|---|---|
| `CLIPROXY_MODEL` | `deepseek-v4-flash` | 模型（cliproxy 可用集合，`scripts/probe_llm.py` 可探测） |
| `CLIPROXY_BASE_URL` | `http://127.0.0.1:8317/v1` | LLM 通道 |
| `PI_MAX_TURNS` | 40 | agent 最大轮数 |
| `PI_CMD_TIMEOUT` | 60s | 命令超时 |
| `PI_WORKER_THREADS` | 2 | 并发任务数 |

## 测试

```bash
./scripts/run.sh test   # pytest：状态机、契约/向量、工具安全边界、CAS/证据、Skill 供应链、worker 并发/恢复/竞争（215 项）
```

## 与蓝图的关系（有意简化，供后续演进）

| 蓝图能力 | MVP 状态 | 说明 |
|---|---|---|
| 控制面/Lease/Fencing | 简化 | 状态机白名单 + 领取互斥（SKIP LOCKED）；Lease TTK、撤销、预算未实现 |
| Runtime 工具 | 有 | 文件操作工具集（工作区 root 约束） |
| 沙箱隔离 | 简化 | 目录级 root 约束 + 命令 deny list（特权/系统变更/网络外联/全局包管）+ setuid 拒绝 + 超时/进程组终止 + 最小 env 白名单 + READ_ONLY 只读工具集；**未做**完全断网/namespace/用户隔离 |
| 契约/digest/签名 | 简化 | 8 类对象 Schema+digestprofile+确定性向量+verified（attempt_contract/task_spec/event_envelope/budget_grant/runtime_capability_report/execution_plan_snapshot/attempt_terminal_envelope/skill_bundle_snapshot）；§9.4 其余对象（node_state/lease/route 等）未覆盖去中心生成——json 契约双实现 |
| 事件序列 | 简化（Outbox 未接） | `pi_events` 表 + 轮询 API |
| Artifact/Evidence | 部分 | **本地 CAS**（`data/cas`，sha256 内容寻址，替代 MinIO）+ `attempt_terminal_envelope.v2` 终态信封（Node 来源、CAS 摘要、`pi_artifacts`/`pi_terminal_envelopes` 归档、`GET /api/v1/tasks/{id}/artifacts`、`/terminal-envelopes`）；OutputArtifactManifest/EvidenceManifest/EvaluationVerdict 未实现（G5） |
| Skill 供应链 | 部分 | `skill_bundle_snapshot.v2`（确定性 tar+文件树摘要+只读静态挂载策略）+ 审批链（`/skills/proposals` 双 approver 槽 UNIQUE+veto+quorum+ApprovalSet）+ `/skills/publications:advance` ACTIVE 指针 + worker 注入技能说明；无 CANARY/隔离/Revocation（恢复经新版本新审批） |
| Gateway 预算/Journal | 简化 | 每尝试 BudgetGrant 预留/结算；Journal 未实现 |
| 交付 | 本地工作区 | 产物在 `workspaces/task-<id>/`；Git 交付未接 |
| 观测 | 事件表 + 日志 | 未接 OTel 链路（S1 的 collector 可后续接） |

**风险提示（实验配置）**：`run_command` 在任务工作区内执行命令——已做加固（**无 shell 执行**、命令 deny list、git 外联子命令拒绝、setuid/长度拒绝、最小环境白名单防凭据泄露、root 路径约束、超时/进程组终止、READ_ONLY 步骤只读工具集+服务端授权白名单、默认仅监听 `127.0.0.1`）；但**不可对抗恶意提示词**（可读工作区外绝对路径、可访问本机网络——无 namespace/用户隔离）。不要把本系统暴露到不可信网络，不要提交不可信来源的任务。

## 已知问题

- 本机 curl 默认走代理 `127.0.0.1:7890`，访问本服务需 `--noproxy '*'`（浏览器直连无碍）。
- 8000/8080 等常用端口被本机其他服务占用，默认使用 8990。
- cliproxy 的 codex 认证（`gpt-5.6-luna`）当前不可用（503 auth_unavailable），默认模型已切 deepseek-v4-flash。