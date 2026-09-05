# S1 基础设施搭建与验证记录（实验配置）

- 依据：《环境搭建与验证手册 v1.3.2》§1.3（S1 宿主与基础设施）、§2、§4.1～§4.4
- 声明：**实验配置**。IN-01～IN-12 未完成 Phase 0 冻结，宿主 H-02/H-03/H-06 不达标，本记录不构成正式验证通过（手册 §0 规则 1、§18.2）。
- 执行时间：2026-09-05 UTC
- 执行者：Reasonix（jian）　复核者：**待指派（独立复核者 ≠ 执行者）**

## 已搭建组件与证据

| 组件 | 镜像 digest | 状态 | 关键证据 |
|---|---|---|---|
| PostgreSQL 15.19 (alpine) | `postgres@sha256:fe0737ba...e57f1b` | healthy | timezone=UTC；DB now() 与宿主 UTC 偏差约 5ms；12 个 svc_* 唯一写入者角色（LOGIN）；CONNECT 授权；log_connections 审计生效 |
| MinIO | `minio/minio@sha256:14cea493...936e` | healthy | buckets `staging/`（临时区，无锁）、`cas/`（对象锁 COMPLIANCE 90DAYS）；数据版本深删被拒 `WORM protected and cannot be overwritten`；staging 上传/删除正常 |
| OTel Collector contrib v0.160.0 | `otel/...@sha256:799dc6cf...72ad6` | healthy | OTLP/HTTP 4318 接收 probe span；debug exporter `spans:1`；file exporter 落盘 `data/otel-logs/otel-events.json`（含 traceId/taskId/service.name 属性） |
| Key Registry（实验） | - | 生成完毕 | 11 个 Ed25519 keyId（私钥 0600）；`keys.lock.json`（issuer/allowedObjectTypes/指纹）；签名往返 Verified；跨 key 反例 Failed（KY-01 雏形） |

## 手册验证项对照（S1 范围内）

| 手册项 | 结果 | 说明 / 证据位置 |
|---|---|---|
| H-01 cgroup v2 | PASS | 宿主 `stat -fc %T /sys/fs/cgroup` → cgroup2fs |
| H-02 时区与时间源 | **部分** | PG 容器内 UTC 且时钟偏差 ~5ms（<2s 达标）；宿主时区 CST，未改（实验配置不强制） |
| H-03 MAC 启用 | **FAIL（不适用）** | AppArmor/SELinux 未启用；正式环境必办 |
| H-04/H-05 用户隔离/ptrace | **待执行** | 宿主无 pi-* 用户；涉及系统级改动，S1 不做 |
| H-06 存储卷分离 | **FAIL（不适用）** | 单块磁盘；正式环境必办 |
| DB-xx（§4.1） | DB-01 待执行（表未创建）；role/授权/UTC/审计 已 PASS | 迁移与权限授予留待实现仓库 |
| OS-01 cas WORM | PASS | `mc rm --versions --force` 对数据版本拒绝；版本化语义：delete marker 可移除、数据版本不可销毁 |
| OS-02/OS-03 | 待执行 | 属应用层（PublishArtifactManifest 事务），S1 无实现 |
| OS-04 沙箱内不可达 | 待执行 | 需 Attempt 沙箱（Phase 1） |
| OB-01 traceId 链 | 待执行 | 需服务实现；S1 验证了 OTel 本身可携带/落盘这些字段 |
| OB-02 token 零泄露 | 待执行 | 需服务实现 |
| KY-01 objectType/issuer 不匹配 | PASS（雏形） | sk-attestation 签名用 sk-node 公钥验证 → Signature Verification Failure |
| KY-02 撤销后失效 | 待执行 | 需 Revocation Service |
| KY-03 私钥材料扫描 | PASS（S1 组件） | MinIO/OTel/PG 镜像内无本项目私钥（私钥仅存在于宿主 deploy/keys，0600） |

## 运行控制值（手册 §0 规则 2 提示）

IN-09/IN-10 的 30s/10s/60s 等数字仅引用蓝图初值，本环境未用于任何 Lease/Grant 逻辑；待 Policy Snapshot 发布后以正式值为准。

## 遗留与后续（S2 起）

1. 宿主准备（H-03/H-04/H-05/H-06）需在正式环境完成，本机不强制。
2. PostgreSQL 表结构、迁移、逐角色权限授予（DB-01～DB-09）依赖实现仓库。
3. `deploy/keys/` 实验 keyId 表须在 Phase 0 ADR 冻结后重新签发并登记 Key Registry。
4. `inputs.lock.json` digest 已固化；任何配置改动后需重算并更新。

## 复核

- [ ] 独立复核（待指派）已核对本记录与归档证据一致（对应蓝图附录 D 签署页前置）。