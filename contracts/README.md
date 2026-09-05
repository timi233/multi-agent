# Phase 0 契约层推进记录（attempt_contract v2 基线）

- 依据：《架构与实现蓝图 v1.3.2》§9.4、§12；《环境搭建与验证手册 v1.3.2》§12.1/§12.2
- 状态：契约基建 + 正反向量 + 双实现比对 + 签名向量已建成并自动化测试（CT-01~CT-04 起步）

## 产物

| 项 | 位置 | 说明 |
|---|---|---|
| Schema 2.0 | `contracts/jsonschema/attempt_contract.v2.schema.json` | `additionalProperties:false`、枚举/上限/uniqueItems/pattern 约束 |
| DigestProfile | `contracts/jsonschema/attempt_contract.v2.digestprofile.json` | 白名单 `immutablePayloadPointers`（16 指针）、`signatureEnvelopePointers`、`mutableDatabasePointers` |
| 主实现 | `app/contracts/codec.py` | RFC 8785-lite JCS + 白名单投影 + `payloadDigest=sha256(JCS)` |
| 独立参考实现 | `scripts/ref_impl/jcs.js` | Node 独立实现（同规范、代码独立） |
| 测试向量 | `contracts/test-vectors/attempt_contract/v2/vectors.json` | 2 正 + 6 负（未知字段/枚举/上限/重复/格式/缺必需） |
| 签名向量 | `contracts/test-vectors/attempt_contract/v2/signature_vectors.json` | Ed25519 有效/篡改/错 key |
| 自动化 | `tests/test_contracts.py` | 5 项（可重算、拒绝、digest 不变、签名、Node 比对） |
| 对象：task_spec | `contracts/jsonschema/task_spec.v2.{schema,digestprofile}.json` | Task 契约视图（提交即冻结，全字段不可变）；`contracts/test-vectors/task_spec/v2/vectors.json` |
| 对象：事件信封 | `contracts/jsonschema/event_envelope.v2.{schema,digestprofile}.json` | 蓝图 §13.4 通用信封；CT-08 作用域必填；`contracts/test-vectors/event_envelope/v2/vectors.json` |
| 对象向量生成 | `scripts/gen_protocol_vectors.py` | task_spec / event_envelope 正反向量（各 2 正 + 6 负） |
| 对象向量断言 | `tests/test_protocol_vectors.py` | 6 项（重算一致、拒绝、CT-08、对象域 digest 独立） |

## 第二版变更（签名信封对齐蓝图 §9.4）

- 签名信封统一为蓝图 §9.4 十字段结构：`signatureAlgorithm / keyId / issuer / issuerWorkloadIdentity / audience / objectType / schemaVersion / payloadDigest / controlPlaneEpoch / signedAt` + `value`（`additionalProperties:false`）。
- 签名输入域分离：`signature_input = JCS({signatureContext, signatureAlgorithm, keyId, issuer, issuerWorkloadIdentity, audience, objectType, schemaVersion, payloadDigest, controlPlaneEpoch, signedAt})`，`signatureContext` 固定版本字符串 `pi.contract.signature.v1`，`audience` 无适用对象时显式 null；业务字节不展开进签名正文（digest 是其语义化别名）。
- 签名信封不进 canonicalPayload（白名单外）：同一对象的 payloadDigest 与是否带签名无关。
- `canonical_payload` 投影对可选指针缺失行为：跳过（投影只含实际存在的白名单字段，task_spec/事件信封有可选字段）。

## 验证结果（对齐手册 §12.2）

| 项 | 结果 |
|---|---|
| CT-01 canonicalPayload/digest 逐字节一致 | PASS：Python 主实现 vs Node 独立参考实现，3 正向量 0 不一致（attempt_contract） |
| CT-02 未知字段/未知枚举/超限字段拒绝 | PASS：attempt_contract 6 负向量 + task_spec/事件信封各 6 负向量全部 Schema 拒绝 |
| CT-03 集合数组乱序/重复 | PASS：`canonicalSortKeys` 机制（蓝图 §12："JCS 不替数组排序——集合数组先按稳定键排序、拒绝重复再计算 digest"）——attempt_contract `/toolAllowlist`（by=value）与 task_spec `/policyTemplateRefs`（by=key, templateRef）启用；乱序正向量 digest 与有序逐字节一致、canonical 层重复/缺键拒绝；Node 参考实现同规范同步（10 正向量 0 不一致）；投影数组字段必须声明 `canonicalSortKeys` 或 `orderedArrays`（有序语义），二者互斥（profile 一致性校验） |
| CT-04 必须签名对象正反向量 | PASS（attempt_contract 起步）：有效签名验通、篡改信封任一字段失败、错 keyId 失败；签名信封不改变 payloadDigest |
| CT-08 事件信封作用域必填 ID | PASS：Task 事件仅要求 taskId（无 runId/attemptId 合法）；Attempt 事件要求 taskId+runId+attemptId 三者，缺一即拒 |

## 状态机模型检查（对齐手册 §13.1 SM-xx）

- 模型与检查器：`scripts/sm_model.py`（Task 白名单直接取自 `app/control/lifecycle.py`；Attempt 模型从 `app/worker.py` 条件 UPDATE 提取）；报告 `contracts/sm-model/report.md`（可复现）。
- 固化断言：`tests/test_sm_model.py`（11 项）。
- 结果：**无违规**。SM-01 白名单穷举（25 对 + 套件全查）PASS；SM-02 终态闭合 PASS；SM-03 每个可达非终态存在 StateDeadlinePolicy 出口且在白名单内 PASS；SM-08 终态转移事件同事务 PASS；死枚举审计：Attempt `RUNNING`/`FAILED` 为声明未用（基线固化）。
- 已知差距（不并入通过判定）：SM-08 `Task QUEUED->RUNNING` 的事件（`ATTEMPT_STARTED`）在后续初始化事务写入，跨事务（1 项，`tests/test_sm_model.py::test_run_all_ok` 锁定）；运行时 deadline 定时留待 Gateway 预算层。

## 验收基准与故障注入（③，手册 §18.2 Phase 0 门槛如实对照）

- 产物：`scripts/acceptance_report.py`（`--json out.json` 输出结构化对照）、`tests/test_acceptance_report.py`（5 项：结构 + **不虚报约束**——PASS 必须带可定位证据、Phase 0 门槛未越必须如实）、`tests/test_fault_injection.py`（5 项崩溃边界矩阵）。
- 门槛声明（如实）：Phase 0 `CONTRACT_VALIDATED` **未达**——手册 §18.2 要求 GT + RT-01~06 全 PASS；当前 GT 未实现、RT-01/02/03/04/05 未达、RT-06 部分（链路可跑但无 RouteAttestation）。平台实际可运行（E2E 冒烟），但不得越级声明。
- 故障注入矩阵（最小可复现子集，对应手册"100 次崩溃"的精神）：预留后 kill 占额保留不恢复（GW-04）；SENT 后 kill 每笔只消费一次（UNIQUE 兜底）；崩溃恢复（recover_stale）结算 Grant 且链完整；aborted 事务回滚一致；UNKNOWN 占额 100% 阻断后续预留（GW-07 侧）。

## Runtime 能力报告（RT，蓝图 §8.2 六问 / 手册 RT-xx 简化版）

- 产物：`contracts/jsonschema/runtime_capability_report.v2.{schema,digestprofile}.json`、`app/runtime/capabilities.py`、`GET /api/v1/runtime/capabilities`、`tests/test_runtime_capabilities.py`（7 项）。
- 语义：引擎事实基线（工具集/模型路由/资源默认参数/隔离边界/已知差距）；**进程内缓存生成一次**（缓存幂等且返回 deepcopy，外部不污染内部事实；`generatedAt` 固定）；`contractId = sha256(JCS(核心事实，不含 generatedAt))[:32]` 复用项目 canonical（jcs）、事实/集合顺序变化即变；签名信封复用 Phase 0 codec（蓝图 §9.4 十字段），**`signature.value` 为真实 Ed25519 签名**——私钥持久化于 `data/keys/runtime_ed25519.pem`（data/ 已排除 git、权限 600），`keyId`=公钥指纹（跨重启稳定），验签用 `app.security.keys.verify`；工具集为集合数组（canonicalSortKeys by=name），供准入/Gateway 身份绑定引用。
- RT-xx 对照（差距已入报告 `knownGaps`，随实现移除）：RT-01 无沙箱故不适用（进程启动正常）；RT-02/03/04/05/07 未达（沙箱/管道/驱动幂等）；RT-06 真实模型链路可跑但证据无 RouteAttestation；RT-08 未达。另含 GW-08（撤销新鲜度）、GW-10（热路径 PG）两项。

## 预算契约 budget_grant v2（② 契约对象扩展）

- 产物：`contracts/jsonschema/budget_grant.v2.{schema,digestprofile}.json`、`scripts/gen_budget_vectors.py`（10 向量：空 journal/链式全生命周期/FAILED 释放/UNKNOWN/pos-signed + 5 负例）、`scripts/verify_vectors_node.js` 已含该对象、`tests/test_budget_contract.py`（9 项）。
- 对象边界：签名 payload 只锚定**不可变授权字段**（grantId/taskId/attemptId/totalBudgetTokens/createdAt 等）；`consumedTokens/status/journal` 为**消费事实**（mutableDatabasePointers，不投影）——每次结算/失败/未知改变事务事实但不改变 Grant 授权签名（`test_grant_immutable_consumption_mutable` 覆盖）。
- 链式 Journal：RESERVED/SENT/SETTLED/FAILED/UNKNOWN 条目含 `previousEntryDigest/entryDigest`（首条根锚 `pi-budget-root-v1`），向量链为 `_entry_digest` **逐条真实计算**；有序性/逐条 digest/consumed 对账由 `BudgetDomain.verified_budget_grant()` 语义校验入口保证（测试覆盖篡改断链/伪造 digest/对账不符）。
- 双实现：Node 参考实现逐字节一致（CT-01 PASS，15 正向量 0 不一致）。

## 执行计划契约 execution_plan_snapshot v2（G1 编排/Run 状态机，蓝图 §10.5.1 单机子集）

- 产物：`contracts/jsonschema/execution_plan_snapshot.v2.{schema,digestprofile}.json`、`scripts/gen_execution_plan_vectors.py`（10 向量：单步/多步/仅 READ_ONLY/含 taskSpecDigest/pos-signed + 5 负例）、`scripts/verify_vectors_node.js` 已含该对象（CT-01 20 正向量 0 不一致）、`tests/test_execution_plan_contract.py`（13 项）、`app/runtime/plans.py::verified_execution_plan()`。
- 对象边界（蓝图 §10.5.1）：Task 进入执行前必须发布**不可变、签名**的执行计划——`plannedAttemptInputs[]`（runKind=IMPLEMENTATION/REVIEW/READ_ONLY、deliverableKind）为**集合归一化**数组：canonical sort 按 `plannedAttemptInputId`，语义层按该键拒绝重复（同 ID 不同内容由 `verified_execution_plan` 显式检查拒收，不依赖 `uniqueItems`）；`canonicalPlannedInputsDigest = sha256(JCS(排序后数组))` 这类广播摘要属不可变签名段（内容变化必变、顺序无关等价）。
- 语义校验 `verified_execution_plan()`（先 Schema 后语义，返回问题列表）：canonicalPlannedInputsDigest 重算相等、顶层 `payloadDigest` self 重算一致、`planKind=INITIAL` 时 `parentExecutionPlanSnapshotId` 必须显式 null、`plannedAttemptInputId` 全局唯一（重复即拒收）、`upstreamBindings[].producerNodeId` 必须指向同计划内已存在 workflowNodeId（禁悬垂引用）；digest 重算全程异常收敛为问题列表（不逃逸）。
- 快照 ID：`executionPlanSnapshotId = sha256(JCS(完整不可变快照前缀，除 ID/self-digest/signature))[:32]`——taskSpecDigest/父计划/inputs/编译器任一变化必变 ID（正向量两两唯一，生成器自检 + 测试回归）。
- 签名信封：issuer=`orchestrator-test`、keyId=`sk-orchestrator-vector`（seed 派生确定性测试密钥，fingerprint `9064b45b...` 已登记 `deploy/keys/keys.lock.json`，`allowedObjectTypes=["execution_plan_snapshots"]`）；pos-signed 向量真实 Ed25519 验签通过。
- 简化差距（记录）：REVIEW runKind 与 CommitBundle/Git 侧阶段（OUTPUT_STAGED→COMMIT_ASSEMBLING）随 G5 补齐；REPAIR planKind 为蓝图保留名且 Schema enum 已收紧为仅 `INITIAL`（Repair 契约字段落地前不签发）；plannedAttemptInputs 内联 promptContent（无 prompt bundle 引用）；run/deliverable 组合不做蓝图级枚举约束。

## Gateway 预算与 Journal（蓝图 §18.2/§18.3、手册 GW-xx）

- 产物：`migrations/002_gateway_budget.sql`（`gw_budget_grants` + `gw_journal` 链式表）、`app/runtime/budget.py`（`BudgetDomain`）、`tests/test_budget.py`（15 项）。
- **账目恒等式**（不双计、不泄漏）：`outstanding = Σ(RESERVED.reserved) − Σ((SETTLED|FAILED).reserved)`；`available = total − consumed − outstanding`。SETTLED 完整释放该 invocation 的预留并单独以 `actual_tokens` 累加 consumed（实耗可超预留，超出由后续 reserve 按余额拦截）——预留 30/实耗 25 后 `available = total − 25`。
- 流程（预算跨崩溃所有权）：attempt 初始化创建 BudgetGrant；每轮 LLM 调用**调用前持久化预留**（RESERVED）→ 发送意图（SENT，不重复占额）→ 响应后结算（SETTLED）/结果不确定（UNKNOWN，保守占额不释放——Provider 可能已执行）/确定未发生（FAILED，释放预留）；每次操作独立提交；结算只经 settle 更新。
- 重试语义：gateway 隐式重试关闭（`retries=0`），agent 层每轮最多 `PI_LLM_ATTEMPTS`（默认 3）次 Provider 物理请求，**每次物理请求独立 invocation + 独立预留**（预算内）。
- 验收对齐：GW-07 预算达到上限后新调用 100% 阻断（0 预算任务不触达 LLM，worker 映射 `BUDGET_EXHAUSTED` 并落结构化 `BUDGET_EXHAUSTED` 事件）；GW-03 精神每笔预留先记账后发送；GW-04 Hash 链 + `journal_entries` 权威计数锚点 + consumed↔ΣSETTLED 对账；GW-06 同 invocationId 不同 requestDigest 拒绝；GW-09 每次调用生成 Journal 事实。
- 威胁边界（如实）：链/计数锚点检出**非协同删行与普通损坏**；恶意协同篡改（删行 + 同步改计数 + 重算无密钥 hash）不在当前威胁模型内（需 DB 权限收紧/触发器/WORM 锚点，见简化差距）。
- 并发与用量：`reserve()` 对 grant 行 `FOR UPDATE` 串行化（并发预留不超总额）；Provider 成功但无 usage 时以 UNKNOWN 保守占额（不按 0 消费释放，防绕过预算）；每次物理请求独立 invocation + 独立预留。
- Grant 生命周期：任务收敛（成功/失败/预算耗尽）在终态事务中 `settle_grant`（SETTLED）。
- 简化差距（记录）：GW-10"热路径不查询 PostgreSQL"未达（每预算操作一次 PG 往返，单实例可接受）；单实例单代次、无 Ledger Service 分片对账、预算为全局每 attempt（`PI_MAX_BUDGET_TOKENS`/`PI_BUDGET_RESERVE_TOKENS`）、Grant 轮换/故障转移未实现。

## G3 Artifact / CAS / Evidence（蓝图 §6.9/§9.5/§11.1/§13.2 单机子集）

- 产物：`attempt_terminal_envelope.v2` 契约（schema/digestprofile/10 向量/verified/Node 双实现 25 正向量/registry sk-terminal-vector）、`migrations/004_cas.sql`（`pi_cas_blobs` 内容寻址表 + `pi_artifacts` + `pi_terminal_envelopes`）、`app/runtime/cas.py`、`app/runtime/evidence.py`、`app/runtime/terminal.py::build_terminal_envelope`、worker 终态收存接线、`GET /api/v1/tasks/{id}/artifacts` 与 `/terminal-envelopes`。
- **AttemptTerminalEnvelope（§11.1 单机子集）**：worker 收敛 Attempt 时签发的 Node 来源终态信封——`outcomeClass`（SUCCESS_COMPLETE/FAILURE_PLATFORM_PROOF/CANCELLED_CONFIRMED 可达，其余保留名）、`runtimeObserved`（platform/reportedBy/missingEvidenceReasons）、输出 `resultArtifacts`（CAS 摘要+尺寸，集合化 by path）、`stopReason`、未确认副作用；**不含权威 PASS**（不覆盖控制面取消/预算/撤销事实）。语义校验：同 path 拒收、self-digest、outcome↔status 搭配、SUCCESS_COMPLETE 不得缺证据（§11.1：缺失证据不能升级为成功）。
- **CAS（§6.9/§9.5 单机子集）**：sha256 内容寻址（同内容去重），blob 落 `data/cas/<sha256>`（原子写 tmp+fsync+rename、路径由 digest 派生杜绝任意路径）；worker 每步终态（成功/业务失败/预算/异常）收存：快照工作区（相对路径排序，超限以 missingEvidenceReasons 如实披露不失败）→ put CAS → 签名信封 → `pi_artifacts`/`pi_terminal_envelopes` 归档（`verified_ok`=TRUE 强制校验）；`verify_envelope_integrity` 复核 CAS 完整性。
- 简化差距（记录）：无对象存储凭据/分片/副本（本地目录 CAS 替代 MinIO）；OutputArtifactManifest 独立对象未建（清单内联于信封 resultArtifacts）；EvidenceManifest/EvaluationVerdict 未实现（随 G4/G5）；DB blob 引用与文件双写无独立对账任务（G6）。

## G2 沙箱硬化（蓝图 §3.4/§6.6、手册 SB/RT-04 单机子集）

- 产物：`app/runtime/tools.py`（`DENIED_COMMANDS` + `assert_command_permitted` + `tool_definitions(read_only)`）、`app/runtime/agent.py::run_attempt(read_only=)`、`app/runtime/capabilities.py`（RT 0.3.1 `isolation.sandboxProfile`）、RT schema `sandboxProfile` 段、`tests/test_sandbox.py`（13 项）。
- **命令策略**（subprocess argv 直传，无 shell）：deny list 拒绝特权/系统变更（sudo/su/chown/mount/iptables/systemctl…）、全局包管理（apt/dpkg/yum…）、网络外联客户端（curl/wget/ssh/git/ping…）、调试/跟踪（strace/tcpdump…）；`chmod +s`/`4755` 等 setuid/setgid 标志拒绝；命令长度 ≤2000；超时（`PI_CMD_TIMEOUT`，默认 60s）整组 SIGKILL（`start_new_session` + `killpg`）。
- **最小环境白名单**：子进程仅继承 `PATH/LANG/HOME`（HOME=工作区），宿主代理/密钥等凭据不继承。
- **只读运行**：READ_ONLY（REVIEW/验收）步骤由 worker 传 `read_only=True`，工具集剔除 write/edit/run_command（只读证据不可改工作区）。
- **RT 事实基线**：`runtime_capability_report` isolation 新增 `sandboxProfile`（type/commandPolicy{shellEnabled:false, setuidRejected, deniedCommands}/process{timeoutSeconds, envWhitelist}/networkIsolation{none-host-network}/readOnlyToolsForReadOnlyRuns）——报告与实现一致（RT-04 NOT_IMPLEMENTED→PARTIAL，验收报告同步）。
- 简化差距（记录）：沙箱级进程/网络隔离（netns/cgroup/用户降权）未实现——`networkEnabledForTools=true`、`networkIsolation=none-host-network` 如实；SB-xx 全量（仅 lo/无路由/无 DNS）需容器运行（G 后续在单机容器部署可达成，当前单进程主机运行不伪造）。

## G1 编排与 Run 状态机（蓝图 §6.3/6.4/§8.2/§10.5.1 单机子集）

- 产物：`migrations/003_run_state_machine.sql`（`pi_runs` 表 + `pi_tasks.plan` 列）、`app/orchestrator.py`（`compile_plan`）、`app/runtime/run_state.py`（白名单 + 表操作）、worker 逐步执行接线、`GET /api/v1/tasks/{id}/runs`（RunOut）、`tests/test_run_state.py`（5 项）+ `tests/test_orchestrator.py`（8 项）。
- **Plan 先行**：Task 进入执行前 `compile_plan` 发布**签名 ExecutionPlanSnapshot**（INITIAL，产物必过 `verified_execution_plan` 语义校验）；旧任务（无 `plan`）编译为默认单步 IMPLEMENTATION（行为兼容回归）；`POST /tasks` 可携带 `plan` 步骤数组（多步：IMPLEMENTATION/READ_ONLY…按 plannedAttemptInputs 次序顺序执行，依赖由步骤次序保证）。
- **Run 状态机**（`pi_runs`，每任务每步一 Run，`UNIQUE(task_id, step_index)`）：`CREATED→READY→EXECUTING→OUTPUT_STAGED→VERIFYING→VERIFIED`；失败映射 `EXECUTING→FAILED|BUDGET_EXHAUSTED`；任意非终态→`CANCELLED`（cancel 同事务收敛全部活动 Run）；`recover_stale` 启动恢复将任务遗留活动 Run 一并收敛 `FAILED(PLATFORM_RESTART)`（不悬挂/不与任务终态漂移）。Attempt 每步独立（独立 AttemptId/BudgetGrant/ATTEMPT_* 事件），步骤终态同事务 `settle_grant`；Grant 结算按 task_id 全量 ACTIVE→SETTLED（多步多 grant 幂等收敛）。
- 事件扩展：`TASK_PLAN_COMPILED`（计划 id/digest/步骤数）、`RUN_CREATED`（runId/stepIndex/kind）、`ATTEMPT_STARTED/FINISHED` 带 runId/stepIndex。
- 简化差距（记录）：每步独立 BudgetGrant（同 `PI_MAX_BUDGET_TOKENS`，Task 级总预算收敛待 G6）；无 Lease/Fencing（§6.5 单实例单代次）；NO_VERDICT/HANDOFF_TO_HUMAN/BLOCKED/FAILED_DEPENDENCY/REPAIR_REQUIRED 为蓝图保留名（子集不达）；REVIEW runKind 不触 Git/评审流（随 G5）；计划每次编译生成新快照（新 input id），同一 task.plan 不重编译旧计划；运行时计划信封与 RT/attempt 共用节点密钥（issuer=pi.orchestrator，正式独立 Orchestrator 密钥随 Phase 0 ADR/G4）。

## 兼容性边界（CT-03 语义变更，如实披露）

- 启用 `canonicalSortKeys` 后，`attempt_contract`（schemaVersion="2"）的 `toolAllowlist` 语义由"有序（元素顺序即声明顺序，JCS 不重排）"改为"**集合（无序）**——投影前按元素值字节序 canonical sort，乱序传入 digest 稳定"。
- 因此同一 schemaVersion="2" 下：既有 `canonicalPayload`、`payloadDigest`、签名输入与 Ed25519 签名**全部失效**（toolAllowlist 原固化为非字母序）；已重生成全部向量（attempt 10 / task_spec 10 / event 8）并经 Python 主实现、Node 独立参考实现、Ed25519 真实验签与可复现性逐字节比对确认。
- 迁移策略：本阶段属契约基线内修订（正式基准未冻结）；进入正式基准前应提升契约版本（v3）或按蓝图 §4.4 以 ADR 冻结该变更。旧 digest/签名一律视为无效，不得用于验签。

## 已知边界（记录，不视为缺陷）

- canonical 编码为 "RFC8785-JCS-lite"：数字仅整数（浮点规范化差异规避）、键全 ASCII、`ensure_ascii` 转义约定——两实现已按同一规范逐字节一致；若蓝图后续引入浮点或非 ASCII 键，需按 RFC 8785 完整语义评估。
- 事件信封 `eventId` 采用 32 位 hex（对齐蓝图 §13.4 示例长度），`trace` 内 run/attempt 作用域 ID 为 16 位 hex（对齐既有 DB/契约约定）；蓝图 ULID 格式若有强制要求需再对齐。
- 事件信封 `payload` 宽松（`additionalProperties:true`），其业务事实的严格 Schema 由各事件类型另立（增量）；批量限制（payload 大小上限）留待 Gateway 预算层。
- 主实现语言为 Python（蓝图 §12.2 指 Go 主实现 + 非 Go 参考实现；本项目按既定技术栈决策以 Python 为主实现、Node 为独立参考实现，满足"两独立实现逐字节一致"精神）。