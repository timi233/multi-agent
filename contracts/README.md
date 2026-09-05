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

## 验证结果（对齐手册 §12.2）

| 项 | 结果 |
|---|---|
| CT-01 canonicalPayload/digest 逐字节一致 | PASS：Python 主实现 vs Node 独立参考实现，2 正向量 0 不一致 |
| CT-02 未知字段/未知枚举/超限字段拒绝 | PASS：6 负向量全部 Schema 拒绝 |
| CT-03 集合数组乱序/重复 | 部分：`uniqueItems` 重复拒绝 PASS；canonical 排序语义留待后续对象启用 |
| CT-04 必须签名对象正反向量 | PASS（attempt_contract 起步）：有效签名验通、篡改 1 字节失败、错 keyId 失败；**签名信封不改变 payloadDigest**（白名单外） |

## 已知边界（记录，不视为缺陷）

- canonical 编码为 "RFC8785-JCS-lite"：数字仅整数（浮点规范化差异规避）、键全 ASCII、`ensure_ascii` 转义约定——两实现已按同一规范逐字节一致；若蓝图后续引入浮点或非 ASCII 键，需按 RFC 8785 完整语义评估。
- 目前仅 attempt_contract 一个对象类型基线；task_spec、事件信封、各 Snapshot 等对象按相同机制增量落地。
- 主实现语言为 Python（蓝图 §12.2 指 Go 主实现 + 非 Go 参考实现；本项目按既定技术栈决策以 Python 为主实现、Node 为独立参考实现，满足"两独立实现逐字节一致"精神）。