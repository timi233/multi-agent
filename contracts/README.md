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
| CT-03 集合数组乱序/重复 | PASS：`canonicalSortKeys` 机制（蓝图 §12："JCS 不替数组排序——集合数组先按稳定键排序、拒绝重复再计算 digest"）——attempt_contract `/toolAllowlist`（by=value）与 task_spec `/policyTemplateRefs`（by=key, templateRef）启用；乱序正向量 digest 与有序逐字节一致、canonical 层重复/缺键拒绝；Node 参考实现同规范同步（10 正向量 0 不一致）；数组字段必须声明 canonicalSortKeys（profile 一致性校验） |
| CT-04 必须签名对象正反向量 | PASS（attempt_contract 起步）：有效签名验通、篡改信封任一字段失败、错 keyId 失败；签名信封不改变 payloadDigest |
| CT-08 事件信封作用域必填 ID | PASS：Task 事件仅要求 taskId（无 runId/attemptId 合法）；Attempt 事件要求 taskId+runId+attemptId 三者，缺一即拒 |

## 状态机模型检查（对齐手册 §13.1 SM-xx）

- 模型与检查器：`scripts/sm_model.py`（Task 白名单直接取自 `app/control/lifecycle.py`；Attempt 模型从 `app/worker.py` 条件 UPDATE 提取）；报告 `contracts/sm-model/report.md`（可复现）。
- 固化断言：`tests/test_sm_model.py`（11 项）。
- 结果：**无违规**。SM-01 白名单穷举（25 对 + 套件全查）PASS；SM-02 终态闭合 PASS；SM-03 每个可达非终态存在 StateDeadlinePolicy 出口且在白名单内 PASS；SM-08 终态转移事件同事务 PASS；死枚举审计：Attempt `RUNNING`/`FAILED` 为声明未用（基线固化）。
- 已知差距（不并入通过判定）：SM-08 `Task QUEUED->RUNNING` 的事件（`ATTEMPT_STARTED`）在后续初始化事务写入，跨事务（1 项，`tests/test_sm_model.py::test_run_all_ok` 锁定）；运行时 deadline 定时留待 Gateway 预算层。

## 兼容性边界（CT-03 语义变更，如实披露）

- 启用 `canonicalSortKeys` 后，`attempt_contract`（schemaVersion="2"）的 `toolAllowlist` 语义由"有序（元素顺序即声明顺序，JCS 不重排）"改为"**集合（无序）**——投影前按元素值字节序 canonical sort，乱序传入 digest 稳定"。
- 因此同一 schemaVersion="2" 下：既有 `canonicalPayload`、`payloadDigest`、签名输入与 Ed25519 签名**全部失效**（toolAllowlist 原固化为非字母序）；已重生成全部向量（attempt 10 / task_spec 10 / event 8）并经 Python 主实现、Node 独立参考实现、Ed25519 真实验签与可复现性逐字节比对确认。
- 迁移策略：本阶段属契约基线内修订（正式基准未冻结）；进入正式基准前应提升契约版本（v3）或按蓝图 §4.4 以 ADR 冻结该变更。旧 digest/签名一律视为无效，不得用于验签。

## 已知边界（记录，不视为缺陷）

- canonical 编码为 "RFC8785-JCS-lite"：数字仅整数（浮点规范化差异规避）、键全 ASCII、`ensure_ascii` 转义约定——两实现已按同一规范逐字节一致；若蓝图后续引入浮点或非 ASCII 键，需按 RFC 8785 完整语义评估。
- 事件信封 `eventId` 采用 32 位 hex（对齐蓝图 §13.4 示例长度），`trace` 内 run/attempt 作用域 ID 为 16 位 hex（对齐既有 DB/契约约定）；蓝图 ULID 格式若有强制要求需再对齐。
- 事件信封 `payload` 宽松（`additionalProperties:true`），其业务事实的严格 Schema 由各事件类型另立（增量）；批量限制（payload 大小上限）留待 Gateway 预算层。
- 主实现语言为 Python（蓝图 §12.2 指 Go 主实现 + 非 Go 参考实现；本项目按既定技术栈决策以 Python 为主实现、Node 为独立参考实现，满足"两独立实现逐字节一致"精神）。