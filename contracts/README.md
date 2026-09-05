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

## Runtime 能力报告（RT，蓝图 §8.2 六问 / 手册 RT-xx 简化版）

- 产物：`contracts/jsonschema/runtime_capability_report.v2.{schema,digestprofile}.json`、`app/runtime/capabilities.py`、`GET /api/v1/runtime/capabilities`、`tests/test_runtime_capabilities.py`（7 项）。
- 语义：引擎事实基线（工具集/模型路由/资源默认参数/隔离边界/已知差距）；**进程内缓存生成一次**（缓存幂等且返回 deepcopy，外部不污染内部事实；`generatedAt` 固定）；`contractId = sha256(JCS(核心事实，不含 generatedAt))[:32]` 复用项目 canonical（jcs）、事实/集合顺序变化即变；签名信封复用 Phase 0 codec（蓝图 §9.4 十字段），**`signature.value` 为真实 Ed25519 签名**——私钥持久化于 `data/keys/runtime_ed25519.pem`（data/ 已排除 git、权限 600），`keyId`=公钥指纹（跨重启稳定），验签用 `app.security.keys.verify`；工具集为集合数组（canonicalSortKeys by=name），供准入/Gateway 身份绑定引用。
- RT-xx 对照（差距已入报告 `knownGaps`，随实现移除）：RT-01 无沙箱故不适用（进程启动正常）；RT-02/03/04/05/07 未达（沙箱/管道/驱动幂等）；RT-06 真实模型链路可跑但证据无 RouteAttestation；RT-08 未达。另含 GW-08（撤销新鲜度）、GW-10（热路径 PG）两项。

## 预算契约 budget_grant v2（② 契约对象扩展）

- 产物：`contracts/jsonschema/budget_grant.v2.{schema,digestprofile}.json`、`scripts/gen_budget_vectors.py`（10 向量：空 journal/链式全生命周期/FAILED 释放/UNKNOWN/pos-signed + 5 负例）、`scripts/verify_vectors_node.js` 已含该对象、`tests/test_budget_contract.py`（8 项）。
- 对象边界：签名 payload 只锚定**不可变授权字段**（grantId/taskId/attemptId/totalBudgetTokens/createdAt 等）；`consumedTokens/status/journal` 为**消费事实**（mutableDatabasePointers，不投影）——每次结算/失败/未知改变事务事实但不改变 Grant 授权签名（`test_grant_immutable_consumption_mutable` 覆盖）。
- 链式 Journal：RESERVED/SENT/SETTLED/FAILED/UNKNOWN 条目含 `previousEntryDigest/entryDigest`（首条根锚 `pi-budget-root-v1`），向量链为 `_entry_digest` **逐条真实计算**；有序性/逐条 digest/consumed 对账由 `BudgetDomain.verified_budget_grant()` 语义校验入口保证（测试覆盖篡改断链/伪造 digest/对账不符）。
- 双实现：Node 参考实现逐字节一致（CT-01 PASS，15 正向量 0 不一致）。

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

## 兼容性边界（CT-03 语义变更，如实披露）

- 启用 `canonicalSortKeys` 后，`attempt_contract`（schemaVersion="2"）的 `toolAllowlist` 语义由"有序（元素顺序即声明顺序，JCS 不重排）"改为"**集合（无序）**——投影前按元素值字节序 canonical sort，乱序传入 digest 稳定"。
- 因此同一 schemaVersion="2" 下：既有 `canonicalPayload`、`payloadDigest`、签名输入与 Ed25519 签名**全部失效**（toolAllowlist 原固化为非字母序）；已重生成全部向量（attempt 10 / task_spec 10 / event 8）并经 Python 主实现、Node 独立参考实现、Ed25519 真实验签与可复现性逐字节比对确认。
- 迁移策略：本阶段属契约基线内修订（正式基准未冻结）；进入正式基准前应提升契约版本（v3）或按蓝图 §4.4 以 ADR 冻结该变更。旧 digest/签名一律视为无效，不得用于验签。

## 已知边界（记录，不视为缺陷）

- canonical 编码为 "RFC8785-JCS-lite"：数字仅整数（浮点规范化差异规避）、键全 ASCII、`ensure_ascii` 转义约定——两实现已按同一规范逐字节一致；若蓝图后续引入浮点或非 ASCII 键，需按 RFC 8785 完整语义评估。
- 事件信封 `eventId` 采用 32 位 hex（对齐蓝图 §13.4 示例长度），`trace` 内 run/attempt 作用域 ID 为 16 位 hex（对齐既有 DB/契约约定）；蓝图 ULID 格式若有强制要求需再对齐。
- 事件信封 `payload` 宽松（`additionalProperties:true`），其业务事实的严格 Schema 由各事件类型另立（增量）；批量限制（payload 大小上限）留待 Gateway 预算层。
- 主实现语言为 Python（蓝图 §12.2 指 Go 主实现 + 非 Go 参考实现；本项目按既定技术栈决策以 Python 为主实现、Node 为独立参考实现，满足"两独立实现逐字节一致"精神）。