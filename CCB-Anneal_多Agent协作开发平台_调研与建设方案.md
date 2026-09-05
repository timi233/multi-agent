# CCB × Anneal 深度调研与多 Agent 协作开发平台建设方案

> 整理日期:2026-09-01
> 调研对象(两个 GitHub 开源项目):
> - **CCB** — https://github.com/SeemSeam/claude_codex_bridge (v8.6.10,AGPL-3.0)
> - **Anneal** — https://github.com/mosonlab/anneal (v0.6.0,MIT)
> 调研方式:GitHub API 全量文件树 + 40+ 关键源码文件逐行研读(已临时缓存后清理);
> 所有结论标注「源码已验证」,部分细节标注「文档声明/合理推断」。

---

## 目录

1. [Anneal 深度调研](#1-anneal-深度调研)
2. [CCB 深度调研](#2-ccb-深度调研)
3. [两者结合分析(能力矩阵 · 架构 · 路线)](#3-两者结合分析)
4. [分布式部署拓扑:多设备运行方案](#4-分布式部署拓扑)
5. [开发用机硬件需求与推荐](#5-开发用机硬件需求与推荐)
6. [脱敏与审核本地模型方案](#6-脱敏与审核本地模型方案)
7. [平台测试策略(正式使用后的测试环节)](#7-平台测试策略)
8. [外部研读:Google Antigravity Teamwork 多 Agent 研究框架](#8-外部研读google-antigravity-teamwork-多-agent-研究框架)
9. [附录:调研文件索引](#9-附录调研文件索引)

---

## 1. Anneal 深度调研

### 1.1 定位

**无人值守的多 Agent 开发流水线协调器(黑灯工厂)。** 用户只写 spec、排任务卡片,它用你自己的 Codex CLI / Claude Code / Pi 订阅额度,把每张卡片跑完 12 步 Full Assurance 链(plan → 评审 → 实现 → 双盲 review → 修复 → 回归验证 → 合入),并在链尾对每个 commit 跑仓库自带验证门控(merge gate)。

实现形态:npm workspaces monorepo,核心三进程 + 两个外部组件:

```
┌─────────────┐    ┌───────────────────┐    ┌──────────────────────┐
│ apps/web    │    │ packages/api      │    │ packages/runner      │
│ React+Vite  │───▶│ Hono @127.0.0.1   │◀───│ 本地轮询循环          │
│ 看板 UI     │    │ :3000(控制面)     │    │ :认领→跑 Agent CLI     │
└─────────────┘    └─────────┬─────────┘    └──────────┬───────────┘
                             │ Prisma                 │ 子进程
                     ┌───────▼────────┐      ┌─────────▼──────────┐
                     │ PostgreSQL 16   │      │ Codex / Claude / Pi│
                     │ 库名 agentos    │      │ (你已有的订阅)     │
                     └────────────────┘      └────────────────────┘
   另有:@anneal/merge-executor(独立进程/独立 OS 主体,机械合并)
        gate worker 服务器(SSH 触发,跑 merge-gate.sh 验证)
```

### 1.2 启动流程(源码验证:`packages/api/src/index.ts`、`packages/runner/src/index.ts`)

**API 控制面**(`npm run dev:api`)严格顺序:
1. `loadStartupConfig()` — 配置错误直接拒绝(exit 78)
2. `acquireControlPlaneOwnership()` — **单实例所有权**,基于 `CONTROL_PLANE_STATE_DIR` 文件锁
3. `holdSharedServiceMaintenanceLock()` — 数据库**共享维护锁**(迁移工具持排它锁时 API 拒绝启动,exit 75)
4. `reconcileAtStartup` — 对账遗留 run 状态
5. `serve(Hono app)` — 挂载 10 组路由(tasks/agents/templates/goals/inbox/runner/session/system/admin/support)
6. 启动 4 个后台 worker:scheduler、merge-evidence-worker、merge-readiness-worker、merge-base-drift-worker

**本地 runner**(`npm run dev:runner`):
- 强制 `RUNNER_TOKEN`(禁止复用 operator 凭据);持共享维护锁(不直连数据库)
- `runStartupPreflight`:探测 codex/claude/pi 二进制,真实调用 --version/--help/auth status
- 主循环:`while(!stopping) { sweepWorkspaces(); pollForTask(); sleep }`

### 1.3 核心运行链路(源码验证)

**认领**(`packages/api/src/run-claim.ts`):
- Runner `POST /runner/tasks/claim`,API 先 reconcileDatabaseRuns
- **CAS 认领**:`update where { id, status: QUEUED, leaseGeneration } set { leaseGeneration+1, fencingToken }`
- 认领时快照 PR 决策与模板 Step 契约(之后任务的 PATCH 不影响已排队 run)
- 队列按 `blockedByRunId` 依赖解锁;`dedupeKey` 唯一约束幂等

**执行**(`packages/runner/src/runner.ts` 的 `executeClaim`):
```
机械 run(merge-executor 专属)→ 拒绝执行(protocol run 只给 mechanical)
预算 gate(maxRunsPerTask 超限 → BUDGET_EXCEEDED)
provisionWorkspace / reuseWorkspace(断点续跑)
buildPrompt → provisionAgentScratch(0600 会话配置)
adapter.preflight → runLease.launch(启动 CLI 子进程)
─ EXECUTE:每心跳 evaluateBudget(maxDuration=240min/maxRuns=5/stallTimeout=10min)
─ agent 退出 → 输出修复协议(该步 spec 要求 task_output 但未持久化时自动 resume 修复)
─ DELIVER:captureWorkspaceResult → git push --set-upstream origin <branch>
          → 按 opensPullRequest 决策开 PR 或写 delivery 指令
→ completeRun(附 failureEnvelope 结构化失败账本 + output 尾部 500k)
```

**Prompt 组装**(`adapters.ts buildPrompt`):foundationalPrompt + rolePrompt + 工具清单 + 平台钉死的工作区约束 + pullRequestBase 权威声明 + 前步 handoff + operator notes + 任务描述。"重写起始 commit/强推"被显式声明为 workflow error。

**Provider 适配**(`adapters/{codex,claude,pi}.ts`):
- Claude:`claude -p --dangerously-skip-permissions --output-format stream-json --strict-mcp-config --model <固定模型>`
- 三者统一挂 agentos MCP 工具集(session-tool-contract.ts):task_output/task_status/task_activity_log/inbox_ask(挂起会话等人工)/files_*,鉴权用 sessionToken + fencingToken

### 1.4 12 步模板链(源码验证:`agents/templates/compound-engineer-workflow/`)

模板是带 frontmatter 的 markdown 数据(stepIndex/layer/agent/outputKind/priorOutputKinds/attachmentsFromPrevious/opensPullRequest/…)。关键隔离:

| 步 | 角色/模型 | priorOutputKinds | 机制要点 |
|---|---|---|---|
| 05 实现 | implementation-plan-executioner | [revised-plan] | `.chain/<branch>/slices/` 切片,8 并发原生子代理(Luna max),每 writer 独立分支+worktree;发布/PR 交给平台 |
| 06 Sol 评审 | review-coordinator-sol(gpt-5.6-sol:xhigh) | [implementation] | 双轴:Standards(Fowler 12 项 code smell)+ Spec(逐条引用),P0/P1/P2 |
| 07 Opus 盲审 | review-coordinator-opus(claude-opus-5) | **[]** | detached checkout + priorOutputKinds=[] + prompt 禁止读前序证据 → **双盲** |
| 08 修复 | senior-dev | [sol-findings, blind-findings] | 无仲裁 Agent,自行逐条裁定 ADOPTED/REJECTED/MERGED;P0/P1 拒绝须给出不可达论证 |
| 10 回归验证 | regression-verifier | 全部 | 机械操作由平台脚本 scripts/regression-verification.sh 拥有 |
| 12 合入 | **mechanical**(非 LLM) | [] | sentinel 角色 merge-integrator,由 @anneal/merge-executor 独立进程直接 GitHub REST/GraphQL,剥离 .chain/ 后合并 |

### 1.5 Merge Gate(独立 worker 服务器,`scripts/gate-worker/`)

- 退出码即裁决:`0 PASS / 1 FAIL / 2 usage / 3 NOT AUTHORITATIVE / 76 no-verdict`
- 验证内容:preflight pin → 依赖安装 → fixtures → parallel_steps 并行波(biome lint + type-aware no-floating-promises、各 workspace unit tests 多 lane)
- step-engine.sh:外部信号停止(129/130/131/137/143)→ no-verdict;SIGSEGV 自崩溃 → FAIL
- merge-executor(decision-table.ts):authorization 活动不可晚于 run 开始、PR 快照 required checks、二次 readChain 防 supersession、merge 后 readback;取消检测 fail-closed,安装令牌 mint + redaction

### 1.6 数据模型(schema.prisma,33 model + 24 enum)

- Run:fencingToken/leaseGeneration/cancelRequestId/pushedBranch/baseSha/headSha/pushStatus/failureEnvelope/output/retryOfRunId(@unique,single-flight)
- Task:chainId/chainLayer(maxDurationMin=240/stallTimeoutMin=10/maxSessionsPerTask=5/spendCap/approvalGate/opensPullRequest)
- SessionExecutionStatus / ChainControlState / MergeGateAttestation / MergeLeaseEvent / MergeRecoveryAttempt

### 1.7 并发、幂等与错误恢复

- fencing generation:每次认领 leaseGeneration+1 + 新 fencingToken,过期租约写入被 401/409 拒绝
- 取消:cancelRequestId durable intent → runner 心跳观察 → 杀进程组 → ACK;intent 未清前所有租约写被 fence
- workspace 回收:控制面只发布 workspaceReclaimAt **intent**,删除只属于 runner
- 失败账本 failureEnvelope:phase + exit facts + 判定类;retryOfRunId single-flight
- 输出修复协议:缺失输出时 resume 同一会话注入修复指令;修复改了 HEAD/tree → 禁止发布

### 1.8 安全与限界

- 四类主体路径隔离:operator 禁 /runner/、/session/;runner 仅 /runner/;merge-executor 仅 /runner/;session 仅自己的 /session/runs/<runId>/
- MERGE_EXECUTOR_TOKEN 必须 ≠ RUNNER/OPERATOR token
- secrets AES-256-GCM 加密落库;per-run 凭据 0600;repo remote 禁带凭据
- **官方明确不是沙箱**;当前仅 Apple Silicon Mac(Node ^20.19 || ^22.13 || >=24)

---

## 2. CCB 深度调研

### 2.1 定位

**可见、可接管的多 Agent CLI 工作台(透明车间)。** 让 Codex、Claude、Gemini 等 17+ CLI agent 家族在 tmux/WezTerm/Herdr 的 pane 里原生可见地协作,支持跨 provider 稳定委派(/ask)、后台 daemon 保活、热配置、手机远程控制。

- 版本 v8.6.10,AGPL-3.0,npm 包 @seemseam/ccb,bin 暴露 ccb/ask/autonew/ctx-transfer
- 平台:Linux/macOS/WSL 正式 + **Windows x64 beta(需 WezTerm + Git Bash + Herdr ≥0.8.0)**
- 镜像:3463★ / 341 fork / 48MB / Python 为主(daemon)+ Node(npm 入口)+ Flutter(mobile)

### 2.2 进程拓扑与启动链(源码验证)

```
你的终端
  └─ ccb.py(Python 主入口,bin/ccb.js 是 node stub → ccb-npm-runner)
       ├─ 平台探测:lib/platforms/windows/os_platform.py(Windows 强制 Herdr)
       └─ run_cli_entrypoint() → lib/cli/(TUI:parser/router/services)
            ├─ ProjectKeeper(ccbd/keeper_main.py)  守护 keeper
            └─ CcbdApp(ccbd/main.py → app.py → app_runtime/)  daemon
                 ├─ serve_forever(poll_interval)
                 ├─ JobDispatcher(ccbd/services/dispatcher.py)
                 ├─ MessageBureau(message_bureau/)
                 └─ 每 agent 一个 provider bridge 进程(FIFO 循环)
```

- daemon 与 UI 分离:关 TUI 项目仍活;startup/shutdown report 记录
- 配置:.ccb/ccb.config TOML v2,`[windows] main="main:codex"` 定义窗口/分栏;ccbd/reload_* 十余个阶段守卫式热加载

### 2.3 /ask 委派实现(源码验证)

一次 `/ask reviewer ...` 的完整链路:
```
1. lib/cli/services/ask.py → ask_runtime/submission.py submit_ask
2. MessageBureauFacade 写 MessageRecord:
   { message_id, origin_message_id(链式), from_actor, target_agents,
     message_class='task_request', reply_policy, retry_policy, priority=100,
     payload_ref(长文本自动 spill 成 artifact), message_state }
3. JobDispatcher:submit_jobs → tick_jobs → AttemptRecord
   { attempt_id, message_id, agent_name, provider, job_id,
     retry_index, health_snapshot_ref, attempt_state }
4. 经目标 agent 的 provider bridge → tmux/herdr pane 原生输入
5. ReplyRecord { reply_id, terminal_status, reply, reply_artifact, diagnostics }
```
- 三级状态机 MessageState/AttemptState/ReplyTerminalStatus + retry_lineage + restore_running_jobs/repair_callback_edges
- route_options:chain 模式(callback)、allowed_chain_targets(限链式委派目标)、bind_chain_workspace_tree
- dispatch 时允许 `--chain`(真正依赖)与独立 ask 分离,不误建 callback 链

### 2.4 完成判定:多探测器服务(源码验证:`lib/completion/detectors/`)

5 个探测器组合判定任务完成,不用脆弱的文本启发:
- anchored_session_stability:回复指纹 + tool_active + 稳定窗 2s → COMPLETED;tool 活跃时拒绝判完成
- protocol_turn / session_boundary / structured_result / terminal_text_quiet
- 结论:error→FAILED、pane_dead→FAILED(DEGRADED)、cancel→CANCELLED

### 2.5 Provider 接入(源码验证)

- 统一层 lib/provider_core/:registry(ProviderCatalog)+ protocol + contracts + fifo_delivery + one_way_inheritance + projected_assets/settings(单向资产投射)
- lib/provider_backends/ 22 个目录:claude(166)、codex(157)、gemini(99)、opencode(57)、droid(69)、agy、copilot、cursor、crush、deepseek、**dsh(DeepSeek Harness,HTTP/WebSocket)**、grok、kimi、kiro、mimo、omp、pi、qoder、qoderclicn、qwen、zai、codebuddy、native_cli_support
- 交互式 CLI 类进原生 pane;service-backed(DSH 等)保持显式托管表面
- **Codex 桥样例**(DualBridge):FIFO 输入(CODEX_TMUX_SESSION 校验)→ req_id/BEGIN/DONE 锚定协议 → ManagedCodexAppServer(HTTP,reconnect/restore)→ binding_tracker;异常指数退避、SIGTERM 优雅停

### 2.6 移动端与安全

- mobile/app(Flutter)+ lib/mobile_gateway/:loopback 127.0.0.1:8787 默认;LAN 必须显式绑定具体内网 IP(wildcard/公网拒绝);远端走 Tailscale Serve(非 Funnel);配对 scope(view/content/terminal/file)
- 凭据:keyring + provider-private 边界;v8.6.10 做 Claude OAuth re-login 凭据刷新、符号链接投影 fail-closed

### 2.7 存储布局(lib/storage/paths.py PathLayout)

- 项目锚点 .ccb/,runtime_state_root 带 marker + ref 双向校验
- ccb_memory.md 项目级共享记忆;provider 缓存三区隔离 shared/external/user
- daemon socket 在 .ccb/ccbd/*.sock;jobs/mailbox/events 独立 store

---

## 3. 两者结合分析

### 3.1 定位对照

| | CCB(v8.6.10,Python daemon) | Anneal(v0.6.0,TypeScript) |
|---|---|---|
| 哲学 | 透明车间:全程可见、人随时接管 | 黑灯工厂:无人值守、自动合入 |
| 权威状态 | daemon + .ccb/ 文件 store | PostgreSQL(Prisma)唯一权威 |
| 委派 | /ask → Message/Attempt/Reply 三级账本 | Task/Run/Session 表 + blockedBy 依赖链 |
| 执行载体 | tmux/WezTerm/Herdr pane + per-provider bridge | 子进程 CLI + per-run MCP 工具注入 |
| 完成判定 | 5 探测器,证据驱动 | 事件流 + fencing 校验 + output 修复协议 |
| 并发控制 | daemon 单实例 + socket 同用户校验(弱 fence) | leaseGeneration + fencingToken CAS(强) |
| 合入 | 无自动 merge,人接管 pane | merge gate 裁决 + merge-executor 机械合入 |
| 人工介入 | 人直接敲 pane / inbox 挂起 | inbox_ask → 飞书交互卡片按钮决策 |
| Provider | 22 个后端(17 CLI 家族+dsh/droid…) | 3 个(Codex/Claude/Pi) |
| 平台 | Win beta(Herdr)/Linux/macOS + 手机 App | 仅 Apple Silicon Mac |

### 3.2 12 维能力矩阵(所有结论源码验证)

| 维度 | CCB | Anneal | 平台建设决策 |
|---|---|---|---|
| Agent Runtime | pane 原生可见+可接管;bridge/FIFO+app-server | 子进程 + per-run MCP(9 工具) | **双通道**:调试/协作用 CCB,产线用 anneal |
| Orchestration | /ask 即席委派 + chain 模式 | 12 步模板链 + scheduler + blockedBy | **都复用**(即席协作 + 自动产线并存) |
| Mailbox | Message/Attempt/Reply + retry_lineage | inboxMessage 表(飞书载体,deliveryStatus 状态机) | **封装适配**:agent↔agent 用 CCB;agent→人 用 anneal 飞书卡 |
| Session/Context | provider-native + compact/clear/restore | Session 表 + resume/fork + prompt 组装 | **选一台作账本**,另一作载体(不双管) |
| Lease/Fencing | 弱(无 fencing token) | **强**:leaseGeneration+fencingToken+cancelRequestId intent | **直接复用 anneal 的** |
| Worktree | .ccb/workspaces/<agent> + bind_chain_workspace_tree | ephemeral worktree + run 分支 + reclaim intent + base/headSha | **复用 anneal**;CCB 目录留给调试 |
| DAG | callback chain(轻量) | chainId/chainIndex/blockedBy/single-flight retry | **复用 anneal** |
| Artifact | text artifact(spill/stub) | task_output 结构化 JSON + 500k 上限 | **复用 anneal 契约**,CCB 面向人阅读 |
| Merge Queue | 无 | gate 裁决码 + 机械合入 + force-with-lease CAS + base-drift 恢复 | **复用 anneal(刚需)** |
| Human Takeover | 人进 pane 直控(天生) | 飞书卡片决策 + approvalGate + Gated 复核 | **双通道都保留** |
| 分布式 | 单机 daemon(手机仅远程控制) | 单机(暂无跨机) | 自建控制面(参考 DSH-EX 基线) |
| HA/安全 | keeper 恢复账本 + token_auth + 同用户校验 | 单实例锁 + 共享维护锁 + 四类 principal + token hash | **复用 anneal 的**,补 CCB 缺失 |

**结论:10 维互补、2 维需选定权威避免双控;唯一硬约束是任务/会话状态只能有一个权威来源。**

### 3.3 推荐结合架构(分层,非拼接)

```
┌────────────── 平台控制面(唯一权威) ──────────────┐
│  Postgres(任务/Attempt/Lease/Mailbox/Merge 状态) │ ←参考 anneal schema
│  认证:四类 principal + token hash                 │ ←复刻 anneal auth.ts
└───────┬───────────────────────────┬──────────────┘
        │ 任务分发(claim/start/heartbeat/complete+fencing)
        ▼                           ▼
┌─ CCB 执行通道(人在环)─┐    ┌─ Anneal 执行通道(无人值守)─┐
│ /ask → message_bureau │    │ 12 步链 runner + per-run MCP│
│ 22 provider pane       │    │ 3 provider 子进程          │
└────┬──────────────────┘    └──────────┬─────────────────┘
     │ commit                          │ run 分支 + task_output
     └───────────────┬──────────────────┘
                     ▼
          ┌─ 集成与合入门禁 ─┐
          │ merge gate 裁决  │ ←复刻 anneal gate-worker
          │ merge-executor  │ ←独立进程/独立主体,机械合入
          └────────┬─────────┘
                   ▼
        ┌─ 人工决策通道 ─┐
        │ 飞书卡片(anneal inbox)+ pane 直控(CCB) │
        └───────────────────┘
```

**四条防双控制面铁律**:
1. Postgres 是任务状态唯一权威;CCB daemon 只当执行器/接入层
2. Lease/Fencing 只有一套(anneal 的)
3. Merge 只有一条路:机械 gate 自动合入 或 人工 pane 提交,按任务模式二选一
4. Session 账本单点:产线记 anneal Session;pane 协作记 CCB Session;attempt_id ↔ pane_id 映射桥接

### 3.4 三套建设路线

| 路线 | 范围 | 投入 | 说明 |
|---|---|---|---|
| **A 快速 MVP** | CCB 原样 + 最小验证门禁脚本 | 1-2 周 | 先跑通人环协作;无自动合入/强 fencing/单机 |
| **B 团队版(推荐)** | A + Postgres 任务账本 + anneal 12 步链/merge gate/merge-executor + 飞书 inbox | 1-2 月 | 双通道齐全;需写约 2-3k 行适配与控制面 |
| **C 生产集群版** | B + 跨机 Node Agent + mTLS + Transactional Outbox + Artifact Registry(+ DSH-EX 基线联动) | 2-3 月+ | 分布式多机生产级 |

建议路径:走 B,不 fork 任何一方——直接依赖上游,自己只写适配与控制面,权威状态始终在自己手里。

---

## 4. 分布式部署拓扑(多设备运行方案)

### 4.1 判断:两种拆法代价完全不同

- **角色级拆分(同任务横跨多设备)**:任务生命周期状态(worktree/会话/prompt 链/待审 diff)要跨机强关联传递,任何一台故障卡死整链。只服务于两种需求:**物理级隔离(双盲终极形态)** 与 **安全合规(评审机不接触实现机)**。
- **项目级拆分(每设备一个自治单元 + 主控)**:设备间唯一共享 git remote + 产物;主控只派发/汇总/跑门禁。通信极简、故障域清晰 → **分布式系统天然正确的拓扑**。
- **混合形态(推荐)**:默认按项目拆分;评审/回归这种"只读 diff + 写结论"的受控步骤,按需调度到隔离设备,享受物理盲审且不用搬实现工作区。

### 4.2 两个开源项目对多设备的现状(源码事实)

| | 是否支持多设备 | 依据 |
|---|---|---|
| Anneal | **控制面/执行器天然分离,改造点最小** | runner → HTTP claim + RUNNER_TOKEN + fencingToken CAS;单实例锁只约束控制面。改 API base URL 指向远程即跨机认领;fencing/attempt 分支本就是并发设计 |
| CCB | **单机 daemon,不能当执行器跨机** | pane 都在本机 tmux/herdr;message_bureau 为本地文件;mobile_gateway 只控制/查看/传文件,不搬执行。价值在"每设备的可见协作工作台" |

### 4.3 推荐形态

```
           ┌────────── 主控(控制面,唯一权威) ──────────┐
           │ Postgres:Task/Attempt/Lease/Merge 状态    │
           │ merge queue + 门禁裁决 + 人工审核(飞书卡片) │
           └───┬───────────────┬───────────────┬───────┘
 lease/fencing│               │               │
               ▼               ▼               ▼
        ┌─ 设备A ─┐       ┌─ 设备B ─┐       ┌─ 设备C ─┐
        │ CCB pane 组 │     │ CCB pane 组 │     │ anneal  ←┐
        │ 实现+协作   │     │ 实现+协作   │     │ runner    │ 评审/回归
        │ (项目1)    │     │ (项目2)    │     │ (冷门禁)   │ 隔离机
        └────┬──────┘       └────┬──────┘       └────┬─────┘
             └── git remote(唯一代码事实源) ←─────────┘
                   主分支只由主控 merge 门禁推进
```

### 4.4 跨机 5 个关键工程要点(分水岭)

1. **网络分区 ≠ 租约失效(最致命)**:heartbeat 5s → suspect 15s → unreachable 45s → lease_ttl 60s;UNREACHABLE 绝不自动等于租约失效,必须等 60s 后数据库 CAS 标记 EXPIRED 才允许调度新 Attempt(DSH-EX Sol 评审结论)
2. **代码一律 git remote 收敛**:多设备绝不共享 worktree;产出 push 到 `agent/<id>/<task_id>/<attempt_id>` 不可变分支;主控 CAS(--force-with-lease / merge_epoch)推进主分支
3. **跨机消息必须幂等**:Transactional Outbox(或 outbox 表 + 消费端 commit + 幂等键);本地双写链路实测教训:事件消费不 commit、payload 不一致,跨机放大十倍
4. **双盲的拓扑红利**:评审设备物理上看不到实现设备证据,anneal 的 prompt 禁令 + priorOutputKinds=[] 在跨机后是物理级天然成立
5. **凭据与消费端边界**:每设备用自己的 CLI 登录;主控只下发 fencing/session token;控制面加密存储 secrets(AES-256-GCM),节点侧凭据 0600;失联恢复后先 reconcile 再认领

---

## 5. 开发用机硬件需求与推荐

### 5.1 破除误区:推理在云端,硬件压力来自本地进程

- LLM 推理在 provider 云端;本地承担的是:每 agent 一个 CLI+bridge+completion 进程、每 run 一份完整 worktree、merge gate 的 lint/单测爆发、会话历史/产物持久化
- 资源大头 = **同时跑的 agent 会话数 × 本地工具链重量级**;内存(而非 CPU)是最不能省的

### 5.2 角色硬件需求

| 角色 | 配置 | 说明 |
|---|---|---|
| 主控(控制面+gate+审核) | 4 核/8GB/SSD 256GB+ | Postgres+调度+门禁;网络必须最稳(它是唯一权威) |
| 执行节点(开发用机) | **8 核/32GB/1TB NVMe** 起步 | 6-8 agent / 工具链压测不卡;1TB 是为多 worktree+依赖缓存 |
| 评审/回归隔离节点 | 4 核/16GB/512GB 无显示器 | 只读 diff+跑测试,无 agent 长连 |

### 5.3 联网核实后的旗舰推荐(2026-09 在售)

**macOS 线(Apple 官方规格)**:
- **首选 Mac Studio M5 系列(9/22 开售)**
  - M5 Max:18 核 CPU(6 super+12 perf)/40 核 GPU 可配/**128GB 统一内存**/SSD 至 8TB/**10GbE 标配**/4× TB5(120Gb/s)/Wi-Fi 7/614GB/s 带宽
  - M5 Ultra:36 核 CPU 可配/**256-512GB**/SSD 至 16TB/80 核 GPU/1.2TB/s
  - 推荐档:Mac Studio M5 Max 18/40 + 128GB + 4TB
- 移动线:16 英寸 MacBook Pro M5 Max:128GB/8TB/3× TB5/Wi-Fi 7;无 10GbE

**Windows 线(AMD/Intel 规格联网核实)**:
- **AMD Ryzen 9 9950X**(Zen 5,Granite Ridge):16C/32T,最高 5.7GHz,DDR5,AM5/X870E,AVX-512 全 512-bit
- **Intel Core Ultra 9 285K**(Arrow Lake-S):24 核(8P+16E)/24T,最高 5.7GHz,DDR5-6400 上限 256GB,LGA 1851/Z890,NPU 13 TOPS
- 时效:Zen 6 已在 3nm/2nm 路线;Nova Lake(18A/TSMC N2P)为继任——上市则跳档
- 配套:128GB DDR5-6000+(64GB 最低及格线)/系统盘 1TB PCIe 5.0 NVMe/数据盘 2-4TB PCIe 5.0 NVMe/**独显不需要**(推理在云端,钱给内存)/2.5GbE 起步

### 5.4 搭配建议

| 角色 | 推荐设备 |
|---|---|
| 主控 + 黑灯产线 | Mac Studio M5 Max 128GB/4TB(anneal 仅 Apple Silicon;10GbE 当集群权威) |
| 人环协作开发机 | Windows 旗舰 9950X/128GB/PCIe5×2 或 MacBook Pro M5 Max(CCB pane 组) |
| 隔离评审节点 | 最便宜无头迷你机(只读+测试) |

一句话:只买一台 → Mac Studio M5 Max 128GB/4TB;一套多机 → Studio 当主控/产线 + 128GB DDR5 的 9950X 强机当开发台,钱砸向内存和两块 PCIe 5.0 NVMe。

---

## 6. 脱敏与审核本地模型方案

### 6.1 结论:必须加,但作为"确定性规则硬拦截 + 本地模型语义识别 + 人工审批兜底"的统一本地安全网关

不要做成普通 Agent(可能被 Prompt Injection 绕过);应是**控制面强制执行、Agent 无法跳过的基础设施层**。

### 6.2 四道检查点

1. **输入脱敏(发送给模型前)**:API Key/Token/密码/私钥、连接串、Cookie、手机号/身份证/邮箱、客户资料/合同/内部域名、.env/SSH Key/硬编码 Secret。低风险自动替换占位符;中风险脱敏后允许;高风险阻断+人工审批;私钥/生产密码/完整身份凭证严禁外发。映射只存本地加密 Vault。
2. **Agent 间消息审核**:Mailbox/Outbox 双向检查,防 Coder 把敏感内容经 Reviewer 泄给外部模型。控制面只把 sanitized_content 交给 Agent,原文非必要不读。
3. **工具调用前审核**:Shell/文件读取范围/MCP/HTTP/Git push/DB 查询/Artifact 上传/外部操作;平台强制策略,不让本地模型只输出"允许/拒绝"就拿最终决定权。
4. **输出与合入审核**:回复前/Artifact 上传前/git commit 前/PR 创建前/Merge Queue 前/部署前再次扫描(Secret 泄漏、后门、绕过认证、违规依赖、Prompt Injection 诱导、占位符误写)。

### 6.3 本地模型负责什么(语义)vs 不应单独负责什么(确定性)

**模型负责**:语义风险识别(生产凭证误判、组合 PII、绕过认证逻辑、诱导外传的 prompt injection、无关工具调用、隐蔽外联行为)。输出结构化 JSON(risk_level/categories/evidence/decision/confidence)。

**必须确定性机制**:API Key/私钥/密码模式、路径访问控制、命令 allowlist/denylist、域名白名单、身份权限校验、Lease/Fencing/幂等、Git 分支保护、Merge 权限、审批签名与审计留痕。

### 6.4 与平台结合:独立 Policy Enforcement Plane + 任务状态机新增安全态

```
QUEUED → SECURITY_SCANNING → APPROVED → SCHEDULED → RUNNING
      → OUTPUT_SCANNING → QUALITY_GATE → MERGE_PENDING → MERGED
可决策:ALLOW / ALLOW_SANITIZED / DENY / REQUIRE_HUMAN_APPROVAL / QUARANTINE
数据分级:PUBLIC(可外部模型)/ INTERNAL(脱敏后外部)/ CONFIDENTIAL(仅本地模型)/ RESTRICTED(隔离节点+本地模型+人工)
```

模型不可用时 fail-open/fail-closed 按数据级别:PUBLIC 可有限 fail-open;CONFIDENTIAL/RESTRICTED 必须 fail-closed。

### 6.5 分阶段

- MVP:Secret Scanner + PII 检测 + 文件/网络策略 + Git/Artifact 输出扫描 + 高风险人工审批
- 团队版:+ 本地语义审核模型 + Mailbox 双向扫描 + Prompt Injection 检测 + 数据分级/脱敏 Vault + 完整审计链路
- 生产版:独立 PEP/PDP + 多模型交叉审核 + 隔离执行节点 + 策略版本化与回放测试 + 误报/漏报监控

---

## 7. 平台测试策略(正式使用后的测试环节)

### 7.1 分层:业务逻辑一套覆盖,平台耦合面才双平台实测

针对**被测软件是否需要双平台测试**,取决于产品兼容性承诺;机制上做成"多平台 lane"而非跑两遍:

| 产品承诺 | 测试做法 |
|---|---|
| 仅 macOS 正式 | 单 lane(Mac)全量 |
| macOS 正式 + Windows beta | 主 lane 全量 + Windows lane 冒烟/兼容,Windows 失败不阻断主流程但阻断"声称支持 Windows"的发布 |
| 双平台正式 | 双 lane 全量,全部通过才允许合入(merge gate 阻断) |
| 还支持 Linux/容器 | 加容器 lane(多数业务测试容器化,省真机) |

### 7.2 两个上游项目的平台耦合事实(源码)

| 耦合面 | macOS | Windows |
|---|---|---|
| anneal 支持 | 唯一正式平台(Applc Silicon) | 不支持 |
| CCB 支持 | 正式 | beta:WezTerm + Git Bash + Herdr ≥0.8 + install.ps1;os_platform.py is_native_windows() / platform_needs_herdr() 分支 |
| 终端复用器 | tmux | Herdr(强制,check_herdr_ready 不满足拒启) |
| 进程管理 | 进程组 kill | Windows 进程树/namespace 清理(runtime_pid_cleanup)、PATH 问题(本地实测 codex 秒退=PATH 缺 Git\usr\bin) |
| 凭据 | Keychain | Credential Manager / 文件 0600 |
| 文件系统 | 大小写敏感 | 260 字符路径限制、反斜杠、权限语义弱化 |

### 7.3 测试环境组织

- **主测试平台 = macOS(M5)**:anneal 只能在 Mac 跑 → 产线全链路 E2E 只能在 Mac 测;CCB macOS 正式,一台机覆盖两家"正式支持面"
- **Windows 作为第二测试线**:专测 pane 生命周期/进程终止/CLI preflight/长路径/凭据/升级回滚(install.ps1 vs install.sh 两套安装器)
- Linux = 第 0 测试线(10.66.66.2):CCB Linux 正式;分布式主控大概率跑 Linux,最接近生产
- CI 双 runner(macOS + Windows)借鉴上游 CCB 自己的 cross-platform-test workflow;Windows lane 只跑平台敏感清单 + 冒烟,不做全量业务矩阵(业务矩阵容器化)

### 7.4 嵌入流水线的机制

- merge gate 扩成多平台 lane:Mac gate worker(主判定)+ Windows gate worker(兼容);lane 结果汇总,全部 PASS 才进 merge queue;把 Windows lane 注册为 merge-executor 决策表的 required check(它已在检查 required checks,一行策略配置即可)
- 12 步链 Tester(step 10)只在主平台跑语义+主测试,不操作 Windows;平台矩阵由机械 gate lane 承担,不消耗 agent token
- 失败策略:Windows lane 失败按产品承诺决定 block merge 或降级为 known-issue(beta 承诺就降级);杜绝"环境没这平台却声称支持过"的自欺

---

## 8. 外部研读:Google Antigravity Teamwork 多 Agent 研究框架

> 来源:https://antigravity.google/blog/teamwork-when-ai-becomes-a-research-partner(2026-08-27,Google Antigravity Blog,约 11 min read)
> 研读日期:2026-09-01。这篇文章与我们前几轮讨论的系统**高度同构**——它讲的正是同类的多 Agent 协作平台,只是跑在 Gemini 生态上。

### 8.1 文章在说什么

Google 更新了多 Agent 编排框架 **Teamwork**(Antigravity 内置,`/teamwork-preview` 命令,基于 **Gemini 3.7 Flash**),并晒出一批硬核成果:

- **7 个理论 CS 开放问题**(含 FOCS 2025 / JMLR 2021 档):稀疏凸优化、子空间近似 coreset、可证明的 LLM 量化、向量嵌入、Prefix-Matrix 分解、Knuth Cycles 猜想(40+ 页证明,Lean 形式化验证)等;其中 5 篇论文已上 arXiv,Knuth 结果经 Lean 机器验证,其余经人类专家复核
- **一个能 boot 操作系统的 RISC-V OoO CPU 模拟器**:0.71% 平均周期对齐误差(对照 BOOM 硬件执行 ground truth),xv6 启动到 shell + 100+ RISC-V 标准 benchmark
- **已合入上游开源库的优化**:Eigen(GeMV 单行/列快速路径,SIMD 4-way 展开)与 ParlayHash → Swiss Parlay(64 线程初插 2× 吞吐、单线程整体 1.5×、内存 -25%)

**核心论点**:松散组织的多 agent 在难问题上会跑偏——互相附和早期错误、在错误假设上自信地越建越高。Teamwork 把"生成候选 → 压力测试 → 综合最优"这个循环做成具体且可配置的框架,让 agent 数小时到数天自主运转,而人只保留两件事:**定目标 + 最终验收**。

### 8.2 三个关键设计(值得抄作业)

**1. Pattern 是规格数据,不是程序(与我们结论完全一致)**
> "A pattern is a specification rather than an executable program. It contains no orchestration code of its own."

这正是 anneal 的做法:12 步模板是带 frontmatter 的 markdown 数据。Teamwork 讲得更明确——"专门机制(如对抗批判循环)可以跨领域零修改移植"。**印证:模板/模式即数据的方向是对的。**

**2. 运行时自适应,而非固定流水线**
> "Agent count and team structure can shift mid-run as the problem reveals itself."

对比:anneal 是固定 12 步链(每步 1 个固定 agent),Teamwork 是动态 spawn agent 数量与回合数。**决策分叉**:确定性流水线(anneal,可审计、成本可预测)vs 自适应编排(Teamwork,对开放问题更聪明但行为不可预期)。建议两级并存:确定性模板链做工程交付,自适应模式做研究探索。

**3. Long Proof 的机制全家桶(对抗评审的终极形态)**
- **Competitive Strategy Search**:多个候选策略并行,每个配一个 **falsifier(专职打假者)**;被否决路线不丢弃,带着反对意见留在合成树里("a broken route may still contain a useful idea")
- **分解 + 依赖图**:子问题独立并行、依赖拓扑序执行
- **子问题级 tournament network**:每个节点读候选样本 + 批判 → 产出改良解;失败则带累积反对意见重跑
- **跨轮学习**:失败草稿保留;verifier 结论蒸馏进 **answer-agnostic pitfall registry(与答案无关的坑库)**;共享知识目录记录已证结论/失败路径/参考文献

> 对照:anneal 的 07 盲审(Opus,数据层隔离)+ 08 裁定 = 弱化版 falsifier;anneal 的输出修复协议 = 弱化版 pitfall registry。**Teamwork 把"失败资产化"做得更彻底——建议吸收进平台:失败记录不是垃圾,而是 pitfall registry + 可复用片段。**

### 8.3 两个工程亮点

1. **RISC-V 模拟器防作弊设计**:把 Spike 参考模拟器源码沙箱化(air-gapped),用 **lockstep co-simulation(锁步协同仿真)** 解决 "silent execution gap"(微架构状态可在数百周期里无声偏离,直到架构级失败才暴露)。**印证铁律:agent 系统要靠外部 ground truth + 不可篡改的验证面防作弊,而不是靠信任。**
2. **TCSBench 71% 的启示**:用 **Flash 级别模型 + 多 agent 协调** 达到超过单大模型的效果("coordinating many agents rather than relying on a single, larger model")。**这正是平台的核心价值主张:不依赖最贵模型,靠编排换智商。**

### 8.4 与我们讨论过的内容对照

| 话题 | 之前的结论 | 本文印证/补充 |
|---|---|---|
| 模板/模式即数据 | anneal 12 步模板是 markdown 数据 | Teamwork pattern 是 spec 非程序,跨域可移植 |
| 双盲/对抗评审 | anneal 07 priorOutputKinds=[] 隔离 | falsifier 专职打假 + 反对意见留存(更强的失败资产化) |
| 人机分工 | "人管目标与验收,机器管执行" | "humans in charge of objectives and final acceptance" 一致 |
| 固定链 vs 自适应 | 需决策:确定性流水线 vs 自适应 | Teamwork 动态 spawn;建议两级并存 |
| 失败处理 | anneal failureEnvelope + single-flight 重试 | pitfall registry + 失败草稿复用(建议吸收) |
| 防作弊 | 外部 ground truth + 门禁 | lockstep co-simulation + 沙箱参考实现 |
| 模型策略 | 不依赖最贵模型 | 3.7 Flash + 编排 = PhD 级研究 |

### 8.5 对平台建设的三条可操作启发

1. **给流水线加"坑库(pitfall registry)"层**:每个失败 Attempt 的 verifier 结论蒸馏成与问题无关的经验条目,后续 Attempt 自动携带——补上 anneal failureEnvelope 与 Teamwork pitfall registry 之间的差距。
2. **把"评审角色升级为 falsifier"**:不只"找 bug",而是"专职尝试打破方案并给出可反驳的反对意见";被否决方案进合成树而不是垃圾桶。
3. **规划"研究模式"通道**:工程交付走确定性 12 步链;开放/不确定问题走 Teamwork 式自适应模式(动态 agent 数 + 对抗搜索)。两者共用同一套任务账本、merge gate 与人工验收,只是编排模式不同。

---

## 9. 附录:调研文件索引

### 8.1 Anneal 已研读文件(缓存后清理)

- 入口/架构:packages/api/src/index.ts、packages/runner/src/index.ts、docs/architecture.md、developer-preview.md、security.md、docker-compose.yml、package.json
- 核心:runner.ts(全)、adapters.ts、adapters/{codex,claude,pi}.ts、session-tool-contract.ts、run-claim.ts、run-lifecycle.ts、run-completion.ts、routes/runner.ts、board.ts、redis 未涉及
- 数据:schema.prisma(Task/Run/Session/ChainControl/Inbox)
- 模板/角色:compound-engineer-workflow 的 02/05/06/07/08/10/12 + roles 的 spec/review-coordinator(-sol/-opus)/merge-integrator
- 门禁:merge-gate.sh(63KB)、gate-worker/{step-engine,gate-dispatch,run-gate}.sh
- 合入:packages/merge-executor/src/{index,preconditions,decision-table,github-app-auth}.ts
- 其他:packages/inbox/src/{index,cards,supervisor}.ts(飞书卡片人工决策)

### 8.2 CCB 已研读文件(缓存后清理)

- 入口:ccb.py、bin/ccb.js、install.sh(118KB)、package.json
- 核心:lib/ccbd/{main,app,keeper_main}.py、lib/ccbd/services/dispatcher.py、lib/ccbd/handlers/{submit,inbox,queue,ack}.py、control_plane_transport/{interface,token_auth}.py
- 委派:lib/cli/services/ask.py、ask_runtime/{submission,watch}.py
- 完成判定:lib/completion/detectors/{base,anchored_session_stability}.py
- Provider:lib/provider_backends/codex/{bridge,comm,...}.py + bridge_runtime/service.py、provider_core/{protocol,registry,contracts}.py
- 存储:lib/storage/paths.py(PathLayout)
- 文档:README.md 全文、CHANGELOG_4.0.md、VERSION(8.6.10)

### 8.3 关键外部参考

- 本平台分布式基线:DSH-EX v1.2.0(Active-Standby 控制面 / Node Agent / attempt 分支 / merge_epoch CAS / Transactional Outbox;远端 10.66.66.2,本地镜像 C:\DSH-Team_软件需求规格说明书_SRS.md)
- Intel/AMD/Apple 2026-09 官方规格页(硬件部分)

---

*文档完。所有「源码已验证」结论均可在对应仓库的上述路径中复核;标注"文档声明/合理推断"的条目(如 CCB merge 语义细节)以两个项目的 release notes 与 README 为准。*