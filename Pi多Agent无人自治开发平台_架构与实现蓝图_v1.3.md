# Pi 多 Agent 无人自治开发平台

## 架构与实现蓝图（v1.3）

文档状态：设计基线候选稿  
基线日期：2026-09-04  
适用范围：内部单租户；首个兑现阶段仅处理 PUBLIC / 已脱敏代码任务  
前序基线：v1.2 保留不改；本文件是其增量实现蓝图，两者及本文件的“继承清单”共同构成 v1.3 规范  
本轮交付范围：Markdown 主文档。可视化图不属于 v1.3 规范性基线；若后续单独制作，只能作为本文的派生视图，不能新增或改变权威关系。

---

## 0. 执行结论

v1.3 的核心不是继续增加 Agent 数量，而是把 v1.2 的约束转化为一个能够真实落地、逐阶段验收、失败时安全收敛的工程蓝图。

本版做出七项确定性决策：

1. **首个可兑现版本是安全单节点纵向 MVP。** 平台运行于 Linux x86_64 单节点容器环境，仅处理 PUBLIC / 已脱敏任务，只创建候选分支，不自动合并、不部署生产。
2. **运行时 Agent 永远不能改变自己的 Skill、权限或成功标准。** Attempt 创建前，签名 ExecutionPlan 已冻结 Workflow、Role / Skill、Prompt / Tool、Route / Model、Gate、Source、Runtime / Sandbox / Dependency 镜像及 Compatibility、预算与撤销水位；AttemptContract 只能逐字段绑定其中一个预定输入。
3. **SkillRoster 被接纳，但只作为离线、可替换、只读优先的 SkillInventoryAdapter。** v1.3 首期只消费 scan/report 的结构化输出，不把 plan/apply/undo、安装或动态加载能力带入运行时。
4. **平台拥有 Skill 权威。** PostgreSQL 中的 Skill Registry 决定身份、状态、审批和撤销；内容寻址 Artifact Store 保存精确字节；独立 Signer 只签署被批准的摘要。SkillRoster 的本地 SQLite 不是平台权威。
5. **成功只能由冻结的验收合同和确定性 Gate 证明。** Reviewer Agent 或 Semantic Judge Agent 的模型意见只能成为非权威证据；受信任宿主 Gate Supervisor 捕获的 GateExecutionFact 与纯规则 Verdict Aggregator 才参与机械判定。
6. **任何 Git 写入都发生在沙箱之外。** Attempt 只产出补丁、文件树和证据；Git Stager 在控制面批准后向任务专属候选分支写入，受保护分支、自动合并和生产发布始终禁止。
7. **分阶段晋级必须由证据包驱动。** 安全、假成功、越权和证据完整性是硬门槛，不能用平均成功率抵消；只有一名实际复核人时，成果最高只能标记为 PoC。

按至少 5 名核心 FTE 并配独立安全 / 评测投入估算，Phase 0 至 Phase 2 的首个内部 MVP 预计需要 **13～19 周**；到 Phase 4.5 的有限生产可用候选，不含可选 HA 预计 **34～49 周**，含 Phase 4B HA 预计 **38～57 周**。工作可以并行，但阶段门槛不能跳过。

---

## 1. 设计目标、边界与非目标

### 1.1 v1.3 要解决的问题

平台需要把“一个或多个 Agent 能写代码”提升为“一个受控系统能够持续地产生可审查候选变更”，并回答以下工程问题：

- 谁可以创建、修改和终结 Task、Run、Attempt？
- Agent 使用的模型、Prompt、Role、Skill、工具和沙箱环境如何被精确冻结？
- 模型输出、进程退出、测试结果和外部副作用分别由谁证明？
- 租约过期、节点宕机、重复消息、晚到结果和撤销如何收敛？
- Skill 如何从不可信来源进入离线治理区，经过评测、审批、签名和发布后成为只读运行时制品？
- 平台如何证明没有假成功、越权写入、运行时配置漂移和不可追溯副作用？
- 如何用明确的 Go / No-Go 指标决定下一阶段，而不是按功能清单主观宣告完成？

### 1.2 首期业务范围

Phase 2 的任务白名单仅包括：

- 确定性缺陷修复；
- 测试补充；
- 小型、边界明确、验收可自动化的功能；
- PUBLIC 仓库或完成脱敏的内部样例仓库；
- 无生产数据、无生产凭据、无自动部署需求的任务。

每个任务必须在进入平台前具备：

- 明确的仓库与冻结基线；
- 可机器判定的验收条件；
- 明确的允许修改路径和禁止修改路径；
- 资源、时间、Token 与成本预算；
- 数据分级与外部副作用声明；
- 候选分支命名和人工评审责任人。

### 1.3 首期明确不做

- 不处理 SECRET、受监管或未经批准的 INTERNAL 数据；
- Attempt 与 Gate 代码执行沙箱完全断网；模型调用意图只能交给沙箱外 Node Runtime Proxy 代转，沙箱不持有 Gateway Grant；
- 不自动安装未知 Skill、Extension、MCP、解释器、依赖或全局配置；
- 不支持运行时 On-demand Skill；
- 不允许 Agent 调用 SkillRoster 改变 Skill；
- 不允许 Attempt 持有 Git Remote 通用写凭据；
- 不自动合并默认分支或受保护分支；
- 不自动部署到生产；
- 不以同一物理主机上的多容器副本宣称高可用；
- 不把模型自评、进程退出码或“任务已完成”文本直接当作成功；
- 不在 Phase 2 引入 Kubernetes、复杂消息中间件和跨区域容灾。

### 1.4 成功的准确含义

首期平台成功不是“无人负责”，而是：

- 任务执行阶段不需要人工逐步操作；
- 所有权限、规格、Skill、模型路线、预算和验收合同在运行前已批准；
- 平台自动完成执行、验证、证据归档和候选分支创建；
- 人类仍负责政策、审批、例外、候选分支评审、最终合并和生产发布；
- 一旦证据不足或边界不满足，系统优先安全失败或交接人工，不得猜测成功。

---

## 2. v1.2 到 v1.3 的收敛变化

v1.2 已建立 Task / Run / Attempt、Lease / Fencing、Route Attestation、Verification Plane、Artifact、Git Saga、预算与安全治理的主体框架。v1.3 不推翻这些约束，而是将其组织成可实现的仓库、组件、接口、供应链和阶段交付计划。

| 主题 | v1.2 状态 | v1.3 决策 |
|---|---|---|
| 文档形态 | 完整闭环架构，但存在大量阶段待办 | 形成独立实现蓝图，明确默认环境、仓库结构、接口和 DoD |
| Skill | 作为 Role Pack / SandboxProfile 的静态能力理解 | 新增 SkillPackage、SkillEvaluation、SkillApproval、SkillBundleSnapshot 与撤销模型 |
| SkillRoster | 尚未进入正式组件图 | 只进入离线治理域，作为可替换 Adapter，不进入 Attempt |
| AttemptContract | 已冻结主要运行参数 | skillBundleSnapshotDigest、bundleArtifactDigest、expectedMountedSkillTreeDigest 与 skillPolicyDigest 升为必填字段 |
| Runtime | 路线 A：Prime 包裹 Pi | 固定 Prime Agent v0.9.1 作为兼容基线；脚本 Provider 先行；Native Pi Driver 并行验证 |
| 部署 | 从单节点逐步到生产硬化 | Phase 2 明确为 Linux x86_64 单节点、PostgreSQL、MinIO/S3、一个 Git Adapter |
| 消息 | Outbox / Inbox 原则已确定 | MVP 使用 PostgreSQL Transactional Outbox + Worker；容量触发后再引入 Broker |
| Git | 候选与合入闭环均已设计 | Phase 2 只实现候选分支；Merge Executor 与生产发布保持关闭 |
| 验收 | 有阶段与基准框架 | 每阶段增加演示效果、定量门槛、No-Go 和回退动作 |
| 人员约束 | Four-Eyes 为关键变更要求 | 单人条件下只能发布 PoC 结论，不能进入生产 ACTIVE 或 Canary |

### 2.1 保留且提升为不可破坏的不变量

1. Task、Run、Attempt 拥有独立状态机和字段级唯一写入者。
2. Attempt 沙箱和 Prime/Pi Runtime 是不可信域，只能产生事实与制品，不能决定平台成功。
3. 终态必须经过强终态屏障、证据完整性检查和 Verdict 聚合。
4. Lease、epoch 与 Fencing Token 是分布式写入的必要条件。
5. 所有重要输入和输出按内容摘要寻址，标签和路径不是身份。
6. 外部副作用必须具备幂等键、读回比对、最小权限和可恢复流程。
7. Prompt、Role Pack、SkillBundle、Gate Pack、模型策略和镜像分别版本化，分别撤销，禁止捆绑为一个不可分析的大版本。
8. 撤销使用 Overlay 与单调 epoch，不改写历史快照。

---

## 3. 总体架构

### 3.1 三条闭环主链

平台由三条相互约束、但权威分离的链路组成。

**执行链**

任务入口 → 控制面 → Source Ingestor → Node Agent → Attempt 沙箱 / Prime-Pi Runtime → Node Runtime Proxy → Model Gateway → 批准的模型服务

**证据与交付链**

Attempt 沙箱 → Node 代理封存制品 → Gate Supervisor → Gate Command Sandbox → Attestation / Semantic Review / Verdict → Git Stager → 候选分支

**Skill 供应链**

允许来源 → 隔离快照 → SkillRoster 结构证据 → 独立安全与许可门禁 → Four-Eyes → SkillBundle 构建与签名 → Registry 发布 → Attempt 只读挂载

本节三条文本链路与 §4 信任域矩阵共同构成规范性全局视图。后续若生成架构图，必须从这些权威关系派生并单独校验；图本身不作为实施输入。

### 3.2 控制环

平台保留双层自治：

- **任务内控制环**：执行 → 收集证据 → Gate → 有界 Repair → 终态。
- **平台治理环**：离线评测 → 审批 → 发布 → Shadow / Canary → 监控 → 撤销或升级。

两层之间只能通过不可变快照和审计事件连接。任务内 Agent 不能进入平台治理环修改模型、Prompt、Role、Skill、Gate 或权限。

### 3.3 MVP 物理部署

Phase 2 使用一台 Linux x86_64 主机，容器化部署以下进程：

- Control API / Orchestrator；
- Lifecycle / Lease Worker；
- Registry / Policy / Revocation Worker；
- Verification Worker；
- Git Stager；
- Node Agent；
- Repository Source Ingestor；
- Node 侧 PrimeRuntimeDriver / Runtime Proxy；
- Model Gateway；
- 受信任 Gate Supervisor、隔离 Gate Command Process 与独立 Attestation / Verdict Worker；
- 离线 Dependency Image Builder 与 Skill Governance Worker；
- PostgreSQL；
- MinIO 或兼容 S3 的对象存储；
- OpenTelemetry Collector 与基础监控。

控制面进程可以拆成多个二进制，但首期共用一个 Go 代码库和一个 PostgreSQL 集群。逻辑边界必须在代码、数据库写入权、服务身份和测试中成立，不能因单节点部署而省略。

### 3.4 沙箱与模型通道

“Attempt / Gate 完全断网”是继承 v1.2 ADR-15 的硬约束：仓库代码执行环境没有网卡、默认路由、DNS、代理、Gateway Socket、对象存储通道或任何可继承网络文件描述符。模型能力由沙箱外的 Node Runtime Proxy 代理：

1. Prime/Pi Runtime 通过受监督的进程控制协议向 Node 报告一个 `ModelCallIntent`；该协议是 Runtime Driver 的 stdio / 管道控制面，不是供仓库代码任意访问的网络能力。
2. Node 校验 attemptId、leaseId、controlPlaneEpoch、resourceExecutionEpoch、全局 revocationEpoch、AttemptContract、AttemptRouteSnapshot、预算与调用序号。
3. 只有 Node 的工作负载身份可向 Domain 5 请求最短时效 `GatewayGrant`；Attempt、仓库子进程、Gate Supervisor 和 Gate Command Process 从不看到该 Grant。
4. Model Gateway 仅执行冻结路线，生成 Route Attestation 与用量事实，并把响应交回 Node。
5. Node 经 Driver 协议把受大小、类型和序号约束的模型响应送回 Runtime；仓库进程无法指定主机、URL、代理、DNS、重定向或供应商凭据。

任何无法证明文件描述符隔离、调用归属或响应关联的实现都输出 `NO_VERDICT`，不得以“本地 Socket”作为断网例外。

### 3.5 Git 通道

Repository Source Ingestor 先在沙箱外把允许的 repository / ref 解析为精确 `GitObjectId`，固定 submodule 与 LFS，生成签名 `SourceBundle`。Attempt 只读接收这个冻结输入，并在隔离工作区产生：

- 文件树或补丁；
- 测试结果；
- Build / Lint / Security Gate 原始证据；
- ProposedTree；
- ArtifactManifest。

Domain 8 内的 Commit Assembler 把 `SourceBundle + ProposedTree + CommitIntent + 规范化提交元数据` 编译为不可变 `CommitBundle`，预计算 tree 与 commit 的 `GitObjectId`；Domain 2 的受信任 Gate Supervisor 再在独立 Domain 7 Gate Command Sandbox 中逐步骤执行这个精确对象。Git Stager 位于高信任域，但**绝不执行仓库代码、重放补丁或重跑 Gate**：

1. 校验签名 CommitBundle、ACCEPTED Verdict、PreDeliveryEvidence 与 DeliveryAuthorization；
2. 校验任务专属 candidate ref、expected ref、独立 GitStagingLease、controlPlaneEpoch、gitStagingEpoch、全局 revocationEpoch 与幂等键；
3. 上传 CommitBundle 已固定的 Git 对象，不重新解释补丁；
4. 对候选 ref 执行 CAS；
5. 读回 ref、commit `GitObjectId` 与 `X-Platform-Operation-Key` trailer；
6. 写入 GitStagingResult，并由 Evidence Service 封装最终 DeliveryEvidenceBundle。

Phase 2 不启用 Merge Executor。任何默认分支、受保护分支或生产发布操作都应在策略层硬拒绝。

---

## 4. 信任域

v1.3 原样延续 v1.2 的 10 个域，并新增 3 个 Skill 供应链域。PostgreSQL 属于 Domain 1 的状态基础设施，不占用或改写信任域编号。

| 域 | 信任级别 | 可以做什么 | 绝对不能做什么 |
|---|---|---|---|
| 1. Control Plane | 高 | 规格、编译、调度、状态、预算、策略、撤销 | 直接相信模型输出或跳过 Gate |
| 2. Node Agent | 中高 | 领取租约、创建沙箱、监督进程、测量资源 | 决定业务成功或复用过期租约 |
| 3. Attempt Sandbox | 不可信 | 修改隔离工作树、产出事实和制品 | 持有控制面、Git 或供应商通用凭据 |
| 4. Prime/Pi Runtime | 不可信 | 按冻结合同调用批准能力 | 修改 Skill、权限、路线、Gate 或终态 |
| 5. Model Gateway | 高 | 认证、冻结路由、预算、限流、审计 | 接受 Agent 自选供应商、模型或凭据 |
| 6. Model Provider | 外部不可信 | 返回模型输出和用量 | 决定平台状态 |
| 7. Gate Execution Sandbox | 不可信且独立 | 对精确 CommitBundle 逐步骤运行确定性 Gate | 持有签名密钥、Git 写权或宣告成功 |
| 8. Attestation / Verdict | 高且不执行仓库代码 | 签署 Gate 事实、检查证据、机械聚合 Verdict | 调用 LLM、执行仓库代码或伪造缺失证据 |
| 9. Git Stager / Merge Executor | 高且最小权限 | Phase 2 仅写任务专属候选 ref、读回校验 | 执行仓库代码、写受保护分支、自动合并 |
| 10. Artifact Store | 高、内容载体 | 保存按摘要寻址的不可变字节 | 单独决定审批和业务状态 |
| 11. Skill Intake / Scanner / Builder | 中、处理不可信输入 | 冻结来源、扫描、评测、构建候选 Bundle | 连接生产控制面、持有签名密钥、直接发布 |
| 12. Skill Registry / Approval | 高、Skill 状态权威 | 身份、评测、审批、Bundle 状态、撤销 | 执行 Skill 或单独签名 |
| 13. Skill Signer / Publisher | 高、职责分离 | 对已批准精确摘要签名和发布 | 分析内容、自创审批、接受 latest 或路径身份 |

### 4.1 强制隔离关系

- Domain 3 / 4 / 7 到 Domain 11 / 12 / 13：禁止网络和管理 API 访问；
- Domain 3 到 Git Remote：禁止；
- Domain 3 到公网：禁止；
- Domain 3 / 4 / 7：完全断网；Domain 4 的模型意图只由 Domain 2 Node Runtime Proxy 代转至 Domain 5，Domain 4 不持有 Gateway Grant；
- Domain 11 到生产 PostgreSQL：禁止直连，只能向 Registry Intake API 提交已校验结构化证据；
- Domain 12 到 Domain 13：只提交精确 skillBundleSnapshotDigest、bundleArtifactDigest、bundleManifestDigest、审批证明和 policyDigest；
- Domain 13 到 Artifact Store：只发布签名与被批准对象，不产生业务审批；
- Node Agent 到 Attempt：只读挂载冻结 Bundle，运行期不能替换。
- Domain 7 Gate Command Process 只能经不可继承的受监督管道输出字节与退出；它无权提交 GateResult。Domain 2 Gate Supervisor 以 waitpid / cgroup / timeout / 流式哈希形成 GateExecutionFact，再向 Domain 8 提交其 digest 与 VerificationLease 证明；Domain 8 不执行仓库代码。
- Domain 8 到 Domain 9：只提交签名 DeliveryAuthorization；Domain 9 不接受 Attempt Lease。

### 4.2 共享与复用规则

- 不同 Attempt 不共享可写文件系统；
- 缓存仅按内容摘要复用，且挂载只读；
- Session Checkpoint 必须绑定 Attempt、Runtime、Route、Role、SkillBundle、Prompt、镜像和 epoch；
- 任何摘要、签名、审批或撤销状态不一致，均 Fail Closed；
- 节点缓存不是权威，不允许在 Registry 或 Artifact Store 不可确认时回退到“最近可用版本”。

---

## 5. 权威边界与唯一写入者

| 对象 / 字段 | 字节或状态权威 | 唯一逻辑写入者 | 其他组件的角色 |
|---|---|---|---|
| TaskSpec 内容 | PostgreSQL | Task API | Lifecycle 与 Agent 只读 |
| TaskAdmissionDecision | PostgreSQL + 签名证据 | Task API | Lifecycle 只读；不能替代治理对象的 Four-Eyes |
| Task.state | PostgreSQL | Lifecycle Service | Task API 只能发命令 |
| WorkflowSnapshot | Registry 元数据 + Artifact 字节 | Workflow Compiler | Orchestrator 只引用摘要 |
| ExecutionPlanSnapshot | Registry 元数据 + Artifact 字节 | Execution Plan Compiler | Lifecycle / Orchestrator 只读；Attempt 必须引用其中一个 plannedAttemptInputDigest |
| EvaluationPlanSnapshot | Registry 元数据 + Artifact 字节 | Evaluation Publisher | Verdict 只引用摘要 |
| Run.state / selectedAttemptId | PostgreSQL | Lifecycle Service | Orchestrator 只发调度意图 |
| Attempt 创建与 CREATED/READY | PostgreSQL | Orchestrator 经 Attempt Service | claim 不重复创建 Attempt |
| Attempt 的 CLAIMED、PROVISIONING、RUNNING、TERMINATING、TERMINAL_REPORTED 及 FAILED_PROVISIONING | PostgreSQL | Node Agent 经 Attempt Service，且必须有有效 Lease/Fencing | Lifecycle 校验事实 |
| Attempt 的 OUTPUT_STAGED、SELECTED、SUPERSEDED、CANCELLED、BUDGET_EXHAUSTED、FAILED、QUARANTINED、LOST、FENCED、TIMED_OUT | PostgreSQL | Lifecycle Service | Node、Artifact、Gateway 只发事实事件；强制终态不依赖失联 Node |
| AttemptContract | PostgreSQL + 签名证据 | Attempt Service 在 claim 事务中一次性生成 | Node 只读验证；续租不修改合同 |
| AttemptTerminalEnvelope | Artifact Store + PostgreSQL 引用 | Node Agent 经 Attempt Service | Lifecycle 只聚合 |
| ExecutionLease / resourceExecutionEpoch | PostgreSQL | Lease Service | Node、Gateway 校验；Git 不复用此 Lease |
| ExecutionAssignment | PostgreSQL | Lease Service | Node 只读 |
| GitStagingLease | PostgreSQL | Delivery Authorization Service | Git Stager 逐请求校验 |
| CommitIntent | PostgreSQL + 签名证据 | Delivery Service 在 Gate 前一次性生成 | Commit Assembler / Git Stager 只读；先分配稳定 operation key，不包含 Verdict 后字段 |
| CandidateStagingOperation.state | PostgreSQL | Delivery Service | Git Stager 只上报外部事实；Lifecycle 只读取结果推动 Task |
| DeliveryAuthorization | PostgreSQL + 签名证据 | Delivery Authorization Service | Git Stager 只读；仅在 ACCEPTED Verdict 后签发 |
| RoutingIntentSnapshot | Registry | Route Resolver | Runtime 不能改 |
| AttemptRouteSnapshot | Registry | Route Resolver，在 claim 前冻结 | Gateway 只生成调用级 Attestation |
| RouteAttestation | Evidence Store | Model Gateway | 不能回写 Route Snapshot |
| Budget reservation / settlement | Budget Ledger | Ledger Service | Gateway、Node 上报带 invocationId 的事实 |
| RolePackSnapshot | Registry 元数据 + Artifact 字节 | Role Pack Publisher | Attempt 只读 |
| PromptSnapshot | Registry 元数据 + Artifact 字节 | Prompt Publisher | Attempt 只读 |
| ToolPolicySnapshot | Registry 元数据 + Artifact 字节 | Tool Policy Publisher | Attempt 只读 |
| GatePackSnapshot | Registry 元数据 + Artifact 字节 | Gate Pack Publisher | Gate Supervisor / Command Process 只读 |
| SandboxProfileSnapshot | Registry 元数据 + Artifact 字节 | Sandbox Profile Publisher | Node / Gate Supervisor 只读 |
| ModelPolicySnapshot | Registry 元数据 + Artifact 字节 | Model Policy Publisher | Gateway 只读 |
| RuntimeImageBuild / SandboxImageBuild | Artifact Store CAS + Registry 引用 | 对应 Execution Image Builder | Builder 不审批、不签名、不发布 |
| RuntimeImageSnapshot / SandboxImageSnapshot 签名 | Evidence Store | Execution Image Signer | Signer 只签已批准 payloadDigest，不改镜像内容 |
| RuntimeImagePublication / SandboxImagePublication | PostgreSQL 发布状态与引用 | Execution Image Publisher | 只校验审批、SBOM、provenance、签名、兼容矩阵与 digest |
| ExecutionImageCompatibilityCandidate | Registry + Evidence | Compatibility Evaluator | 只绑定已形成的镜像 / Profile digest 与测试证据，不签名、不发布 |
| ExecutionImageCompatibilitySnapshot | Registry + 签名证据 | Compatibility Signer | Policy Approver 先决定；不可变且不承载发布状态 |
| ExecutionImageCompatibilityPublication | PostgreSQL 发布状态与引用 | Compatibility Publisher | 只发布精确已批准、已签名、未撤销的 Compatibility Snapshot |
| SkillPackageVersion 内容 | Skill Registry | Skill Intake Service | SkillRoster 仅提供 Evaluation 输入 |
| SkillPackageVersion.lifecycleState | Skill Registry | Skill Governance Lifecycle Service | Intake / Evaluator 只发事实 |
| SkillEvaluation | Skill Registry + Evidence | 对应独立 Evaluator | Approval Aggregator 只读 |
| ApprovalScopeSnapshot / ApprovalProposal | Skill Registry | Governance Proposal Service | Approver 不能改范围 |
| ApprovalDecision | Skill Registry + 签名证据 | Approver 经 Governance API | Aggregator 只读 |
| ApprovalSet | Skill Registry | Approval Aggregator | Builder 只读 |
| PolicyApprovalScopeSnapshot / PolicyApprovalProposal | Registry | Policy Governance Proposal Service | Approver 不得改 subject、基线、diff、quorum 或 Gate 范围 |
| PolicyApprovalDecision | Registry + 签名证据 | Policy Approver 经 Governance API | Aggregator / Publisher 只读 |
| PolicyApprovalSet | Registry | Policy Approval Aggregator | 对 canonical Decision 集机械计算 quorum / veto；Publisher 只读 |
| SkillBundleBuild / 未签名 Snapshot | Skill Registry + Artifact 字节 | Skill Builder | Signer 不能改内容 |
| SkillBundleSignature | Evidence Store | Skill Signer | Publisher 只读校验 |
| SkillPublicationPointer | Skill Registry | Skill Publisher | Runtime 只读解析 |
| SourceBundle 字节与 Manifest | Artifact Store CAS + PostgreSQL 引用 | Source Ingestor 经 Artifact Service | Attempt 只读 |
| DependencyImageBuild / 未签名 Snapshot | Artifact Store CAS + PostgreSQL 引用 | Dependency Builder 经 Artifact Service | Signer 不能改内容；Attempt / Gate 不接受未发布对象 |
| DependencyImageSignature | Evidence Store | Dependency Image Signer | Signer 不构建镜像、不做审批、不改 payload |
| DependencyImagePublication | PostgreSQL 发布状态与引用 | Dependency Image Publisher | 只校验审批、签名、扫描与精确 digest |
| Attempt 运行时输出字节 | Artifact Store CAS | Node Agent 经 Artifact Service | Agent 不能宣告其有效 |
| ArtifactManifest 发布状态与引用 | PostgreSQL | Artifact Service | Store 单独存在不代表已发布 |
| CommitBundle 内容 / 未签名 Snapshot | Artifact Store CAS + PostgreSQL 引用 | Commit Assembler 经 Artifact Service | Gate / Git Stager 只读；Assembler 不运行仓库代码 |
| CommitBundleSignature | Evidence Store | Commit Bundle Signer | Signer 不组装内容、不执行 Gate、不签发交付授权 |
| GateExecutionFact / GateResult | Evidence Store | Domain 2 Gate Supervisor | Domain 7 Command Process 与 Agent 无权写权威结果 |
| GateAttestation | Evidence Store | Attestation Signer | Signer 不执行仓库代码 |
| SemanticReview | Evidence Store | 独立 Review Worker | 不能覆盖确定性 Gate |
| Verdict | PostgreSQL + Evidence 引用 | Deterministic Verdict Aggregator | Reviewer 无写终态权 |
| 候选 Git ref 实际值 | Git Provider | Git Stager | Attempt 无 Git Remote 凭据；Lifecycle 只保存读回引用 |
| 审计事件 | WORM / Artifact + 索引 | 各受信任服务经 Outbox | 禁止覆盖或删除历史 |

### 5.1 数据库与对象存储的一致性

大对象使用两步发布：

1. 上传到临时命名空间并计算 digest；
2. 校验字节、类型、大小和扫描结果；
3. 在对象存储中 `seal bytes`，使内容按 digest 不可变；这不是跨系统事务提交点；
4. PostgreSQL 单事务写 `artifact_manifests.status=PUBLISHED`、引用与 Outbox；该事务才是唯一业务发布点；
5. 消费者只接受数据库状态为 PUBLISHED 且重新校验通过的 Manifest；
6. 事务前已 seal 但未被 PUBLISHED 引用的对象视为 orphan，由 GC 在保留期后清理。

数据库保存“哪个摘要处于什么业务状态”，对象存储保存“该摘要对应哪些精确字节”。任何一侧单独存在都不构成可执行对象。

---

## 6. 组件实现蓝图

### 6.1 Task API 与准入

职责：

- 校验数据分级、任务类型和仓库白名单；
- 冻结 repoUrl、baseRevision、允许路径、禁止路径和验收目标；
- 服务端分配 taskId；调用方提供 Idempotency-Key，或由可信 CI 入口从稳定事件身份确定性派生；
- 拒绝重复但内容不一致的请求；
- 生成不可变 `TaskAdmissionDecision(taskAdmissionDecisionId, taskId, taskSpecDigest, admissionPolicyId, admissionPolicyDigest, repositoryAllowlistDigest, taskClass, dataClassification, requestedBudgetDigest, checkResults[], result=ACCEPTED|REJECTED, reasonCodes[], decidedAt, payloadDigest, signature)`；Task API 是唯一写入者，集合字段 canonical sort 且拒绝重复；
- 产生 TaskAccepted / TaskRejected 事件。

Phase 2 只开放内部认证 API，不建设复杂租户、自助计费和外部开发者门户。

### 6.2 Registry

Registry 统一保存版本化元数据，但不同对象具有独立表和状态机：

- Workflow Registry；
- Role Pack Registry；
- Skill Registry；
- Prompt Registry；
- Model Policy Registry；
- Gate Pack Registry；
- Sandbox Profile Registry；
- Tool Policy Registry；
- Execution Plan Registry；
- Source Bundle Registry；
- Dependency Image Registry；
- Runtime / Sandbox Image 与 Compatibility Registry；
- Skill / Policy Approval Registry；
- Revocation Overlay。

所有发布对象使用不可变 Snapshot。更新意味着创建新版本，不允许原地覆盖 ACTIVE 对象。

### 6.3 Workflow Compiler 与 Execution Plan Compiler

Workflow Compiler 从 TaskSpec 与已批准策略生成 WorkflowSnapshot；Execution Plan Compiler 再解析全部发布指针和预冻结路线，输出 Task 进入 EXECUTING 前唯一可用的 ExecutionPlanSnapshot：

- DAG 节点、角色和依赖；
- 每个 Attempt 的输入摘要；
- Role Pack 与 SkillBundle；
- Prompt、模型策略和 Gate Pack；
- 预算与超时；
- Sandbox Profile；
- SourceBundle，Runtime / Sandbox / Dependency Image Snapshot、各自 OCI digest 与 Compatibility Snapshot；
- 修复轮数；
- 外部副作用上限。

编译期必须证明：

- Skill.requiredCapabilities 是 Role Pack 和 SandboxProfile 能力交集的子集；
- 数据等级不超过模型、Skill、工具和 Sandbox 的批准范围；
- Reviewer 与 Implementer 满足独立性要求；
- 每个成功路径都能到达确定性 Gate；
- 所有循环都有次数、时间和成本上限；
- 所有外部副作用都由受信任组件执行。

Execution Plan Compiler 必须为每个 plannedAttemptInput 生成稳定 digest；WorkflowSnapshot 不吸收运行环境版本，ExecutionPlanSnapshot 只引用不可变对象，二者均不能在执行中原地修改。

### 6.4 Orchestrator

Orchestrator 只做 DAG 就绪判定和调度意图，不直接写 Attempt 运行态或终态。它根据已完成依赖、预算、策略和撤销状态，只能从已发布 ExecutionPlanSnapshot 的 plannedAttemptInputs 中选择一个既有 plannedAttemptInput / AttemptRouteSnapshot，经 Attempt Service 创建 `CREATED → READY` 的 Attempt，并通过事务 Outbox 发布可认领事件；不得创建未计划 Route。Node claim 只能把这个既有 Attempt 原子地从 READY 改为 CLAIMED并生成最终 AttemptContract / Lease / Assignment，不能再次创建 Attempt 或 Route。

MVP 不引入独立消息 Broker；Worker 通过 PostgreSQL Outbox / Inbox 和 SKIP LOCKED 模式处理事件。出现以下任一条件时，才提交引入 Broker 的 ADR：

- Outbox backlog 持续超过 SLO；
- 数据库事件轮询占用超过额定资源预算；
- 单节点目标无法满足；
- 跨区域复制成为正式范围。

### 6.5 Lifecycle、Lease 与 Fencing

Lifecycle 是全部 Task 状态、全部 Run 状态及 Attempt 的平台收敛状态（OUTPUT_STAGED、SELECTED、SUPERSEDED、CANCELLED、BUDGET_EXHAUSTED、FAILED、QUARANTINED、LOST、FENCED、TIMED_OUT）的唯一写入者。Node Agent 经 Attempt Service 是 CLAIMED、PROVISIONING、RUNNING、TERMINATING、TERMINAL_REPORTED、FAILED_PROVISIONING 的唯一逻辑写入者；API 内部仍以 CAS 和有效 Lease/Fencing 为前提。Lease Service 负责 claim、heartbeat、renew、expire、cancel、fence 和 orphan reconciliation。

AttemptContract 一旦发布不得改变；续租通过独立 LeaseGrant 完成。所有事实提交必须携带 attemptId、leaseId、epoch、fencingToken 和 eventId。

### 6.6 Node Agent 与 Sandbox Supervisor

Node Agent 是宿主级受信任代理：

- 校验节点资格、ExecutionPlan / AttemptContract、Runtime / Sandbox / Dependency Snapshot 与 OCI digest、Compatibility、Bundle、签名、审批与撤销 epoch；
- 创建隔离工作区和网络命名空间；
- 以只读方式挂载 Role Pack、SkillBundle 和工具；
- 只读挂载签名 SourceBundle、Dependency OCI、RolePack 与 SkillBundle；Sandbox OCI 用于创建隔离环境，Runtime OCI 按冻结布局装载；四者必须与 Compatibility Snapshot 一致；
- 在宿主侧提供 Runtime Proxy，通过受监督 Driver 管道接收 ModelCallIntent；不向仓库代码注入 Gateway Socket 或 Grant；
- 监督完整进程树；
- 捕获 stdout、stderr、退出原因、资源和系统调用证据；
- 代表 Attempt 上传制品；
- 在超时、撤销、租约丢失时终止进程树并清理。

Phase 2 的 PUBLIC / 脱敏任务使用强化容器，并明确接受同宿主旁路风险；处理任何 INTERNAL 数据时强制使用 MicroVM + 专用节点，且不得与其他数据等级共置。容器或 MicroVM 名称都不能替代逃逸、文件描述符继承和旁路测试证据。

### 6.7 PrimeRuntimeDriver

路线 A 的实现顺序：

1. 实现 ScriptedRuntimeDriver，验证终态、乱序、重试、静默和崩溃协议；
2. 接入固定兼容基线 Prime Agent v0.9.1；
3. 通过 RuntimeDriver 合同隔离 Prime/Pi 的内部 API；
4. 并行验证 Native Pi Driver，但不阻塞 Phase 2；
5. 只有当量化触发条件满足时才切换路线。

Driver Adapter 位于 Node 侧受信任调解边界，Prime/Pi Runtime 仍是 Domain 4 不可信组件；Adapter 对 Runtime 发出的所有事件按不可信输入解析。Driver 必须把“模型说完成”“进程退出”“静默窗口”“产物已上传”分开报告。只有 Node Agent 捕获的事实和受信任 Gate 能形成终态输入。

### 6.8 Model Gateway

职责：

- 只接受 Node Runtime Proxy 的工作负载身份，校验其代转的 Attempt 身份、Lease、epoch、invocationId 和撤销状态；
- 把模型别名解析成冻结的真实 provider/model/thinking；
- 强制最大请求、并发、Token、成本、重试与超时；
- 去除供应商长期凭据；
- 记录请求摘要、响应摘要、真实模型身份、用量与错误；
- 生成可验证 Route Attestation。

已有模型代理服务可以通过 ModelGatewayAdapter 复用，但在 Phase 0 必须重新验证其实时模型目录、认证、限流、错误语义和实际 completion；历史可用不等于 v1.3 当前可用。

### 6.9 Artifact 与 Evidence Service

Artifact Service 提供：

- 分块上传；
- digest 校验；
- 对内容寻址字节执行 seal；
- Manifest；
- WORM 保留；
- 引用索引；
- 恶意内容隔离；
- 生命周期和 GC。

证据分成两个不可混用的阶段：Git 前使用 `PreDeliveryEvidence + DeliveryAuthorization`；Git 读回后再形成最终 `DeliveryEvidenceBundle`。最终包至少包含：

- Task / Run / Attempt 标识与合同摘要；
- Lease / epoch；
- Runtime、模型、Route Attestation；
- Role Pack、SkillBundle、Prompt、Gate Pack、Sandbox Image 摘要；
- 输入与输出 ArtifactManifest；
- SourceBundle、DependencyImageSnapshot、CommitBundle；
- GateResult 与 SemanticReview；
- Verdict；
- Git Staging 与读回证据；
- 用量、预算、时钟和错误；
- 审批、例外与撤销 epoch。

### 6.10 Verification Plane

Verification Plane 跨三个明确边界：Domain 2 的 Gate Supervisor 负责可信宿主捕获，Domain 7 只运行不可信仓库命令，Domain 8 只负责组装 / 签名与机械裁决：

- Commit Assembler：在 Domain 8 的受信任控制器中，只从已发布 SourceBundle、选定输出 Artifact、冻结 CommitIntent 与规范化元数据组装不可变 Git tree / commit 对象；不执行仓库代码，不调用 LLM，也不拥有 Git 写凭据；
- Commit Bundle Signer：只对 Commit Assembler 产生的精确 payloadDigest 签名，不能改写内容、执行 Gate 或签发 DeliveryAuthorization；签名 CommitBundle 经 Artifact Service 发布，Gate 与 Git Stager 按 digest 分别读取；
- Gate Supervisor（Domain 2）：持有 VerificationLease，逐步骤直接创建 Domain 7 命令进程，以宿主 waitpid、cgroup、signal、timeout 和只写管道流式哈希形成 GateExecutionFact / GateResult；
- Gate Command Process（Domain 7）：在独立完全断网沙箱中，对精确 CommitBundle 运行单个测试、构建、Lint、类型、安全规则或路径 / 树一致性命令；只能输出字节与退出，不能写权威结果、持有 Lease 或签名；
- Semantic Review Worker：使用独立 Review Run / Route，只读接收带来源标签的数据区，Prompt 与数据物理分段，按严格 Schema 产生非权威风险证据；
- Evidence Completeness Checker：检查必填字段和摘要链；
- Verdict Aggregator：按冻结规则确定 ACCEPTED、REJECTED、REPAIRABLE、HANDOFF_TO_HUMAN 等结果。

这里不定义名为 Verifier 的模型角色。Gate Supervisor 是确定性宿主监督器，Gate Command Process 是不可信命令进程，Verdict Aggregator 是纯规则组件；三者都不配置 RolePack 或 SkillBundle。Reviewer Agent / Semantic Judge Agent 不得覆盖失败 Gate 或宣告成功。

Critical Gate 禁止由一个 shell wrapper 间接汇总。Gate Supervisor 必须逐步骤直接创建受限命令进程并捕获 `waitpid`、signal、timeout、cgroup 资源事实，对 stdout / stderr 流式哈希；Domain 7 内的 JUnit、摘要、退出说明等均仅作解释证据，不能覆盖宿主捕获事实。

### 6.11 Git Stager

Git Stager 使用一个首期 Git Provider Adapter，具体供应商在 Phase 0 冻结。它必须支持：

- 精确 base commit；
- 任务专属候选 ref；
- compare-and-swap 或等价读回校验；
- 幂等创建；
- 最小权限短期凭据；
- 禁止受保护分支；
- 失败对账与隔离；
- CandidateBundle 回传。

Git Stager 只消费已签名 CommitBundle 和独立 `GitStagingLease / DeliveryAuthorization`，不复用已结束的 Attempt ExecutionLease，不下载依赖，不启动解释器，也不执行任何仓库代码。

不具备可靠 ref CAS 或读回能力的 Git 供应商不能进入 Phase 2。

### 6.12 Observability 与 Reconciler

所有服务使用统一 traceId、taskId、runId、attemptId、eventId。OpenTelemetry 负责 Trace / Metric / Log 关联，PostgreSQL 保存权威索引，原始证据进入 Artifact Store。

Reconciler 周期性核对：

- DB 状态与活动 Lease；
- Node 实际进程；
- Outbox / Inbox；
- Artifact 发布状态；
- Gateway 预算；
- Git candidate ref；
- SourceBundle、DependencyImageSnapshot 与 CommitBundle；
- Revocation epoch。

Reconciler 可以推动安全收敛，但不能把缺失证据补写成成功。

### 6.13 Repository Source Ingestor

Source Ingestor 是沙箱外只读 Git 客户端，负责把 TaskSpec 中的仓库意图变成可执行的冻结输入：

- 通过 allowlist 和只读凭据解析 repositoryId / ref 到带算法的 `GitObjectId`；
- 固定并校验 submodule、LFS、文件模式、大小、路径、许可与数据分级；
- 生成规范化 `SourceArtifactManifest` 与签名 `SourceBundle`，上传经 Artifact Service 发布；
- Task、AttemptContract、GateExecution、CommitBundle 和 Git Staging 全部绑定同一 sourceBundleDigest；
- ref 在冻结后发生变化不会改写 SourceBundle，只会在 Git CAS 时产生 SUPERSEDED 或新 Task 决策。

Source Ingestor 没有写 Git 权限，也不执行仓库代码。

### 6.14 Dependency Image Supply Chain

每个允许的仓库 / 工具链组合在受控 MicroVM 构建环境预构建 `DependencyImageSnapshot`：

- 输入是冻结锁文件、基础镜像 digest、构建器 digest 和批准的镜像源策略；
- Builder 只输出未签名 OCI image digest、SBOM、SCA、漏洞事实、provenance、工具清单和兼容 SandboxProfile 证据；
- 独立 Evaluator / Approver 形成结论，Dependency Image Signer 只签精确已批准 payloadDigest，Publisher 只发布验证通过的引用；Builder、Approver、Signer、Publisher 身份分离；
- Attempt 与 Gate 只读挂载同一已签名镜像且完全离线；
- 运行时发现依赖缺失时只能产生 `DependencyChangeProposal`，不能在线安装；
- 提案由独立流水线重新冻结、构建、扫描、审批和发布新 digest。

---

## 7. SkillRoster 与 Skill 供应链

### 7.1 唯一合法生态位

SkillRoster 在 v1.3 中是 **SkillInventoryAdapter**：

- 运行位置：Domain 11 离线治理工作区；
- 信任级别：不可信输入解析器；
- 首期模式：对“冻结 Skill 快照”只读的 scan/report；工具自身只能写一次性临时 state；
- 输出：结构化盘点、路径、fingerprint、placement、link、exposure 与 finding 证据；
- 权威性：无；
- 发布权：无；
- 运行时可见性：无。

Adapter 必须在一次性容器中运行，使用空 HOME、独立临时 SQLite、固定 SkillRoster 二进制 digest、无生产凭据，并且**只挂载显式冻结的 Skill 源快照**；禁止挂载治理机真实 Agent 根、生产 Session、Prompt / Response 或用户目录。每次输出记录 scanId、reportId、二进制 digest 和 JSON Schema version。

首期允许适配：

~~~text
skillroster scan --summary --json
skillroster report --full --json
~~~

首期不接入：

~~~text
find --load
setup
plan
apply
undo
source-root confirm
source-root revoke
~~~

即使未来使用变更类命令，也只能作用于一次性治理工作区，不能修改运行中的 Agent、Role Pack、SkillBundle 或沙箱挂载。

### 7.2 能力边界

| SkillRoster 可以提供 | SkillRoster 不能证明 |
|---|---|
| 确定性盘点与结构化报告 | Skill 内容没有恶意行为 |
| 来源、placement、fingerprint 和 drift 证据 | 来源本身可信或获准发布 |
| 链接和路径风险线索 | 沙箱绝不会逃逸 |
| 暴露与有限使用证据 | 未调用的 Skill 一定无用 |
| Plan / Receipt / Undo 的本地确定性模型 | 平台级审批、签名、RBAC、Canary 和撤销 |
| 扫描字节是否变化 | 这些字节安全、合规或许可证正确 |

因此，“SkillRoster 扫描通过”只形成一项 SkillEvaluation，不得直接产生 APPROVED 或 ACTIVE。

官方能力依据保留为：

- [SkillRoster 项目](https://github.com/tt-a1i/skillroster)
- [SkillRoster Product Specification](https://github.com/tt-a1i/skillroster/blob/main/docs/product-spec.md)

### 7.3 Skill 聚合与状态机

不得把内容版本、审批、构建和环境发布压成一个状态。四个聚合分别 CAS：

~~~text
PackageVersion:
DISCOVERED → SOURCE_FROZEN → SCANNED → EVALUATED → REVIEW_PENDING
REVIEW_PENDING → APPROVAL_ELIGIBLE | REJECTED
APPROVAL_ELIGIBLE → SUPERSEDED | REVOKED

ApprovalProposal:
DRAFT → SUBMITTED
SUBMITTED → QUORUM_REACHED | REJECTED | EXPIRED | WITHDRAWN | SUPERSEDED
DRAFT → WITHDRAWN

BundleBuild:
DRAFT → BUILT → REPRODUCED → VERIFIED → SIGNED
任意非终态 → FAILED
SIGNED → REVOKED

PublicationPointer（按 LAB / BENCHMARK / CANARY / PRODUCTION 隔离）:
UNSET → ACTIVE
ACTIVE → SUPERSEDED | SUSPENDED | REVOKED
~~~

SkillBundleSnapshot 内容永远不可变，CANARY / ACTIVE 是带 environmentScope 的发布指针状态，不写进 Snapshot。恢复通过新 PackageVersion、新摘要、新 ApprovalSet、新构建与新 Canary 完成；永久 Revocation 不可解除，临时隔离使用独立 ContainmentRecord。

### 7.4 来源冻结

只接受：

- 平台内部仓库；
- 明确 allowlist 的外部仓库；
- 不可变 commit、tag object 或发布制品 digest；
- 可确认作者或内部责任人的来源；
- 所有 submodule、LFS 或二次生成内容均已固定。

拒绝：

- 浮动分支和 latest 标签作为发布身份；
- 未固定 URL；
- 任意本机目录直接发布；
- 自动发现结果直接晋升为可信来源；
- 下载脚本在扫描后引入的新字节。

源码先进入一次性隔离区并形成 sourceSnapshotDigest，后续解包、扫描、评测、审批和构建全部针对同一快照，消除“扫描一份、打包另一份”的 TOCTOU。

### 7.5 文件与包格式门禁

MVP 硬拒绝：

- 绝对路径、父路径穿越、盘符或 UNC 路径；
- symlink、hardlink、junction、reparse point；
- socket、FIFO、device file；
- 大小写折叠重名和 Unicode 规范化碰撞；
- 压缩炸弹、超深目录、超长路径和超量文件；
- canonical path 逃离工作根；
- 预编译二进制、动态库、内核模块、WASM；
- MCP Server、自动 MCP 配置、安装器和生命周期 hook；
- 自动下载、自更新、vendored 解释器和未知依赖；
- 密钥、Token、Cookie、私钥、生产地址或个人数据；
- 未在 manifest 声明的隐藏文件和生成物。

MVP 允许：

- SKILL.md；
- 明确引用的 Markdown / 文本参考；
- 模板和受限静态资源；
- 逐文件申报并完成审查的脚本。

脚本必须声明 entrypoint、language、fileDigest、requiredTools、requiredCapabilities、networkRequirement、externalDependencies、expectedInputs、expectedOutputs 与 resourceLimits。首期要求 networkRequirement = none、externalDependencies = none。

### 7.6 独立安全与许可门禁

平台必须在 SkillRoster 之外实现或接入 Secret scanning、AV / YARA 或等价恶意样本检测、脚本静态分析、依赖漏洞扫描、SBOM、SPDX 与 Notice 策略、语义安全审查、隔离行为测试和隐藏反例测试。

语义审查重点查找诱导 Agent 忽略平台规则、读取凭据、访问宿主或其他 Attempt、外传数据、关闭 Gate、修改 Skill / Role / AGENTS / 全局配置、获取更高权限或创建长期凭据的内容。

自动语义检查只能给出风险证据，不能由拥有发布权的模型自动批准。

### 7.7 Four-Eyes 与职责分离

每个生产 Skill 变更至少需要两个互相独立、且均不同于 proposalActor 的审批决定：

- 功能 / Role 负责人：确认必要性、适配性和最小暴露；
- 安全负责人：确认代码、脚本、权限、数据、来源与许可风险。

提交者不得批准自己。Builder、功能 Approver、安全 Approver、Signer 分离。`ApprovalScopeSnapshot` 绑定 packageDigest、sourceSnapshotDigest、sourceRevision、provenanceDigest、evaluationSetDigest、policyDigest、allowedRoles、allowedTaskClasses、requiredCapabilitySet、allowedDataClass、allowedEnvironmentScopes、sandboxProfileDigest 与期限；`ApprovalSet` 保存独立 `ApprovalDecision[]`、角色、身份、签名、quorum 和 veto。

v1.2 的 Four-Eyes 同样适用于 EvaluationPlanSnapshot、RoutingIntentSnapshot、ModelPolicy 与 SandboxProfile 的新建或变更。任何把 quorum、veto 或 required Gate 降到安全基线以下的变更，除 proposer 与常规 approver 外还必须有额外 Security Approver。

任意字节、来源、能力、Role、数据等级、SandboxProfile 或政策变化都使旧审批失效。单人组织允许评测和 PoC，但不能把 Bundle 标记为生产 ACTIVE。

### 7.8 Bundle 构建、签名与挂载

SkillBundle 与 Role Pack、Prompt、Gate Pack、Sandbox Image 分离：

- 每个相对路径、字节摘要、大小、类型和许可位进入 manifest；
- 相同输入在干净环境中可重复构建；
- Builder 无审批和签名权限；
- Signer 只接受 Registry 发出的精确 digest；
- 只对摘要签名，不对路径、标签或 latest 签名；
- Bundle 通过带 LAB / BENCHMARK / CANARY / PRODUCTION 作用域的发布指针独立灰度、撤销和回滚；
- Node 启动前验证 digest、签名、审批、Role 绑定、SandboxProfile 兼容性和 revocationEpoch；
- 不一致时进入 FAILED_PROVISIONING 或 QUARANTINED，不得降级为空 Skill。

运行时挂载至少使用 ro、nodev、nosuid、noexec。noexec 只是纵深防护，不能阻止解释器读取脚本，不能替代脚本审查和 ToolPolicy。

### 7.9 使用证据与裁剪

生产平台不解析原始 Session 来判断 Skill 价值，而是产生结构化 Trace：

~~~text
EXPOSED → MATCHED → LOADED → INVOKED → OUTCOME_LINKED
~~~

- 只有 Node / Gateway 出口侧事件可以证明曝光、装载和调用；
- Agent 自报不能成为调用证据；
- Outcome 必须关联 Gate / Verdict；
- 未观察到调用不等于无用；
- 覆盖不完整时禁止产生 unused 结论；
- 安全兜底 Skill 不能只因低频被删除；
- 裁剪必须通过冻结基准的离线对照实验。

SkillRoster 的会话解析能力只可用于脱敏实验数据或人工治理工作站，不作为生产 Trace 权威，也不读取生产原始 Prompt / Response。

### 7.10 运行时不变量

1. SkillRoster 二进制不进入 Attempt、Gate 或 Prime/Pi Runtime 镜像。
2. Attempt 只看到合同冻结的一个 SkillBundleSnapshot。
3. Runtime 不得 scan、find、load、plan、apply、undo、安装、更新或复制覆盖 Skill。
4. Runtime 不支持 On-demand Skill。
5. 能力不足只能产生 MissingCapabilityProposal，不能改变当前 Attempt。
6. Skill 不得扩大 ToolPolicy、网络、模型、数据、Git、依赖或解释器权限。
7. 调度、Node admission、Gateway 外部调用和 Git Staging 前均检查撤销状态。
8. 平台回滚选择历史已批准 digest，不使用 SkillRoster Undo 改运行时目录。

---

## 8. 关键状态机

### 8.1 Task

Task API 的请求校验发生在 Task 聚合创建前；通过后以 ACCEPTED 为初态，拒绝请求形成 REJECTED Task 与证据。正式迁移如下：

| from | to | 唯一写入者 | guard / proofRefs | event | terminal |
|---|---|---|---|---|---|
| — | ACCEPTED / REJECTED | Lifecycle Service | TaskSpec、准入决定、idempotency record | TaskAccepted / TaskRejected | REJECTED 是 |
| ACCEPTED | PLANNING | Lifecycle Service | TaskSpec digest 与签名 TaskAdmissionDecision(ACCEPTED) 精确匹配 | TaskPlanningStarted | 否 |
| PLANNING | EXECUTING | Lifecycle Service | 签名 ExecutionPlanSnapshot 已发布；其 digest 覆盖 Workflow、EvaluationPlan、RolePack、SkillBundle / SkillPolicy、Prompt、ToolPolicy、RoutingIntent / AttemptRoute、ModelPolicy、GatePack、SandboxProfile、SandboxImage、RuntimeImage、DependencyImage、SourceBundle 与预算，且审批 / 签名 / 撤销水位均有效 | TaskExecutionStarted | 否 |
| PLANNING | FAILED_SPEC_AMBIGUOUS | Lifecycle Service | 无法形成可机器验证目标 | TaskSpecAmbiguous | 是 |
| EXECUTING | VERIFYING | Lifecycle Service | 必需 Run 已形成不可变输出 | TaskVerificationStarted | 否 |
| EXECUTING | BUDGET_EXHAUSTED / FAILED | Lifecycle Service | 预算账本或必需执行失败证明 | TaskBudgetExhausted / TaskFailed | 是 |
| VERIFYING | DELIVERY_PENDING | Lifecycle Service | 必需 Run 为 VERIFIED、ACCEPTED Verdict、PreDeliveryEvidence 完整 | TaskDeliveryPending | 否 |
| VERIFYING | FAILED / NO_VERDICT / HANDOFF_TO_HUMAN | Lifecycle Service | Verdict 到 Task 的冻结映射规则 | TaskFailed / TaskNoVerdict / TaskHandoffPrepared | 是 |
| DELIVERY_PENDING | SUCCEEDED | Lifecycle Service | candidate ref 读回、GitStagingResult、DeliveryEvidenceBundle 全部有效 | TaskSucceeded | 是 |
| DELIVERY_PENDING | MERGING | Lifecycle Service | 仅未来自动合入模式；MergePolicy 与 MergeLease 已批准 | TaskMergeStarted | 否 |
| MERGING | SUCCEEDED | Lifecycle Service | Merge CONFIRMED、目标 ref 读回与最终证据 | TaskSucceeded | 是 |
| MERGING | RECONCILING | Lifecycle Service | Git / DB 确认不确定且存在 PREPARED 操作 | TaskReconciliationRequired | 否 |
| DELIVERY_PENDING | RECONCILING | Lifecycle Service | Git 结果不确定且存在 PREPARED 操作 | TaskReconciliationRequired | 否 |
| ACCEPTED / PLANNING / EXECUTING / VERIFYING | CANCEL_REQUESTED | Lifecycle Service | 授权取消命令、当前 rowVersion；停止并 fence 子 Run / Attempt | TaskCancelRequested | 否 |
| DELIVERY_PENDING / MERGING | CANCEL_REQUESTED | Lifecycle Service | Candidate / Merge 操作尚未发出外部 CAS，或 Git Stager 已证明无外部副作用且对应 Grant 已 fenced | TaskCancelRequested | 否 |
| CANCEL_REQUESTED | CANCELLED | Lifecycle Service | 全部子 Run / Attempt 已收敛，且不存在 in-flight、APPLIED 或确认不明的外部副作用 | TaskCancelled | 是 |
| CANCEL_REQUESTED | QUARANTINED | Lifecycle Service | 取消收敛期间出现安全 / 来源 / 证据异常，能力已 fenced 且外部副作用状态明确 | TaskQuarantined | 是 |
| ACCEPTED / PLANNING / EXECUTING / VERIFYING / DELIVERY_PENDING | QUARANTINED | Lifecycle Service | 安全、来源、证据或撤销证明；外部副作用状态明确 | TaskQuarantined | 是 |
| MERGING | QUARANTINED | Lifecycle Service | 仅限外部 CAS 尚未发出且对应 Grant 已 fenced；否则创建 ContainmentRecord 并进入 / 保持 RECONCILING | TaskQuarantined | 是 |
| RECONCILING | SUCCEEDED / FAILED / HANDOFF_TO_HUMAN / QUARANTINED | Lifecycle Service | 先读回 GitObjectId 与 operation key；安全事件存在时保持 ContainmentRecord | TaskReconciled | 是 |

`MERGING` 路径仅作为 v1.2 的未来兼容状态保留；Phase 2 策略使其不可达。对 `MERGING @ APPLIED`、外部 CAS 已发出但确认不明、以及 `RECONCILING` 的 Task，取消命令必须返回 `409 SIDE_EFFECT_RECONCILIATION_REQUIRED`，不能写 CANCEL_REQUESTED；系统先完成 CONFIRMED / RECONCILING，之后若要撤回应创建新 Task。安全隔离命令始终立即创建 ContainmentRecord 并 fence 新能力，但在不确定副作用对账完成前不伪造 QUARANTINED 终态。Phase 2 的 SUCCEEDED 精确表示“任务专属候选 ref 与最终交付证据已确认”，不表示合并或部署。

### 8.2 Run

| from | to | 唯一写入者 | guard / proofRefs | event | terminal |
|---|---|---|---|---|---|
| — | CREATED | Lifecycle Service | Workflow 节点实例化 | RunCreated | 否 |
| CREATED | BLOCKED / READY | Lifecycle Service | 依赖快照与就绪条件 | RunBlocked / RunReady | 否 |
| BLOCKED | READY | Lifecycle Service | 必需上游已满足 | RunReady | 否 |
| BLOCKED | FAILED_DEPENDENCY / CANCELLED | Lifecycle Service | 上游不可恢复终态 / Task 取消 | RunDependencyFailed / RunCancelled | 是 |
| READY | EXECUTING | Lifecycle Service | 至少一个 READY Attempt、预算预留 | RunExecutionStarted | 否 |
| EXECUTING | OUTPUT_STAGED | Lifecycle Service | selectedAttempt 候选已 PUBLISHED，选择 CAS 成功 | RunOutputStaged | 否 |
| EXECUTING | RETRY_WAIT / AWAITING_EXTERNAL | Lifecycle Service | 可重试基础设施失败 / 冻结外部等待条件 | RunRetryScheduled / RunAwaitingExternal | 否 |
| EXECUTING | BUDGET_EXHAUSTED / FAILED / CANCELLED | Lifecycle Service | 对应不可变证明 | RunBudgetExhausted / RunFailed / RunCancelled | 是 |
| RETRY_WAIT | READY | Lifecycle Service | backoff 到期且预算、策略仍允许 | RunReady | 否 |
| AWAITING_EXTERNAL | READY / FAILED / CANCELLED | Lifecycle Service | 外部结果、超时或取消证明 | RunExternalResolved | 视目标而定 |
| OUTPUT_STAGED | VERIFYING | Lifecycle Service | CommitBundle 与 EvaluationPlan 已冻结 | RunVerificationStarted | 否 |
| VERIFYING | VERIFIED | Lifecycle Service | EvaluationVerdict = ACCEPTED 且证据完整 | RunVerified | 是 |
| VERIFYING | PARTIALLY_VERIFIED / DEGRADED_SUCCESS / CONDITIONAL_SUCCESS | Lifecycle Service | v1.2 ADR-19 的精确映射和未满足项 | RunProgressiveOutcomeRecorded | 是 |
| VERIFYING | REPAIR_REQUIRED | Lifecycle Service | Verdict = REPAIRABLE 且 Repair 预算允许 | RunRepairRequired | 否 |
| VERIFYING | NO_VERDICT / QUARANTINED / HANDOFF_TO_HUMAN / FAILED | Lifecycle Service | 对应 Verdict / 安全 / Gate 证明 | RunNoVerdict / RunQuarantined / RunHandoffPrepared / RunFailed | 是 |
| REPAIR_REQUIRED | SUPERSEDED | Lifecycle Service | 新 Repair Run 已创建并通过 `parentRunId` 关联 | RunSuperseded | 是 |
| REPAIR_REQUIRED | FAILED / HANDOFF_TO_HUMAN | Lifecycle Service | Repair 不可用、超限或需人工 | RunFailed / RunHandoffPrepared | 是 |
| CREATED / READY / RETRY_WAIT / AWAITING_EXTERNAL / OUTPUT_STAGED / VERIFYING / REPAIR_REQUIRED | CANCELLED | Lifecycle Service | Task 取消已接受；活动 Attempt / Gate 已终止或 fenced；无不明外部副作用 | RunCancelled | 是 |
| CREATED / BLOCKED / READY / EXECUTING / RETRY_WAIT / AWAITING_EXTERNAL / OUTPUT_STAGED / VERIFYING / REPAIR_REQUIRED | QUARANTINED | Lifecycle Service | 安全、来源、证据或撤销证明；活动能力已 fenced，关联外部副作用状态明确 | RunQuarantined | 是 |

Phase 2 只有 VERIFIED 可推动 Task 进入 DELIVERY_PENDING。三个渐进式结果必须展示未满足项，且不计入 PositiveTaskE2ESR。取消 / 隔离与正常完成并发时，Lifecycle 以 rowVersion CAS 决胜；若已存在确认不明的 Git 操作，Run 先保持非终态并随 Task 进入 RECONCILING，读回前不得写 CANCELLED / QUARANTINED。

### 8.3 Attempt

| from | to | 唯一写入者 | guard / proofRefs | event | terminal |
|---|---|---|---|---|---|
| — | CREATED | Orchestrator 经 Attempt Service | Run、Route、SourceBundle、合同输入已解析 | AttemptCreated | 否 |
| CREATED | READY | Orchestrator 经 Attempt Service | 调度前置与预算满足 | AttemptReady | 否 |
| CREATED | CANCELLED / TIMED_OUT / QUARANTINED | Lifecycle Service | Task / Run 取消、创建截止时间或安全 / 来源证明 | AttemptCancelled / AttemptTimedOut / AttemptQuarantined | 是 |
| READY | CLAIMED | Node Agent 经 Attempt Service | 单事务 CAS、ExecutionLease、Assignment、最终 AttemptContract | AttemptClaimed | 否 |
| READY | CANCELLED / TIMED_OUT / QUARANTINED | Lifecycle Service | Task / Run 取消、调度截止时间的数据库时钟证明，或安全 / 来源证明 | AttemptCancelled / AttemptTimedOut / AttemptQuarantined | 是 |
| CLAIMED | PROVISIONING | Node Agent 经 Attempt Service | 有效 Lease/Fencing、Node admission | AttemptProvisioningStarted | 否 |
| CLAIMED | CANCELLED / TIMED_OUT / LOST / FENCED / QUARANTINED | Lifecycle Service | 取消且无副作用，或 deadline、Heartbeat + Lease 过期、Node / 撤销 epoch、安全证明 | 对应强制终态事件 | 是 |
| PROVISIONING | RUNNING | Node Agent 经 Attempt Service | 实际挂载摘要、完全断网、进程树已监督 | AttemptRunning | 否 |
| PROVISIONING | FAILED_PROVISIONING | Node Agent 经 Attempt Service | 沙箱 / 镜像 / 资源失败事实 | AttemptProvisioningFailed | 是 |
| PROVISIONING | CANCELLED / TIMED_OUT / LOST / FENCED / QUARANTINED | Lifecycle Service | 进程树终止证明，或 deadline、Heartbeat + Lease 过期、Node / 撤销 epoch、安全证明 | 对应强制终态事件 | 是 |
| RUNNING | TERMINATING | Node Agent 经 Attempt Service | 取消、超时预警或 Stop 命令 | AttemptTerminating | 否 |
| RUNNING | TERMINAL_REPORTED | Node Agent 经 Attempt Service | `AttemptTerminalEnvelope`、完整进程树静默证明 | AttemptTerminalReported | 否 |
| RUNNING | TIMED_OUT / BUDGET_EXHAUSTED / LOST / FENCED / QUARANTINED | Lifecycle Service | 数据库时间、Budget、Heartbeat + Lease、撤销 / 安全证明；可达节点须附进程树终止证明 | 对应强制终态事件 | 是 |
| TERMINATING | CANCELLED / TIMED_OUT / LOST / FENCED / QUARANTINED | Lifecycle Service | Node 提交进程树已终止事实；失联场景须有 Lease 过期、Node fencing / epoch 提升证明 | 对应强制终态事件 | 是 |
| TERMINAL_REPORTED | OUTPUT_STAGED | Lifecycle Service | ArtifactManifest 已 PUBLISHED 且合同摘要匹配 | AttemptOutputStaged | 否 |
| TERMINAL_REPORTED | FAILED / QUARANTINED | Lifecycle Service | 运行失败或证据 / 摘要异常 | AttemptFailed / AttemptQuarantined | 是 |
| OUTPUT_STAGED | SELECTED / SUPERSEDED / QUARANTINED | Lifecycle Service | Run 选择 CAS，或发布后发现安全 / 摘要异常 | AttemptSelected / AttemptSuperseded / AttemptQuarantined | 是 |

Node 可以提交 `AttemptTerminalEnvelope`，并按字段级写入权推进其持有的状态子集；它不能写 OUTPUT_STAGED、SELECTED、SUPERSEDED、CANCELLED、BUDGET_EXHAUSTED、FAILED、QUARANTINED、LOST、FENCED、TIMED_OUT 或其他强制终态。Node 失联时，Lifecycle 以数据库 deadline、Heartbeat、Lease 过期、Node fencing 与 epoch 证明收敛，不等待失联 Node 自报；可达节点则必须先证明完整进程树静默。晚到、重复或 epoch 不匹配的事实只进入隔离审计区，不改变正式状态。`NO_VERDICT` 是 Run / Task 结果，不是 Attempt 状态。

### 8.4 SkillBundle 撤销

撤销不改写原对象：

~~~text
PRODUCTION PublicationPointer 指向的 bundle
  → SkillRevocationRecord CAS
  → revocationEpoch + 1
  → 停止新 Attempt
  → fence 未确认节点
  → 撤销活动 Attempt 的外部能力
  → 隔离 Artifact 与候选 ref
  → 生成爆炸半径
  → 新 digest 重新评测、审批、签名、Canary
~~~

反向索引必须支持：

~~~text
packageDigest
  → skillBundleSnapshotDigest[]
  → rolePackDigest[]
  → attemptId[]
  → taskId[]
  → artifactDigest[]
  → candidateRef[]
~~~

### 8.5 CandidateStagingOperation

CommitIntent 在 Gate 前分配稳定 operationIdempotencyKey；CandidateStagingOperation 只在 Verdict 与交付授权形成后创建。其状态迁移如下，唯一写入者均为 Delivery Service，Git Stager 只能发布签名 `GitStagingResult` 事实：

| from | to | guard / proofRefs | event | terminal |
|---|---|---|---|---|
| — | PREPARED | ACCEPTED Verdict、PreDeliveryEvidence、签名 DeliveryAuthorization、有效 GitStagingLease，且全部 digest / identity / epoch / TTL 匹配 | CandidateStagingPrepared | 否 |
| PREPARED | APPLYING | Stager workload identity 与授权 subject 匹配；Lease 有效；外部调用记录已按 operation key 持久化 | CandidateStagingApplying | 否 |
| PREPARED | EXPIRED / FAILED | 未发出外部 CAS，且授权过期或本地前置校验不可恢复失败 | CandidateStagingExpired / CandidateStagingFailed | 是 |
| APPLYING | CONFIRMED | ref 读回等于 proposedCommitGitObjectId，且 commit trailer operation key 匹配 | CandidateStagingConfirmed | 是 |
| APPLYING | SUPERSEDED | ref 已变化且 operation key 不匹配；证明不是本操作生效 | CandidateStagingSuperseded | 是 |
| APPLYING | RECONCILING | 外部调用超时、ack 丢失或结果不确定 | CandidateStagingReconciling | 否 |
| APPLYING | FAILED | 外部系统明确拒绝且读回证明本操作未生效 | CandidateStagingFailed | 是 |
| RECONCILING | CONFIRMED / SUPERSEDED / FAILED | 强制先读回 ref、GitObjectId 与 operation key 后机械判定 | CandidateStagingReconciled | 是 |

APPLYING 只表示外部 CAS 可能已发出，不等同成功。此时取消请求不得把 Operation 或 Task 直接写为 CANCELLED；先进入 RECONCILING。任何同 key 重试必须复用原 CommitIntent、CommitBundle 与授权范围，不得生成第二个逻辑提交。

---

## 9. 契约总原则与通用字段

### 9.1 契约原则

1. PostgreSQL 是生命周期、租约、审批、发布指针和引用关系的唯一状态权威。
2. Artifact Store 只承载内容寻址的字节、Manifest 和证据，不能反向驱动业务状态。
3. Git 只承载代码对象和候选 ref，不是 Task / Run / Attempt 成功状态的权威。
4. SkillRoster 只产生扫描报告和评估证据，不拥有 Skill 注册、审批、发布或撤销状态。
5. 所有进入 Attempt 的 Role、Prompt、Tool、Skill、Sandbox、Runtime 和 Route 均冻结为不可变快照。
6. 快照不可修改；变化创建新版本，问题通过 Revocation Overlay 撤销。
7. 系统不宣称分布式 Exactly Once，而是组合使用事务 Outbox、至少一次投递、Inbox 去重、业务幂等、CAS 和外部读回对账。
8. Runtime、沙箱和仓库代码只能报告事实，不能发布 Skill、决定成功、签署 Gate 或修改控制面状态。
9. v1.3 首期为单租户，不虚构 tenantId 隔离语义；资源归属使用 projectId 与 repositoryId。
10. 安全关键 Schema 拒绝未知字段、未知枚举、超限字段和不支持的版本。

### 9.2 ID、时间和 Trace

| 字段 | 规则 |
|---|---|
| 实体 ID | UUIDv7，由服务端生成 |
| traceId | 128-bit、小写 32 位十六进制；Task 生命周期内不变 |
| spanId | 64-bit、小写 16 位十六进制 |
| 时间 | UTC、RFC 3339 Nano |
| schemaVersion | major.minor；不兼容变化提升 major |
| 路径 | UTF-8 NFC、正斜杠；禁止绝对路径、父路径、NUL 与大小写碰撞 |

所有事件携带 schemaVersion、traceId、spanId、parentSpanId、serviceName 和 controlPlaneEpoch。taskId、runId、attemptId 按事件作用域逐级必填：TaskAccepted 只要求 taskId，Run 事件要求 taskId + runId，Attempt 事件才要求三者。Skill 治理没有 Task 标识时，也必须创建独立治理 traceId。

### 9.3 摘要

统一格式：

~~~text
sha256:<64 lowercase hex>
~~~

- JSON 对象先按安全关键 Schema 严格校验，再按 RFC 8785 JCS 规范化；
- 每个 Schema 版本必须发布 `DigestProfile`，明确 immutablePayloadPointers、selfDigestPointers、signatureEnvelopePointers 与 mutableDatabasePointers。`canonicalPayload` 只由 immutablePayloadPointers 白名单投影产生；未知字段直接拒绝，不能靠“计算时忽略”兼容；
- `/payloadDigest`、`/contractDigest` 及该对象自己的 `/*SnapshotDigest` 只在其被该 Schema 明确列入 selfDigestPointers 时排除；对其他对象的引用 digest 必须保留。禁止使用模糊的通配删除规则；
- `/signature`、`/signatures` 和整个签名信封，以及 `/rowVersion`、`/state`、`/updatedAt`、`/updatedBy` 等被 DigestProfile 明确标记的数据库可变字段不进入 canonicalPayload；业务时间、审批决定和安全范围若属于不可变事实则必须进入；
- `payloadDigest = sha256(JCS(canonicalPayload))`；AttemptContract 的 `contractDigest`、各 Snapshot 的自身份 digest 都是该 payloadDigest 的语义化别名，字段本身不参与递归计算；
- Blob 针对原始字节计算；
- Package / Bundle 对规范化路径排序后的 Manifest 计算；
- JCS 不替数组排序：每个数组字段必须在 Schema 中声明“有序语义”或 canonicalSortKey；集合类数组先按稳定键排序、拒绝重复再计算 digest；
- 下载、挂载、验证和发布时均重新计算；
- URL、标签、文件名和数据库自报均不能代替字节校验。

### 9.4 签名信封

默认算法在 Phase 0 通过 ADR 冻结，建议 Ed25519。签名与业务 payload 分离；统一信封包含：

~~~text
signatureAlgorithm
keyId
issuer
issuerWorkloadIdentity
audience
objectType
schemaVersion
payloadDigest
controlPlaneEpoch
signedAt
signature
~~~

签名输入采用域分离并固定为 `JCS({signatureContext, signatureAlgorithm, keyId, issuer, issuerWorkloadIdentity, audience, objectType, schemaVersion, payloadDigest, controlPlaneEpoch, signedAt})`；`signatureContext` 固定版本字符串，`audience` 无适用对象时在信封和签名输入中都显式为 null。验证方先按受信任 Key Registry 校验 algorithm / keyId / issuer 绑定，再复算完整签名输入；除 signature 本身外，任何可影响验签、授权、审计或时效的信封元数据都不能留在签名覆盖范围外。

每个核心 Schema 都必须在 `contracts/test-vectors/<object-type>/<schema-version>/` 提供：原始合法对象、预期 canonicalPayload UTF-8 字节、预期 digest、预期签名输入、有效签名，以及 payload、algorithm、keyId、issuer、workload identity、audience、epoch、signedAt 各自篡改的失败向量。Phase 0 要求 Go 主实现与至少一个独立参考实现逐字节一致；任一语言、平台或 JSON 库产生不同结果即 No-Go。

必须签名的对象包括 TaskAdmissionDecision、SourceBundle、DependencyImageSnapshot、RuntimeImageSnapshot、SandboxImageSnapshot、ExecutionImageCompatibilitySnapshot、SkillEvaluation、ApprovalDecision、PolicyApprovalDecision、SkillBundleSnapshot、RolePackSnapshot、ExecutionPlanSnapshot、AttemptContract、AttemptTerminalEnvelope 的 Node 来源证明、ArtifactManifest、CommitIntent、CommitBundle、GateAttestation、SemanticReviewAttestation、EvaluationVerdict、RevocationRecord、DeliveryAuthorization 和 GitStagingResult。

签名只证明来源和内容未被改写，不证明内容安全或验收通过。

### 9.5 可变对象和 CAS

只有注册记录、运行状态、租约、审批流程和发布指针可以变化，并携带 rowVersion、state、updatedAt、updatedBy。

HTTP 使用：

~~~text
ETag: "rv:<rowVersion>"
If-Match: "rv:<expectedRowVersion>"
~~~

内部调用使用 expectedRowVersion 与 expectedState。不可变快照不通过 rowVersion 修改；内容变化必须生成新 ID 和 digest。

---

## 10. 核心数据模型

### 10.1 SkillPackage 与 SkillPackageVersion

SkillPackage 表示稳定逻辑身份：

~~~text
skillPackageId
namespace
name
ownerTeam
description
registryState = ACTIVE | DISABLED
allowedSourcePolicyId
createdAt
rowVersion
~~~

SkillPackageVersion 表示一次不可变实际内容：

~~~text
skillPackageVersionId
skillPackageId
declaredVersion
sourceKind = INTERNAL_REPOSITORY | ALLOWLISTED_MIRROR
sourceRepositoryId
sourceGitObjectId = {algorithm, hex}
sourceRoot
sourceSnapshotDigest
lifecycleState = DISCOVERED | SOURCE_FROZEN | SCANNED | EVALUATED | REVIEW_PENDING | APPROVAL_ELIGIBLE | REJECTED | SUPERSEDED | REVOKED
entrypointPath
normalizedFileManifest[]
packageDigest
manifestDigest
allowedContentClasses[]
declaredExternalDependencies[]
containsBinary
containsMcpDefinition
containsExecutableScript
provenanceEvidenceRef
ingestionPolicyRevision
ingestedAt
ingestedBy
~~~

### 10.2 SkillEvaluation

每个工具或人工审查产生独立、不可变的评估：

~~~text
skillEvaluationId
skillPackageVersionId
inputPackageDigest
evaluationKind
evaluatorId
evaluatorVersion
rulePackId
rulePackDigest
startedAt
completedAt
result = PASS | FAIL | INDETERMINATE
findings[]
evidenceManifestRef
limitations[]
payloadDigest
signature
~~~

推荐 evaluationKind：

- SKILLROSTER_STRUCTURE_REPORT；
- PATH_AND_SYMLINK_POLICY；
- CONTENT_ALLOWLIST；
- SECRET_SCAN；
- LICENSE_REVIEW；
- SCRIPT_STATIC_REVIEW；
- MANUAL_SECURITY_REVIEW；
- DUPLICATION_AND_STALENESS_REPORT。

SkillRoster 的 PASS 只覆盖它声明负责的规则；INDETERMINATE 不得按通过处理。总体准入由治理服务根据冻结策略机械聚合。

### 10.3 ApprovalScopeSnapshot、ApprovalProposal 与 ApprovalSet

`ApprovalScopeSnapshot` 是审批真正签署的不可变对象：

~~~text
approvalScopeSnapshotId
skillPackageVersionId / packageDigest
sourceSnapshotDigest / provenanceDigest
evaluationIds[] / evaluationSetDigest
policyId / policyDigest
allowedRoleIds[]
allowedTaskClasses[]
requiredCapabilities[]
allowedDataClassifications[]
allowedEnvironmentScopes[] = LAB | BENCHMARK | CANARY | PRODUCTION
allowedSandboxProfileIds[] / allowedSandboxProfileDigests[]
validFrom / validUntil
scopeDigest = payloadDigest
~~~

集合数组按 ID / digest 的字节序 canonical sort 并拒绝重复。审批流程与决策为：

~~~text
SkillApprovalProposal:
skillApprovalProposalId
approvalScopeSnapshotId / scopeDigest
proposalActorIdentity / proposedAt / reason / ticketRef
state = DRAFT | SUBMITTED | QUORUM_REACHED | REJECTED | EXPIRED | WITHDRAWN | SUPERSEDED
rowVersion
requiredQuorum
requiredRoles[] = FUNCTION_APPROVER | SECURITY_APPROVER
vetoPolicyDigest

SkillApprovalDecision:
approvalDecisionId
skillApprovalProposalId / approvalScopeSnapshotId / scopeDigest
skillPackageVersionId / packageDigest
approvalRole = FUNCTION_APPROVER | SECURITY_APPROVER
approverIdentity
decision = APPROVED | REJECTED
decidedAt / validUntil / reason / ticketRef
payloadDigest / signingKeyId / signature

SkillApprovalSet:
approvalSetId
skillApprovalProposalId / approvalScopeSnapshotId / scopeDigest
skillPackageVersionId / packageDigest
decisionIds[] / decisionDigests[]
resolvedQuorum / resolvedRoles[] / vetoPresent
validFrom / validUntil
payloadDigest
~~~

每个 Decision 单独签名；`UNIQUE(skillApprovalProposalId, approvalRole, approverIdentity)`，决定不可覆盖，改变决定必须创建新 Proposal。数据库强制 proposalActor、FUNCTION_APPROVER、SECURITY_APPROVER、Builder 与 Signer 的职责分离；同一身份不能填充两个必需审批槽。Approval Aggregator 将 decisionIds / digests canonical sort、拒绝重复身份，只在 quorum 满足、必需角色齐全、无 veto、全部 scope / package digest 与有效期仍成立时创建不可变 ApprovalSet，并以 CAS 把 Proposal 改为 QUORUM_REACHED。Bundle Builder 必须重算 ApprovalSet payloadDigest 并验证其中完整 Decision 集，不能只信 approvalSetId。

#### 10.3.1 通用政策对象的 Four-Eyes 合同

EvaluationPlanSnapshot、RoutingIntentSnapshot、ModelPolicySnapshot、SandboxProfileSnapshot、RuntimeImageSnapshot、SandboxImageSnapshot、DependencyImageSnapshot、ExecutionImageCompatibilitySnapshot 与 SkillBundle 组合使用独立的通用政策审批对象，不能借用 Skill Package ApprovalSet：

~~~text
PolicyApprovalScopeSnapshot:
policyApprovalScopeSnapshotId
subjectType = EVALUATION_PLAN | ROUTING_INTENT | MODEL_POLICY | SANDBOX_PROFILE | RUNTIME_IMAGE | SANDBOX_IMAGE | DEPENDENCY_IMAGE | EXECUTION_IMAGE_COMPATIBILITY | SKILL_BUNDLE
subjectId / subjectRevision / subjectDigest
baselineSubjectId / baselineSubjectDigest          # 新建时显式为 null
canonicalDiffDigest
subjectConstraintsDigest
securityBaselineId / securityBaselineDigest
governancePolicyId / governancePolicyDigest
requiredGateIds[] / requiredGateEvidenceDigests[]
allowedEnvironmentScopes[] / allowedDataClassifications[]
requiredQuorum
requiredApprovalRoles[] = OWNER_APPROVER | SECURITY_APPROVER
vetoPolicyDigest
weakeningFlags[]
additionalSecurityApproverRequired
validFrom / validUntil
payloadDigest

PolicyApprovalProposal:
policyApprovalProposalId
policyApprovalScopeSnapshotId / policyApprovalScopeDigest
proposalActorIdentity / proposedAt / reason / ticketRef
state = DRAFT | SUBMITTED | QUORUM_REACHED | REJECTED | EXPIRED | WITHDRAWN | SUPERSEDED
rowVersion

PolicyApprovalDecision:
policyApprovalDecisionId
policyApprovalProposalId / policyApprovalScopeDigest
subjectType / subjectId / subjectDigest
baselineSubjectDigest / canonicalDiffDigest
approvalRole = OWNER_APPROVER | SECURITY_APPROVER | ADDITIONAL_SECURITY_APPROVER
approverIdentity
decision = APPROVED | REJECTED
decidedAt / validUntil / reason / ticketRef
payloadDigest / signingKeyId / signature

PolicyApprovalSet:
policyApprovalSetId
policyApprovalProposalId / policyApprovalScopeDigest
subjectType / subjectId / subjectDigest
decisionIds[] / decisionDigests[]                 # canonical sort，拒绝重复身份
resolvedQuorum / resolvedRoles[] / vetoPresent
allRequiredGatesPassed
validFrom / validUntil
payloadDigest
~~~

Approval Aggregator 只从签名 Decision 和不可变 Scope 机械计算 PolicyApprovalSet。proposalActor 不能成为 Approver；常规 Owner Approver、安全 Approver 必须是不同身份。`weakeningFlags` 非空，或 canonical diff 降低 quorum、veto、required Gate、隔离、网络、数据、凭据、签名或审计基线时，`additionalSecurityApproverRequired=true`，且额外 Security Approver 必须同时不同于 proposer、Owner Approver 和首位 Security Approver。发布器在发布前重新校验 subjectDigest、baselineDigest、diffDigest、全部 Gate、身份独立性、quorum、veto、有效期、环境范围与撤销水位；任一不符 Fail Closed。

`subjectType=DEPENDENCY_IMAGE` 时，subjectId / subjectDigest 指向未签名 DependencyImageSnapshot 的 ID / payloadDigest，`subjectConstraintsDigest` 指向不可变 `DependencyImageApprovalScope(lockfileManifestDigest, baseImageDigest, builderImageDigest, ociImageDigest, sbomDigest, scaReportDigest, vulnerabilityReportDigest, provenanceDigest, dependencyPolicyDigest, buildNetworkPolicyDigest, compatibleSandboxProfileDigests, allowedEnvironmentScopes, allowedDataClassifications, payloadDigest)`。Signer 只接受该 Scope、Snapshot 和 PolicyApprovalSet 三者逐字段一致的输入。

`subjectType=EXECUTION_IMAGE_COMPATIBILITY` 时，subjectId / subjectDigest 与 subjectConstraintsDigest 指向 ExecutionImageCompatibilityCandidate 的 ID / payloadDigest，不得指向最终 Compatibility Snapshot；最终 Snapshot 只单向引用 Candidate 与 PolicyApprovalSet，形成无环依赖。

`subjectType=SKILL_BUNDLE` 时，`subjectConstraintsDigest` 必须指向以下不可变对象，顶层 Bundle 审批由同一套 PolicyProposal / Decision / Set 产生：

~~~text
BundleCompositionScope:
bundleCompositionScopeId / draftBundleId
packageEntries[] = {
  skillPackageVersionId / packageDigest
  mountPath / entrypointPath
  packageApprovalSetId / packageApprovalSetDigest
}
packageSetDigest / mountMapDigest
intendedRoleIds[] / allowedTaskClasses[]
selectionPolicyId / selectionPolicyDigest
compilerId / compilerVersion / compilerImageDigest
buildInputsDigest
allowedDataClassifications[] / allowedEnvironmentScopes[]
runtimeMountPolicyDigest
payloadDigest
~~~

Package 列表和 mount map canonical sort、拒绝路径冲突与重复。对 `subjectType=SKILL_BUNDLE`，PolicyApprovalScope 的 `subjectId/subjectDigest` **必须**等于 BundleCompositionScope 的 ID / payloadDigest，`subjectConstraintsDigest` 也绑定该 digest；不得指向尚未生成的最终 SkillBundleSnapshot。Bundle 的 PolicyApprovalSet 因而只依赖未带审批、签名和时间的组合候选；最终 Snapshot 再单向引用 ApprovalSet，并逐字段证明 packageSet、mountMap、selectionPolicy、compiler 与 buildInputs 一致。该依赖图必须通过反环测试向量。Bundle PolicyApprovalSet 不能替代每个 Package 自身的 ApprovalSet；Builder 必须同时校验两层审批。

### 10.4 SkillBundleSnapshot

~~~text
skillBundleSnapshotId
skillBundleSnapshotDigest = payloadDigest
bundleName
bundleRevision
intendedRoleIds[]
packageVersions[]
compilerId
compilerVersion
compilerImageDigest
selectionPolicyId
selectionPolicyDigest
bundleApprovalSetId
bundleApprovalDecisionIds[]
bundleApprovalSetDigest
approvedBundleCompositionScopeId
approvedBundleCompositionScopeDigest
staticDiscoveryIndexDigest
instructionMaterialDigest
bundleArtifactRef
bundleArtifactDigest
bundleManifestDigest
expectedMountedSkillTreeDigest
runtimeMountPolicy
buildInputsDigest
builtAt
payloadDigest
signature
~~~

构建过程由独立 `SkillBundleBuild` 聚合记录 `DRAFT → BUILT → REPRODUCED → VERIFIED → SIGNED`。环境可用性由 `SkillPublicationPointer(environmentScope, roleId, currentSkillBundleSnapshotDigest, state, rowVersion)` 表示；从该 Snapshot 再解析 bundleArtifactDigest 与 expectedMountedSkillTreeDigest。Snapshot 本身没有 ACTIVE / CANARY 状态。

首期 runtimeMountPolicy 固定为：

~~~text
mountMode = READ_ONLY
runtimeDiscoveryMode = STATIC_INDEX_ONLY
runtimeMutationAllowed = false
runtimeInstallAllowed = false
networkRequired = false
executableBinaryAllowed = false
mcpAutoInstallAllowed = false
~~~

三个 digest 含义不得混用：`skillBundleSnapshotDigest` 是含 ID、审批引用和 builtAt 的快照元数据身份；`bundleArtifactDigest` 是可挂载归档的精确字节身份；`bundleManifestDigest` / `expectedMountedSkillTreeDigest` 是规范化文件树身份。`SKILL-REPRO-001` 只比较后两类确定性构建身份，不要求不同构建记录的 Snapshot metadata digest 相同；构建 ID、时间戳和签名不得进入归档字节或文件树摘要。

每个 packageVersions 元素必须携带 skillPackageId、skillPackageVersionId、packageDigest、mountPath、entrypointPath、approvedScopeDigest、approvalSetId、approvalSetDigest 与两类 approvalDecisionIds。顶层 `bundleApprovalSet*` 必须引用 `subjectType=SKILL_BUNDLE` 的 PolicyApprovalSet，只证明 Bundle 选包与组合决策，不能替代逐 Package 的 Four-Eyes。

### 10.5 RolePackSnapshot

~~~text
rolePackSnapshotId
roleId
roleRevision
roleDefinitionDigest
promptBundleId
promptBundleDigest
toolPolicyId
toolPolicyDigest
skillBundleSnapshotId
skillBundleSnapshotDigest
bundleArtifactDigest
expectedMountedSkillTreeDigest
inputSchemaDigest
outputSchemaDigest
runtimeCapabilityPolicyDigest
defaultModelPolicyRef
compatibleRuntimeDrivers[]
compatibleRuntimeVersions[]
compilerId
compilerVersion
buildInputsDigest
approvalRefs[]
payloadDigest
signature
~~~

Role Pack 只引用 SkillBundle，不吸收其生命周期，从而允许 Skill、Prompt、ToolPolicy 和 Runtime 分别评测、Canary 与撤销。

#### 10.5.1 ExecutionPlanSnapshot

Task 进入 EXECUTING 前必须发布一个不可变、签名的 ExecutionPlanSnapshot，避免“状态已执行、输入仍在补齐”：

~~~text
executionPlanSnapshotId
taskId / taskSpecDigest
workflowSnapshotId / workflowDigest
evaluationPlanId / evaluationPlanDigest
sourceBundleId / sourceBundleDigest
plannedAttemptInputs[] = {
  plannedAttemptInputId / workflowNodeId
  rolePackSnapshotId / rolePackDigest
  skillBundleSnapshotId / skillBundleSnapshotDigest
  bundleArtifactDigest / expectedMountedSkillTreeDigest
  skillPolicyId / skillPolicyDigest
  promptBundleId / promptDigest
  toolPolicyId / toolPolicyDigest
  routingIntentSnapshotId / routingIntentDigest
  attemptRouteSnapshotId / attemptRouteDigest
  modelPolicyId / modelPolicyDigest
  gatePackId / gatePackDigest
  sandboxProfileId / sandboxProfileDigest
  sandboxImageSnapshotId / sandboxImageSnapshotDigest / sandboxOciImageDigest
  runtimeImageSnapshotId / runtimeImageSnapshotDigest / runtimeOciImageDigest
  dependencyImageSnapshotId / dependencyImageSnapshotDigest / dependencyOciImageDigest
  executionImageCompatibilitySnapshotId / executionImageCompatibilitySnapshotDigest
  runtimeDriverId / runtimeDriverVersion
  budgetReservationId / budgetPolicyDigest
  plannedAttemptInputDigest
}
canonicalPlannedInputsDigest
planKind = INITIAL | REPAIR
parentExecutionPlanSnapshotId / parentExecutionPlanDigest       # INITIAL 时为 null
parentRunId / triggeringVerdictId / triggeringVerdictDigest     # INITIAL 时为 null
repairRound / failureEvidenceSetDigest / failureFingerprint     # INITIAL 时为 null
repairInstructionDigest / allowedChangeSet[] / allowedChangeSetDigest
inheritedInputRefsDigest / changedInputRefsDigest
approvalSetRefs[] / revocationEpoch
compiledBy / compiledAt / payloadDigest / signature
~~~

数组 canonical sort 并拒绝重复。Orchestrator 只能从这个集合创建 Attempt；AttemptContract 必须逐字段复算并引用同一个 plannedAttemptInputDigest。任何输入变更都创建新 ExecutionPlanSnapshot，绝不改写旧计划。

`planKind=REPAIR` 仅用于 §14.3 的新 Repair Run，并必须把 parent plan / Run、触发 Verdict、失败证据集合、稳定 failureFingerprint、repairRound、结构化 repair instruction、继承项和变化项全部纳入摘要。Phase 3 默认 `allowedChangeSet` 只允许 `REPAIR_INSTRUCTION`，以及从父计划已经冻结并获批的候选中选择 `ATTEMPT_ROUTE_SELECTION`；TaskSpec、Workflow、EvaluationPlan、SourceBundle、RolePack、SkillBundle、Prompt、ToolPolicy、GatePack、SandboxProfile、三类镜像、Compatibility、安全基线、数据等级、网络、能力、审批 quorum 与预算上限均必须继承且逐 digest 相等。任何扩大 allowedChangeSet、策略弱化或使用父计划外的治理对象，都必须先形成新的适用 PolicyApprovalSet；验收目标、SourceBundle 或安全边界变化则创建新 Task，不得伪装为 Repair。

### 10.6 AttemptContract

AttemptContract 是 Node 启动沙箱前必须完整验证的冻结执行合同：

~~~text
attemptContractId
taskId
runId
attemptId
traceContext
executionPlanSnapshotId / executionPlanDigest
plannedAttemptInputId / plannedAttemptInputDigest
workflowSnapshotId / workflowDigest
taskSpecSnapshotId / taskSpecDigest
rolePackSnapshotId / rolePackDigest
skillBundleSnapshotId / skillBundleSnapshotDigest
bundleArtifactDigest / expectedMountedSkillTreeDigest
skillPolicyId / skillPolicyDigest
promptBundleId / promptDigest
toolPolicyId / toolPolicyDigest
modelPolicyId / modelPolicyDigest
gatePackId / gatePackDigest
routingIntentSnapshotId / routingIntentDigest
attemptRouteSnapshotId / attemptRouteDigest
evaluationPlanId / evaluationPlanDigest
sandboxProfileId / sandboxProfileDigest
sandboxImageSnapshotId / sandboxImageSnapshotDigest / sandboxOciImageDigest
runtimeImageSnapshotId / runtimeImageSnapshotDigest / runtimeOciImageDigest
dependencyImageSnapshotId / dependencyImageSnapshotDigest / dependencyOciImageDigest
executionImageCompatibilitySnapshotId / executionImageCompatibilitySnapshotDigest
sourceBundleId / sourceBundleDigest
runtimeDriverId / runtimeDriverVersion
primeVersion / primeSourceDigest
inputArtifactManifestRefs[]
repositoryId / baseGitObjectId
executionLeaseBinding
budgetReservationId / budgetPolicyDigest
allowedCapabilities[]
networkPolicyDigest = complete-offline
filesystemPolicyDigest
gitPolicy
runtimePolicy
outputContract
revocationEpoch / lastFullySynchronizedAt
notBefore / notAfter
contractDigest
signature
~~~

executionLeaseBinding 包含 leaseId、ownerInstanceId、controlPlaneEpoch、resourceExecutionEpoch、revocationEpoch 与 leasePolicyDigest。合同中的 revocationEpoch 必须等于其 ExecutionPlanSnapshot 的全局 revocationEpoch；它是该合同准入时要求的最低完整撤销视图，而不是按 Skill、Route 或凭据拆分的局部集合。

合同不保存明文能力令牌。控制面另向 Node 发最长建议 60 秒、受 audience 和 action 约束的 LeaseGrant；Node 再按调用申请 GatewayGrant。Attempt 与 Runtime 看不到两种 Grant。正常续租只轮换 LeaseGrant，不修改 AttemptContract。

AttemptRouteSnapshot 可以被 AttemptContract 引用，但不得包含 attemptContractDigest，避免内容摘要形成环。需要证明两者绑定时，由 Lifecycle 在二者冻结后签发不参与两者摘要计算的 `RouteContractBindingAttestation`。

首期固定安全策略：

~~~text
skillMountMode = READ_ONLY
runtimeSkillMutation = DENY
runtimeSkillScanPlanApplyUndo = DENY
networkMode = DENY_ALL
modelCallMode = NODE_MEDIATED_CONTROL_PIPE
gatewayGrantVisibility = NODE_ONLY
gitRemoteCredentials = NONE
gitRemoteWrite = DENY
rlmForWriteAttempt = DISABLED
deliveryMode = CANDIDATE_BRANCH
~~~

### 10.7 RevocationRecord

~~~text
revocationId
revocationSchemaVersion = 1
subjectType = WORKFLOW_SNAPSHOT | EVALUATION_PLAN_SNAPSHOT | EXECUTION_PLAN_SNAPSHOT |
  ROUTING_INTENT_SNAPSHOT | ATTEMPT_ROUTE_SNAPSHOT | MODEL_POLICY_SNAPSHOT |
  SANDBOX_PROFILE_SNAPSHOT | GATE_PACK_SNAPSHOT | ROLE_PACK_SNAPSHOT |
  PROMPT_SNAPSHOT | TOOL_POLICY_SNAPSHOT | SKILL_PACKAGE_VERSION |
  SKILL_APPROVAL_DECISION | SKILL_APPROVAL_SET | POLICY_APPROVAL_DECISION |
  POLICY_APPROVAL_SET | SKILL_BUNDLE_SNAPSHOT | SOURCE_BUNDLE |
  DEPENDENCY_IMAGE_SNAPSHOT | RUNTIME_IMAGE_SNAPSHOT | SANDBOX_IMAGE_SNAPSHOT |
  EXECUTION_IMAGE_COMPATIBILITY_SNAPSHOT | CREDENTIAL_PROFILE | SIGNING_KEY
subjectId
subjectDigest
reasonCode
reason
revocationEpoch
effectiveControlPlaneEpoch
effectiveAt
createdBy
securityTicketRef
payloadDigest
signature
~~~

`subjectType` 是版本化闭集，未知值按 §9.1 Fail Closed。命中后的同步动作也是合同的一部分：

| subjectType 组 | Scheduler | Node / Gateway | Gate Supervisor | Git Stager / Publisher |
|---|---|---|---|---|
| Workflow / EvaluationPlan / ExecutionPlan / Role / Prompt / Tool / Skill / Approval | 阻止依赖闭包内新 Run / Attempt | Node 拒绝 admission 并 fence 活动 Attempt；Gateway 停止该合同的新调用 | 拒绝新 Gate；在飞 Gate 终止并输出无裁决事实 | Stager 拒绝新 PREPARED / APPLYING；Publisher 禁止推进指针 |
| RoutingIntent / AttemptRoute / ModelPolicy / CredentialProfile | 阻止新 Attempt | Gateway 在每次调用前拒绝并撤销短 Grant；Node fence | 已冻结 Gate 不受模型路线影响，但语义 Review Run 停止 | 无 ACCEPTED 完整证据则拒绝 Staging |
| Source / Dependency / Runtime / Sandbox / Compatibility / SandboxProfile / GatePack | 阻止新 Attempt / Gate | Node 拒绝挂载并 fence；Gateway 停止关联合同外部能力 | 立即停止相关 Gate，隔离输出 | Stager 拒绝相关 CommitBundle；Publisher 禁止相关发布 |
| SigningKey | 阻止使用该 key 信任链的新对象 | Node / Gateway 拒绝该 key 签名的依赖闭包 | Gate / Attestation 拒绝该 key 链 | Stager / Publisher 拒绝；启动反向索引与重签流程 |

Registry 在写入任意 subjectType 的 RevocationRecord 时，必须在同一 PostgreSQL 事务内 CAS 递增唯一的全局 `revocationEpoch`，把新值写入记录并发布 Outbox。首期不采用 skillEpoch、routeEpoch、credentialEpoch 等分域水位，避免新增 subjectType 时漏入某个域。Workflow、EvaluationPlan、ExecutionPlan、Route、Policy、Profile、GatePack、Role、Prompt、Tool、Skill、Approval、Bundle、Source、三类镜像、Compatibility、CredentialProfile 与 SigningKey 的撤销都推进同一个全局水位。

各组件只有在本地 `lastFullySynchronizedRevocationEpoch` **精确等于**合同、Grant 或授权要求的 `revocationEpoch` 时才可处理；本地值较低表示尚未同步，较高表示请求基于旧视图，两者都 Fail Closed。撤销传播事件必须携带 revocationEpoch、subjectType、subjectDigest、effectiveControlPlaneEpoch 和反向依赖根；缺号时停止处理受保护动作，补齐全局有序日志后再恢复。

安全 RevocationRecord 永久有效且没有 expiresAt；恢复只能创建新 digest 与新审批。需要临时暂停时使用独立 `ContainmentRecord(subject, reason, startsAt, reviewAt, releasedBy)`，解除暂停不改变永久撤销历史。

### 10.8 SourceBundle

~~~text
sourceBundleId
repositoryId
requestedRef
resolvedGitObjectId = {algorithm, hex}
submoduleEntries[] = {path, repositoryId, gitObjectId}
lfsObjects[] = {oidAlgorithm, oid, size}
normalizedTreeManifestDigest
sourceArtifactManifestId / sourceArtifactManifestDigest
ingestorId / ingestorVersion / ingestorImageDigest
sourcePolicyDigest / dataClassification
createdAt / payloadDigest / signature
~~~

SourceBundle 一经发布不可变。Git ref、submodule、LFS 或文件模式任一变化都产生新 digest，不得在 Attempt 内解析浮动版本。

### 10.9 DependencyImageSnapshot

~~~text
DependencyImageBuild / unsigned snapshot:
dependencyImageSnapshotId
dependencyImageSnapshotDigest = payloadDigest
repositoryId / toolchainClass
lockfileManifestDigest
baseImageDigest / builderImageDigest
ociImageDigest
sbomDigest / scaReportDigest / vulnerabilityReportDigest
provenanceDigest / dependencyPolicyDigest
compatibleSandboxProfileDigests[]
buildNetworkPolicyDigest
builtAt / payloadDigest

DependencyImageSignature:
dependencyImageSnapshotId / dependencyImageSnapshotDigest
approvalSetId / approvalSetDigest
signingKeyId / signerId / signedAt / signature

DependencyImagePublication:
dependencyImagePublicationId / dependencyImageSnapshotDigest
environmentScope / state / publishedBy / publishedAt / rowVersion
~~~

构建环境本身使用受控 MicroVM；发布前网络被关闭并在干净环境复验。Builder、Approver、Signer、Publisher 必须是分离身份：Signer 只签精确 payloadDigest，Publisher 只验证审批、扫描、签名和 digest 后发布引用。Attempt 和 Gate 只读使用已发布的 ociImageDigest，不执行包管理器安装。

#### 10.9.1 RuntimeImageSnapshot 与 SandboxImageSnapshot

两类镜像不能只作为裸 digest：

- `RuntimeImageSnapshot` 是 Prime/Pi Runtime、Runtime Driver shim 及其自身运行库；不包含仓库依赖、RolePack、SkillBundle 或宿主 Supervisor。
- `SandboxImageSnapshot` 是 Attempt / Gate 的最小隔离根文件系统与受控执行基座；容器与 MicroVM 分别构建，INTERNAL 只能选择 `isolationClass=MICROVM`。它不包含业务 Runtime 或仓库依赖。

~~~text
RuntimeImageSnapshot:
runtimeImageSnapshotId / runtimeName / runtimeVersion
runtimeImageSnapshotDigest = payloadDigest
ociImageDigest / runtimeBinaryDigest / driverShimDigest
runtimeProtocolVersion / supportedDriverCapabilities[]
sbomDigest / scaReportDigest / vulnerabilityReportDigest / provenanceDigest
builderImageDigest / sourceManifestDigest
requiredSandboxCapabilitiesDigest / runtimeCapabilityManifestDigest
payloadDigest

SandboxImageSnapshot:
sandboxImageSnapshotId / isolationClass = HARDENED_CONTAINER | MICROVM
sandboxImageSnapshotDigest = payloadDigest
ociImageDigest / guestKernelDigest / rootfsDigest / bootstrapDigest
seccompPolicyDigest / syscallPolicyDigest / defaultNetworkPolicyDigest
sbomDigest / scaReportDigest / vulnerabilityReportDigest / provenanceDigest
builderImageDigest / sourceManifestDigest
providedSandboxCapabilitiesDigest / isolationCapabilityManifestDigest
payloadDigest

ExecutionImageCompatibilityCandidate:
executionImageCompatibilityCandidateId
runtimeImageSnapshotId / runtimeImageSnapshotDigest / runtimeOciImageDigest
sandboxImageSnapshotId / sandboxImageSnapshotDigest / sandboxOciImageDigest
dependencyImageSnapshotId / dependencyImageSnapshotDigest / dependencyOciImageDigest
sandboxProfileId / sandboxProfileDigest
capabilityMatchDigest / compatibilityTestEvidenceDigest
compatibilityPolicyId / compatibilityPolicyDigest
testedBy / testedAt / payloadDigest

ExecutionImageCompatibilitySnapshot:
executionImageCompatibilitySnapshotId
candidateId / candidateDigest
policyApprovalSetId / policyApprovalSetDigest
environmentScope / allowedDataClassifications[]
payloadDigest / signingKeyId / signerId / signedAt / signature

ExecutionImageCompatibilityPublication:
executionImageCompatibilityPublicationId
executionImageCompatibilitySnapshotId / executionImageCompatibilitySnapshotDigest
environmentScope
state = PUBLISHED | SUPERSEDED | REVOKED
publishedBy / publishedAt / rowVersion

ExecutionImageSignature:
imageType / imageSnapshotId / imageSnapshotDigest
policyApprovalSetId / policyApprovalSetDigest
signingKeyId / signerId / signedAt / signature

ExecutionImagePublication:
imagePublicationId / imageType / imageSnapshotDigest
environmentScope / state / publishedBy / publishedAt / rowVersion
~~~

每类镜像都采用 Builder → Policy Four-Eyes → Signer → Publisher 分权，任何角色不得兼任相邻高风险职责。RuntimeImageSnapshot 与 SandboxImageSnapshot 的 payload **不互相引用对方 digest**；它们只声明能力。独立 Compatibility Candidate 在两者 digest 已形成后绑定 Runtime、Sandbox、Dependency 与 SandboxProfile，完成测试，再以 `subjectType=EXECUTION_IMAGE_COMPATIBILITY` 走 Policy Four-Eyes，生成单向引用 ApprovalSet 的签名 Compatibility Snapshot。Compatibility Publisher 随后创建独立 Publication；Snapshot 本身不含可变发布状态。合同字段 `runtimeOciImageDigest`、`sandboxOciImageDigest`、`dependencyOciImageDigest` 必须分别等于对应 Snapshot 的 `ociImageDigest`。Publisher 只发布精确批准 digest；Node admission 必须同时验证 Compatibility Publication 为 PUBLISHED、环境与数据等级适用、未撤销，并验证其 Snapshot 正好绑定合同中的四个 Snapshot / OCI digest。兼容性变化创建新 Candidate、Snapshot、ApprovalSet 与 Publication，不得改写旧对象。所有镜像和兼容快照分别进入撤销反向索引；任一命中撤销即阻止新 Attempt，并 fence 使用该 digest 的活动执行。

### 10.10 CommitBundle 与交付授权

~~~text
CommitIntent:
commitIntentId
repositoryId / candidateRef / expectedRefGitObjectId
selectedAttemptId / sourceBundleDigest / outputArtifactManifestDigest
operationIdempotencyKey
normalizedCommitMetadataTemplateDigest / commitTrailerPolicyDigest
createdAt / payloadDigest / signature

CommitBundle payload:
commitBundleId
commitIntentId / commitIntentDigest
sourceBundleId / sourceBundleDigest
selectedAttemptId / outputArtifactManifestDigest
proposedTreeDigest
treeGitObjectId = {algorithm, hex}
parentGitObjectIds[]
normalizedCommitMetadataDigest
operationIdempotencyKey
proposedCommitGitObjectId = {algorithm, hex}
pathPolicyDigest
createdByVerificationJobId
payloadDigest

CommitBundleSignature:
commitBundleId / commitBundleDigest
signingKeyId / signerId / signedAt / signature

GitStagingLease:
gitStagingLeaseId
repositoryId / candidateRef
commitIntentId / commitIntentDigest / operationIdempotencyKey
ownerWorkloadIdentity / ownerInstanceId
gitStagingEpoch / controlPlaneEpoch / revocationEpoch
notBefore / expiresAt
state = ACTIVE | FENCED | EXPIRED | RELEASED
rowVersion

DeliveryAuthorization:
deliveryAuthorizationId
audience = GIT_STAGER
authorizedStagerWorkloadIdentity / leaseOwnerInstanceId
repositoryId / candidateRef / expectedRefGitObjectId
commitIntentId / commitIntentDigest
commitBundleId / commitBundleDigest / proposedCommitGitObjectId
verdictId / verdictDigest / preDeliveryEvidenceDigest
operationIdempotencyKey
allowedAction = STAGE_CANDIDATE_REF
gitStagingLeaseId / gitStagingEpoch / controlPlaneEpoch / revocationEpoch
issuedAt / notBefore / expiresAt
issuerId / signingKeyId / payloadDigest / signature

CandidateStagingOperation:
candidateStagingOperationId
gitStagingLeaseId / gitStagingEpoch
deliveryAuthorizationId / deliveryAuthorizationDigest
repositoryId / candidateRef / expectedRefGitObjectId
commitBundleDigest / verdictDigest / preDeliveryEvidenceDigest
operationIdempotencyKey
state = PREPARED | APPLYING | RECONCILING | CONFIRMED | SUPERSEDED | FAILED | EXPIRED
createdAt / updatedAt / rowVersion
~~~

`operationIdempotencyKey` 在 Gate 前由不可变 CommitIntent 分配，并作为 `X-Platform-Operation-Key` trailer 进入规范化提交元数据，因此参与 proposedCommitGitObjectId 计算和 Gate 验证。CandidateStagingOperation 只在 ACCEPTED Verdict、PreDeliveryEvidence、独立 GitStagingLease 与签名 DeliveryAuthorization 全部存在后创建为 PREPARED，不承载任何“未来才产生”的空字段。DeliveryAuthorization 的 audience、精确 Stager workload identity、Lease owner、仓库、ref、精确 Git 对象、Verdict、证据、controlPlaneEpoch、gitStagingEpoch、全局 revocationEpoch、动作和 TTL 必须逐项匹配；它不是可转用的 bearer token，只授权该受信任服务身份上传已验证 Git 对象并 CAS 指定 candidate ref，不授权 Git Stager 重建树、修改提交元数据、运行 Gate、写其他 ref 或使用 Attempt ExecutionLease。

---

## 11. v1.2 契约补齐

为消除 Skill 运行时漂移，以下已有对象新增必填字段：

| 对象 | v1.3 必填补充 |
|---|---|
| RoutingIntentSnapshot | rolePackSnapshotId/digest、skillBundleSnapshotId/digest、skillSelectionPolicyDigest、promptDigest、toolPolicyDigest、evaluationPlanDigest |
| AttemptRouteSnapshot | rolePackDigest、skillBundleSnapshotDigest、bundleArtifactDigest、expectedMountedSkillTreeDigest、provider/model/thinking、routePolicyDigest；不得包含 AttemptContractDigest |
| RouteContractBindingAttestation | attemptRouteDigest、attemptContractDigest、Lifecycle 签名；不参与前两者摘要 |
| AttemptTerminalEnvelope | expected / observed RolePack、skillBundleSnapshotDigest、bundleArtifactDigest、mountedSkillTreeDigest、SourceBundle，以及 Runtime / Sandbox / Dependency 的 Snapshot 与 OCI digest |
| SessionCheckpointManifest | rolePackDigest、skillBundleSnapshotDigest、bundleArtifactDigest、mountedSkillTreeDigest、attemptContractDigest、sourceBundleDigest，以及 Runtime / Sandbox / Dependency 的 Snapshot 与 OCI digest |
| ArtifactManifest | producerAttemptContractDigest、producerRolePackDigest、producerSkillBundleSnapshotDigest、producerBundleArtifactDigest、producerMountedSkillTreeDigest |
| CommitBundle | sourceBundleDigest、proposedTreeDigest、operationIdempotencyKey / trailer、规范化提交元数据、预计算 tree / commit GitObjectId |
| GateExecution | commitBundleDigest、proposedCommitGitObjectId、gatePackDigest、sandboxProfileDigest、sandboxImageSnapshotDigest / sandboxOciImageDigest、dependencyImageSnapshotDigest / dependencyOciImageDigest、executionImageCompatibilitySnapshotDigest |
| EvaluationVerdict | evaluationPlanDigest、全部 Attestation digest、canonical 有序输入集合 digest |
| Git staging 证明 | commitBundleDigest、deliveryAuthorizationDigest、controlPlaneEpoch、gitStagingEpoch、revocationEpoch、operationIdempotencyKey、读回 GitObjectId |

任何 expected / observed 不一致均为硬错误：

~~~text
SKILL_BUNDLE_DRIFT
ROLE_PACK_DRIFT
ATTEMPT_CONTRACT_DRIFT
~~~

这些错误只能使 Run 进入 NO_VERDICT，或使受影响聚合进入 QUARANTINED，不得降级为 Warning 或成功。

### 11.1 AttemptTerminalEnvelope 的事实边界

终态信封分别报告：

- Driver 观察到的模型与进程状态；
- Node 捕获的退出码、信号、资源、子进程和静默状态；
- 实际挂载摘要；
- 输出 Manifest；
- 未完成动作；
- 是否请求 Checkpoint；
- 是否存在不可确认副作用。

AttemptTerminalEnvelope 不是 Verdict。Lifecycle 汇总 Attempt 事实形成 Run 结果；只有 Evidence Completeness Checker 和 Verification Plane 完成后才能推进 Run。

---

## 12. API 蓝图

### 12.1 传输约定

- 人工、CI 和管理入口：HTTP / JSON，前缀为 /api/v1 与 /governance/v1；
- 服务间：mTLS gRPC；
- Node 与 Runtime Driver：仅使用 Sandbox Supervisor 创建并继承给 Runtime 主进程的专用 stdio / 匿名管道；启动任何仓库子进程前关闭对应描述符并明确禁止继承。MVP 禁止 UDS、TCP 和本地 gRPC；未来若启用，必须新增 ADR、威胁模型、FD 隔离证明与逃逸反例验收；
- 事件：PostgreSQL Transactional Outbox，MVP 由 Outbox Poller 投递；
- Blob：Node 经 Artifact Service 上传，Attempt 不持有对象存储凭据；
- 身份：mTLS / OIDC 工作负载身份，正文自报 actorId 不参与授权。

所有修改请求携带 Idempotency-Key、traceparent 与 schemaVersion。可变资源增加 If-Match 和 expectedState；执行副作用增加 leaseId、fencingToken、capabilityToken 与 operationIdempotencyKey。

### 12.2 控制面与 Node API

| API | 调用方 | 关键校验 | 结果 |
|---|---|---|---|
| POST /api/v1/tasks | 用户 / CI | TaskSpec、数据等级、预算、幂等键 | 创建 Task |
| POST /api/v1/tasks/{id}:cancel | 用户 / 控制面 | If-Match、当前状态、Candidate / Merge 外部操作阶段；APPLIED / 确认不明 / RECONCILING 返回 409 并先对账 | 请求取消或明确拒绝 |
| POST /internal/v1/nodes/{id}:claim-attempt | Node | 既有 READY Attempt、节点身份、容量、epoch | 单事务 CAS READY→CLAIMED，创建 ExecutionLease、ExecutionAssignment 和最终 Contract；不创建 Attempt / Route |
| POST /internal/v1/leases/{id}:renew | Lease Owner | Owner、epoch、数据库时间 | 更新到期时间并发新 LeaseGrant |
| POST /internal/v1/attempts/{id}:mark-provisioning | Node | Lease、Fencing、CAS | 进入 PROVISIONING |
| POST /internal/v1/attempts/{id}:mark-running | Node | 挂载摘要、Lease、CAS | 进入 RUNNING |
| POST /internal/v1/attempts/{id}:report-terminal | Node | 信封 Schema、大小、签名、Fencing | 记录终态事实 |
| POST /internal/v1/attempts/{id}:force-terminal | Lifecycle | 取消、预算、deadline、Lease / Heartbeat、Node epoch、撤销、安全或已上报失败证明 | CANCELLED / BUDGET_EXHAUSTED / FAILED / QUARANTINED / LOST / FENCED / TIMED_OUT |
| POST /internal/v1/runs/{id}:select-output | Lifecycle | 输出已发布、Run CAS | 冻结唯一获胜输出 |

禁止提供通用 set-state API。claim-attempt 的数据库事务中禁止同步调用 Gateway、Git、对象存储或 Runtime。

### 12.3 Skill 治理 API

| API | 作用 |
|---|---|
| POST /governance/v1/skill-package-versions | 从内部或 allowlist 来源创建不可变版本 |
| POST /governance/v1/skill-package-versions/{id}:evaluate | 调度离线评估 |
| GET /governance/v1/skill-package-versions/{id}/evaluations | 查看评估和证据 |
| POST /governance/v1/skill-approval-proposals | 创建审批提案 |
| POST /governance/v1/skill-approval-proposals/{id}:record-decision | 功能或安全 Approver 写一个独立签名 Decision；服务机械计算 quorum / veto |
| POST /governance/v1/skill-revocations | 紧急撤销对象 |
| POST /governance/v1/skill-bundles:build | 确定性构建 Bundle |
| POST /governance/v1/skill-publication-pointers/{scope}:advance | 按环境作用域 CAS 指向已签名 Snapshot |
| POST /governance/v1/role-packs:compile | 绑定 Role、Prompt、Tool 与 Skill |
| POST /governance/v1/role-packs/{id}:publish | 发布 Role Pack |
| POST /governance/v1/policy-approval-proposals | 为 EvaluationPlan、RoutingIntent、ModelPolicy、SandboxProfile、RuntimeImage、SandboxImage、DependencyImage、ExecutionImageCompatibility、SkillBundle 组合或安全基线创建带不可变 Scope 的通用审批提案 |
| POST /governance/v1/policy-approval-proposals/{id}:record-decision | 写独立签名 Decision；降低基线时强制额外 Security Approver |

SkillRoster Adapter 的内部能力表只允许：

~~~text
ScanImmutableSource
GenerateReport
GetAdapterCapabilities
~~~

生产 Adapter 不暴露 PlanMutation、ApplyMutation、UndoMutation、InstallDependency 或 ModifyGlobalConfiguration。

源码与依赖供应链另有受控入口：

| API | 调用方 | 关键校验 | 结果 |
|---|---|---|---|
| POST /internal/v1/source-bundles:ingest | Task / Source Service | 只读仓库 allowlist、精确 ref、数据等级、幂等键 | 签名 SourceBundle |
| POST /governance/v1/dependency-images:build | Dependency Builder | 锁文件、基础镜像、构建策略、MicroVM 身份 | 候选 DependencyImageSnapshot |
| POST /governance/v1/dependency-images/{id}:publish | Dependency Publisher | SCA、SBOM、provenance、审批、签名 | 发布不可变 digest |
| POST /governance/v1/execution-images:build | Runtime / Sandbox Image Builder | imageType、冻结来源、构建策略、受控构建器身份 | 未签名 RuntimeImageSnapshot / SandboxImageSnapshot |
| POST /governance/v1/execution-images/{id}:publish | Execution Image Publisher | PolicyApprovalSet、SBOM、SCA、provenance、兼容矩阵、独立签名 | 发布精确镜像 digest |

### 12.4 Runtime Driver API

~~~text
NegotiateCapabilities
StartAttempt
StreamAttemptEvents
RequestCheckpoint
CancelAttempt
CollectTerminalEnvelope
~~~

每条命令携带 commandId、attemptId、attemptContractDigest、generation 和 fencingToken。

- 同 commandId、同请求摘要返回原结果；
- 同 commandId、不同摘要拒绝；
- 事件流以稳定 cursor 重放；
- 事件缺口、能力协商失败、Skill 漂移或无法证明子进程静默时输出 NO_VERDICT；
- Driver 不提供 MarkRunSuccessful。

### 12.5 Model Gateway API

核心方法：

~~~text
InvokeModel
CancelInvocation
GetRouteAttestation
SettleUsage
~~~

只有 Node Runtime Proxy 可以调用 InvokeModel。请求携带稳定 `invocationId`、`providerRequestId`（获知后补记）、AttemptContract、AttemptRouteSnapshot、requestSequence、requestDigest，以及 Node 持有的 LeaseGrant / GatewayGrant。Gateway 校验签名与 TTL、双 epoch、已完整同步的撤销水位、预算预留和实际 provider / model / thinking；同 invocationId 不重复结算或发起第二次逻辑调用。

实际路线不一致时：

1. 终止调用链；
2. 发布 ModelRouteMismatchDetected；
3. Runtime 调用被终止，Lifecycle 使 Run 进入 NO_VERDICT 或 Task / Attempt 进入 QUARANTINED；
4. 禁止透明 fallback。

### 12.6 Artifact API

~~~text
CreateStagingSession
IssueUploadGrant
CommitStagedObjects
PublishArtifactManifest
ReadArtifactManifest
QuarantineArtifactManifest
~~~

PublishArtifactManifest 只有在 Blob 已 seal、重算 digest 一致、大小和类型合规、扫描完成、Attempt 未被 Fencing / 撤销、producerAttemptContractDigest 匹配，并且 PostgreSQL 的 PUBLISHED + refs + outbox 事务成功时才允许执行。

Node 不直接写 OUTPUT_STAGED。Manifest 发布后由 Outbox 事件触发 Lifecycle CAS。

### 12.7 Gate、Attestation 与 Verdict API

~~~text
ClaimVerificationJob
ReportGateExecution
SignGateAttestation
PublishSemanticReviewAttestation
ComputeVerdict
~~~

- Domain 2 Gate Supervisor 持有 VerificationLease，不持有签名密钥；Domain 7 Gate Command Process 不持 Lease；
- 退出码、超时和信号由 Gate 沙箱外捕获；
- Attestation Signer 不运行仓库代码；
- Verdict Aggregator 不调用 LLM；
- ComputeVerdict 只接受冻结 EvaluationPlan、Artifact、GateAttestation 与 SemanticReviewAttestation；
- 证据缺失、签名无效、摘要不一致或独立性不足返回 NO_VERDICT。
- Gate Supervisor 必须绑定 SourceBundle、DependencyImageSnapshot 与 CommitBundle；其创建的每个 Domain 7 命令环境完全断网。

### 12.8 Git Adapter API

首期开放：

~~~text
StageCandidateRef
ReadCandidateRef
CloseCandidateRef
ReconcileCandidateOperation
~~~

首期硬禁：

~~~text
UpdateProtectedRef
ForcePush
DeleteProtectedRef
AutoMerge
~~~

StageCandidateRef 输入至少包括 repositoryId、candidateRef、expectedRefGitObjectId、commitBundleDigest、proposedCommitGitObjectId、selectedAttemptId、gitStagingLeaseId、gitStagingEpoch、deliveryAuthorizationDigest、verdictDigest、preDeliveryEvidenceDigest 与 operationIdempotencyKey。它不接收 Attempt fencingToken。

若 CAS 返回不确定或确认丢失，必须先读回：

- ref 与预期 GitObjectId 匹配，且提交中的 `X-Platform-Operation-Key` trailer 与数据库 PREPARED 记录一致：按原操作成功；
- ref 已变化且 Operation Key 不匹配：SUPERSEDED；
- 无法确认：RECONCILING。

`CloseCandidateRef` 在 Phase 2 只写数据库隔离 Overlay，并在 Provider 支持时关闭关联 PR；默认不删除 Git ref。删除或移动 ref 是单独、可审计且需人工授权的清理操作。

---

## 13. 幂等、事件与错误语义

### 13.1 幂等身份

| 字段 | 语义 |
|---|---|
| Idempotency-Key | 一次 API 命令的稳定身份 |
| operationIdempotencyKey | 一次逻辑外部副作用的稳定身份 |
| deliveryId | 某次投递身份，重试可变化 |
| eventId | 领域事件身份，重投保持不变 |
| attemptId | 一次实际执行；重试原则上生成新 ID |

服务器保存 scope、idempotencyKey、requestDigest、responseStatus、responseDigest、resourceRefs、createdAt 与 expiresAt。

- 同键、同摘要：返回首次结果；
- 同键、不同摘要：409 IDEMPOTENCY_KEY_REUSE；
- 客户端超时：使用原键查询或重试；
- 幂等记录保留期不得短于最长对账周期。

### 13.2 CAS

每次状态迁移同时校验 aggregateId、expectedState、expectedRowVersion 与 proofRefs。成功后 rowVersion 加一。

~~~text
409 INVALID_STATE_TRANSITION
412 STALE_ROW_VERSION
~~~

失败返回当前状态与版本，但不自动覆盖。

### 13.3 Epoch 与 Fencing

逻辑 Token：

~~~text
fencingToken = (
  controlPlaneEpoch,
  resourceExecutionEpoch
)
~~~

每个外部能力代理逐请求执行：

1. 本地验签；
2. 校验 audience 与 action；
3. 校验 TTL；
4. 校验 controlPlaneEpoch **等于**本代理已完整同步的当前 epoch；低于则 stale，高于则先拒绝并同步，不得猜测放行；
5. 校验 resourceExecutionEpoch 与该 resourceId 的已同步当前 epoch 精确相等；
6. 校验撤销集合；
7. 校验 token 绑定的 resourceId、attemptId、leaseId、owner、合同与请求摘要。

模型调用热路径不允许同步查询 PostgreSQL。撤销状态主动传播；若传播通道超过 lastSync + 2 × tokenTTL 未同步，代理 Fail Closed。

旧 Node 或 Worker 的晚到结果可以进入隔离审计区，但不得推进状态、发布正式 Manifest、选择 Run 输出或更新候选 ref。

### 13.4 通用事件信封

~~~json
{
  "schemaVersion": "1.3",
  "eventId": "0199a001-93d1-7abc-8d11-001122334455",
  "eventType": "ArtifactManifestPublished.v1",
  "occurredAt": "2026-09-04T08:30:12.123456Z",
  "producer": {
    "serviceName": "artifact-service",
    "instanceId": "artifact-01"
  },
  "trace": {
    "traceId": "8f2b4fd756e3417fa2821d79b04f9231",
    "spanId": "6f2b4fd756e3417f",
    "parentSpanId": "5e1a3ec645d2306e",
    "taskId": "0199a001-0c88-7111-8000-111111111111",
    "runId": "0199a001-1d99-7222-8000-222222222222",
    "attemptId": "0199a001-2eaa-7333-8000-333333333333"
  },
  "aggregate": {
    "type": "ArtifactManifest",
    "id": "0199a001-4fcc-7555-8000-444444444444",
    "version": 1
  },
  "controlPlaneEpoch": 42,
  "operationIdempotencyKey": "artifact-publish:0199a001-4fcc-7555-8000-444444444444:v1",
  "payloadDigest": "sha256:<digest>",
  "payload": {
    "artifactManifestDigest": "sha256:<digest>",
    "producerAttemptContractDigest": "sha256:<digest>"
  }
}
~~~

事件 Schema 按作用域决定 trace 内的必填 ID，不要求不存在的父实体。ArtifactManifestPublished 使用 ArtifactManifest 自身 aggregate/version；Lifecycle 消费后再独立产生 AttemptOutputStaged，禁止借用 Attempt aggregate version。

### 13.5 Outbox / Inbox

- 状态变化与 Outbox Event 在一个 PostgreSQL 事务提交；
- eventId 重投不变，deliveryId 可变；
- Inbox 唯一键为 consumerId + eventId；
- 消费者在同一事务登记 Inbox 并改变自身状态；
- aggregate.version 低于当前版本时记录 stale 并 no-op；
- 版本空洞时暂停该 Aggregate 并触发补发 / 对账；
- Dispatcher 故障不改变已提交权威状态。

### 13.6 核心事件目录

**Skill 治理**

~~~text
SkillPackageVersionIngested.v1
SkillEvaluationCompleted.v1
SkillApprovalGranted.v1
SkillApprovalRejected.v1
SkillRevoked.v1
SkillBundleBuilt.v1
SkillBundlePublished.v1
SkillBundleRevoked.v1
RolePackPublished.v1
RevocationWatermarkAdvanced.v1
SourceBundlePublished.v1
DependencyImagePublished.v1
~~~

**执行与交付**

~~~text
TaskAccepted.v1
RunReady.v1
AttemptClaimed.v1
AttemptProvisioningStarted.v1
AttemptRunning.v1
AttemptTerminalReported.v1
ArtifactManifestPublished.v1
AttemptOutputStaged.v1
RunOutputSelected.v1
GateAttestationPublished.v1
SemanticReviewAttestationPublished.v1
VerdictComputed.v1
CommitBundlePublished.v1
DeliveryAuthorizationIssued.v1
CandidateRefStaged.v1
GitStagingResultPublished.v1
TaskCandidateDelivered.v1
~~~

**故障与隔离**

~~~text
LeaseExpired.v1
AttemptFenced.v1
AttemptLost.v1
SkillBundleDriftDetected.v1
ModelRouteMismatchDetected.v1
EvidenceIncomplete.v1
ArtifactQuarantined.v1
CandidateRefReconciliationRequired.v1
GlobalStopActivated.v1
~~~

事件名表示已经发生的事实。RunGate、MergeCode 等是命令，不能伪装为事件。

### 13.7 标准错误码

~~~text
IDEMPOTENCY_KEY_REUSE
STALE_ROW_VERSION
INVALID_STATE_TRANSITION
LEASE_EXPIRED
FENCING_REJECTED
CONTROL_PLANE_EPOCH_STALE
DIGEST_MISMATCH
SIGNATURE_INVALID
REVOCATION_HIT
SKILL_NOT_APPROVED
SKILL_BUNDLE_DRIFT
ROLE_PACK_DRIFT
ATTEMPT_CONTRACT_DRIFT
MODEL_ROUTE_MISMATCH
EVIDENCE_INCOMPLETE
PROTECTED_REF_FORBIDDEN
RECONCILIATION_REQUIRED
~~~

---

## 14. 端到端流程

### 14.1 Skill 准入与 Role Pack 编译

1. 治理人员提交内部或 allowlist 来源及精确 revision。
2. Intake 在一次性环境冻结源字节并生成 sourceSnapshotDigest。
3. 平台安全解包器执行路径、链接、大小、类型和碰撞门禁。
4. SkillRoster Adapter 对同一快照生成结构报告，Adapter 验证工具 digest、JSON Schema、覆盖和版本。
5. Secret、许可、脚本、语义和行为评估器分别产生 SkillEvaluation。
6. Governance Policy 聚合评估，缺失或 INDETERMINATE 视为不满足。
7. 提案人创建 ApprovalScopeSnapshot；独立功能 Approver 与安全 Approver 分别签署 ApprovalDecision，满足 quorum 且无 veto 后形成 ApprovalSet。
8. Builder 从精确摘要确定性构建 SkillBundle。
9. 按统一测试 `SKILL-REPRO-001` 在至少 3 个独立干净环境、总计 100 次构建，要求 100/100 digest 一致。
10. Signer 只对 Registry 授权的精确 digest 签名。
11. Publisher 按 LAB / BENCHMARK / CANARY / PRODUCTION 环境作用域 CAS 更新 PublicationPointer 和 Artifact 引用；不修改 Snapshot 状态。
12. Role Pack Compiler 验证 Skill 能力是 Role 与 Sandbox 能力交集的子集。
13. 新 RolePackSnapshot 通过离线基准、Shadow / Canary 后才可供新 Attempt 使用。

### 14.2 单任务执行

1. Task API 接收 TaskSpec，校验白名单、数据、预算和调用方幂等键。
2. Source Ingestor 只读解析仓库 ref，固定 submodule / LFS，发布签名 SourceBundle；Dependency Registry 解析已批准 DependencyImageSnapshot。
3. Workflow Compiler 生成不可变 DAG 与 EvaluationPlan。
4. Route Resolver 冻结 RoutingIntent / AttemptRoute；Registry 解析 Role / Skill / Prompt / Tool / Model / Gate / Sandbox 快照。
5. Orchestrator 创建 Run、既有 Route 和 `CREATED → READY` Attempt。
6. Node 使用 claim-attempt 单事务 CAS `READY → CLAIMED`，获得 ExecutionLease、Assignment 与最终 AttemptContract。
7. Node 重新验证 SourceBundle、依赖 / Runtime 镜像、SkillBundle、签名、审批和撤销水位。
8. Node 创建完全断网沙箱，挂载只读 SourceBundle、DependencyImage、RolePack 与 SkillBundle。
9. Prime/Pi Runtime 执行冻结合同；模型意图由 Node Runtime Proxy 代转，Runtime 不持有 Gateway Grant。
10. Node 捕获 AttemptTerminalEnvelope，并通过 Artifact Service seal 字节、事务发布输出 Manifest。
11. Delivery Service 先以稳定规则创建不可变 CommitIntent 与 operationIdempotencyKey；Commit Assembler 把该 key 的 trailer 连同 ProposedTree 固定为签名 CommitBundle；Domain 2 Gate Supervisor 在独立完全断网的 Domain 7 命令沙箱逐步骤执行 Gate，独立 Review Run 只产生语义证据。
12. Verdict Aggregator 机械聚合并生成 PreDeliveryEvidence；只有 ACCEPTED 才签发绑定 Stager identity、双 epoch 与 TTL 的独立 DeliveryAuthorization / GitStagingLease，随后 Delivery Service 创建 CandidateStagingOperation PREPARED。
13. Delivery Service CAS 到 APPLYING；Git Stager 只从 Artifact Store 按 digest 读取签名 CommitBundle，上传其固定对象并对任务专属 candidate ref 执行 CAS，不运行仓库代码。
14. Git Stager 读回 GitObjectId / Operation Key，Evidence Service 形成最终 DeliveryEvidenceBundle。
15. Lifecycle 仅在全部读回和证据成立后把 Task 从 DELIVERY_PENDING 改为 SUCCEEDED。

### 14.3 有界修复

Phase 3 才启用：

1. 首轮 Verdict 为 REPAIRABLE；
2. Reviewer 输出结构化问题和证据引用；
3. 基础设施失败或获批 provider / model / thinking 路线切换，只能发生在形成 Verdict 之前：原 Run 从 EXECUTING 进入 RETRY_WAIT，再到 READY；Lifecycle 只能复用同一 plannedAttemptInput / Route，或选择 ExecutionPlanSnapshot 中已预冻结的另一 plannedAttemptInput / AttemptRouteSnapshot，再创建新 Attempt、新 Session 和新的 RouteContractBindingAttestation。不得在 Task 已 EXECUTING 后动态创建未计划 Route；若所需路线不在计划中，原 Run 不能继续，必须转 FAILED / HANDOFF 或由 Task 级新计划决策处理；这不是 Repair；
4. 一旦 Verdict 已使原 Run 进入 REPAIR_REQUIRED，任何新的语义执行——即使只打算更换路线——都必须创建新 `Repair Run`，保存 parentRunId、repairRound、failureFingerprint、冻结 Repair Contract，并在其下创建全新 Attempt；旧 Run 只可进入 SUPERSEDED / FAILED / HANDOFF_TO_HUMAN，不能退回 READY 或 EXECUTING；
5. 若验收条件变化，则创建新 Spec / Workflow 并由 Task 级决策重新准入，不能称为 Repair；
6. 最多允许 `1 initial + 2 repair = 3` 次语义执行，且受时间、Token 与成本上限约束；
7. 每个 Repair Run 重新冻结合同并执行全部确定性 Gate；
8. 达到上限仍不通过则 FAILED 或 HANDOFF_TO_HUMAN。

### 14.4 Skill 撤销与污染响应

1. Registry CAS 写 SkillRevocationRecord 并提升 revocationEpoch；
2. Scheduler 同步阻止新 Attempt；
3. 广播到 Node、Gateway、Artifact、Verification 与 Git Stager；
4. 未确认水位的 Node 被 Fencing；
5. 终止相关完整进程树并撤销短凭据；
6. 隔离相关 Session、CommitBundle、Artifact，并对候选 ref 写隔离 Overlay / 关闭关联 PR；默认不删除历史 ref；
7. 按 package → bundle → role → attempt → task → artifact → ref 生成爆炸半径；
8. 保留原始报告、审批、签名、Trace 和终止证据；
9. 禁止同一 Bundle 自动重试；
10. 恢复必须形成新来源 / 修复提交、新 digest、新审批、新签名和新 Canary。

---

## 15. 代码仓库与技术栈

### 15.1 推荐单仓结构

~~~text
/
├─ cmd/
│  ├─ control-api/
│  ├─ control-worker/
│  ├─ node-agent/
│  ├─ source-ingestor/
│  ├─ dependency-builder/
│  ├─ model-gateway/
│  ├─ verification-worker/
│  ├─ git-stager/
│  └─ governance-cli/
├─ services/
│  ├─ task/
│  ├─ registry/
│  ├─ workflow/
│  ├─ lifecycle/
│  ├─ lease/
│  ├─ routing/
│  ├─ budget/
│  ├─ artifact/
│  ├─ source/
│  ├─ dependency/
│  ├─ verification/
│  ├─ delivery/
│  ├─ governance/
│  └─ reconciler/
├─ internal/
│  ├─ db/
│  ├─ outbox/
│  ├─ inbox/
│  ├─ authz/
│  ├─ canonical/
│  ├─ signing/
│  ├─ tracing/
│  └─ errors/
├─ contracts/
│  ├─ openapi/
│  ├─ proto/
│  ├─ jsonschema/
│  └─ examples/
├─ adapters/
│  ├─ runtime-scripted/
│  ├─ runtime-prime/
│  ├─ skillroster/
│  ├─ dependency-builder/
│  ├─ git-provider/
│  ├─ model-provider/
│  └─ artifact-s3/
├─ runtime-bridge/
│  └─ prime/
├─ policies/
│  ├─ sandbox/
│  ├─ dependencies/
│  ├─ skills/
│  ├─ roles/
│  ├─ models/
│  └─ gates/
├─ migrations/
├─ deploy/
│  ├─ compose/
│  ├─ images/
│  ├─ seccomp/
│  └─ systemd/
├─ test/
│  ├─ contract/
│  ├─ state-machine/
│  ├─ fault-injection/
│  ├─ sandbox/
│  ├─ source-ingestion/
│  ├─ dependency-image/
│  ├─ security/
│  ├─ benchmark/
│  └─ e2e/
├─ runbooks/
└─ docs/
   ├─ architecture/
   ├─ adr/
   └─ threat-model/
~~~

### 15.2 技术选择

| 层 | 默认选择 | 原因 |
|---|---|---|
| 控制面、Node、Gateway、Worker | Go | 单一主要语言、并发与静态二进制、适合基础设施服务 |
| Prime Runtime Bridge | TypeScript / Node，保持极薄 | 与 Prime/Pi 生态兼容；不承载权威业务逻辑 |
| 状态与事务 | PostgreSQL | CAS、事务、Outbox、审计索引 |
| 大制品 | MinIO / S3 API | 内容寻址、版本与生命周期 |
| 容器 | OCI + cgroup v2 + seccomp + AppArmor/SELinux | Phase 2 可落地的隔离组合 |
| 服务间协议 | gRPC + Protobuf | 强类型内部合同 |
| 外部 API | HTTP / JSON + OpenAPI | 审批、CI 和管理易接入 |
| 事件 | PostgreSQL Outbox / Inbox | MVP 降低运维复杂度 |
| 可观测性 | OpenTelemetry | Trace、Metric、Log 关联 |
| 签名 | Ed25519 候选 | 简洁、可离线验证；Phase 0 ADR 冻结 |
| 制品清单 | JSON Manifest + SHA-256 | 跨语言、可审计、内容寻址 |

所有具体版本在 Phase 0 进入 dependency lock，不使用 latest 标签。Prime Agent v0.9.1 是首个兼容基线，不等于永久版本。

### 15.3 代码边界

- 每张权威表只有一个服务包拥有写 Repository；
- 其他组件只能经 API 或事件访问，不共享“万能数据库账号”；
- adapter 不能直接修改核心领域状态；
- runtime-bridge 不链接数据库、签名、Git 或 Artifact SDK；
- policy 与 contract 作为版本化制品进入测试；
- generated code 与手写业务代码目录分离；
- Schema 变化必须带兼容测试、迁移、回滚和 ADR；
- 首期一个 Git Provider Adapter，不为尚未验证的多供应商抽象过度设计。

### 15.4 环境

| 环境 | 数据 | 外部副作用 | 用途 |
|---|---|---|---|
| local | 合成 | 无 | 单元、Schema、状态机 |
| integration | 合成 | 本地模拟 | PostgreSQL / MinIO / Git 模拟集成 |
| security-lab | 恶意合成样本 | 无 | 沙箱、路径、撤销、故障注入 |
| benchmark | 冻结 PUBLIC / 脱敏集 | 隔离候选仓库 | 正式阶段验收 |
| shadow | 真实批准输入副本 | 禁止 | Phase 4.5 对照 |
| canary | 有限真实输入 | 仅隔离候选分支 | 受控生产前验证 |

---

## 16. 安全设计与威胁模型

### 16.1 总体威胁

| 编号 | 威胁 | 主要控制 | 失败方式 |
|---|---|---|---|
| T01 | 仓库 / Issue / Skill Prompt Injection | 来源标签、Role 最小权限、确定性 Gate、语义风险证据 | 隔离 / NO_VERDICT |
| T02 | 沙箱逃逸和宿主读取 | 完全断网、非 root、只读挂载、FD 白名单、seccomp、MAC、路径门禁 | 终止 / QUARANTINED |
| T03 | 凭据泄露 | Gateway / Stager 代理、短令牌、日志脱敏、DLP | 撤销 / Global Stop |
| T04 | 模型路线漂移 | Route Snapshot、实际身份读回、Route Attestation | MODEL_ROUTE_MISMATCH |
| T05 | 假成功 | 独立 Gate、签名 Attestation、Evidence Completeness、机械 Verdict | NO_VERDICT / FAILED |
| T06 | 过期 Lease 或旧 Node 复活 | 双 epoch Fencing、短 Grant、逐副作用校验 | FENCING_REJECTED |
| T07 | 重复消息 / 不确定确认 | Outbox / Inbox、幂等键、CAS、读回对账 | RECONCILING |
| T08 | Git 基线漂移或越权 | 精确 base GitObjectId、候选命名空间、最小权限、ref CAS | SUPERSEDED / 拒绝 |
| T09 | 制品污染 | 临时区、重算 digest、扫描、seal bytes、数据库事务发布 | QUARANTINED |
| T10 | 审计泄露原始数据 | 数据分级、结构化 Trace、字段白名单、保留策略 | 拒绝写入 / 隔离 |
| T11 | 撤销传播延迟 | 单调 watermark、短 TTL、主动广播、过期 Fail Closed | 停止外部能力 |
| T12 | 控制面自身被攻陷 | 服务身份、唯一写入者、职责分离、WORM、Break-glass 审计 | Global Stop / 恢复 |

### 16.2 Skill 专项威胁

| 编号 | 威胁 | v1.3 控制 |
|---|---|---|
| SK-T01 | 恶意 Skill 诱导越权 | 人工语义审查、隐藏反例、Runtime 无扩权能力 |
| SK-T02 | symlink / hardlink / junction 逃逸 | MVP 全拒绝，平台安全解包器二次校验 |
| SK-T03 | 压缩穿越 / 压缩炸弹 | 根句柄约束、文件数 / 深度 / 字节上限 |
| SK-T04 | 脚本后门 | 逐文件申报、静态扫描、人工审查、隔离行为测试 |
| SK-T05 | 自动安装 MCP / 依赖 | 格式硬拒绝、运行时断网、无包管理凭据 |
| SK-T06 | 浮动来源漂移 | commit / object / digest 固定，同一快照扫描与构建 |
| SK-T07 | TOCTOU | 先冻结 CAS 快照，打包前复算 |
| SK-T08 | 同名异物 | 来源 + revision + digest 身份，歧义 Fail Closed |
| SK-T09 | 使用证据伪造 | 可信出口 Trace、覆盖声明、隐藏回归 |
| SK-T10 | 借 Role Pack 扩权 | requiredCapabilities 子集编译约束 |
| SK-T11 | Registry / Signer 身份被盗 | Four-Eyes、Builder / Approver / Signer 分离、KMS/HSM |
| SK-T12 | Runtime 动态改写 | 无 SkillRoster、只读挂载、无治理 API |
| SK-T13 | 已撤销 Skill 的在飞使用 | Revocation epoch、Gateway 逐请求校验、Node Fencing |
| SK-T14 | 污染经多 Role 扩散 | 反向索引、分层 Canary、爆炸半径限制 |
| SK-T15 | 许可或权属错误 | SPDX、来源责任人、Notice、人工 / 法务门禁 |
| SK-T16 | SkillRoster / Adapter 自身污染 | 固定二进制 digest、SBOM、隔离运行、Schema 校验、可替换 |

### 16.3 能力与凭据矩阵

| 主体 | 可持有 | 不得持有 |
|---|---|---|
| Attempt / 仓库进程 | 本地隔离工作区能力；无网络 Grant | DB、对象存储、Git、Gateway Grant、模型供应商密钥 |
| Prime/Pi Runtime | 受监督 Driver 管道与冻结工具能力 | 网络 Socket、Gateway Grant、通用控制面写权、治理 API、Signer |
| Node Agent / Runtime Proxy | claim / heartbeat / Artifact / Gateway 短 Grant | Git Remote、Skill 发布、Verdict 签名 |
| Model Gateway | 供应商凭据、Route / Budget 校验权 | Task 终态、Git 写权 |
| Gate Supervisor（Domain 2） | 验证工作区、逐步进程控制与 VerificationLease | Attestation 私钥、Git 写权、模型 Grant |
| Gate Command Process（Domain 7） | 单步只读输入、临时可写工作区、stdout / stderr 管道 | VerificationLease、权威结果写权、签名密钥、Git / 模型 /网络能力 |
| Attestation Signer | 精确 GateResult digest 签名 | 仓库执行、模型调用 |
| Git Stager | 单仓库候选命名空间短凭据、GitStagingLease | Attempt ExecutionLease、默认 / 受保护分支、生产部署、仓库执行能力 |
| Source Ingestor | Git 只读凭据、SourceBundle 上传 Grant | Git 写权、仓库执行、模型调用 |
| Dependency Builder | 隔离构建源访问、候选镜像上传 Grant | 生产 Registry 写权、Signer、运行时自动安装 |
| Dependency / Execution Image Signer | 精确已批准 Snapshot digest 的签名能力 | 镜像构建、审批决策、发布指针写权 |
| Execution Image Publisher | 已签对象与 PolicyApprovalSet 只读权、发布指针 CAS | 构建器、审批槽、Signer 私钥 |
| Compatibility Evaluator / Signer | 前者写 Candidate 测试事实；后者只签已批准 Candidate | 互相兼任、修改镜像、运行时准入旁路 |
| Skill Builder | 读取已批准 Package、写候选 Bundle | 审批和签名 |
| Skill Approver | 审批决策 | Builder 文件写权、Signer 私钥 |
| Skill Signer | 精确已批准 digest 签名 | 内容修改、审批创建 |
| Commit Assembler | 冻结输入读取、CommitIntent、未签名 Git 对象输出 | 仓库执行、Git 写权、Gate / Verdict 决定权 |
| Commit Bundle Signer | 精确 CommitBundle payloadDigest 签名 | 内容组装、Gate 执行、DeliveryAuthorization |
| Delivery Authorization Service | ACCEPTED Verdict / PreDeliveryEvidence 读取、GitStagingLease 与授权签发 | Git 凭据、仓库执行、修改 CommitBundle / Verdict |
| Verdict Aggregator | 冻结 EvaluationPlan 与签名证据读取、纯规则 Verdict 写权 | 模型调用、仓库执行、Git 写权、补造证据 |

能力令牌不得进入日志、Artifact、Session Checkpoint 或终态信封。所有令牌携带 audience、actions、subject、epoch、notBefore 与 expiresAt。

### 16.4 Kill Switch

分三级：

- **Scope Stop**：停止某个 Skill、Role、模型、仓库或任务类；
- **Execution Stop**：停止新 Attempt，终止指定活动 Attempt，保留控制面读服务；
- **Global Stop**：停止所有调度和外部副作用，提升 controlPlaneEpoch，Fencing 全部节点。

触发 Global Stop 的最低条件包括：控制面 epoch 回退、签名根泄露、受保护分支写入、无法界定的凭据泄露、跨 Attempt 数据污染或系统性假成功。

---

## 17. 测试、评测与验收方法

### 17.1 测试层次

| 层次 | 重点 | 必须证明 |
|---|---|---|
| 单元测试 | 纯函数、策略、规范化、digest | 正反例、边界值 |
| 状态机模型测试 | Task / Run / Attempt / Skill | 非法迁移不可达、终态不可逆 |
| Schema / 契约测试 | API、事件、Manifest、签名 | 未知字段 / 版本 Fail Closed |
| 集成测试 | PostgreSQL、MinIO、Outbox、Adapter | 原子性、幂等、读回 |
| Runtime 协议测试 | Scripted Driver、Prime Driver | 静默、崩溃、取消、重放 |
| 沙箱安全测试 | 网络、文件、进程、凭据 | 逃逸和越权全部拒绝 |
| Skill 供应链测试 | 路径、链接、漂移、许可、脚本 | 扫描不等于批准 |
| 故障注入 | DB、Store、Node、Gateway、Git | 不产生假成功和重复副作用 |
| E2E | Task 到候选分支 | 完整证据和可重放 |
| 基准 / 对照 | 质量、成本、延迟 | 统计口径冻结、保留集防污染 |

### 17.2 基准集

Phase 0 建立两套互不混淆的冻结基准：

- **Positive Set：73 个**应当产生候选分支的 PUBLIC / 脱敏任务，覆盖缺陷修复、测试补充和小型功能；
- **Negative / Adversarial Set：不少于 40 个**不应产生候选分支的任务，覆盖模糊规格、安全诱导、超预算 / 超时、Git 漂移以及 Skill / Route / Evidence 漂移。

负例不进入 PositiveTaskE2ESR 分母。全量基准共同计算 ExpectedOutcomeMatch；UnsafeAcceptanceRate 只以负例为分母。

至少 20% 为保留集，不参与 Prompt、Workflow、Role 或 Skill 调优。每个任务含输入基线、允许路径、禁止路径、确定性 Gate、预期终态、预算和数据等级。

### 17.3 指标定义

- **PositiveTaskE2ESR**：仅对期望成功的 73 个任务统计，从受理到候选分支、全部 Gate、最终 Verdict 和 DeliveryEvidenceBundle 均完整通过的任务比例；同时报告每任务 3 次的运行级比例和类别宏平均。
- **ExpectedOutcomeMatch**：正例与负例的实际终态精确匹配预期终态的比例。
- **UnsafeAcceptanceRate**：负例错误形成候选分支的比例；硬门槛为观测 `0/N`。
- **False Success**：平台宣告成功，但真实 Gate、制品或外部副作用不满足成功合同。
- **Evidence Completeness**：要求字段、日志、摘要、路由证明、审批和制品引用均存在且可校验的 Attempt 比例。
- **Infrastructure Failure**：非任务自身原因导致的调度、数据库、制品、节点、驱动或网关失败。
- **C**：Phase 0 根据目标单机硬件实测并冻结的额定并发 Attempt 数。
- **SuccessWithin2Repairs**：首次执行加最多两次 Repair（总计最多 3 次语义执行）内通过的正向任务比例。

False Success 是安全硬门槛。确定性反例套件要求逐例 100% 符合，不用置信区间替代回归门禁；来自冻结任务分布的独立模型运行则报告观测 `0/N` 及单侧 95% 上界，不能把样本内为 0 宣称为真实风险为零。主观标签采用盲态双标并报告 Cohen's κ；客观 Gate Oracle 必须确定性重放一致。

### 17.4 契约专项验收

Phase 1 前必须通过：

1. 安全关键 JSON Schema 全部拒绝未知字段。
2. `SKILL-REPRO-001`：相同 Package 输入在至少 3 个独立干净环境总计构建 100 次，bundleArtifactDigest 与 bundleManifestDigest / expectedMountedSkillTreeDigest 均 100/100 一致；Snapshot metadata digest 不参与此判定。
3. 任一 Bundle 文件改动 1 Byte，Node 在 Runtime 启动前拒绝。
4. SkillRoster 报告通过但 Secret / Script Review 失败时，Package 不获批。
5. 提交者等于审批者时，审批 API 拒绝。
6. 已撤销 Bundle 不创建新 Attempt，活动 Attempt 在窗口内 Fencing。
7. 同幂等键同请求不重复副作用，同键不同请求返回冲突。
8. 重复、乱序和确认丢失不重复产生 Artifact、Attestation 或 Git ref。
9. 旧 epoch Node 复活后的状态写和副作用全部拒绝。
10. TerminalEnvelope 的 Role / Skill expected 与 observed 不一致时不能 PASS。
11. Gate 沙箱伪造“tests passed”文本但退出码非零时，Attestation 必须失败。
12. Verdict Aggregator 静态和运行检查证明不存在模型调用。
13. Git CAS 成功但确认丢失时，读回识别原操作且不创建第二提交。
14. 任取 20 个 Task，可沿一个 traceId 找到从规格到候选 Git 的完整链。
15. 日志、事件和证据中不存在明文 LeaseGrant。
16. SourceBundle 固定 ref、submodule、LFS 与文件模式；浮动或不一致输入全部拒绝。
17. DependencyImage 在 MicroVM 构建并含 SBOM / SCA / provenance；Attempt / Gate 在线安装全部拒绝。
18. Gate 对 CommitBundle 预计算 GitObjectId 验证；Git Stager 未执行仓库代码且读回对象完全相同。
19. 高于本地已同步 epoch 的 Token 先拒绝并触发同步，不存在 fail-open 窗口。

### 17.5 可复现规则

正式报告必须记录：

- 基准集版本与保留集比例；
- 运行日期、硬件、镜像 digest；
- 组件、策略、Role、Skill、Prompt、Gate、模型与 Runtime 版本；
- 每任务随机种子或供应商可用的确定性参数；
- 原始结果和失败，不删除异常样本；
- 重试、撤销、排除和人工干预；
- 统计方法与置信区间。

---

## 18. 可观测性、SLO 与预算

### 18.1 必备指标

**状态与可靠性**

- task / run / attempt 各状态数量与停留时间；
- Lease 续约、过期、Fencing、孤儿收敛；
- Outbox backlog、Inbox duplicate、版本空洞；
- Reconciler 差异与修复结果；
- Artifact staging / publish / quarantine；
- Git CAS、读回和 reconciliation。

**模型与预算**

- 按 Task、Role、Attempt、模型的输入 / 输出 Token；
- 实际 provider/model/thinking 与路线匹配；
- reservation、settlement、超限拦截；
- Accepted Task 成本和失败浪费；
- Gateway 延迟、错误、重试和熔断。

**Skill**

- Package / Bundle 数量与状态；
- EXPOSED / MATCHED / LOADED / INVOKED / OUTCOME_LINKED；
- Trace coverage 和 attribution quality；
- Bundle 构建可重复率；
- 审批过期、撤销水位和传播延迟；
- 每 Role 的 Skill 数量与上下文 Token。

**安全**

- 网络、路径、凭据、受保护分支拒绝；
- Route / Role / Skill / Contract drift；
- Secret / DLP Finding；
- Break-glass 和权限变化；
- QUARANTINED / Global Stop。

### 18.2 Phase 2 初始 SLO

| 指标 | 目标 |
|---|---|
| Evidence Completeness | 100% |
| Route Attestation 覆盖 | 100% |
| False Success | 0 |
| 预算超限阻断 | 100% |
| 控制面 API p95 | < 500 ms，不含模型时间 |
| 调度到沙箱启动 p95 | < 30 s |
| 进程退出后合法终态收敛 | 99% 在 60 s 内；其余在 2 个 Lease TTL 内 |
| Infrastructure Failure | ≤ 2% |
| 受保护分支未授权写入 | 0 |
| 运行时 Skill 漂移 | 0 |
| 撤销后新 Attempt 使用旧 Bundle | 0 |

### 18.3 预算模型

每个 Task、Run 和 Attempt 分别设置：

- 最大墙钟时间；
- 最大模型调用次数；
- 最大输入 / 输出 Token；
- 最大金额；
- 最大 Runtime 重试；
- 最大 Repair 轮数；
- 最大 Artifact 字节；
- 最大并发子任务；
- 最大外部副作用。

预算流程：

~~~text
估算 → 预留 → 每次调用前检查 → 实时记账 → 结算
              ↓
        80% Warning
        90% 降低非必要工作
       100% 硬停止
~~~

安全 Gate、终止清理和证据封存保留独立应急预算，不能因业务 Token 用尽而被省略。

### 18.4 告警

- Critical：假成功、受保护分支写入、凭据泄露、控制面 epoch 回退、签名失败、跨 Attempt 污染；
- High：Route / Skill / Role drift、撤销传播超 SLO、Artifact 摘要不一致、Git 不可确认副作用；
- Warning：成本异常、Outbox backlog、启动延迟、错误率和容量逼近；
- Info：正常发布、Canary 晋级、Bundle supersede 和例行对账。

Critical 默认触发 Scope Stop 或 Global Stop，不等待模型判断。

---

## 19. 分阶段实现路线与 Go / No-Go

### 19.1 总体晋级规则

- 所有硬门槛必须同时满足，平均分不能抵消安全、越权、假成功或证据缺失。
- 验收必须使用冻结测试集、固定合同版本和可复现环境。
- Go 决策至少由实施负责人和独立复核人签署。
- 只有一名实际人员时，成果只能标记为 PoC，不得进入生产 Shadow / Canary。
- 跨信任域越权、假成功、凭据泄露、受保护分支写入、不可追溯副作用均触发全局 No-Go。
- 失败样本、回退操作和证据全部保留，不得通过删除失败记录改善指标。
- 可以并行研发后续能力，但不得以未通过的前置阶段作为正式依赖。

### 19.2 阶段总览

| 阶段 | 周期 | 核心结果 | 最高可声明成熟度 |
|---|---:|---|---|
| Phase 0 | 3～4 周 | 架构、合同、基准、责任冻结 | 可实施设计 |
| Phase 1 | 4～6 周 | Runtime、安全边界、状态机和 Skill 供应链 PoC | 受控 PoC |
| Phase 2 | 6～9 周 | PUBLIC 单节点候选分支闭环 | 内部 MVP |
| Phase 3 | 6～8 周 | 多角色和有界 Repair 产生量化收益 | 增强型 MVP |
| Phase 4A | 8～12 周 | INTERNAL、MicroVM、专用节点与备份恢复 | 可恢复生产候选 |
| Phase 4B（可选） | 4～8 周 | 独立故障域、同步复制与真实 HA | 高可用生产候选 |
| Phase 4.5 | 7～10 周 | Shadow 与分档 Canary；时间和样本量取较晚满足者 | 有限生产可用 |
| Phase 5 | 持续 | 独立演进、评测、撤销和优化 | 持续运营 |

### 19.3 Phase 0：架构、合同与基线冻结

**目标**

把 v1.3 转化为可实现、可测试、可否决的合同集合，消除权威、信任边界和验收口径歧义。

**交付物**

- 本 v1.3 Markdown 主文档；
- Task、Run、Attempt、CandidateStagingOperation 状态机，以及 Lease、ExecutionPlan、Route、SourceBundle、三类执行镜像与 Compatibility、CommitIntent、CommitBundle、DeliveryAuthorization、Gate、Verdict、Artifact、Evidence、PolicyApproval、RolePack、SkillBundle 合同；
- ADR-01 至 ADR-34 的状态与责任人；
- SkillRoster 只读 Adapter 合同和能力清单；
- 信任域、网络、凭据、数据、外部副作用矩阵；
- 73 个正向 PUBLIC / 脱敏任务与不少于 40 个负向 / 对抗任务；
- 仓库骨架、CI、Schema 生成与兼容检查；
- Prime v0.9.1、SkillRoster、Linux、Git、PostgreSQL、MinIO/S3、模型服务的依赖锁定计划；
- 阶段预算、风险登记和 Go / No-Go 签署模板。

**要求实现的演示效果**

对一个样例 Task 进行不执行真实副作用的全链路走读：TaskSpec → SourceBundle / Runtime / Sandbox / Dependency / Compatibility 冻结 → Workflow / ExecutionPlan 编译 → 预冻结 Route → Role / Skill 绑定 → AttemptContract → 模拟执行 → CommitIntent / CommitBundle → Gate Supervisor 宿主事实 → Verdict → DeliveryAuthorization / CandidateStagingOperation → 候选分支提案 → DeliveryEvidenceBundle。评审人能指出每个状态、digest 和字段的唯一权威。

**Go 门槛**

- 所有持久状态均有唯一权威：100%；
- 所有跨信任域流量均记录调用方、接收方、认证、数据分类和失败策略：100%；
- 所有外部副作用均有幂等键、权限边界和审计事件：100%；
- AttemptContract 对 ExecutionPlan、plannedAttemptInput、Workflow、RolePack、SkillBundle 三摘要、SkillPolicy、Prompt、ToolPolicy、RoutingIntent / AttemptRoute、ModelPolicy、GatePack、SandboxProfile、Runtime / Sandbox / Dependency 的 Snapshot 与 OCI digest、ExecutionImageCompatibility、SourceBundle、预算和双 epoch 覆盖：100%；
- 73 个正向任务和不少于 40 个负向任务全部完成分类、脱敏和验收审查；第 15 天完成首批 20 个试制样本，第 30 天未全部冻结则 Phase 0 自动延期；
- 至少 20% 为未参与调优的保留集；
- 客观 Gate Oracle 在冻结环境重放一致率为 100%；主观标签由两名盲态标注者独立标注、报告 κ，分歧在冻结前由第三方裁决；
- P0 未决权威归属、未定成功语义和阻塞问题：0；
- 核心契约的 Schema 正反例与 DigestProfile / 签名固定测试向量覆盖：100%，Go 与独立参考实现逐字节一致；
- 依赖均有精确版本 / digest 或可替换 Adapter 合同。
- 基准建设投入不少于 1.5～2 FTE，且质量负责人独立于实现结果优化。

**No-Go**

- Attempt、Verdict、Skill 或 Artifact 存在两个可写权威；
- False Success 无法机器定义；
- SkillRoster 被设计为运行时能力；
- 基准包含未授权数据或不可验证成功条件；
- 只有一名复核人却声明为生产可用。

**回退**

停留在文档、Schema、模拟器和本地测试阶段；不启动真实模型、远程 Git 写入或正式沙箱执行。

### 19.4 Phase 1：Runtime、状态机与安全隔离 PoC

**周期与依赖**

4～6 周；Phase 0 全部硬门槛通过，Linux x86_64 安全实验节点可用。

**目标**

优先证明最危险、最难事后修复的假设：Runtime Driver 可控、状态机无假成功、Lease / Fencing 有效、沙箱隔离成立、SkillBundle 运行时不漂移。

**交付物**

- ScriptedRuntimeDriver，随后接固定 Prime / Pi 兼容基线；
- 最小 Lifecycle、Lease、双 epoch Fencing 和终态上报；
- 完全断网、只读工具和只读 SkillBundle 沙箱，以及 Domain 2 Gate Supervisor / Domain 7 Gate Command Process 的宿主捕获边界；
- Source Ingestor、SourceBundle、RuntimeImageSnapshot、SandboxImageSnapshot、预构建 DependencyImageSnapshot 与 Compatibility Snapshot 的最小闭环；
- SkillRoster 只读 scan/report Adapter；
- 来源、fingerprint、链接、审批、构建和撤销检查；
- 内容寻址 SkillBundle Builder 与 `SKILL-REPRO-001` 可重复构建证明；
- 故障注入器和 200 个状态 / 安全场景；
- 最小 Evidence Bundle 生成与校验工具。

**要求实现的演示效果**

同一任务依次演示正常完成、进程崩溃、重复终态、乱序事件、过期 Lease、完全断网、FD 继承、软链接逃逸、运行时修改 Skill、在线安装依赖、Bundle 摘要不匹配和未授权模型路线。平台必须准确完成、拒绝或回收，且不出现假成功。

**Go 门槛**

- Runtime Gate 与 Skill Supply-chain Gate 分别签署；两者都通过才可进入 Phase 2；
- v1.2 RLM-001～RLM-010 与 200 个脚本终态 / 安全场景：逐例 100% 符合预期；
- False Success：0/200；
- 合法 / 非法状态迁移规则测试：100%；
- 不少于 10,000 条重复乱序事件，重复外部副作用：0；
- 过期 Lease / epoch 提交：100/100 被拒绝；
- 网络、路径、链接、宿主凭据逃逸反例：100% 被拒绝；
- Attempt 前后 SkillBundle 字节 digest 一致，运行时漂移：0；
- `SKILL-REPRO-001`：至少 3 个独立干净环境、总计 100 次构建，bundleArtifactDigest 与 bundleManifestDigest / expectedMountedSkillTreeDigest：100/100 一致；
- 摘要不符、审批缺失、来源不在 allowlist：100% Fail Closed；
- 100 次 Runtime / Node 崩溃恢复，无双 Worker、无重复副作用、无旧 Lease 成功写入；
- 未授权 provider / model / thinking 组合：100% 拒绝；无法形成强终态证明时稳定输出 Run `NO_VERDICT`；
- Gate Critical 逐步骤直接执行、宿主 waitpid、stdout / stderr 流式哈希与“伪造通过文本”反例全部通过；
- SourceBundle / DependencyImage / CommitBundle 的摘要链完整率：100%；
- 每个终态的合同、Route、日志、Artifact 与 digest 完整率：100%；
- Native Pi Driver 可行性与切换成本报告完成；不要求 Phase 1 实现切换；
- 未处置 Critical / High 安全问题：0。

**No-Go**

任一假成功、沙箱逃逸、凭据暴露、过期 Lease 成功写入、运行时 Skill 修改、Bundle 不可重现或终态证据缺失。

**回退**

有问题的 Runtime Driver 可退回 Scripted Provider。SkillRoster Adapter 可被另一个满足同一只读合同的 Adapter 替代；若没有任何 Adapter 通过 Skill Supply-chain Gate，只能继续 Runtime 子轨 PoC，不能进入 Phase 2。PoC 不接真实仓库，保留全部失败证据。

### 19.5 Phase 2：PUBLIC 单节点纵向 MVP

**周期与依赖**

6～9 周；Phase 1 通过，PostgreSQL、MinIO/S3、一个 Git Provider、Model Gateway 和单节点环境就绪。

**目标**

完成首个真正可用的端到端闭环：提交脱敏代码任务，平台在单节点完成编排、实现、验证和候选分支交付，不自动合并或部署。

**交付物**

- Task API、Registry、Workflow Compiler、Orchestrator、Lifecycle；
- PostgreSQL 权威状态和内容寻址 Artifact Store；
- Repository Source Ingestor、签名 SourceBundle 与预构建 DependencyImageSnapshot；
- Node Agent、Sandbox Supervisor、PrimeRuntimeDriver、Model Gateway；
- CommitBundle、Deterministic Gate、Attestation / Verdict、PreDeliveryEvidence 与 DeliveryEvidenceBundle；
- 一个 Git Provider Adapter 与候选分支权限模型；
- RolePack / SkillBundle 发布、绑定和撤销；
- OpenTelemetry、预算、告警、Reconciler 与 Runbook；
- 73 个正向任务每任务 3 次，以及负向 / 对抗集的正式验收报告。

**要求实现的演示效果**

从 API 提交一个 PUBLIC 代码任务，在无执行期人工操作的情况下完成：

~~~text
提交
→ 编译
→ 调度
→ 沙箱实现
→ 独立 Gate
→ Verdict
→ 候选分支
→ 可审计 Evidence Bundle
~~~

最终只产生候选分支和评审材料，不写受保护分支、不自动合并、不部署。

**Go 门槛**

- 73 个正向任务 × 3 次，共 219 次正式运行；负向 / 对抗集逐例至少运行 1 次；
- PositiveTaskE2ESR ≥ 70%，任一正向任务类别 ≥ 50%；同时报告运行级比例和类别宏平均；
- ExpectedOutcomeMatch ≥ 95%；UnsafeAcceptanceRate = 0/N；
- Evidence Completeness：100%；
- False Success：0；
- Role、Skill、Prompt、Image、Model Policy、Gate Pack digest 匹配：100%；
- 默认 / 受保护分支与生产环境的未授权写入：0；
- Git 操作全部使用任务专属候选分支和最小权限短凭据：100%；
- Infrastructure Failure ≤ 2%；
- 控制面 API p95 < 500 ms，不含模型时间；
- 调度到沙箱启动 p95 < 30 秒；
- 进程退出后 99% 在 60 秒内收敛，其余在 2 个 Lease TTL 内回收；
- 至少 30 个控制面、节点和网络故障注入场景，无重复副作用、错误成功或证据丢失；
- 额定并发 C 下持续 8 小时，无状态损坏、死锁、越权或不可回收 Attempt；
- 正常负载连续 7 天耐久运行，孤儿进程、孤儿 Lease 和重复外部副作用均为 0；
- 预算超限阻断和 Route Attestation 覆盖：100%；
- Bundle 撤销后新 Attempt 使用该 Bundle：0。

**No-Go**

- 任一 False Success、凭据泄露、越权 Git 写入或不可校验证据；
- 219 次正向验收 PositiveTaskE2ESR < 70%，两轮限定整改后仍未达标；
- 任一负例被错误接受并产生 candidate ref；
- 单节点故障被宣传为高可用；
- Bundle 撤销后新 Attempt 仍能使用。

**回退**

关闭新任务准入，排空或终止在途 Attempt，撤销当前版本并恢复上一已验收快照；问题候选分支隔离标记，不自动删除。

---

### 19.6 Phase 3：多角色与有限修复闭环

**周期与依赖**

6～8 周；Phase 2 稳定通过，并积累真实失败分类和人工复核样本。

**目标**

引入 Implementer Agent、Reviewer Agent、Semantic Judge Agent 的职责分离和最多两轮 Repair，提高成功率，但不把成功权交给模型。确定性 Gate Supervisor、Gate Command Process 与 Verdict Aggregator 不是 Agent 角色，也不加载 RolePack / SkillBundle。

**交付物**

- 多角色 Workflow、fan-out / fan-in 和独立 RolePack / SkillBundle；
- Reviewer 与 Semantic Judge 的结构化证据合同；
- 不调用模型的 Verdict Aggregator；
- 最多两轮、受时间 / Token / 成本约束的 Repair Run；
- 角色隔离、自审禁止、Attempt 血缘与修复因果链；
- 单角色 / 多角色对照报告。

**要求实现的演示效果**

Implementer 首轮失败后，Reviewer 基于只读 Artifact 给出结构化问题；平台创建新的 Repair Run 及其 Attempt；独立 Gate Supervisor 在新 Domain 7 命令沙箱中对新 CommitBundle 重跑确定性 Gate；Verdict Aggregator 根据冻结规则结束任务。所有角色输入、路线、预算和因果链可回放。

**Go 门槛**

- 公平 A/B 使用相同 benchmark digest、相同总 Token / 费用预算、相同 EvaluationPlan / Gate / Sandbox / SourceBundle / 重试；单 Agent 组使用多 Agent 中最强路线；
- 每组每个 73 正向任务至少 3 次，共 438 次；Gate 盲态，人工复核盲评；报告点估计、95% 置信区间和预注册显著性检验；
- 同一冻结基准的 SuccessWithin2Repairs ≥ 80%；
- 相对公平单 Agent 基线成功率提高至少 10 个百分点，或逃逸缺陷降低至少 50%；未满足时只能以预批准的业务价值证据决定是否保留；
- 预标注可修复的首轮失败，至少 70% 在两轮 Repair 内修复；
- 首次执行成功率相对公平单 Agent 基线回归 ≤ 3 个百分点；
- 修复轮数、Token、时间和成本受合同上限约束：100%；
- Reviewer / Semantic Judge 与双人人工复核的样本量先按基线率、配对相关性、α 与 80% 功效计算；不得以固定 100 例自动宣称充分；Cohen’s κ ≥ 0.70；
- 人工植入严重安全问题被错误判安全：0；
- Verdict 对同一证据包重放 1,000 次，结果一致率：100%；
- 自审、共享可写目录、跨 Attempt 污染和越权工具调用：0；
- Accepted Task 中位成本 ≤ Phase 2 的 1.5 倍；
- 额定并发 C 连续 8 小时，无死锁、孤儿子任务或重复副作用。

**No-Go**

- 模型 Reviewer / Semantic Judge 能绕过确定性 Gate；
- 多角色成功率下降超过 3 个百分点；
- 成本增长超过 50%，且成功率未提高 10 个百分点、逃逸缺陷也未降低 50%；
- Repair 不能被预算和状态机可靠终止；
- 任一角色获得 Role Pack 之外的 Skill 或工具。

**回退**

通过版本化 Workflow 开关禁用多角色和 Repair，恢复 Phase 2 单 Implementer 路径，保留对照证据。

### 19.7 Phase 4A：安全加固、INTERNAL 与可恢复模式

**周期与依赖**

8～12 周；Phase 3 通过，身份、KMS / Secret Broker、数据治理和安全复核资源到位。不要求独立故障域，因此本阶段不得宣称 HA。

**目标**

处理经批准 INTERNAL 数据，并证明控制面、权威数据和制品层能够从备份安全恢复。

**交付物**

- SSO / RBAC、短凭据、KMS / Secret Broker、Break-glass 审计；
- INTERNAL 数据分类、留存、脱敏、删除与导出政策；
- **MicroVM + 专用节点强制策略**，INTERNAL 不与其他数据等级或租户共置；
- 签名 SourceBundle / DependencyImage / 运行制品、SBOM、来源证明、漏洞和许可扫描；
- PostgreSQL、Artifact、控制面和 Gateway 的备份恢复方案；
- Kill Switch、安全事件和 PUBLIC-only 回退 Runbook；
- PUBLIC 与 INTERNAL 双基准报告。

**要求实现的演示效果**

运行一个获批 INTERNAL 测试任务，证明其只在专用节点 MicroVM 中执行且不暴露原始数据与凭据；随后丢失整个测试环境，从受控备份恢复到可对账状态，不产生错误成功或重复副作用。

**Go 门槛**

- 未关闭 Critical / High 安全问题：0；
- INTERNAL Attempt / Gate 使用 MicroVM + 专用节点：100%；
- 发布制品签名、SBOM、来源证明和 digest 覆盖：100%；
- 1,000 个合成敏感数据 / DLP 测试，越权进入 Prompt、日志、制品或遥测：0；
- INTERNAL 访问的主体、目的、任务、字段分类和审批引用：100%；
- 生产权限变化和 Break-glass 的 Four-Eyes 或强制事后复核：100%；
- 备份恢复连续 3 次成功；已确认元数据与制品的恢复 RPO ≤ 15 分钟，完整环境恢复 RTO ≤ 60 分钟；
- 72 小时额定容量稳定性测试，Infrastructure Failure < 1%；
- PUBLIC PositiveTaskE2ESR 相对 Phase 3 回归 ≤ 3 个百分点；
- INTERNAL 正向基准至少 100 个任务 × 3 次，PositiveTaskE2ESR ≥ 70%，任一类别 ≥ 50%，对应负例 UnsafeAcceptanceRate = 0/N。

**No-Go 与回退**

数据治理未签署、任一 INTERNAL 执行未使用 MicroVM / 专用节点、任一敏感数据泄露、恢复目标不达标或恢复产生假成功时 No-Go。回退为关闭 INTERNAL 准入并恢复 PUBLIC-only。

### 19.8 Phase 4B（可选）：独立故障域与真实 HA

**周期与依赖**

4～8 周；Phase 4A 通过，并且至少两个真实独立故障域、同步数据库复制和跨域对象存储可用。

**目标与交付物**

建立 PostgreSQL 同步提交、跨域 Artifact、无状态控制面 N+1、Gateway 故障转移和 controlPlaneEpoch 提升流程。没有这些物理条件就跳过本阶段，并持续声明“可恢复、非 HA”。

**Go 门槛**

- 至少 10 类 N-1 故障演练，无错误成功、双 Worker、重复提交或已确认制品丢失；
- 已提交权威元数据 RPO = 0，控制面服务 RTO ≤ 5 分钟；
- Git / Artifact 不确定副作用均进入 RECONCILING 并由读回收敛；
- 数据库、Artifact 和控制面实例跨独立故障域；同一物理主机多容器不计 HA；
- 连续 7 天故障域级耐久演练满足 SLO。

任一双主、epoch 回退、确认数据丢失或假成功即 No-Go，退回 Phase 4A 可恢复模式并撤销 HA 声明。

### 19.9 Phase 4.5：Shadow 与 Canary

**周期与依赖**

7～10 周；Phase 4A 全部硬门槛通过，真实任务流、对照组、值守和一键回退可用。只有业务明确要求 HA 时才依赖 Phase 4B。Shadow 可在 Phase 4A 后四周提前累计，但只能在安全边界已冻结且外部写入为 0 时计算；否则顺序执行。每个门槛均以“最短时间与最小样本量较晚满足者”为准。

**目标**

在不扩大外部副作用的前提下，用真实工作负载证明可运营性，再逐步开放有限候选分支。自动合并和生产部署仍禁止。

**交付物**

- Shadow 流量复制与结果对照；
- 5% → 10% → 25% Canary；
- Kill Switch、自动降级、版本撤销和回退演练；
- 值班、告警、事故复盘模板；
- 人工评审工作台和候选分支审查流程。
- 直接采纳率、修改后采纳率、人工复核时长和修改量的业务价值报告，至少覆盖 3 个试点仓库。

**要求实现的演示效果**

同一真实任务由现有流程与 Pi 平台同时处理。Shadow 结果不写 Git；Canary 仅允许隔离候选分支。触发故障后，2 分钟内停止新准入、5 分钟内恢复上一稳定版本。

**Go 门槛**

- Shadow 持续至少 4 周且至少 500 个真实任务，以较晚满足者为准；
- Shadow 外部写入和用户可见副作用：0；
- 5%、10%、25% 每档至少 7 天且至少 100 个任务；
- 每档 Critical 事故、边界突破、假成功、泄露和受保护分支写入：0；
- 相对对照组 PositiveTaskE2ESR 的非劣界限预设为 −3 个百分点；样本量须先按基线率、配对 / 仓库聚类、α 与 80% 功效计算，100 个任务只作为最低资格而非充分性证明；
- p95 非模型平台延迟增加 ≤ 20%；
- Accepted Task 中位成本增加 ≤ 20%，除非收益阈值事先批准；
- 告警到停止新任务 ≤ 2 分钟；
- 完整版本回退 ≤ 5 分钟；
- 3 次人工 + 3 次自动 Kill Switch / 回退演练：100% 成功；
- 候选分支人工复核：100%；
- 至少 3 个试点仓库分别报告直接采纳率、修改后采纳率、人工复核时长和修改量；
- 自动合并和自动部署：0。

**No-Go**

任一档出现全局停止事件即退回上一档或 Shadow；同类问题重复两次则退出 Canary，返回对应研发阶段。延长观察不能替代修复。

### 19.10 Phase 5：持续优化

**周期**

长期持续；按月评估、按季度安全与权限复核。

**目标**

在不削弱安全和可审计性的前提下，独立优化模型、Prompt、Workflow、Role Pack、SkillBundle、Gate Pack 和资源效率。

**交付物**

- 版本化实验、离线评测、Shadow、Canary 和自动回退；
- 模型、Prompt、Role、Skill、Gate、Image 的独立快照和撤销；
- Skill 使用证据、上下文成本、有效性与陈旧度报告；
- 月度质量 / 成本报告；
- 季度威胁模型、权限与恢复能力复核；
- 事故样本永久进入回归集。

**每项变更的 Go 门槛**

- 假设、对照、影响指标和回退版本：100%；
- 保留集成功率的 95% 置信区间非劣界限：−3 个百分点；实验前按基线率、配对 / 聚类结构、α 与 80% 功效计算样本量；
- False Success、边界突破和未授权副作用：0；
- Evidence Completeness 和 digest 可验证率：100%；
- p95 平台延迟与 Accepted Task 成本恶化 ≤ 10%，除非有预先批准的量化收益；
- Skill 裁剪只有在同 Role 累计暴露至少 30 天且至少 100 个合格 Attempt 后才有候选资格；是否足以证明非劣仍以功效分析为准；
- 裁剪后上下文 Token 至少下降 10%，PositiveTaskE2ESR 回归 ≤ 2 个百分点；
- 新模型、Prompt、RolePack、SkillBundle、GatePack 或 Image 不捆绑发布；
- 每季度完成权限、依赖、威胁模型、恢复能力和基准污染复核。

**No-Go 与回退**

无对照、样本不足、证据不完整、质量越过非劣界限或任何安全硬失败时不发布。回退只撤销单一变更快照，不运行时修改 Skill，不使用 force 绕过门禁。

---

## 20. 每阶段完成定义

| 阶段 | 必须能证明 | 不能宣称 |
|---|---|---|
| Phase 0 | 规格、权威、合同和门槛无歧义 | 平台已经可运行 |
| Phase 1 | 最危险协议与安全假设在 PoC 成立 | 能处理真实业务 |
| Phase 2 | 单节点 PUBLIC 任务可形成候选分支 | 高可用、INTERNAL、自动合并 |
| Phase 3 | 多角色 / Repair 有统计收益 | 模型可以决定成功 |
| Phase 4A | INTERNAL、MicroVM、专用节点与备份恢复经过实测 | 高可用 |
| Phase 4B | 独立故障域、同步复制与 N-1 经过实测 | 跨区域容灾或自动合并 |
| Phase 4.5 | 真实流量有限可运营 | 无边界自治或无人责任 |
| Phase 5 | 每项变化可独立评测和撤销 | 自动自我改写平台 |

最终完成标准不是“Agent 能写代码”，而是：

- 平台稳定产生可审查候选分支；
- 每个成功都有确定性 Gate 和完整证据；
- Runtime 不能改变 Skill、权限或成功标准；
- 模型、Prompt、Role、Skill、Gate、Image 能独立识别、灰度、撤销和复盘；
- 故障、攻击或模型错误出现时，系统安全失败而非错误成功。

---

## 21. 工作包、90 天启动计划与责任

### 21.1 工作包

| 工作包 | 主要输出 | 前置 | 主要责任 |
|---|---|---|---|
| WP-01 Architecture & ADR | 规范、威胁模型、ADR、权威矩阵 | 无 | 架构负责人 |
| WP-02 Contracts | OpenAPI、Proto、JSON Schema、兼容测试 | WP-01 | 控制面负责人 |
| WP-03 Lifecycle | 状态机、Lease、Fencing、Outbox / Inbox | WP-02 | 控制面负责人 |
| WP-04 Runtime | Scripted Driver、Prime Driver、终态屏障 | WP-02 | Runtime 负责人 |
| WP-05 Node & Sandbox | Node Agent、隔离、资源、进程树 | WP-02 | Runtime / 安全 |
| WP-06 Skill Supply Chain | Intake、Adapter、评估、审批、Bundle | WP-01/02 | 安全 / 平台 |
| WP-07 Source & Dependency | SourceBundle、DependencyImage、SBOM / SCA / provenance | WP-01/02 | 安全 / 平台 |
| WP-08 Gateway & Budget | Node 代转模型、Attestation、限额、结算 | WP-02/03 | 平台负责人 |
| WP-09 Artifact & Evidence | CAS、Manifest、签名、WORM、GC | WP-02/03 | 平台负责人 |
| WP-10 Verification | CommitBundle、Gate、Attestation、Verdict | WP-02/09 | 质量负责人 |
| WP-11 Git Delivery | DeliveryAuthorization、Candidate、CAS、读回、Reconciler | WP-03/09/10 | 集成负责人 |
| WP-12 Benchmark | 73 正向任务、负向集、保留集、统计与报告 | WP-01 | 独立评测负责人 |
| WP-13 Operations | 部署、监控、告警、Runbook、演练 | WP-03～11 | SRE |

### 21.2 第 0～30 天

- 冻结 v1.3、P0 ADR 和范围；
- 创建单仓骨架、CI、Schema / lint / test 门禁；
- 实现 Task / Run / Attempt / Skill 状态机的可执行模型；
- 定义 15 个核心 Schema 和错误码；
- 第 15 天完成首批 20 个试制样本；第 30 天前冻结全部 73 个正向任务和不少于 40 个负向 / 对抗任务，否则 Phase 0 自动延期；
- 建立 Scripted Provider 场景语言；
- 完成 SkillRoster Adapter 的能力协商和只读 JSON Schema；
- 完成威胁模型、网络、凭据与数据矩阵；
- 冻结 Phase 1 节点、容器、签名和 Artifact 实验环境。

退出目标：Phase 0 Go 决策。

### 21.3 第 31～60 天

- 实现 Lifecycle / Lease / Fencing / Outbox / Inbox；
- 实现 ScriptedRuntimeDriver 和 200 场景框架；
- 实现最小 Node Agent、完全断网沙箱和宿主侧 Runtime Proxy；
- 实现 Artifact staging / publish 与 Evidence Skeleton；
- 实现 SourceBundle / DependencyImageSnapshot / CommitBundle 骨架；
- 打通 Skill 来源冻结、结构扫描、独立评估与 Bundle 构建；
- 验证 Bundle 可重复构建和 1 Byte 漂移拒绝；
- 注入重复、乱序、崩溃、超时、撤销和网络逃逸；
- 开始 Prime v0.9.1 Driver 兼容层，不影响脚本路径。

退出目标：Phase 1 主要风险已有可执行证据。

### 21.4 第 61～90 天

- 关闭 Phase 1 的 200 场景、安全和证据门槛；
- 实现 Task API、Workflow Compiler 和最小 Registry；
- 接入真实 PostgreSQL / MinIO；
- 接入冻结模型路线和 Gateway 用量；
- 接入一个隔离 Git Provider 测试仓库；
- 完成 Task → Attempt → Artifact → Gate → Verdict → 本地候选 ref 的第一条纵向切片；
- 扩充基准集并开始 219 次正式运行前的 dry-run；
- 形成 Phase 2 剩余差距、容量 C 和风险报告。

90 天目标是“Phase 1 关闭并形成 Phase 2 纵向切片”，不是承诺完整 Phase 2 已经验收。

### 21.5 建议人员

上述 13～19 周估算以 **至少 5 名核心 FTE + 独立评测 / 安全投入** 为前提，并确保以下职责实际有人承担：

- 架构 / 控制面；
- Runtime / Node；
- 沙箱 / Skill 供应链安全；
- Verification / Benchmark；
- SRE / Artifact / Git 集成。

阶段 0～2 的基准建设需要 1.5～2 FTE 峰值投入并保持独立签署；SRE 在 Phase 0～1 可为 0.5 人，Phase 2 起应全职。Builder、功能 Approver、安全 Approver、Signer 必须是满足职责分离的身份；缺少安全或红队能力时必须引入外部评审。

3 人路线只能完成缩减 PoC，不能按本表周期交付 Phase 2，也不能形成生产 PublicationPointer。Phase 4A 及以后建议 6～8 人，并具备独立安全、数据治理、SRE 和值守能力。单人路线只适合原型，不作生产试点承诺。

### 21.6 RACI 最小矩阵

| 决策 / 动作 | A 最终负责 | R 执行 | C 必须咨询 | I 知会 |
|---|---|---|---|---|
| Task 准入政策 | 产品 / 平台 Owner | 架构负责人 | 安全、质量 | 使用方 |
| 状态与合同 | 架构负责人 | 控制面工程师 | Runtime、SRE | 全组 |
| Skill 功能审批 | Role Owner | 治理人员 | 安全 | 平台 Owner |
| Skill 安全审批 | 安全负责人 | 安全评审人 | Role Owner、法务 | 平台 Owner |
| Bundle 签名发布 | 平台 Owner | Publisher | 两名 Approver | SRE |
| Gate 与基准 | 质量负责人 | 评测工程师 | 产品、安全 | 全组 |
| Candidate 写入 | 平台 Owner | Git Stager | 仓库 Owner | 质量 |
| Go / No-Go | 平台 Owner | 阶段负责人 | 安全、质量、SRE | 利益相关方 |
| Global Stop | 值班负责人 | SRE / 控制面 | 安全 | 全员 |

---

## 22. ADR 路线

### 22.1 v1.2 ADR 的处理

规范基线固定为 `Pi多Agent无人自治开发平台_架构闭环版_v1.2.md`，SHA-256 为 `8621b9b2f016d4e5779f99d5fcd0e2474b63489fd9d0d852c057938151309a41`。若本文件与该精确版本冲突，除非 v1.3 新 ADR 明确写明“取代的 v1.2 ADR / 条款、理由、风险接受人和迁移”，否则以 v1.2 更严格的约束为准。

| v1.2 规范 | v1.3 继承落点 | 状态 |
|---|---|---|
| §5.2 十个信任域 | 本文 §4，编号 1～10 原样保留 | Normative |
| §6.1.1 Four-Eyes | 本文 §7.7、§10.3 | Normative |
| §7.1～7.4 三层状态机与字段级写入权 | 本文 §5、§8 | Normative |
| §8 Lease / Fencing / epoch | 本文 §6.5、§13.3 | Normative |
| §9 不可变契约与 §10 路由独立性 | 本文 §9～§11 | Normative |
| §11 Runtime、RLM 与终态屏障 | 本文 §6.7、Phase 1 | Normative |
| §13.2.1 完全断网、预构建依赖镜像 | 本文 §3.4、§6.14、§10.9 | Normative |
| §14 Git / Artifact / Verification | 本文 §3.5、§6.9～6.11、§10.10 | Normative |
| §15.2 Attempt / Repair Run 边界 | 本文 §14.3 | Normative |
| §17～18 基准与阶段 Gate | 本文 §17、§19 | Normative；阈值降低必须新 ADR |
| ADR-01～24 | 本节映射及 v1.2 §28 原文 | 全部保留 |

Phase 0 不应重写这些 ADR，而应：

- 核对状态与实现是否仍一致；
- 把 Pending 项落实为责任人、验证方法和日期；
- 对被 v1.3 新 Skill 模型影响的合同发补充决议；
- 不允许用“实现方便”隐式推翻已批准边界。

### 22.2 v1.3 新增 ADR

| ADR | 决策主题 | 优先级 | Phase 0 退出状态 |
|---|---|---:|---|
| ADR-25 | SkillRoster 生态位与运行时 Skill 不变性 | P0 | Accepted |
| ADR-26 | SkillPackage 格式、来源冻结与安全门禁 | P0 | Accepted |
| ADR-27 | Skill Registry、Artifact 与 SkillBundle 权威 | P0 | Accepted |
| ADR-28 | Four-Eyes、签名、发布、Canary 与回退 | P0 | Accepted |
| ADR-29 | Skill 能力声明与 Role / Sandbox 最小权限编译 | P0 | Accepted |
| ADR-30 | Skill 使用证据、隐私与裁剪判断 | P0 | Accepted |
| ADR-31 | Skill 撤销、污染扩散与重建恢复 | P0 | Accepted |
| ADR-32 | Skill 许可、Notice 与第三方责任 | P1 | Proposed 或 Accepted |
| ADR-33 | SourceBundle 与 DependencyImage 冻结供应链 | P0 | Accepted |
| ADR-34 | CommitBundle、独立 DeliveryAuthorization 与 Git Staging | P0 | Accepted |

### ADR-25｜SkillRoster 生态位与运行时 Skill 不变性

决策：只作为离线、可替换、只读优先的治理 Adapter；v1.3 仅允许 Scan / Report JSON；Runtime、Attempt、Gate 镜像不包含 SkillRoster；Agent 只能提出 MissingCapabilityProposal。

验收：镜像 SBOM / 文件清单无 SkillRoster；Attempt 到治理域访问全部拒绝；100 次运行时 Skill 改写反例全部失败。

### ADR-26｜SkillPackage、来源冻结与门禁

决策：平台拥有独立 PackageVersion；MVP 拒绝链接、二进制、安装器、MCP、自动更新和外部依赖；SkillRoster 证据与 Secret / SCA / 恶意代码 / 许可 / 语义审查分离；扫描与打包基于同一快照。

验收：路径、链接、碰撞、压缩炸弹、浮动来源反例全部阻止；`SKILL-REPRO-001` 在至少 3 个独立干净环境总计 100 次构建摘要 100/100 一致；1 Byte 漂移使旧审批失效。

### ADR-27｜Skill 权威模型

决策：PostgreSQL Registry 是状态权威，Artifact Store 是字节载体；SkillBundle 独立于 Role Pack 和 Sandbox Image；运行链合同强制携带 skillBundleSnapshotDigest、bundleArtifactDigest 与 expectedMountedSkillTreeDigest，三者语义不得混用。

验收：任何缺失 / 不一致 Fail Closed；任一 Attempt 可反查 Package、审批、签名和来源；Registry / Store 不一致不启动。

### ADR-28｜Four-Eyes、签名和发布

决策：功能与安全两类 ApprovalDecision 身份不同且均不同于 proposalActor；Builder、两类 Approver、Signer 分离；签名绑定精确摘要、policyDigest 和 ApprovalSet；Bundle Snapshot 不可变，按环境 PublicationPointer 独立 Canary、撤销、回退；单人环境仅 PoC。

验收：自批、过期审批、摘要变化、未知 key、Role 不匹配均拒绝；篡改签名测试全部失败；回退只选择历史批准摘要。

### ADR-29｜Skill 能力与最小权限

决策：Skill 声明 Role、任务类、工具、脚本、数据和网络；requiredCapabilities 必须是 Role / Sandbox 能力交集的子集；v1.3 不支持运行时 On-demand。

验收：请求额外网络、Git 或凭据权限的 Skill 编译失败；不同 Role 无非必要 Skill 泄漏；裁剪后安全隐藏反例仍全部通过。

### ADR-30｜使用证据和隐私

决策：生产数据源为平台 Trace，不解析原始 Session；区分 Exposed、Matched、Loaded、Invoked、Outcome-Linked；缺失观察不等于无用；安全 Skill 降级须离线对照和 Four-Eyes。

验收：Registry 不保存原始 Prompt / Response；使用结论携带 coverage、source、quality 与 trace；Agent 自报不能改变统计。

### ADR-31｜撤销与污染恢复

决策：撤销使用 Overlay 和单调 epoch；可按 Package、Bundle、Role、来源、签名 key、policyDigest 撤销；命中后停止 Attempt、撤销能力、隔离制品和候选 ref；恢复必须新 digest、新审批、新 Canary。

验收：新 Attempt 同步拒绝；活动 Attempt 在 60 秒内停止外部能力；反向追踪 100% 完整；同一污染输入不自动重调度。

### ADR-32｜许可与第三方责任

决策：Skill、脚本、模板和引用资源分别记录 SPDX、来源、权属与 Notice；未知或冲突许可阻止发布；SkillRoster 自身许可不覆盖其扫描对象；对外分发前完成人工 / 法务确认。

验收：PRODUCTION PublicationPointer 所指 Package / Bundle 100% 有许可结论或内部专有记录；Notice 缺失、禁止许可和来源不明反例全部阻止。

### ADR-33｜SourceBundle 与 DependencyImage 冻结供应链

决策：Attempt 不解析浮动 Git ref、不访问远程仓库、不在线安装依赖。Source Ingestor 在沙箱外以只读权限固定 GitObjectId / submodule / LFS 并发布 SourceBundle；Dependency Builder 只在受控 MicroVM 构建未签名 Snapshot 与原始扫描证据，独立 Evaluator / Approver 判定，Dependency Image Signer 只签已批准精确 payloadDigest，Publisher 最后发布引用。Attempt 与 Gate 离线只读挂载。

验收：ref / submodule / LFS 漂移、来源越界和在线安装全部拒绝；SourceBundle、Attempt、CommitBundle、Gate、Git Staging 的 sourceBundleDigest 100% 一致；DependencyImage 100% 有 SBOM / SCA / provenance。

### ADR-34｜CommitBundle 与独立 Git Delivery 授权

决策：Verification Plane 预计算不可变 tree / commit GitObjectId，Gate 验证精确 CommitBundle。Git Stager 不执行仓库代码，不重放补丁，不复用 Attempt Lease；只凭独立 GitStagingLease / DeliveryAuthorization 上传已验证对象并 CAS candidate ref。

验收：Gate 对象与 Git 读回 GitObjectId 100% 一致；确认丢失可用 Operation Key trailer 对账；Git Stager 镜像无构建器 / 解释器且执行审计中仓库代码为 0。

---

## 23. 风险登记

| 风险 | 概率 / 影响 | 早期信号 | 应对 | Owner |
|---|---|---|---|---|
| Prime / Pi 上游接口变化 | 中 / 高 | Driver 合同测试失败 | 固定版本、Adapter、Scripted Driver、切换触发器 | Runtime |
| 假成功未被基准发现 | 中 / 极高 | 人工复核与 Verdict 分歧 | 反例、保留集、事故样本永久化 | Quality |
| 容器隔离不足 | 中 / 极高 | 逃逸测试 / 审计告警 | PUBLIC Fail Closed、外部红队；INTERNAL 强制 MicroVM + 专用节点 | Security |
| Source / 依赖输入漂移 | 中 / 极高 | ref、submodule、LFS、镜像摘要不一致 | SourceBundle、DependencyImage、离线只读挂载 | Security |
| Gate 与 Git 对象时序循环 | 低 / 极高 | Gate digest 与 Git 读回不同 | CommitBundle 预计算、Stager 禁止重建 / 执行 | Quality |
| Skill 供应链污染 | 中 / 极高 | digest / 来源 / 行为异常 | 冻结来源、分权、撤销、爆炸半径 | Security |
| 模型代理路线漂移 | 中 / 高 | routeAttestation 不匹配 | 实时目录验证、禁止透明 fallback | Gateway |
| 单节点故障 | 高 / 中 | 主机 / 磁盘告警 | 清晰非 HA 声明、备份、可恢复 Runbook | SRE |
| 基准污染 | 中 / 高 | 保留集异常提升 | 隐藏保留集、版本锁、评测独立 | Quality |
| Four-Eyes 人力不足 | 中 / 高 | 同人提交与审批 | 只标 PoC、补充独立人员 | Owner |
| 成本乘法放大 | 中 / 高 | Repair / Retry 激增 | 分层预算、硬上限、熔断 | Budget |
| Git CAS 能力不足 | 低～中 / 高 | ref 读回不可靠 | Phase 0 实测，不满足则换 Provider / 模式 | Integration |
| 制品 / DB 不一致 | 中 / 高 | Reconciler 差异 | 两步发布、Outbox、Quarantine | Platform |
| 许可权属不清 | 中 / 高 | SPDX / Notice 缺失 | 阻止发布、法务复核 | Governance |
| 需求蔓延至自动合并 | 高 / 极高 | 提前请求生产权限 | 策略硬禁、单独未来 ADR | Owner |
| 告警过载 | 中 / 中 | Critical 被忽略 | 分级、演练、SLO 调优 | SRE |

---

## 24. 成本与价值核算

在没有团队单价、模型费率、任务量和目标硬件前，不给出伪精确金额。Phase 0 建立以下模型：

~~~text
建设成本
= 人月 × 全成本单价
+ 基础设施与测试环境
+ 安全 / 红队 / 法务
+ 基准标注与复核
+ 预备金

月度运营成本
= 控制面与执行计算
+ 模型调用
+ Artifact / 日志 / 备份
+ 可观测性
+ 值班与安全复核
+ Git / 身份 / KMS

单位 Accepted Task 成本
= 同期全部平台可变成本 / Accepted Task 数
~~~

收益至少同时衡量：

- 候选变更的人工工时节省；
- Lead Time 变化；
- PositiveTaskE2ESR 与 ExpectedOutcomeMatch；
- 人工退回率；
- 假成功与事故成本；
- 平台维护和评审成本。

只有在 Shadow / Canary 有真实对照后才计算 Break-even。模型 Token 下降但人工复核或事故成本上升，不算优化成功。

---

## 25. 验收追踪矩阵

| 需求 | 首次证明阶段 | 验证方法 | 必须证据 |
|---|---|---|---|
| Runtime 不能修改 Skill | Phase 1 | 100 次改写 / 安装反例 | mount、syscall、SBOM、终止记录 |
| SkillRoster 不进入运行时 | Phase 1 | 镜像文件与 SBOM 扫描 | imageDigest、scan report |
| Bundle 可重复 | Phase 1 | 3 环境构建 + 100 次重复 | manifest、digest、builder image |
| 过期 Lease 无副作用 | Phase 1 | 100 次旧 epoch 请求 | FENCING_REJECTED |
| 无假成功 | Phase 1 / 2 | 确定性反例逐例通过 + 219 正向 E2E + 负向集 | Gate、Verdict、人工核对 |
| 冻结源码与依赖 | Phase 1 / 2 | ref / submodule / LFS / 在线安装反例 | SourceBundle、DependencyImage、SBOM / provenance |
| Gate 与 Git 同一对象 | Phase 2 | CommitBundle 到 Git 读回比对 | tree / commit GitObjectId、Operation Key |
| 路线不漂移 | Phase 2 | 每调用实际身份核验 | Route Attestation |
| Evidence 完整 | Phase 2 | Schema + 引用遍历 | Evidence Bundle |
| 只写候选分支 | Phase 2 | Git 权限与反例 | token scope、ref 读回 |
| 撤销阻断新运行 | Phase 2 | 100 次撤销注入 | epoch、Node / Gateway 拒绝 |
| 多角色带来净收益 | Phase 3 | 438 次公平盲态 A/B | 成功率、逃逸缺陷、成本、时延、显著性 |
| INTERNAL 不泄露 | Phase 4A | MicroVM / 专用节点 + 1,000 个 DLP 样本 | Admission、DLP / Trace / Artifact 证明 |
| 可恢复 | Phase 4A | 3 次完整备份恢复 | RPO / RTO 演练报告 |
| 真正 HA | Phase 4B（可选） | 独立故障域 N-1 | RPO=0 / RTO / epoch 演练报告 |
| 真实负载可运营 | Phase 4.5 | Shadow + 5/10/25% Canary | 对照、告警、回退记录 |

---

## 26. 数据库逻辑表与迁移顺序

### 26.1 最小表集合

**任务与执行**

~~~text
tasks
task_specs
routing_intent_snapshots
attempt_route_snapshots
route_contract_binding_attestations
evaluation_plan_snapshots
tool_policy_snapshots
runs
attempts
attempt_contracts
attempt_terminal_envelopes
leases
execution_assignments
resource_execution_epochs
control_plane_epochs
budget_reservations
budget_ledger_entries
model_invocations
session_checkpoints
~~~

**制品、验证与 Git**

~~~text
artifact_manifests
artifact_references
source_bundles
source_artifact_manifests
execution_plan_snapshots
dependency_image_snapshots
dependency_image_approvals
dependency_image_signatures
dependency_image_publications
execution_image_builds
runtime_image_snapshots
sandbox_image_snapshots
execution_image_signatures
execution_image_publications
execution_image_compatibility_candidates
execution_image_compatibility_snapshots
commit_bundles
commit_bundle_signatures
commit_intents
gate_executions
gate_attestations
semantic_review_attestations
evaluation_verdicts
pre_delivery_evidence
delivery_authorizations
git_staging_leases
candidate_staging_operations
git_staging_results
delivery_evidence_bundles
reconciliation_cases
~~~

**Registry 与 Skill 治理**

~~~text
workflow_snapshots
role_pack_snapshots
prompt_snapshots
model_policy_snapshots
gate_pack_snapshots
sandbox_profile_snapshots
skill_packages
skill_package_versions
skill_evaluations
skill_approval_proposals
skill_approval_scope_snapshots
skill_approval_decisions
skill_approval_sets
skill_bundle_builds
skill_bundle_snapshots
skill_bundle_packages
skill_publication_pointers
revocation_records
containment_records
revocation_watermarks
policy_approval_proposals
policy_approval_scope_snapshots
policy_approval_decisions
policy_approval_sets
bundle_composition_scopes
~~~

**可靠性与审计**

~~~text
idempotency_records
outbox_events
inbox_events
audit_index
node_registrations
node_heartbeats
~~~

### 26.2 数据库硬约束

- ID 主键；
- digest 字段格式 CHECK；
- 逻辑唯一身份 UNIQUE；
- eventId 与 Inbox 组合唯一；
- 幂等 scope + key 唯一，并保存 requestDigest；
- 一个 Run 只能有一个 selectedAttemptId；
- 一个活动资源只能有一个当前 resourceExecutionEpoch；
- proposalActor 不得等于任何必需 approver；功能与安全 Approver 身份不同；quorum 和 veto 由数据库约束与策略共同校验；
- Snapshot 内容字段不可 UPDATE，由权限与 Trigger 双重限制；
- ACTIVE 发布指针只能指向存在、签名有效、未撤销的 Snapshot；
- 所有外键删除使用 RESTRICT；历史对象不级联删除；
- Outbox 与领域变化同事务；
- 数据库使用 UTC，Lease 以数据库时间为准。

### 26.3 实现顺序

1. 先实现 Schema、完整状态迁移表、字段级写入矩阵与 Repository 接口；
2. 再实现 Outbox / Inbox 和幂等；
3. 再实现 Lease / Fencing；
4. 再引入 Artifact、SourceBundle 与 DependencyImage 引用；
5. 再引入 Runtime、Node 与 Node-mediated Model Gateway；
6. 再引入 CommitBundle、Gate / Attestation / Verdict；
7. 再引入独立 DeliveryAuthorization，最后开放 Git candidate 副作用；
8. Skill 治理可与 2～6 并行，但必须在真实 Attempt 前完成。

每次迁移包含向前脚本、兼容窗口、回滚策略、数据验证和演练证据。禁止在服务启动时隐式执行生产迁移。

---

## 27. 架构就绪条件与下一步

### 27.1 v1.3_READY_FOR_IMPLEMENTATION

只有以下条件全部满足，文档状态才能从“设计基线候选稿”改为“Approved for Implementation”：

- ADR-01～34 的 P0 项全部 Accepted；
- 所有派生说明材料不得与本文的信任域、写入权和合同依赖冲突；
- 核心 JSON Schema / Proto / OpenAPI 有正反例；
- 73 个正向任务、不少于 40 个负向 / 对抗任务及保留集策略获独立评测负责人签字；
- Prime / Pi、SkillRoster、Git、模型服务、PostgreSQL、MinIO/S3 的版本或 digest 冻结；
- Git Provider 的候选 ref、CAS、读回和权限经过实测；
- Sandbox、网络、凭据、签名和撤销策略经过安全复核；
- 功能 Approver、安全 Approver 与 proposalActor 身份可分离；Builder 与 Signer 也满足职责分离；
- Phase 1 预算、节点和责任人已经落实；
- 所有未决项有 Owner、截止日期和阻塞级别。

### 27.2 Phase 0 需要现场冻结的实现输入

这些不是架构方向问题，但必须在写业务代码前固定：

- 首个 Git Provider 与隔离测试组织 / 仓库；
- Source Ingestor 的只读凭据、submodule / LFS 规则与 GitObjectId 算法；
- Linux 发行版、内核、容器 Runtime、cgroup、seccomp 与 MAC 配置；
- PostgreSQL、MinIO / S3、OpenTelemetry 的精确版本和 digest；
- 依赖镜像基础源、MicroVM Builder、SBOM / SCA 工具与漏洞阻断阈值；
- 单节点 CPU、内存、磁盘、IOPS 和额定并发 C；
- 首批批准的模型、真实 provider/model/thinking 名称和费率；
- 工作负载身份、Secret Broker、Signer key 存储方式；
- Artifact / Audit / Trace 保留期和销毁规则；
- 73 个任务的语言、框架和类别配比；
- 安全评审、许可评审和 Go / No-Go 签署人。

### 27.3 推荐的第一个实现迭代

第一个迭代只做“可执行合同骨架”，不接真实 Git 写入：

1. 初始化单仓和 CI；
2. 建立 Task / Run / Attempt 及四个 Skill 聚合的状态机；
3. 提交 ExecutionPlanSnapshot、AttemptContract、SourceBundle、RuntimeImageSnapshot、SandboxImageSnapshot、DependencyImageSnapshot、ExecutionImageCompatibilitySnapshot、Skill / Policy Approval 三对象、SkillBundleSnapshot、CommitIntent、CommitBundle、DeliveryAuthorization、CandidateStagingOperation、Event Envelope、Error Envelope 的 Schema 与 DigestProfile / 签名测试向量；
4. 实现 PostgreSQL 迁移、CAS、Outbox / Inbox 和幂等；
5. 实现 ScriptedRuntimeDriver；
6. 完成 20 个正反状态场景；
7. 生成第一份可验证 Evidence Bundle；
8. 以此校正 v1.3 合同，再进入 Node / Sandbox。

这样第一批代码就能直接验证架构最关键的“权威、终态、幂等和证据”假设，不依赖模型表现。

### 27.4 最终判断

SkillRoster 适合成为本项目的一部分，但其合法位置已经被严格限定：

> v1.3 接纳 SkillRoster 的确定性离线盘点与治理证据能力，不接纳其本地文件变更模型成为运行时 Agent 能力。运行时 Skill 集必须由平台 Registry 批准、由内容寻址 Artifact 固定、由独立 Signer 签名、由 Role Pack 静态引用并只读挂载；动态安装、自我修改或临时加载均为架构违规。

在这一前提下，SkillRoster **预期**补强 Role Pack 裁剪、结构风险发现、来源 / fingerprint 证据和治理可审计性；实际效果必须由 Phase 1 供应链测试与 Phase 5 对照实验验证。它不能替代平台的恶意代码、安全、许可、审批、签名、发布与撤销体系。

---

## 附录 A：示例 SkillBundleSnapshot

以下是 Phase 0 Schema 必须接受的正例骨架；占位摘要在测试夹具中替换为合法 64 位小写十六进制值。

~~~json
{
  "schemaVersion": "1.3",
  "objectType": "SkillBundleSnapshot",
  "skillBundleSnapshotId": "0199b100-1000-7000-8000-000000000001",
  "skillBundleSnapshotDigest": "sha256:<skill-bundle-snapshot-digest>",
  "bundleName": "implementer-public-minimal",
  "bundleRevision": 3,
  "intendedRoleIds": ["implementer"],
  "packageVersions": [
    {
      "skillPackageId": "0199b100-2000-7000-8000-000000000002",
      "skillPackageVersionId": "0199b100-3000-7000-8000-000000000003",
      "packageDigest": "sha256:<package-digest>",
      "mountPath": "/opt/pi/skills/code-inspection",
      "entrypointPath": "SKILL.md",
      "approvedScopeDigest": "sha256:<scope-digest>",
      "approvalSetId": "0199b100-3500-7000-8000-000000000003",
      "approvalDecisionIds": [
        "0199b100-3600-7000-8000-000000000003",
        "0199b100-3700-7000-8000-000000000003"
      ],
      "approvalSetDigest": "sha256:<package-approval-set-digest>"
    }
  ],
  "compilerId": "skill-bundle-compiler",
  "compilerVersion": "1.3.0",
  "compilerImageDigest": "sha256:<compiler-image-digest>",
  "selectionPolicyId": "public-minimum-exposure-v1",
  "selectionPolicyDigest": "sha256:<policy-digest>",
  "bundleApprovalSetId": "0199b100-4000-7000-8000-000000000004",
  "bundleApprovalDecisionIds": [
    "0199b100-4100-7000-8000-000000000005",
    "0199b100-4200-7000-8000-000000000006"
  ],
  "bundleApprovalSetDigest": "sha256:<bundle-approval-set-digest>",
  "approvedBundleCompositionScopeId": "0199b100-4300-7000-8000-000000000007",
  "approvedBundleCompositionScopeDigest": "sha256:<bundle-composition-scope-digest>",
  "staticDiscoveryIndexDigest": "sha256:<static-index-digest>",
  "instructionMaterialDigest": "sha256:<instruction-material-digest>",
  "runtimeMountPolicy": {
    "mountMode": "READ_ONLY",
    "runtimeDiscoveryMode": "STATIC_INDEX_ONLY",
    "runtimeMutationAllowed": false,
    "runtimeInstallAllowed": false,
    "networkRequired": false,
    "executableBinaryAllowed": false,
    "mcpAutoInstallAllowed": false
  },
  "bundleArtifactRef": "cas://sha256/<bundle-artifact-digest>",
  "bundleArtifactDigest": "sha256:<bundle-artifact-digest>",
  "bundleManifestDigest": "sha256:<bundle-manifest-digest>",
  "expectedMountedSkillTreeDigest": "sha256:<mounted-skill-tree-digest>",
  "buildInputsDigest": "sha256:<build-inputs-digest>",
  "builtAt": "2026-09-04T08:00:00Z",
  "payloadDigest": "sha256:<skill-bundle-snapshot-digest>",
  "signature": {
    "signatureAlgorithm": "Ed25519",
    "keyId": "governance-publisher-key",
    "issuer": "skill-bundle-publisher",
    "issuerWorkloadIdentity": "spiffe://pi/governance/skill-bundle-publisher",
    "audience": null,
    "objectType": "SkillBundleSnapshot",
    "schemaVersion": "1.3",
    "payloadDigest": "sha256:<skill-bundle-snapshot-digest>",
    "controlPlaneEpoch": 42,
    "signedAt": "2026-09-04T08:00:01Z",
    "signature": "<base64url-signature>"
  }
}
~~~

## 附录 B：示例 AttemptContract

~~~json
{
  "schemaVersion": "1.3",
  "objectType": "AttemptContract",
  "attemptContractId": "0199c200-1000-7000-8000-000000000001",
  "taskId": "0199c200-2000-7000-8000-000000000002",
  "runId": "0199c200-3000-7000-8000-000000000003",
  "attemptId": "0199c200-4000-7000-8000-000000000004",
  "traceContext": {
    "traceId": "8f2b4fd756e3417fa2821d79b04f9231",
    "spanId": "6f2b4fd756e3417f",
    "parentSpanId": "5e1a3ec645d2306e"
  },
  "frozenRefs": {
    "executionPlanSnapshotId": "0199c200-4a00-7000-8000-000000000004",
    "executionPlanDigest": "sha256:<execution-plan-digest>",
    "plannedAttemptInputId": "0199c200-4b00-7000-8000-000000000004",
    "plannedAttemptInputDigest": "sha256:<planned-attempt-input-digest>",
    "workflowSnapshotId": "0199c200-5000-7000-8000-000000000005",
    "workflowDigest": "sha256:<workflow-digest>",
    "taskSpecSnapshotId": "0199c200-5100-7000-8000-000000000005",
    "taskSpecDigest": "sha256:<task-spec-digest>",
    "rolePackSnapshotId": "0199c200-5200-7000-8000-000000000005",
    "rolePackDigest": "sha256:<role-pack-digest>",
    "skillBundleSnapshotId": "0199c200-5300-7000-8000-000000000005",
    "skillBundleSnapshotDigest": "sha256:<skill-bundle-snapshot-digest>",
    "bundleArtifactDigest": "sha256:<bundle-artifact-digest>",
    "expectedMountedSkillTreeDigest": "sha256:<mounted-skill-tree-digest>",
    "skillPolicyId": "public-static-skills-v1",
    "skillPolicyDigest": "sha256:<skill-policy-digest>",
    "promptBundleId": "implementer-public-v1",
    "promptDigest": "sha256:<prompt-digest>",
    "toolPolicyId": "implementer-tools-v1",
    "toolPolicyDigest": "sha256:<tool-policy-digest>",
    "modelPolicyId": "public-model-policy-v1",
    "modelPolicyDigest": "sha256:<model-policy-digest>",
    "gatePackId": "go-public-v1",
    "gatePackDigest": "sha256:<gate-pack-digest>",
    "routingIntentSnapshotId": "0199c200-5400-7000-8000-000000000005",
    "routingIntentDigest": "sha256:<routing-intent-digest>",
    "attemptRouteSnapshotId": "0199c200-5500-7000-8000-000000000005",
    "attemptRouteDigest": "sha256:<attempt-route-digest>",
    "evaluationPlanId": "0199c200-5600-7000-8000-000000000005",
    "evaluationPlanDigest": "sha256:<evaluation-plan-digest>",
    "sandboxProfileId": "public-container-offline-v1",
    "sandboxProfileDigest": "sha256:<sandbox-profile-digest>",
    "sandboxImageSnapshotId": "0199c200-5650-7000-8000-000000000005",
    "sandboxImageSnapshotDigest": "sha256:<sandbox-image-snapshot-digest>",
    "sandboxOciImageDigest": "sha256:<sandbox-oci-image-digest>",
    "runtimeImageSnapshotId": "0199c200-5680-7000-8000-000000000005",
    "runtimeImageSnapshotDigest": "sha256:<runtime-image-snapshot-digest>",
    "runtimeOciImageDigest": "sha256:<runtime-oci-image-digest>",
    "dependencyImageSnapshotId": "0199c200-5700-7000-8000-000000000005",
    "dependencyImageSnapshotDigest": "sha256:<dependency-image-snapshot-digest>",
    "dependencyOciImageDigest": "sha256:<dependency-oci-image-digest>",
    "executionImageCompatibilitySnapshotId": "0199c200-5750-7000-8000-000000000005",
    "executionImageCompatibilitySnapshotDigest": "sha256:<execution-image-compatibility-snapshot-digest>",
    "sourceBundleId": "0199c200-5800-7000-8000-000000000005",
    "sourceBundleDigest": "sha256:<source-bundle-digest>"
  },
  "runtime": {
    "runtimeDriverId": "prime-runtime-driver",
    "runtimeDriverVersion": "1.3.0",
    "primeVersion": "0.9.1",
    "primeSourceDigest": "sha256:<prime-source-digest>",
    "rlmForWriteAttempt": "DISABLED"
  },
  "source": {
    "repositoryId": "repo-pi-demo",
    "baseGitObjectId": {
      "algorithm": "sha256",
      "hex": "<git-object-hex>"
    },
    "sourceBundleDigest": "sha256:<source-bundle-digest>"
  },
  "inputArtifactManifestRefs": [
    "cas://sha256/<source-artifact-manifest-digest>"
  ],
  "executionLeaseBinding": {
    "leaseId": "0199c200-8000-7000-8000-000000000008",
    "ownerInstanceId": "node-linux-01",
    "controlPlaneEpoch": 42,
    "resourceExecutionEpoch": 7,
    "leasePolicyDigest": "sha256:<lease-policy-digest>"
  },
  "security": {
    "skillMountMode": "READ_ONLY",
    "runtimeSkillMutation": "DENY",
    "runtimeSkillScanPlanApplyUndo": "DENY",
    "networkMode": "DENY_ALL",
    "modelCallMode": "NODE_MEDIATED_CONTROL_PIPE",
    "gatewayGrantVisibility": "NODE_ONLY",
    "gitRemoteCredentials": "NONE",
    "gitRemoteWrite": "DENY",
    "deliveryMode": "CANDIDATE_BRANCH"
  },
  "budget": {
    "budgetReservationId": "0199c200-9000-7000-8000-000000000009",
    "budgetPolicyDigest": "sha256:<budget-policy-digest>",
    "maxInputTokens": 200000,
    "maxOutputTokens": 30000,
    "maxCostMicros": 3000000
  },
  "allowedCapabilities": ["workspace.read", "workspace.write", "tool.test"],
  "networkPolicyDigest": "sha256:<complete-offline-network-policy-digest>",
  "filesystemPolicyDigest": "sha256:<filesystem-policy-digest>",
  "gitPolicy": {
    "remoteCredentials": "NONE",
    "remoteWrite": "DENY",
    "allowedPathPolicyDigest": "sha256:<path-policy-digest>"
  },
  "runtimePolicy": {
    "rlmForWriteAttempt": "DISABLED",
    "modelCallMode": "NODE_MEDIATED"
  },
  "outputContract": {
    "requiredArtifactTypes": ["PROPOSED_TREE", "ATTEMPT_TERMINAL_ENVELOPE"],
    "maxArtifactBytes": 1073741824
  },
  "revocationEpochSet": {
    "controlPlaneEpoch": 42,
    "skillEpoch": 18,
    "routeEpoch": 9,
    "credentialEpoch": 12
  },
  "lastFullySynchronizedAt": "2026-09-04T08:09:55Z",
  "notBefore": "2026-09-04T08:10:00Z",
  "notAfter": "2026-09-04T09:10:00Z",
  "contractDigest": "sha256:<contract-digest>",
  "signature": {
    "signatureAlgorithm": "Ed25519",
    "keyId": "attempt-service-contract-key",
    "issuer": "attempt-service",
    "issuerWorkloadIdentity": "spiffe://pi/control/attempt-service",
    "audience": "spiffe://pi/node/node-linux-01",
    "objectType": "AttemptContract",
    "schemaVersion": "1.3",
    "payloadDigest": "sha256:<contract-digest>",
    "controlPlaneEpoch": 42,
    "signedAt": "2026-09-04T08:09:59Z",
    "signature": "<base64url-signature>"
  }
}
~~~

## 附录 C：统一错误信封

~~~json
{
  "error": {
    "code": "FENCING_REJECTED",
    "message": "The execution grant is no longer current.",
    "retryable": false,
    "traceId": "8f2b4fd756e3417fa2821d79b04f9231",
    "details": {
      "attemptId": "0199c200-4000-7000-8000-000000000004",
      "presentedControlPlaneEpoch": 41,
      "currentControlPlaneEpoch": 42,
      "presentedResourceExecutionEpoch": 6,
      "currentResourceExecutionEpoch": 7,
      "evidenceRef": "cas://sha256/<evidence-digest>"
    }
  }
}
~~~

## 附录 D：评审签署页

| 角色 | 姓名 / 身份 | 结论 | 日期 | 签名 / Ticket |
|---|---|---|---|---|
| 平台 Owner |  |  |  |  |
| 架构负责人 |  |  |  |  |
| 安全负责人 |  |  |  |  |
| 质量 / 基准负责人 |  |  |  |  |
| Runtime 负责人 |  |  |  |  |
| SRE 负责人 |  |  |  |  |
| Git / 仓库 Owner |  |  |  |  |

在本页完成签署前，本文件保持“设计基线候选稿”，不得被表述为已完成生产架构批准。
