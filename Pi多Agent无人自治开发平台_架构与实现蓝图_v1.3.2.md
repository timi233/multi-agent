# Pi 多 Agent 无人自治开发平台

## 架构与实现蓝图（v1.3.2）

文档状态：功能与架构修订候选稿；规则已写入，Schema、原型、故障注入与独立复审尚待验证。
基线日期：2026-09-05
适用范围：内部单租户；首个功能兑现阶段处理 PUBLIC / 已脱敏代码任务。
前序文件：v1.0、v1.1、v1.2、v1.3、v1.3.1 保留原文件，作为设计历史和来源证据。
本版规范权威：v1.3.2 正文、规范性附录 H 与本版契约清单共同定义当前要求；旧版未明确移入本文的条款不自动继承。被替代条款见 §22、附录 F。
本轮范围：仅功能、架构、协议、安全边界、状态、数据与验收。不估算人工成本、人员投入、研发工期或商业回报，也不以这些因素裁剪能力。
时间与预算字段：Lease TTL、请求时限、恢复目标、稳定性观察窗口、Token / 调用 / 金额上限属于运行控制，保留其技术语义；它们不是建设排期或投入评价。
交付范围：完整 Markdown 版本文件。本文不是实现代码、运行证明或已批准的生产发布指令；图表仅为派生视图。

---

## 0. 执行结论

v1.3.2 将 v1.3.1 的设计整改进一步落实为可实现的对象依赖、授权刷新与失败收敛规则。本版不评估人工成本或研发周期；功能先后由依赖关系和验收证据决定。

1. **首个功能闭环仍是安全单节点、单 Implementer 的候选交付。** PUBLIC / 已脱敏任务在完全断网的执行沙箱中生成候选变更，独立确定性 Gate 验证，Git Stager 只交付任务专属 ref；自动合并和生产部署保持禁用。
2. **冻结执行内容与当前授权分离。** ExecutionPlan / AttemptContract 固定执行输入；全局 revocationEpoch 仍是唯一撤销日志序号，但历史合同中的值只记录准入基线。新动作必须使用当前完整撤销视图下的 RevocationCheckAttestation 与短授权，不能仅因历史合同水位较低而重写合同或全局终止任务。
3. **审批先绑定候选内容，再生成最终快照。** RolePackCandidate 不引用自身审批；PolicyApprovalScope / Decision / Set 只绑定 Candidate；RolePackSnapshot 单向引用二者。不得以排除安全字段的摘要技巧掩盖依赖环。
4. **执行失败与不确定性有显式出口。** 修正 Commit Assembler 时序，补齐候选交付失败、授权过期、优雅取消、证据封存失败等迁移；已知失败直接收敛，不确定外部副作用先对账。
5. **预算具有跨崩溃的消费所有权。** BudgetGrant 绑定 Gateway 实例代次，调用前持久化预留和发送意图；不确定调用保守占额，不能在重启、Grant 轮换或故障转移时恢复成可用余额。
6. **Runtime 接入由能力证据决定。** Prime v0.9.1 SDK / 进程内桥接是候选路线，Native Pi 是独立候选；两者接受同一管道、工具隔离、终态与调用归属合同。Scripted Driver 只能证明协议，不能替代真实模型能力验收。
7. **多角色依赖支持受控后绑定。** 计划冻结上游选择规则，可信 Input Binding Service 在上游输出发布后生成不可变 InputBindingSnapshot；Reviewer 不预知未来输出摘要，也不产生无限 Review 递归。
8. **成功率、安全性和终态匹配分别验收。** 正向运行成功率有明确分母；ExpectedOutcomeMatch 保留原义作为报告项，不再用整体 95% 隐式抬高正向门槛。负例错误接受、假成功和关键反例仍为零容忍。
9. **规范单一且可追踪。** Phase 2 显式声明功能开关、合同依赖和拒绝路径；整改状态区分“规范已修订”“实现已验证”“复审已关闭”，本版不虚构后两者。

---

## 1. 设计目标、边界与非目标

### 1.1 平台需要解决的问题

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
- 运行资源、墙钟时限、Token与模型消费上限；
- 数据分级与外部副作用声明；
- 候选分支命名和人工评审责任人；
- **离线可行性声明（本版适用）：Phase 2 白名单任务的全部 Gate 必须完全离线运行、不需要新增未经预构建的依赖**；运行时发现依赖缺失只能产生 `DependencyChangeProposal`（§6.14），不能进入当前 Attempt。

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

## 2. 版本收敛与 v1.3.2 架构修订

### 2.1 历史来源与本版边界

v1.0 明确控制面、Pi 运行时、独立验证和无人执行目标；v1.1 / v1.2 补充状态、Fencing、安全和证据闭环；v1.3 引入实现组件与 Skill 供应链；v1.3.1 对模板审批、事件发布者、Repair、预算热路径和运行时可行性做了文字整改。

v1.3.2 保留上述明确的安全与功能约束，但不将“旧版本更严格”作为冲突裁定方法。只有本文明确承接的规则才构成当前规范；旧版的自动合入、测试目录一刀切禁止、全局水位相等和阶段要求不得隐式覆盖本文。

本版不讨论建设投入、人员数量、排期或商业收益。Four-Eyes、职责分离、授权身份和独立复核仍是安全模型的一部分，不因本轮不讨论人工成本而取消。

### 2.2 功能与架构修订矩阵

| 编号 | 修订主题 | 规范落点 | 首次验证阶段 |
|---|---|---|---|
| A32-01 | RolePack Candidate → Approval → Snapshot 无环构建 | §10.3.1、§10.5、附录 G | Phase 0 合同 |
| A32-02 | 历史撤销基线与当前动作授权分离；无关任务可刷新后继续 | §10.7、§13.3 | Phase 1 |
| A32-03 | BudgetGrant 独占、持久化预留、不确定用量与故障转移 | §18.3、§26 | Phase 1 |
| A32-04 | Task / Run / Attempt / CandidateOperation 失败、取消及对账闭合 | §8、§12.2、§14 | Phase 0 模型 / Phase 1 注入 |
| A32-05 | Prime / Native Pi 同合同接入、无网络与真实模型验证分离 | §3.4、§6.7、§12.4 | Phase 0 能力实验 / Phase 1 |
| A32-06 | 正向成功率、负例安全与终态匹配分开定义；任务范围一致 | §1.2、§17、§19 | Phase 0 基准 |
| A32-07 | 单一规范与 Phase 2 功能开关，不以旧条款暗中扩大范围 | §19.2.1、§22、附录 F/H | Phase 0 |
| A32-08 | 上游产物后绑定、Review Run 终止路径和 Repair 输入血缘 | §6.3、§10.5.2、§14.3 | Phase 0 合同 / Phase 3 |
| A32-09 | 状态专属证据要求，失联证明不冒充完整执行证据 | §11.1、§17.3 | Phase 1 |
| A32-10 | Schema 版本、API、事件、数据库和示例同步 | §9.2、§11～13、§26、附录 B/G/H | Phase 0 |

### 2.3 不可破坏的不变量

1. Task、Run、Attempt、候选交付操作拥有独立状态和唯一字段级写入者。
2. Runtime、仓库代码和模型输出不拥有成功、审批、签名或外部写入权威。
3. 执行内容冻结；输入变化生成新对象，临时授权刷新不能改变成功标准和权限范围。
4. Lease / Fencing 校验与撤销依赖闭包检查同时成立才能发出新动作。
5. Git 只接收已验证的精确 CommitBundle；任何不确定写入先读回对账。
6. 审批摘要不能循环；历史证据不能为恢复方便而改写。
7. 安全失败不能被成功率平均值、模型意见或运行成本抵消。
8. Phase 2 代码执行完全断网，依赖预构建，SkillRoster 严格处于离线治理域。

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

Attempt / Runtime / Gate代码执行环境完全断网：没有TCP/UDS、默认路由、DNS、代理、Gateway Socket、对象存储通道或可继承网络描述符。网络命名空间本身不能证明本地Socket与FD旁路被禁止，须验证系统调用和描述符能力。

1. Domain4 Runtime仅经Supervisor建立的专用stdio/匿名管道提交ModelCallIntent，声明调用身份、序号和结构化请求；Runtime不指定任意主机/URL/凭据。
2. Node Runtime Proxy验证合同、InputBinding、当前Lease和撤销证明，将合法意图交给Domain5 Model Gateway。
3. Gateway校验真实冻结路线与BudgetGrant，持久化预留/发送意图后请求模型，生成RouteAttestation与用量事实。
4. 响应按调用ID、序号、类型与长度限制交回Runtime。仓库工具进程不得继承Runtime控制管道、签名key或Grant。
5. Supervisor以不同受限主体及MAC/ptrace/proc访问控制隔离Runtime控制通道与仓库工具进程；仅close-on-exec或不同cgroup不足以替代旁路验证。

Runtime中的SDK或shim仍是不可信执行域；其能请求的动作由Node依据冻结政策限制。模型请求中仓库内容是带来源标签的不可信数据。无论Prime或NativePi，不能证明管道归属、FD隔离和响应关联时输出NO_VERDICT并拒绝交付。

Prime默认daemon的Socket路径不属于允许的接入方式。候选SDK桥接、真实模型调用与NativePi替代均按§6.7独立实验，不预先断言可用。

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

**评审材料（PR/MR）语义（本版适用）：** Phase 2 默认只创建任务专属候选 ref 与评审材料（候选分支 + DeliveryEvidenceBundle 摘要链接），**不自动创建 PR/MR**。自动创建 PR/MR 属于独立的受控外部副作用，若后续启用，必须另行定义 `CreateReviewRequest` 合同：携带稳定 operationIdempotencyKey、最小权限凭据、创建后读回确认、与评审材料证据关联，并纳入 reconcile 流程。`CloseCandidateRef` 只有在确实存在平台创建的 review request 时才允许关闭关联 PR；对平台未创建的任何 PR/MR 对象，Git Adapter 一律不得写。

---

## 4. 信任域

本版保留原有10类信任域编号和3类Skill供应链域；实际职责按本节定义，不自动继承旧版未列规则。PostgreSQL 属于 Domain 1 的状态基础设施，不占用或改写信任域编号。

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
| Attempt 的 CLAIMED、PROVISIONING、RUNNING、TERMINATING、TERMINAL_REPORTED 及 FAILED_PROVISIONING | PostgreSQL | Node Agent 经 Attempt Service；执行迁移用Lease/Fencing，终止报告用有效执行证明或TerminalReportGrant | Lifecycle按状态守卫校验事实 |
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
| RolePackCandidate | Registry+Artifact | Role Pack Compiler | Approval只绑定Candidate，Publisher生成最终Snapshot |
| RuntimeIntegrationCandidate / Snapshot | Registry+Artifact | Runtime Integration Compiler / Publisher | Candidate测试、独立审批后发布 |
| InputBindingSnapshot | Registry+Artifact | Input Binding Service | AttemptContract只读引用 |
| RequiredRunBinding | PostgreSQL | Lifecycle Service | Orchestrator只发意图 |
| RevocationViewCheckpoint / RevocationCheckAttestation | 撤销日志+签名证据 | Revocation Service | 授权服务与代理只验签 |
| BudgetGrant分配和父级额度 | PostgreSQL Ledger | Ledger Service | Gateway不得增发或重置余额 |
| InvocationReservation / 消费Journal | Gateway持久化事实+Ledger结算引用 | 绑定的Gateway代次 | 只能消费自身分片，Ledger对账 |
| TerminalReportGrant / terminalReportGeneration | PostgreSQL+签名证据 | Lifecycle Service | Node只停止与报告 |
| CandidateOperation AuthorizationBinding | PostgreSQL追加记录 | Delivery Service | 不改变logicalOperationDigest |

### 5.1 数据库与对象存储的一致性

大对象使用原子发布。步骤如下：

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
- 产生 TaskAdmissionDecided 事件；Lifecycle Service 消费该决定后创建 Task 状态并发布 TaskAccepted / TaskRejected（见 §8.1）。Task API 不发布 TaskAccepted / TaskRejected，保证每个事件只有唯一发布者。

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
- Reviewer 与 Implementer 满足独立性要求（编译期结构和调度期输入绑定分别验证，见下）；
- 每个成功路径都能到达确定性 Gate；
- 所有循环都有次数、时间和成本上限；
- 所有外部副作用都由受信任组件执行。

**Reviewer / Semantic Judge 独立性判定规则（v1.3.2：编译期结构检查与调度期绑定检查）：** 对每个调用 Semantic Review 或 Reviewer Agent 的 Workflow，编译器必须验证以下全部条件，任一不满足即编译失败（`DIVERSITY_UNSATISFIED`）：

1. Reviewer Run 与 Implementer Run 是不同 Run / Attempt，拥有各自独立的 ExecutionPlanSnapshot 条目；
2. 编译期冻结Reviewer输入槽位的producer与只读规则；调度时由InputBindingSnapshot绑定选定Attempt的不可变ArtifactManifest。Reviewer可写临时目录与Implementer物理隔离，输入只读；
3. Reviewer 与 Implementer 使用不同的 rolePackDigest / promptDigest（同一 Prompt / Role Pack 不得同时服务两个对立角色）；
4. 高风险安全评审默认要求 Reviewer 与 Implementer 使用不同 provider / model family，并满足本节列明的独立性分层规则（不同真实上游厂商、不同模型族、不共享网关故障域；无法满足时显式标记 DIVERSITY_UNSATISFIED，不得静默降级）；
5. 路线和角色独立性由Workflow Compiler用冻结的AttemptRouteSnapshot / RolePackSnapshot确定性校验；实际产物绑定由Input Binding Service在上游发布后复核，不允许把“人工置信”作为判定输入。

Execution Plan Compiler 必须为每个 plannedAttemptInput 生成稳定 digest；WorkflowSnapshot 不吸收运行环境版本，ExecutionPlanSnapshot 只引用不可变对象，二者均不能在执行中原地修改。

### 6.4 Orchestrator

Orchestrator 只做 DAG 就绪判定和调度意图，不直接写 Attempt 运行态或终态。它根据已完成依赖、预算、策略和撤销状态，只能从已发布 ExecutionPlanSnapshot 的 plannedAttemptInputs 中选择一个既有 plannedAttemptInput / AttemptRouteSnapshot，经 Attempt Service 创建 `CREATED → READY` 的 Attempt，并通过事务 Outbox 发布可认领事件；不得创建未计划 Route。Node claim 只能把这个既有 Attempt 原子地从 READY 改为 CLAIMED并生成最终 AttemptContract / Lease / Assignment，不能再次创建 Attempt 或 Route。

MVP 不引入独立消息 Broker；Worker 通过 PostgreSQL Outbox / Inbox 和 SKIP LOCKED 模式处理事件。出现以下任一条件时，才提交引入 Broker 的 ADR：

- Outbox backlog 持续超过 SLO；
- 数据库事件轮询占用超过额定资源预算；
- 单节点目标无法满足；
- 跨区域复制成为正式范围。

### 6.5 Lifecycle、Lease 与 Fencing

Lifecycle 是全部 Task 状态、全部 Run 状态及 Attempt 的平台收敛状态（OUTPUT_STAGED、SELECTED、SUPERSEDED、CANCELLED、BUDGET_EXHAUSTED、FAILED、QUARANTINED、LOST、FENCED、TIMED_OUT）的唯一写入者。Node Agent 经 Attempt Service 是 CLAIMED、PROVISIONING、RUNNING、TERMINATING、TERMINAL_REPORTED、FAILED_PROVISIONING 的唯一逻辑写入者；API内部以CAS为前提；执行迁移验证有效Lease/Fencing，终止报告按§11.1/§13.3验证报告专用授权。Lease Service 负责 claim、heartbeat、renew、expire、cancel、fence 和 orphan reconciliation。

AttemptContract 一旦发布不得改变；续租通过独立 LeaseGrant 完成。所有事实提交必须携带 attemptId、leaseId、epoch、fencingToken 和 eventId。

**LeasePolicySnapshot（v1.3.2 技术初值）：** Execution Lease TTL 初值 **30 秒**，Node heartbeat 间隔 **10 秒**，续租须在到期前至少 **10 秒**发起；Verification Lease与GitStaging Lease 的 TTL 在 Phase 0 按作业类型冻结（初值建议 60 秒）。所有 SLO、测试与文档中“2 个 Lease TTL”等单位必须引用同一个已发布 `LeasePolicySnapshot(id, leaseType, ttlSeconds, heartbeatSeconds, renewBeforeSeconds, expiresAtPolicy, pauseGraceSeconds, terminalReportTtlSeconds, payloadDigest, signature)`，禁止各章自行假设数字。

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

**同宿主隔离要求（本版适用）：** 即使 Phase 2 单节点部署，“独立 Gate”防假成功的有效性也要求 Attempt 沙箱与 Gate Command 使用**不同的宿主用户、不同的 cgroup 树与不同的 mount / PID / network namespace 集合**；Attempt 进程组不得访问 Gate Supervisor 的通信管道或 Gate 输入目录。“Attempt 篡改 Gate 输入或通信管道”必须作为反例纳入 Phase1建立、Phase2回归的200个故障/安全场景（见 §19.4、§17.3）。

### 6.7 Runtime Driver：Prime / Native Pi / Scripted

平台只依赖 AgentRuntimeDriver 合同。PrimeRuntimeDriver 与 NativePiRuntimeDriver 必须满足同一能力清单，不把某个上游的默认 daemon 或 SDK API 当作平台安全保证。

| 路线 | 本版定位 | 可声明的能力 |
|---|---|---|
| ScriptedRuntimeDriver | 确定性协议模拟器 | 状态、乱序、取消、崩溃、预算与证据管道；不能声明代码智能或真实模型兼容 |
| PrimeRuntimeDriver | 固定 Prime v0.9.1 的 SDK / 进程内嵌入桥接候选 | 只有能力实验通过后才进入真实执行 |
| NativePiRuntimeDriver | 精确版本 / 源码摘要冻结的独立候选 | 不能因绕过 Prime 而自动视为满足隔离和终态要求 |

Runtime 主进程位于 Domain 4 的受限环境，SDK 嵌入不意味着把不可信 Runtime 装入高信任 Node 进程。受信任 Node Driver 只解析协议；Domain 4 内的 Runtime shim 使用受监督管道向 Node 请求模型调用。不得把长期凭据、Gateway Socket 或通用网络能力带入 Domain 4。

RuntimeIntegrationCandidate 至少记录 runtimeName/version/sourceDigest、启动入口、SDK / CLI 模式、shimDigest、runtimeProtocolVersion、modelTransport、toolProcessIsolationProfileDigest、禁用的动态发现 / 自更新 / 调度能力和 CapabilityTestEvidence digest。选中路线形成 RuntimeIntegrationSnapshot，经政策审批、镜像签名与 Compatibility 验证后供计划引用；不可执行的候选不能发布。

Phase 0 能力实验必须分别回答：

1. 无 TCP、UDS、DNS、继承网络 FD 的 Runtime 能否启动，且不自动启动依赖 Socket 的后台链路；
2. provider 调用能否全部映射为 ModelCallIntent，覆盖流式响应、工具调用、取消、异常、上下文压缩与重试；
3. Node 能否识别每次调用归属、序号和终态；对畸形、超大、乱序协议稳定拒绝；
4. 仓库工具子进程是否无法获得控制管道，是否被不同身份 / MAC 规则阻止经 /proc、ptrace 或其他旁路获取 Runtime 通道；
5. Runtime 自带恢复、消息、计划任务、扩展发现和 Skill 装载是否符合平台冻结边界；
6. 真实模型能力实验是否完成一次“调用 → 工具执行 → 后续调用 → 取消或完成”，而非只证明模拟响应可用。

真实模型实验在隔离的 PUBLIC 合成任务环境进行，Gate 和 Runtime 仍不联网，模型只由 Node 代理转发。实验身份只允许限定测试调用，无真实 Git 写入权；实验不是正式 Phase 2 运行。未通过时记录缺失能力，不以延长运行或切换名称掩盖失败。

本版引用的上游证据仅说明可选接入入口：

- [Prime v0.9.1 Daemon Architecture](https://github.com/PrimeIntellect-ai/prime-agent/blob/v0.9.1/packages/coding-agent/docs/daemon.md)：常规 daemon 使用本地 socket；直接 SDK 路径可在进程内运行。
- [Prime v0.9.1 Agent Connection](https://github.com/PrimeIntellect-ai/prime-agent/blob/v0.9.1/packages/coding-agent/docs/agent-connection.md)：存在 InProcessAgentConnection；它本身不定义托管控制面或安全沙箱。

这些上游说明不是本平台零网络、模型桥接或安全测试的通过证明。RLM 在写 Attempt 中始终禁用；Phase 2 同时禁用只读 RLM 和后台自治子树，后续启用须独立能力与静默屏障验证。

### 6.8 Model Gateway

职责：

- 只接受 Node Runtime Proxy 的工作负载身份，校验其代转的 Attempt 身份、Lease、epoch、invocationId 和撤销状态；
- 把模型别名解析成冻结的真实 provider/model/thinking；
- 强制最大请求、并发、Token、成本、重试与超时；
- 去除供应商长期凭据；
- 记录请求摘要、响应摘要、真实模型身份、用量与错误；
- 生成可验证 Route Attestation。

已有模型代理服务可以通过 ModelGatewayAdapter 复用，但在 Phase 0 必须重新验证其实时模型目录、认证、限流、错误语义和实际 completion；历史可用不等于本版当前可用。

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


**输出与终态证据的无环封存：** OutputArtifactManifest只列业务输出字节，不把引用该Manifest的AttemptTerminalEnvelope重新装入自身。先seal输出并冻结Manifest内容摘要，再签Node信封引用该摘要；Lifecycle按正常/取消守卫决定发布正常Manifest或隔离。Evidence Service随后生成单向引用Manifest与信封的EvidenceManifest。outputContract分别声明requiredArtifactTypes与requiredEvidenceTypes，不能混装形成相互摘要引用。

### 6.10 Verification Plane

Verification Plane 跨三个明确边界：Domain 2 的 Gate Supervisor 负责可信宿主捕获，Domain 7 只运行不可信仓库命令，Domain 8 只负责组装 / 签名与机械裁决：

- Commit Assembler：在 Domain 8 的受信任控制器中，只从已发布 SourceBundle、选定输出 Artifact、冻结 CommitIntent 与规范化元数据组装不可变 Git tree / commit 对象；不执行仓库代码，不调用 LLM，也不拥有 Git 写凭据；
- Commit Bundle Signer：只对 Commit Assembler 产生的精确 payloadDigest 签名，不能改写内容、执行 Gate 或签发 DeliveryAuthorization；签名 CommitBundle 经 Artifact Service 发布，Gate 与 Git Stager 按 digest 分别读取；
- Gate Supervisor（Domain 2）：持有 VerificationLease，逐步骤直接创建 Domain 7 命令进程，以宿主 waitpid、cgroup、signal、timeout 和只写管道流式哈希形成 GateExecutionFact / GateResult；
- Gate Command Process（Domain 7）：在独立完全断网沙箱中，对精确 CommitBundle 运行单个测试、构建、Lint、类型、安全规则或路径 / 树一致性命令；只能输出字节与退出，不能写权威结果、持有 Lease 或签名；
- Semantic Review Worker：使用独立 Review Run / Route，只读接收带来源标签的数据区，Prompt 与数据物理分段，按严格 Schema 产生非权威风险证据；
- Evidence Completeness Checker：检查必填字段和摘要链；
- Verdict Aggregator：按冻结规则确定 ACCEPTED、REJECTED、REPAIRABLE、HANDOFF_TO_HUMAN 等结果。

**Semantic Review 的信任域归属（本版适用）：** Semantic Review Worker 会调用 LLM，因此它不属于 Domain 8。其**编排控制属于 Domain 1**（作为正式 Review Run 创建、调度、预算与审计），**LLM 执行通过独立 Review Run 走 Domain 3 / 4 / 5 链路**；Domain 8 只负责验签、封装 SemanticReviewAttestation 与机械裁决，不调用 LLM、不执行仓库代码。Semantic Review只读接收选定Attempt的输出与冻结GatePack；输入不可写，临时输出目录独立隔离。P2不创建此类Run，P3才启用。

这里不定义名为 Verifier 的模型角色。Gate Supervisor 是确定性宿主监督器，Gate Command Process 是不可信命令进程，Verdict Aggregator 是纯规则组件；三者都不配置 RolePack 或 SkillBundle。Reviewer Agent / Semantic Judge Agent 不得覆盖失败Gate或宣告成功；Phase2不创建此类Run，Phase3才按冻结Workflow启用。

Critical Gate 禁止由一个 shell wrapper 间接汇总。Gate Supervisor 必须逐步骤直接创建受限命令进程并捕获 `waitpid`、signal、timeout、cgroup 资源事实，对 stdout / stderr 流式哈希；Domain 7 内的 JUnit、摘要、退出说明等均仅作解释证据，不能覆盖宿主捕获事实。

### 6.11 Git Stager

Git Stager 使用一个首期 Git Provider Adapter，具体供应商在 Phase 0 冻结。它必须支持：

- 精确 base commit；
- 任务专属候选 ref；
- 原子compare-and-swap并在调用后读回校验；
- 幂等创建；
- 最小权限短期凭据；
- 禁止受保护分支；
- 失败对账与隔离；
- CandidateBundle 回传。

Git Stager 只消费已签名 CommitBundle 和独立 `GitStagingLease / DeliveryAuthorization`，不复用已结束的 Attempt ExecutionLease，不下载依赖，不启动解释器，也不执行任何仓库代码。

不同时具备可靠ref CAS和读回能力的Git供应商不能进入Phase2；CAS能力和读回能力分别验证。

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
- ref冻结后变化不改写SourceBundle。SourceFreshnessPolicy必须声明PINNED_BASE_ALLOWED或REQUIRE_REMOTE_BASE_UNCHANGED：后者在交付授权前单独读取目标base ref并记录判定，变化则HANDOFF_TO_HUMAN/新Task；candidate ref CAS不能检测另一个base ref的漂移。P2默认PINNED_BASE_ALLOWED并在评审材料显示冻结base；要求严格base未变的任务若Provider不支持base与candidate联合原子条件，只能报告检查时点，不能承诺跨ref原子性。

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

SkillRoster在本版中是 **SkillInventoryAdapter**：

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

发布制品来源（`sourceKind=PUBLISHED_ARTIFACT`，本版承接）必须同时满足：对象按内容摘要寻址、携带 SBOM / provenance 与来源链、位于 allowlist 的制品库、工件 digest 与清单一致；不满足任一项则拒绝。该来源与仓库来源一样进入一次性隔离区并形成 sourceSnapshotDigest。

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

本版Four-Eyes同样适用于 EvaluationPolicyTemplateSnapshot、RoutingPolicyTemplateSnapshot、ModelPolicySnapshot 与 SandboxProfileSnapshot 的新建或变更。**政策模板（template）与任务实例（instance）必须分开命名与审批**：凡是可被多个 Task 复用的“政策模板/策略对象”（EvaluationPlanTemplate、RoutingIntentTemplate、ModelPolicy、SandboxProfile、GatePack、Prompt、ToolPolicy、RolePack、WorkflowTemplate）的新建或变更必须走 Four-Eyes；而从已批准模板与 TaskSpec 确定性编译出的逐任务实例（EvaluationPlanSnapshot、RoutingIntentSnapshot、ExecutionPlanSnapshot）属于任务执行输入，**不需要逐任务人工审批**，但必须在实例中携带 templateRef / templateDigest、compilerId / compilerVersion 与派生输入 digest，且不可超出模板允许范围。任何把 quorum、veto 或 required Gate 降到安全基线以下的模板变更，除 proposer 与常规 approver 外还必须有额外 Security Approver。

任意字节、来源、能力、Role、数据等级、SandboxProfile 或政策变化都使旧审批失效。单人组织允许评测和 PoC，但不能把 Bundle 标记为生产 ACTIVE。

### 7.8 Bundle 构建、签名与挂载

SkillBundle 与 Role Pack、Prompt、Gate Pack、Sandbox Image 分离：

- 每个相对路径、字节摘要、大小、类型和许可位进入 manifest；
- 相同输入在干净环境中可重复构建；
- Builder 无审批和签名权限；
- Signer 只接受 Registry 发出的精确 digest；
- 只对摘要签名，不对路径、标签或 latest 签名；
- Bundle 通过带 LAB / BENCHMARK / CANARY / PRODUCTION 作用域的发布指针独立灰度、撤销和回滚；
- Node启动前验证digest、签名、审批、Role绑定、SandboxProfile兼容性，以及历史基线上的当前撤销检查与短Grant；
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

以下迁移是本版的正式白名单。未列出的迁移一律拒绝；所有写入要求 expectedState、expectedRowVersion、proofRefs。事件名取目标语义并由唯一状态写入者发布。失败和取消不能跳过子执行回收或外部副作用对账。

### 8.1 Task

Task API 只发布 TaskAdmissionDecided；Lifecycle 创建 Task 并独占其全部状态与 Task 事件。

| from | to | guard / proofRefs | terminal |
|---|---|---|---|
| — | ACCEPTED / REJECTED | 不可变准入决定、TaskSpec digest、幂等记录 | REJECTED 是 |
| ACCEPTED | PLANNING | 准入通过且内容匹配 | 否 |
| PLANNING | EXECUTING | 签名 ExecutionPlan 已发布、当前授权有效、RequiredRunBinding 已建立 | 否 |
| ACCEPTED / PLANNING | FAILED_SPEC_AMBIGUOUS / FAILED / NO_VERDICT | 规格歧义 / 编译失败 / 无法获得可靠输入；不得存在活动执行 | 是 |
| EXECUTING | VERIFYING | 当前 RequiredRunBinding 的必需输出已冻结，允许启动独立验证 | 否 |
| EXECUTING | FAILED / BUDGET_EXHAUSTED / NO_VERDICT / HANDOFF_TO_HUMAN | 对应执行或依赖失败，全部活动能力已终止或 fenced | 是 |
| VERIFYING | DELIVERY_PENDING | 当前必需 Run 为 VERIFIED、ACCEPTED Verdict、PreDeliveryEvidence 完整 | 否 |
| VERIFYING | REPAIR_PLANNING | Phase 3 开关启用、REPAIRABLE、轮次和预算允许、失败证据已归档；此时不要求新计划已生成 | 否 |
| REPAIR_PLANNING | EXECUTING | 新 REPAIR 计划及 Repair Run 已发布，RequiredRunBinding CAS 替换完成 | 否 |
| VERIFYING / REPAIR_PLANNING | FAILED / NO_VERDICT / HANDOFF_TO_HUMAN / BUDGET_EXHAUSTED | 冻结 Verdict 映射 / 计划失败 / 上限耗尽；相关执行均收敛 | 是 |
| DELIVERY_PENDING | SUCCEEDED | 候选操作 CONFIRMED、Git 读回和最终 DeliveryEvidenceBundle 完整；未接受取消、无活动安全隔离 | 是 |
| DELIVERY_PENDING | FAILED | 候选操作 FAILED / EXPIRED，或封存重试耗尽；副作用已证明未发生或已精确确认，保存失败原因与 disposition | 是 |
| DELIVERY_PENDING | HANDOFF_TO_HUMAN | 候选操作 SUPERSEDED，或候选已确认但最终交付证明无法完成；保存读回与保留 / 隔离处理 | 是 |
| DELIVERY_PENDING | RECONCILING | APPLYING / 结果不明 / ack 丢失；即使 DB 尚为 PREPARED，也先查持久 dispatch intent | 否 |
| ACCEPTED / PLANNING / EXECUTING / VERIFYING / REPAIR_PLANNING | CANCEL_REQUESTED | 授权取消、停止新能力，向可达 Node 发报告专用授权 | 否 |
| DELIVERY_PENDING | CANCEL_REQUESTED | CAS 尚未发送；Delivery Service 已 fence 写权并证明无在飞操作 | 否 |
| CANCEL_REQUESTED | CANCELLED | 全部子 Run / Attempt 收敛、无在飞或已生效外部写入 | 是 |
| CANCEL_REQUESTED | RECONCILING | 取消期间发现并发 dispatch intent 或外部效果不明 | 否 |
| 任意非终态且非 RECONCILING | QUARANTINED | 安全命中、能力已 fenced、副作用状态明确；若不明确则转 RECONCILING 并建立 ContainmentRecord | 是 |
| RECONCILING | SUCCEEDED / FAILED / HANDOFF_TO_HUMAN / QUARANTINED / CANCELLED | 先读回操作身份和 ref；满足各目标完整守卫；仅证明从未写入且取消已接受时可 CANCELLED | 是 |

所有非终态具有 StateDeadlinePolicy 的处理入口。deadline 到期不等于任意写失败：有在飞副作用必须进入 RECONCILING；无副作用时按当前阶段形成 FAILED / NO_VERDICT / BUDGET_EXHAUSTED 等证明。已确认 candidate ref 的 Task 即使因交付证据失败而终结，也须在输出中列明已存在的 ref，不得声称未写入或自动删除。

Phase 2 禁用 Repair：REPAIRABLE 由冻结映射转 HANDOFF_TO_HUMAN，不创建 REPAIR_REQUIRED Run 或 REPAIR_PLANNING Task。MERGING 与自动部署不属于本版可达状态；未来启用须独立规范和 ADR。

### 8.2 Run

Lifecycle 独占 Run.state / selectedAttemptId / RequiredRunBinding。runKind 与 deliverableKind 在计划中冻结。

| from | to | guard / proofRefs | terminal |
|---|---|---|---|
| — | CREATED | Workflow 节点实例化、计划条目明确 | 否 |
| CREATED | BLOCKED / READY | 依赖未齐 / InputBinding 就绪 | 否 |
| BLOCKED | READY | 可信上游输出和绑定完整 | 否 |
| BLOCKED | FAILED_DEPENDENCY | 必需上游不可恢复，或依赖等待时限耗尽 | 是 |
| READY | EXECUTING | 至少一个既有 READY Attempt、合法预算预留 | 否 |
| EXECUTING | OUTPUT_STAGED | 合法输出已 PUBLISHED，selectedAttempt CAS 成功且未取消 | 否 |
| EXECUTING | RETRY_WAIT / AWAITING_EXTERNAL | 冻结的基础设施重试 / 外部等待条件 | 否 |
| RETRY_WAIT / AWAITING_EXTERNAL | READY | backoff / 外部条件满足，计划内路线、预算与授权有效 | 否 |
| OUTPUT_STAGED | COMMIT_ASSEMBLING | CODE_CHANGE；CommitIntent 和 operation key 已分配；此时不要求 CommitBundle 存在 | 否 |
| COMMIT_ASSEMBLING | VERIFYING | 组装成功，签名 CommitBundle 已 PUBLISHED，精确对象与 EvaluationPlan 对齐 | 否 |
| OUTPUT_STAGED | VERIFYING | REVIEW_EVIDENCE / READ_ONLY_EVIDENCE；只验证 Schema、来源、路线和完整性，不生成 Git 对象 | 否 |
| VERIFYING | VERIFIED | ACCEPTED Verdict、该 Run kind 的必需证据完整 | 是 |
| VERIFYING | REPAIR_REQUIRED | Phase 3 的 IMPLEMENTATION Run、REPAIRABLE、轮次与预算允许 | 否 |
| REPAIR_REQUIRED | SUPERSEDED | 新 Repair Run 已创建、parentRunId 和 RequiredRunBinding CAS 完成 | 是 |
| CREATED / READY / EXECUTING / RETRY_WAIT / AWAITING_EXTERNAL / OUTPUT_STAGED / COMMIT_ASSEMBLING / VERIFYING / REPAIR_REQUIRED | FAILED / NO_VERDICT / HANDOFF_TO_HUMAN / BUDGET_EXHAUSTED | 对应不可恢复失败、证据缺失或上限耗尽；活动能力已收敛；不得覆盖已发布成功 | 是 |
| 任意非终态 | CANCELLED / QUARANTINED | 对应已接受取消 / 安全证明，活动 Attempt / Gate 已终止或 fenced；关联外部副作用明确 | 是 |

COMMIT_ASSEMBLING 的临时重试保持同一状态，复用同一 CommitIntent、组装作业身份与固定提交元数据；不得重分配 operation key。重试上限后进入 FAILED / NO_VERDICT。REVIEW Run 不触发 Review 或 Repair；其拒绝、失败和缺证据由实现 Run 的冻结评估规则处理。

Phase 2/3 的候选成功只认可 VERIFIED。PARTIALLY_VERIFIED、DEGRADED_SUCCESS、CONDITIONAL_SUCCESS 本版仅为保留名，不是可达成功状态；需要渐进式交付时另行定义未满足项、证据与交付权限。

### 8.3 Attempt

Orchestrator 经 Attempt Service 独占创建与 CREATED → READY；Node 经 Attempt Service 独占 CLAIMED、PROVISIONING、RUNNING、TERMINATING、TERMINAL_REPORTED、FAILED_PROVISIONING；其余终结和输出选择由 Lifecycle 独占。

| from | to | guard / proofRefs | terminal |
|---|---|---|---|
| — | CREATED | 既有 Run、计划条目、稳定 Attempt ID | 否 |
| CREATED | READY | InputBindingSnapshot、预算和调度前置齐备 | 否 |
| READY | CLAIMED | 单事务 claim CAS、Lease / Assignment / 最终 AttemptContract | 否 |
| CLAIMED | PROVISIONING | 当前执行授权、输入验签、Node admission | 否 |
| PROVISIONING | RUNNING | 摘要、离线隔离、进程监督与能力匹配 | 否 |
| PROVISIONING | FAILED_PROVISIONING | 可验证的环境创建失败、已启动进程完成清理 | 是 |
| RUNNING | TERMINATING | Stop / 已接受取消 / timeout / budget / 安全终止；新执行权关闭，保留报告专用权 | 否 |
| RUNNING | TERMINAL_REPORTED | 正常终态信封、完整进程树静默、有效执行或报告证明 | 否 |
| TERMINATING | TERMINAL_REPORTED | TerminalReportGrant、Node 来源、进程静默与停止原因 | 否 |
| TERMINAL_REPORTED | OUTPUT_STAGED | 正常完成、未取消 / 超时 / 超预算 / 撤销、正常 Manifest 已 PUBLISHED，合同与输出完整 | 否 |
| TERMINAL_REPORTED | CANCELLED / TIMED_OUT / BUDGET_EXHAUSTED / FAILED / QUARANTINED | 与已冻结stopReason或证据失败对应；上传/seal/引用发布超限时保存已收到的信封并失败；停止输出选择 | 是 |
| OUTPUT_STAGED | SELECTED / SUPERSEDED | Run 选择 CAS、未接受取消、输出未被隔离 | 是 |
| OUTPUT_STAGED | CANCELLED / QUARANTINED | 发布后取消或安全证明，尚未被合法 SELECTED | 是 |
| CREATED / READY / CLAIMED / PROVISIONING | CANCELLED / TIMED_OUT / QUARANTINED | 对应取消、截止或安全事实，已开始进程须清理 / fencing | 是 |
| CLAIMED / PROVISIONING / RUNNING / TERMINATING | LOST / FENCED / TIMED_OUT / BUDGET_EXHAUSTED / QUARANTINED | DB 时间、Lease / Node 失联或强制停止证明；可达节点须附清理事实 | 是 |

TERMINATING 增加 BUDGET_EXHAUSTED 出口。取消后的信封只能生成取消 / 失败证据，不能借 OUTPUT_STAGED 进入候选成功。LOST / FENCED 不要求失联 Node 自报；平台保存 Lease、epoch 与最后观察事实，明确缺失的运行证据。任一已终态 Attempt 的新字节只能进入附加隔离审计。

### 8.4 撤销与暂停状态

永久撤销采用 §10.7 Overlay，不原地改 Snapshot。反向索引覆盖 Package → Bundle → Role / Plan → InputBinding / Attempt → Artifact → CommitBundle → candidate ref，以及模板、镜像、路线、签名 key 的全部反向依赖。

AUTH_REFRESH_REQUIRED 是能力状态而非 Task 终态。只有无关水位推进可在有效 Lease 内 CLEAR 后恢复；命中撤销、Lease 过期或 resourceExecutionEpoch 已提升的 Attempt 不能恢复为 RUNNING。

### 8.5 CandidateStagingOperation

Delivery Service 是唯一状态写入者；Git Stager 只报告签名事实。不可变的 logicalOperationDigest 覆盖 CommitIntent、CommitBundle、仓库、ref、expectedRef 和 operation key。授权刷新记录为追加 AuthorizationBinding，不改 logicalOperationDigest。

| from | to | guard / proofRefs | terminal |
|---|---|---|---|
| — | PREPARED | ACCEPTED Verdict、PreDeliveryEvidence、CommitBundle、GitStagingLease 与当前 DeliveryAuthorization 全部有效 | 否 |
| PREPARED | APPLYING | 身份与当前授权匹配；dispatch intent 已持久化 | 否 |
| PREPARED | EXPIRED / FAILED / CANCELLED | 证明未发送 CAS；分别为授权到期且不再刷新、不可恢复前置失败、取消已接受 | 是 |
| APPLYING | CONFIRMED | 读回 ref = proposed commit，operation key trailer 匹配 | 是 |
| APPLYING | SUPERSEDED | ref 为第三个值，证明非本操作且记录外部漂移 | 是 |
| APPLYING | FAILED | Provider 明确拒绝且读回 / operation journal 证明本操作未生效 | 是 |
| APPLYING | RECONCILING | 超时、ack 丢失、重启或取消并发导致结果不明 | 否 |
| RECONCILING | CONFIRMED / SUPERSEDED / FAILED / CANCELLED | 先读回并证明；CANCELLED 仅限无效果、旧 sender 已 fenced 且取消已接受 | 是 |
| RECONCILING | PREPARED | 证明 CAS 未发生、旧 sender 无在飞动作、新授权有效、同一逻辑操作仍允许重试 | 否 |

APPLYING 不等于成功。首次 expectedRef=null 表示原子创建且 ref 必须不存在；更新使用精确 expectedRef CAS，不能用“先读后无条件写”代替。ref 仍为旧值但 sender 是否在飞未知时，必须保持 RECONCILING；不能仅凭一次读回判断从未发送。

同 key 重试复用 CommitIntent、CommitBundle 和固定提交对象。结果不明时允许在有限策略后生成对账升级事件并通知人工，但 Operation 保持非终态、Task 保持 RECONCILING、相关写权隔离；不得因升级人工而伪造确认。CONFIRMED 后永久保留结果；后续取消 / 删除属于独立操作，不回滚历史状态。

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
9. 本版首期为单租户，不虚构 tenantId 隔离语义；资源归属使用 projectId 与 repositoryId。
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


v1.3.2改变RolePack字段层级、撤销授权语义和Attempt输入绑定，是不兼容的契约修订。本版契约集合统一采用schemaVersion=2.0；HTTP路径与事件payload版本分别为/v2和.v2。文档版本v1.3.2不等于Schema小版本。

旧1.3对象保留历史只读和原签名校验，不通过改版本字符串、补默认字段或静默忽略来升级。离线迁移必须解析旧对象、生成新的Candidate/Binding/当前授权和所需审批，产生新的ID、digest与签名；旧ExecutionPlan/Attempt不在原地续成2.0。没有实际持久数据时从2.0建库；若存在旧运行须先收敛并对账后分批切换。

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

必须签名的对象包括 TaskAdmissionDecision、SourceBundle、DependencyImageSnapshot、RuntimeImageSnapshot、SandboxImageSnapshot、ExecutionImageCompatibilitySnapshot、SkillEvaluation、ApprovalDecision、PolicyApprovalDecision、SkillBundleSnapshot、RolePackSnapshot、ExecutionPlanSnapshot、AttemptContract、AttemptTerminalEnvelope 的 Node 来源证明、ArtifactManifest、CommitIntent、CommitBundle、GateAttestation、SemanticReviewAttestation、EvaluationVerdict、RevocationRecord、DeliveryAuthorization 和GitStagingResult；本版新增InputBindingSnapshot、RuntimeIntegrationSnapshot、RevocationViewCheckpoint、RevocationCheckAttestation、BudgetGrant、TerminalReportGrant、AuthorizationBinding及各运行政策Snapshot也必须签名。

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
sourceKind = INTERNAL_REPOSITORY | ALLOWLISTED_MIRROR | PUBLISHED_ARTIFACT
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

Evaluation / Routing 政策模板、ModelPolicy、SandboxProfile、各类镜像、Compatibility Candidate、SkillBundle Composition 与 RolePackCandidate 使用独立的通用政策审批对象；逐任务 EvaluationPlan / RoutingIntent / ExecutionPlan 实例不逐个审批，不能借用 Skill Package ApprovalSet：

~~~text
PolicyApprovalScopeSnapshot:
policyApprovalScopeSnapshotId
subjectType = EVALUATION_POLICY_TEMPLATE | ROUTING_POLICY_TEMPLATE | MODEL_POLICY | SANDBOX_PROFILE | RUNTIME_IMAGE | SANDBOX_IMAGE | DEPENDENCY_IMAGE | EXECUTION_IMAGE_COMPATIBILITY | SKILL_BUNDLE | GATE_PACK | PROMPT | TOOL_POLICY | ROLE_PACK | WORKFLOW_POLICY_TEMPLATE | RUNTIME_INTEGRATION | INPUT_BINDING_POLICY | LEASE_POLICY | STATE_DEADLINE_POLICY | REVOCATION_FRESHNESS_POLICY | CLOSURE_POLICY | BUDGET_POLICY | OUTCOME_EVIDENCE_PROFILE | PHASE_PROFILE
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

**模板与实例边界（本版适用）：** 本节的 subjectType 一律审批“政策模板/政策对象”，不审批逐任务实例。`EVALUATION_POLICY_TEMPLATE` 的 subject 是 EvaluationPlanTemplate，`ROUTING_POLICY_TEMPLATE` 的 subject 是 RoutingIntentTemplate，GATE_PACK / PROMPT / TOOL_POLICY / WORKFLOW_POLICY_TEMPLATE 的 subject 是对应未含审批引用的不可变政策内容；ROLE_PACK 的 subject 是 §10.5 RolePackCandidate。最终快照包含审批引用时，必须使用先 Candidate 后 Approval 再 Snapshot 的无环结构。由模板与 TaskSpec 确定性编译出的任务实例（EvaluationPlanSnapshot、RoutingIntentSnapshot、ExecutionPlanSnapshot）**不逐任务走 Four-Eyes**，但必须携带 `templateRef / templateDigest`、`compilerId / compilerVersion` 与派生输入 digest，并满足“实例 digest 可重算、引用模板已批准且未撤销、实例不超出模板允许范围”三条件；任一不满足则编译产物视为无效，不得进入 Attempt。GatePack 定义“什么算成功”，属于全系统最安全敏感的政策对象，其审批、quorum 与 veto 必须与其余 subjectType 同样走本节合同，并禁止降低 required Gate 基线。

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

### 10.5 RolePackCandidate 与 RolePackSnapshot

RolePack 的审批对象是 RolePackCandidate；最终 Snapshot 只单向引用 Candidate 与审批结果。构建顺序为 Candidate → Scope → Proposal / Decision → ApprovalSet → Snapshot → Publication，不存在指向未来对象的摘要。

~~~text
RolePackCandidate:
  rolePackCandidateId
  roleId / roleRevision / roleDefinitionDigest
  promptBundleId / promptBundleDigest
  toolPolicyId / toolPolicyDigest
  skillBundleSnapshotId / skillBundleSnapshotDigest
  bundleArtifactDigest / expectedMountedSkillTreeDigest
  inputSchemaDigest / outputSchemaDigest
  runtimeCapabilityPolicyDigest
  defaultModelPolicyRef / defaultModelPolicyDigest
  compatibleRuntimeDrivers[] / compatibleRuntimeVersions[]
  compilerId / compilerVersion / compilerImageDigest
  buildInputsDigest
  allowedTaskClasses[] / allowedEnvironmentScopes[] / allowedDataClassifications[]
  payloadDigest

RolePackSnapshot:
  rolePackSnapshotId
  candidateId / candidateDigest
  resolvedRoleContent
  policyApprovalSetId / policyApprovalSetDigest
  approvalDecisionIds[] / approvalDecisionDigests[]
  payloadDigest / signature

RolePackPublication:
  publicationId / rolePackSnapshotId / rolePackDigest
  environmentScope / state / rowVersion
~~~

resolvedRoleContent 包含 Candidate 除 rolePackCandidateId 与 payloadDigest 外的全部字段；引用对象摘要、允许能力、输入输出 Schema 和适用范围均进入两者各自的 DigestProfile。Snapshot 的审批摘要必须进入自身 payload；不得为消环而从 payload 隐式排除审批或安全字段。

subjectType=ROLE_PACK 的 PolicyApprovalScope、Decision、Set 的 subjectId / subjectDigest 必须等于 Candidate ID / payloadDigest；subjectConstraintsDigest 绑定同一 Candidate。新 Candidate 的 baselineSubject 指向前一批准 Candidate，而不是前一最终 Snapshot。初次创建 baseline 显式为 null。

Publisher 校验 Candidate 内容、审批主体、签名 Decision、身份独立性、quorum / veto、有效期、环境和最新撤销依赖闭包；全部通过后发布 Snapshot。输入内容变化必须产生新 Candidate、新审批和新 Snapshot。Snapshot ID / digest 不出现在 Candidate、Scope 或 Decision 中。

Role Pack 与 Skill、Prompt、ToolPolicy 各有独立身份及撤销入口；变更其中任一依赖不会自动替换已有 RolePackSnapshot 内的引用。

#### 10.5.1 ExecutionPlanSnapshot

Task 进入 EXECUTING 前必须发布一个不可变、签名的 ExecutionPlanSnapshot，避免“状态已执行、输入仍在补齐”：

~~~text
executionPlanSnapshotId
taskId / taskSpecDigest
workflowTemplateId / workflowTemplateDigest
compilerId / compilerVersion / compilerImageDigest
derivedInputsDigest
workflowSnapshotId / workflowDigest
evaluationPlanId / evaluationPlanDigest
sourceBundleId / sourceBundleDigest
plannedAttemptInputs[] = {
  plannedAttemptInputId / workflowNodeId
  runKind = IMPLEMENTATION | REVIEW | READ_ONLY
  deliverableKind = CODE_CHANGE | REVIEW_EVIDENCE | READ_ONLY_EVIDENCE
  inputBindingPolicyId / inputBindingPolicyDigest
  upstreamBindings[] = {slotId, producerWorkflowNodeId, allowedArtifactType, selectionRule, required}
  runtimeIntegrationSnapshotId / runtimeIntegrationSnapshotDigest
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


本版不要求在计划编译时预测上游 ArtifactManifest digest。upstreamBindings 固定生产节点、选择规则、类型和只读约束；实际输出只可由 §10.5.2 的 InputBindingSnapshot 后绑定。没有上游依赖时也生成仅含 SourceBundle 的初始绑定；空值不代表绕过校验。

INITIAL 的 repair 专属字段均显式为 null；REPAIR 的 allowedChangeSet 默认仅允许 REPAIR_INSTRUCTION 和父计划内 ATTEMPT_ROUTE_SELECTION。Repair 另绑定 parentOutputManifestDigest 与失败证据，作为修复输入，不修改原 SourceBundle。预算 reservation 的新分片只能来自原 Task 上限内未分配额度，不得通过新计划重置累计消费。

#### 10.5.2 InputBindingSnapshot 与 RequiredRunBinding

~~~text
InputBindingSnapshot:
  inputBindingSnapshotId
  taskId / runId / attemptId
  executionPlanDigest / plannedAttemptInputDigest
  inputBindingPolicyDigest
  sourceBundleDigest
  entries[] = {
    slotId, producerRunId, producerAttemptId,
    artifactManifestId, artifactManifestDigest,
    artifactType, mountMode=READ_ONLY
  }
  boundBy / boundAt / payloadDigest / signature

RequiredRunBinding:
  taskId / workflowNodeId / activeRunId
  supersedesRunId / repairRound
  rowVersion / updatedBy
~~~

Input Binding Service 位于 Domain 1。Attempt ID 在 CREATED 阶段分配；上游输出已 PUBLISHED、Run 选择已冻结、摘要和来源验证通过后，服务按计划中的规则生成绑定并使 Attempt 可 READY。绑定不含 attemptContractDigest，最终 AttemptContract 单向引用它，避免摘要环。初始 SourceBundle 槽位的 producerRunId / producerAttemptId 为 null，其余槽位均必填。

同一 slot 必须唯一，禁止 Runtime 提交任意路径或自行选择 producer。依赖缺失保持 BLOCKED；依赖不可恢复失败则 FAILED_DEPENDENCY。Reviewer 的输入绑定使用实现 Run 已选中的输出，不要求实现 Run 已通过包含 Reviewer 的最终 Verdict，以免评审等待自身完成。

RequiredRunBinding 由 Lifecycle 唯一 CAS 写入，保存当前有效 Run。Task 验收只检查当前绑定与固定必需依赖；历史 SUPERSEDED Run 保留证据但不再要求 VERIFIED。Review 使用 REVIEW_EVIDENCE，只做 Schema、来源、路线与完整性验证，不生成 CommitBundle，也不再创建下一级 Reviewer。Workflow 编译器拒绝循环依赖与 REVIEW → REVIEW 的递归审查。

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
runtimeIdentity = {runtimeName, runtimeVersion, runtimeSourceDigest}
inputBindingSnapshotId / inputBindingSnapshotDigest
inputArtifactManifestRefs[]
runtimeIntegrationSnapshotId / runtimeIntegrationSnapshotDigest
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

executionLeaseBinding 包含 leaseId、ownerInstanceId、controlPlaneEpoch、resourceExecutionEpoch、revocationEpoch 与 leasePolicyDigest。两处 revocationEpoch 必须等于 ExecutionPlanSnapshot 的编译准入基线；该历史字段不随撤销日志推进而变化。Node 启动或续用能力时另验证 §10.7 当前 RevocationCheckAttestation 和短授权；它们不进入 AttemptContract payload。

inputArtifactManifestRefs 必须等于 InputBindingSnapshot 中规范排序的引用集合，且 Task / Run / Attempt、SourceBundle、plannedAttemptInputDigest 精确匹配。RuntimeIntegrationSnapshot 与 runtimeIdentity、RuntimeImage 和 Compatibility 必须一致。任何绑定差异均拒绝启动，不能用实时目录或最新输出替换。

ExecutionLeaseBinding 的 resourceExecutionEpoch 是该 Attempt 的执行代次；实际 fencing 或失联后不得复活同一 Attempt。无关撤销仅刷新短授权，不提升该资源执行代次。controlPlaneEpoch 变化的恢复按 Lease / Fencing 执行，不视为无关撤销刷新。

合同不保存明文能力令牌。控制面向Node签发受audience/action约束的LeaseGrant，再按调用取得GatewayGrant；TTL取Lease剩余有效期与RevocationFreshnessPolicy动作上限的较小值。Attempt与Runtime不可见。正常续租或无关撤销刷新只生成合同外新Grant，不改变AttemptContract。

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

**AttemptContract字段权威：** §10.6为本版唯一字段模型，附录B按此模型展示。正式实现必须把字段类型、null规则、枚举、长度和未知字段拒绝规则写成同一个Schema2.0，并从它生成夹具；本文的字段清单本身不等于已存在的机器Schema。禁用frozenRefs等第二套层级和分域撤销水位。当前Grant与RevocationCheckAttestation在合同外绑定同一contractDigest，不能写入合同形成循环或运行中改写。

### 10.7 撤销记录、完整视图与当前授权

#### 10.7.1 唯一撤销日志

RevocationRecord 保留 revocationId、subjectType、subjectId、subjectDigest、reasonCode、reason、revocationEpoch、effectiveControlPlaneEpoch、effectiveAt、createdBy、securityTicketRef、payloadDigest、signature。任意撤销在同一 PostgreSQL 事务写记录、递增唯一全局 revocationEpoch 并写 Outbox。禁止 skillEpoch / routeEpoch / credentialEpoch 等分域日志水位。

subjectType 闭集为：

~~~text
WORKFLOW_POLICY_TEMPLATE | EVALUATION_POLICY_TEMPLATE | ROUTING_POLICY_TEMPLATE |
WORKFLOW_SNAPSHOT | EVALUATION_PLAN_SNAPSHOT | EXECUTION_PLAN_SNAPSHOT |
ROUTING_INTENT_SNAPSHOT | ATTEMPT_ROUTE_SNAPSHOT | MODEL_POLICY_SNAPSHOT |
SANDBOX_PROFILE_SNAPSHOT | GATE_PACK_SNAPSHOT | ROLE_PACK_CANDIDATE |
ROLE_PACK_SNAPSHOT | PROMPT_SNAPSHOT | TOOL_POLICY_SNAPSHOT |
SKILL_PACKAGE_VERSION | SKILL_APPROVAL_DECISION | SKILL_APPROVAL_SET |
POLICY_APPROVAL_DECISION | POLICY_APPROVAL_SET | SKILL_BUNDLE_SNAPSHOT |
SOURCE_BUNDLE | DEPENDENCY_IMAGE_SNAPSHOT | RUNTIME_IMAGE_SNAPSHOT |
SANDBOX_IMAGE_SNAPSHOT | EXECUTION_IMAGE_COMPATIBILITY_SNAPSHOT |
RUNTIME_INTEGRATION_SNAPSHOT | INPUT_BINDING_SNAPSHOT |
CREDENTIAL_PROFILE | SIGNING_KEY | INPUT_BINDING_POLICY | LEASE_POLICY |
STATE_DEADLINE_POLICY | REVOCATION_FRESHNESS_POLICY | CLOSURE_POLICY |
BUDGET_POLICY | OUTCOME_EVIDENCE_PROFILE | PHASE_PROFILE
~~~

subjectId 与 digest 的绑定必须可从可信 Registry 重算；对于凭据配置与签名 key，digest 指配置 / 公钥描述符，不能包含秘密字节。未知类型、缺记录和无法构造依赖闭包一律拒绝。

#### 10.7.2 冻结基线与新鲜授权

ExecutionPlan / AttemptContract 的 revocationEpoch 记录不可变的历史准入基线，不能直接作为后续每次调用的当前授权。新增两个签名对象：

~~~text
RevocationViewCheckpoint:
  checkpointId / controlPlaneEpoch
  headRevocationEpoch / completeLogDigest
  issuedAt / expiresAt / freshnessPolicyDigest
  payloadDigest / signature

RevocationCheckAttestation:
  revocationCheckAttestationId
  subjectType / subjectId / subjectDigest
  action / audience / resourceId
  dependencyClosureDigest / closurePolicyDigest
  checkedRevocationEpoch / checkpointDigest
  matchedRevocationIds[] / result = CLEAR | REVOKED
  checkedAt / expiresAt
  controlPlaneEpoch / payloadDigest / signature
~~~

Revocation Service 是唯一签发者；签发前根据受信任引用字段递归构造依赖闭包，至少包括模板、计划、Role / Prompt / Tool / Skill、审批 Decision / Set、Source、RuntimeIntegration、镜像 / Compatibility、输入绑定、路线 / 凭据以及相关签名公钥描述符。闭包不是调用方自报清单；缓存按根摘要与 closurePolicyDigest 寻址，新增未知引用类型必须拒绝而非跳过。临时 Grant / 本次检查证明不回填被检查对象，防止摘要自引用。

短 LeaseGrant、GatewayGrant、GitStagingLease、DeliveryAuthorization、PublicationAuthorization 的 revocationEpoch 表示当前动作检查水位，必须绑定 revocationCheckAttestationDigest。接收方同时验证：

1. 本地有签名有效且未过期的 RevocationViewCheckpoint，已经连续应用日志至该 checkpoint 的 head，无缺号、无回退；
2. localCompleteEpoch = checkpoint.headRevocationEpoch = attestation.checkedRevocationEpoch = actionGrant.revocationEpoch；
3. 该水位不低于冻结合同的历史基线；CLEAR、对象摘要、资源、动作、audience、闭包、Lease 和签名均正确；
4. 本地已观察到更高水位时，旧检查证明立即失效；先补日志并重检查，再签新授权，不能用旧 checkpoint 倒退视图；
5. 网络断开或未收到新 head 只能在已签名新鲜度窗口内处理，不能声称实时全局一致；checkpoint、检查证明和 action Grant 的有效期均不得超出 RevocationFreshnessPolicy 的同一上界。传播过期立即暂停新动作。

RevocationFreshnessPolicy 初值 maxViewAgeSeconds=30、maxActionGrantSeconds=30、clockSkewToleranceSeconds=2；到期采用扣除偏差裕量的保守校验，禁止因容忍时钟偏差延长有效期。需变更必须审批和重新验证。已在飞请求无法撤回的外部效果按不确定调用 / Git 对账处理；不得伪造“撤销瞬时消除了已发动作”。

#### 10.7.3 命中、无关推进与恢复

| 情形 | 控制面 / Node / Gateway | Gate / Git / Publisher |
|---|---|---|
| 闭包命中永久撤销 | 拒绝新能力，提升相关执行 fencing，停止进程并隔离相关输出；旧 Attempt 不复活 | 停止相关 Gate；拒绝新写入 / 发布；已发 Git 操作先对账并保留 Containment |
| 水位推进但闭包 CLEAR | 暂停受保护新动作，刷新检查与短 Grant；不重写合同，不重跑已完成调用，不取消无关 Task | 对精确原 Gate / CommitBundle / 操作重新检查，CLEAR 后更新授权并继续 |
| 日志缺号或 checkpoint 过期 | 先同步；在冻结 pauseGraceSeconds 内恢复则续行，超限终止并形成明确失败证据 | Gate 保留事实；Git 已发操作保持 RECONCILING，未发操作可安全过期 |
| SigningKey 撤销 | 拒绝受影响信任链；不能凭自报 issuer 绕过 | 反向索引定位、隔离并走独立重签 / 发布；不把旧签名改写为有效 |

pauseGraceSeconds 在 LeasePolicySnapshot 冻结，初值 60；暂停不延长 Lease。暂停期间只能续租、同步、停止和封存，不得模型调用或 Git 写入。实际 Lease 已过期 / 被 fence 时，即使撤销检查 CLEAR，也只能创建新 Attempt；不能借授权刷新复活旧所有者。

只有无关撤销可在内容不变时刷新授权。若对象本身被永久撤销，恢复必须有新内容摘要及适用审批；与候选交付相关的撤销还要重新验证产物，不能仅重签 DeliveryAuthorization。临时暂停使用 ContainmentRecord；解除 Containment 不删除永久撤销。

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
runtimeIntegrationCandidateDigest
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
revocationCheckAttestationDigest
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
revocationCheckAttestationDigest
issuedAt / notBefore / expiresAt
issuerId / signingKeyId / payloadDigest / signature

CandidateStagingOperation:
candidateStagingOperationId
logicalOperationDigest
authorizationBindingIds[]
gitStagingLeaseId / gitStagingEpoch
deliveryAuthorizationId / deliveryAuthorizationDigest
repositoryId / candidateRef / expectedRefGitObjectId
commitBundleDigest / verdictDigest / preDeliveryEvidenceDigest
operationIdempotencyKey
state = PREPARED | APPLYING | RECONCILING | CONFIRMED | SUPERSEDED | FAILED | EXPIRED | CANCELLED
createdAt / updatedAt / rowVersion
~~~

`operationIdempotencyKey` 在 Gate 前由不可变 CommitIntent 分配，并作为 `X-Platform-Operation-Key` trailer 进入规范化提交元数据，因此参与 proposedCommitGitObjectId 计算和 Gate 验证。CandidateStagingOperation 只在 ACCEPTED Verdict、PreDeliveryEvidence、独立 GitStagingLease 与签名 DeliveryAuthorization 全部存在后创建为 PREPARED，不承载任何“未来才产生”的空字段。

**candidate ref CAS 语义（本版适用）：** `expectedRefGitObjectId` 为空表示“该 candidate ref 当前必须不存在”，首次创建时审核空值 CAS（ref 不存在 + 创建操作原子化）；非空则必须携带当前读回 commit（Git Stager 在操作前读回并比对）。**多轮 Repair 的 candidate ref 更新策略**：Verdict 形成前的 Repair（Repair Run 内重执行）不触碰远端 candidate ref，全部修改停留在新的 CommitBundle 与输出 Manifest；Verdict 已 ACCEPTED 并首次交付成功后，任何后续修改必须创建新Task，由新Task授权以当前读回commit为CAS基线的独立更新操作，不得在候选 ref 上追加未授权提交。`CloseCandidateRef` 只在平台创建的 review request 确实存在时关闭关联 PR（见 §3.5）。DeliveryAuthorization 的 audience、精确 Stager workload identity、Lease owner、仓库、ref、精确 Git 对象、Verdict、证据、controlPlaneEpoch、gitStagingEpoch、全局 revocationEpoch、动作和 TTL 必须逐项匹配；它不是可转用的 bearer token，只授权该受信任服务身份上传已验证 Git 对象并 CAS 指定 candidate ref，不授权 Git Stager 重建树、修改提交元数据、运行 Gate、写其他 ref 或使用 Attempt ExecutionLease。

---


AuthorizationBinding作为追加记录保存bindingId、candidateStagingOperationId、logicalOperationDigest、deliveryAuthorizationId/digest、gitStagingLeaseId/epoch、revocationCheckAttestationDigest、supersedesBindingId、createdAt和签名。PREPARED下无关撤销刷新可CAS切换currentBindingId；APPLYING/RECONCILING必须先确认旧发送状态，只有证明未生效且原发送者fenced后才能切换新发送授权。CONFIRMED不再重发，最终证据记录实际使用的binding。

## 11. v1.3.2 契约关联清单

下列字段与本版模型共同校验；新关联对象和安全语义采用Schema2.0，不通过旧版未知字段忽略机制兼容：

| 对象 | 当前必填关联 |
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

这些错误只能使Run进入NO_VERDICT，或使受影响非终态聚合进入QUARANTINED；已终态对象追加Containment并隔离其产物，不改写历史终态；不得降级为 Warning 或成功。

### 11.1 AttemptTerminalEnvelope与终态证据边界

Node信封分别报告Runtime观察、宿主退出/信号/资源/进程树静默、实际挂载摘要、InputBinding、输出Manifest、未完成动作、stopReason和不可确认副作用。信封不包含权威PASS，不得覆盖控制面的取消、预算或撤销事实。

OutcomeEvidenceProfile是冻结的政策对象，至少含profileId、outcomeClass、requiredEvidenceTypes、requiredFields、allowedMissingWithReason、closureRules与payloadDigest。类型为SUCCESS_COMPLETE、FAILURE_NODE_REPORTED、FAILURE_PLATFORM_PROOF、CANCELLED_CONFIRMED、DELIVERY_RECONCILING。

SUCCESS_COMPLETE不允许缺失终态、输出、Gate、Verdict或Git确认。FAILURE_PLATFORM_PROOF允许失联Node的日志和信封缺失，但必须包含DB时钟、Lease/epoch、最后心跳、fencing决定和missingEvidenceReasons。其他profile按实际动作检查证据；均不能升级为成功。

TerminalReportGrant包含grantId、attemptId、attemptContractDigest、nodeWorkloadIdentity、terminalReportGeneration、stopRequestId、stopReason、allowedActions、maxEvidenceBytes、notBefore、expiresAt、controlPlaneEpoch、payloadDigest和signature；无stop请求的正常封存使用明确NORMAL_COMPLETION授权。该Grant不能被Gateway或Git Stager接受，签发和撤销由Lifecycle管理，报告expiry不得延长业务执行期限。

StateDeadlinePolicy包含policyId、aggregateType、state、maxResidenceSeconds、maxLocalRetries、retryableReasonCodes、onExhaustedTransition、requiredProofProfile、payloadDigest与signature。Node清理和Artifact封存时限是独立技术限制，不能用ExecutionLease到期推断上传成功。对账超过等待窗口只产生升级事件并保持未决，不强行映射成功或已知失败。

## 12. API 蓝图

### 12.1 传输约定

- 人工、CI 和管理入口：HTTP / JSON，前缀为 /api/v2 与 /governance/v2；
- 服务间：mTLS gRPC；
- Node 与 Runtime Driver：仅使用 Sandbox Supervisor 创建并继承给 Runtime 主进程的专用 stdio / 匿名管道；启动任何仓库子进程前关闭对应描述符并明确禁止继承。MVP 禁止 UDS、TCP 和本地 gRPC；未来若启用，必须新增 ADR、威胁模型、FD 隔离证明与逃逸反例验收；
- 事件：PostgreSQL Transactional Outbox，MVP 由 Outbox Poller 投递；
- Blob：Node 经 Artifact Service 上传，Attempt 不持有对象存储凭据；
- 身份：mTLS / OIDC 工作负载身份，正文自报 actorId 不参与授权。

所有修改请求携带 Idempotency-Key、traceparent 与 schemaVersion。可变资源增加 If-Match 和 expectedState；执行副作用增加 leaseId、fencingToken、capabilityToken 与 operationIdempotencyKey。

### 12.2 控制面、Node与当前授权API

| API | 调用方 | 校验与结果 |
|---|---|---|
| POST /api/v2/tasks | 用户/CI | 写TaskSpec与TaskAdmissionDecision；Lifecycle异步创建Task，返回admissionId和taskId，不伪装Task已执行 |
| POST /api/v2/tasks/{id}:cancel | 用户/控制面 | If-Match与副作用阶段；接受则创建cancelRequestId并关执行权；已发或未知写入返回409 SIDE_EFFECT_RECONCILIATION_REQUIRED |
| POST /internal/v2/input-bindings:resolve | Input Binding Service | 计划槽位、选定输出和PUBLISHED证明；生成不可变InputBindingSnapshot |
| POST /internal/v2/nodes/{id}:claim-attempt | Node | 既有READY Attempt；单事务CAS、Lease、Assignment和最终合同，不再创建Run/Route |
| POST /internal/v2/leases/{id}:renew | Lease owner | 当前owner/epoch/DB时间；同步拒绝命中撤销，签新LeaseGrant不改合同 |
| POST /internal/v2/revocation-checks:issue | 受信任授权服务 | 从Registry构造闭包，签当前checkpoint与检查证明；拒绝Runtime自报CLEAR |
| POST /internal/v2/attempts/{id}:mark-provisioning | Node | Lease/Fencing与当前撤销授权 |
| POST /internal/v2/attempts/{id}:mark-running | Node | 冻结绑定、实际挂载、隔离事实与CAS |
| POST /internal/v2/attempts/{id}:request-stop | Lifecycle | 关闭执行权、冻结stopReason、向原owner签TerminalReportGrant |
| POST /internal/v2/attempts/{id}:report-terminal | Node | Node签名、正常执行证明或TerminalReportGrant、报告代次、大小与CAS；不能改写已终态 |
| POST /internal/v2/attempts/{id}:force-terminal | Lifecycle | DB/Lease/fencing/预算/安全事实；按§8白名单收敛 |
| POST /internal/v2/runs/{id}:select-output | Lifecycle | 输出已发布且未接受取消、RequiredRunBinding和CAS |

准入与claim在PostgreSQL事务内校验当前撤销日志水位和依赖禁用状态；撤销事务先于准入事务提交时，不得批准命中依赖的新Attempt。已在撤销前签发的短授权按§10.7传播窗口处理，不能宣称物理启动瞬时零延迟阻断。Node在实际启动前再次检查当前有效授权。

禁止通用set-state API。claim事务内不调用Gateway、Git、对象存储或Runtime；所需输入先seal并发布。签名使用预先本地可用的受限签名能力，若必须调用远端Signer，则采用独立CLAIM_PREPARATION记录准备合同，最终原子CAS校验全部预置结果后才向Node返回Assignment；不得以远端I/O拖住claim事务或发布半成品合同。

### 12.3 Skill 治理 API

| API | 作用 |
|---|---|
| POST /governance/v2/skill-package-versions | 从内部或 allowlist 来源创建不可变版本 |
| POST /governance/v2/skill-package-versions/{id}:evaluate | 调度离线评估 |
| GET /governance/v2/skill-package-versions/{id}/evaluations | 查看评估和证据 |
| POST /governance/v2/skill-approval-proposals | 创建审批提案 |
| POST /governance/v2/skill-approval-proposals/{id}:record-decision | 功能或安全 Approver 写一个独立签名 Decision；服务机械计算 quorum / veto |
| POST /governance/v2/skill-revocations | 紧急撤销对象 |
| POST /governance/v2/skill-bundles:build | 确定性构建 Bundle |
| POST /governance/v2/skill-publication-pointers/{scope}:advance | 按环境作用域 CAS 指向已签名 Snapshot |
| POST /governance/v2/role-packs:compile | 绑定 Role、Prompt、Tool 与 Skill |
| POST /governance/v2/role-packs/{id}:publish | 发布 Role Pack |
| POST /governance/v2/policy-approval-proposals | 为Evaluation政策模板、Routing政策模板、ModelPolicy、SandboxProfile、RuntimeImage、SandboxImage、DependencyImage、ExecutionImageCompatibility、SkillBundle组合、RolePackCandidate、RuntimeIntegrationCandidate及本版运行政策创建带不可变Scope的通用审批提案 |
| POST /governance/v2/policy-approval-proposals/{id}:record-decision | 写独立签名 Decision；降低基线时强制额外 Security Approver |

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
| POST /internal/v2/source-bundles:ingest | Task / Source Service | 只读仓库 allowlist、精确 ref、数据等级、幂等键 | 签名 SourceBundle |
| POST /governance/v2/dependency-images:build | Dependency Builder | 锁文件、基础镜像、构建策略、MicroVM 身份 | 候选 DependencyImageSnapshot |
| POST /governance/v2/dependency-images/{id}:publish | Dependency Publisher | SCA、SBOM、provenance、审批、签名 | 发布不可变 digest |
| POST /governance/v2/execution-images:build | Runtime / Sandbox Image Builder | imageType、冻结来源、构建策略、受控构建器身份 | 未签名 RuntimeImageSnapshot / SandboxImageSnapshot |
| POST /governance/v2/execution-images/{id}:publish | Execution Image Publisher | PolicyApprovalSet、SBOM、SCA、provenance、兼容矩阵、独立签名 | 发布精确镜像 digest |

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

方法：InvokeModel、CancelInvocation、GetInvocationStatus、GetRouteAttestation、SettleUsage。仅Node Runtime Proxy的工作负载身份可调用，Runtime不持有任何Grant。

InvokeModel携带invocationId、requestSequence、requestDigest、AttemptContract、AttemptRouteSnapshot、InputBinding摘要、LeaseGrant、GatewayGrant、当前RevocationCheckAttestation、BudgetGrant与指定gatewayGeneration。Gateway按§13.3验权并按§18.3先持久预留再发送。

GetInvocationStatus对已结算返回固定结果，对DISPATCH_INTENT/UNKNOWN返回未决状态及可安全采取的查询动作，不把请求重试等同第二次Provider发送。CancelInvocation只发取消意图，不代表供应商已停止计费；未确认消耗仍占额。

实际provider/model/thinking与冻结路线不一致时停止调用、生成ModelRouteMismatchDetected并进入隔离/NO_VERDICT。禁止透明fallback；新模型路线只能来自冻结计划的候选并通过新Attempt使用。

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
- ref为不同于expected和proposed的第三个值且Operation Key不匹配：SUPERSEDED；
- ref仍为expected值且发送状态不明，或无法确认：RECONCILING；只有同时证明旧sender已fenced且无在飞请求才可安全重试。

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

### 13.3 Epoch、Fencing 与授权刷新

fencingToken = (controlPlaneEpoch, resourceExecutionEpoch)。外部能力代理逐请求校验签名、主体、audience、action、TTL、resource / Attempt / Lease、请求摘要及双 epoch。双 epoch 必须等于受信任控制面已同步的当前资源代次；高于本地先同步，低于则拒绝。

撤销检查独立遵循 §10.7：历史 AttemptContract.revocationEpoch 只是基线；当前检查证明和动作 Grant 的 revocationEpoch 必须与本地完整 checkpoint 水位相等。禁止把历史合同值与最新日志直接比较后无差别 fence 全部任务，也禁止仅以“local >= baseline”省略依赖闭包检查。

模型热路径不查询 PostgreSQL；只检查本地有效 Lease / GatewayGrant、撤销证明与持久消费状态。当前 checkpoint 过期、日志缺号、授权未刷新时停止新调用；同步完成后仅无关依赖可刷新继续。

Execution capability 与 TerminalReport capability 分离。取消 / 撤销立即禁止新增执行动作，同时可向原 Node 签发只允许 Stop、CollectTerminalEnvelope、SealFailureEvidence 的 TerminalReportGrant；它绑定 attemptContractDigest、原 owner、terminalReportGeneration、cancelRequestId / stopReason、大小上限与独立 expiry，不允许模型调用、正常 Manifest 发布、选择输出或 Git 写入。终止报告不要求旧 ExecutionLease 仍能执行，但必须验证当前报告授权、Node 来源与状态 CAS；原 Node key 被撤销时不接受其新签名为权威，由 Lifecycle 的平台事实路径收敛。

旧执行代次的普通事件只进入隔离审计。已有合法 TerminalReportGrant 的报告可在 TERMINATING 接收；聚合终态一旦写入，后来的报告不能逆转状态，只能附加审计证据。取消与成功并发由 Lifecycle 的状态 / rowVersion / cancelRequest CAS 决胜，成功发布守卫必须检查未接受取消。

### 13.4 通用事件信封

~~~json
{
  "schemaVersion": "2.0",
  "eventId": "0199a001-93d1-7abc-8d11-001122334455",
  "eventType": "ArtifactManifestPublished.v2",
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
SkillPackageVersionIngested.v2
SkillEvaluationCompleted.v2
SkillApprovalGranted.v2
SkillApprovalRejected.v2
SkillRevoked.v2
SkillBundleBuilt.v2
SkillBundlePublished.v2
SkillBundleRevoked.v2
RolePackPublished.v2
RevocationWatermarkAdvanced.v2
SourceBundlePublished.v2
DependencyImagePublished.v2
~~~

**执行与交付**

~~~text
TaskAdmissionDecided.v2      # Task API 发布（唯一发布者）
TaskAccepted.v2              # Lifecycle 消费 TaskAdmissionDecided 后发布（唯一发布者）
TaskRejected.v2              # Lifecycle 发布（唯一发布者）
RunReady.v2
AttemptClaimed.v2
AttemptProvisioningStarted.v2
AttemptRunning.v2
AttemptTerminalReported.v2
ArtifactManifestPublished.v2
AttemptOutputStaged.v2
RunOutputSelected.v2
GateAttestationPublished.v2
SemanticReviewAttestationPublished.v2
VerdictComputed.v2
CommitBundlePublished.v2
DeliveryAuthorizationIssued.v2
CandidateRefStaged.v2
GitStagingResultPublished.v2
TaskCandidateDelivered.v2
~~~

**故障与隔离**

~~~text
LeaseExpired.v2
AttemptFenced.v2
AttemptLost.v2
SkillBundleDriftDetected.v2
ModelRouteMismatchDetected.v2
EvidenceIncomplete.v2
ArtifactQuarantined.v2
CandidateRefReconciliationRequired.v2
GlobalStopActivated.v2
~~~

事件名表示已经发生的事实。RunGate、MergeCode 等是命令，不能伪装为事件。


### 13.6.1 v1.3.2新增事件及唯一发布者

| 事件族（payload版本.v2） | 唯一发布者 |
|---|---|
| RolePackCandidateCompiled / RuntimeIntegrationCandidateEvaluated | 对应Compiler / Evaluator |
| InputBindingPublished | Input Binding Service |
| RequiredRunBindingChanged / TaskRepairPlanning / RunCommitAssemblingStarted | Lifecycle |
| ExecutionStopRequested / TerminalReportGrantIssued | Lifecycle |
| RevocationViewCheckpointIssued / RevocationCheckCompleted | Revocation Service |
| BudgetGrantIssued / BudgetGrantFenced / BudgetSettlementConfirmed | Ledger Service |
| InvocationReserved / InvocationDispatchIntentRecorded / InvocationBecameUnknown | 绑定Gateway代次 |
| CandidateAuthorizationBound / CandidateStagingExpired / CandidateStagingFailed | Delivery Service |

§8的每次迁移还产生TransitionRecorded.v2，包含aggregateType/id、from/to、旧/新rowVersion、reasonCode、proofRefs、cancelRequestId（不适用为null）与唯一writerIdentity。领域事件与TransitionRecorded引用同一事务事实，不成为第二个状态权威。正式事件manifest按状态表和发布者矩阵校验完整性。

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
SIDE_EFFECT_RECONCILIATION_REQUIRED
PROFILE_FEATURE_DISABLED
INPUT_BINDING_INVALID
RUNTIME_CAPABILITY_UNSATISFIED
REVOCATION_VIEW_STALE
REVOCATION_REFRESH_REQUIRED
BUDGET_GRANT_OWNER_MISMATCH
BUDGET_JOURNAL_UNAVAILABLE
INVOCATION_OUTCOME_UNKNOWN
TERMINAL_REPORT_ONLY
SCHEMA_VERSION_UNSUPPORTED
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

1. Task API校验任务范围并写准入决定；Lifecycle消费TaskAdmissionDecided创建ACCEPTED/REJECTED Task。
2. Source Ingestor冻结仓库输入；Registry提供已批准镜像、Role/Skill、模板和政策。
3. Workflow Compiler编译DAG与EvaluationPlan实例；Route Resolver冻结候选路线；Execution Plan Compiler发布完整ExecutionPlan。Lifecycle创建Run与RequiredRunBinding。
4. Orchestrator经Attempt Service创建CREATED Attempt；Input Binding Service绑定已发布Source及计划允许的上游输出，齐备后READY。
5. Node claim原子创建Lease/Assignment/合同，验证当前撤销检查、RuntimeIntegration及镜像Compatibility后启动断网Runtime。
6. 模型意图由Node代理；Gateway检查当前执行授权，按持久预算reservation发送，响应回到同一Runtime调用序号。
7. 正常完成时Node封存终态事实与制品；Lifecycle检查未取消且证据完整后OUTPUT_STAGED，按Run CAS选择输出。失败走对应失败证据profile。
8. CODE_CHANGE Run进入COMMIT_ASSEMBLING；Delivery Service分配CommitIntent，Assembler组装固定Git对象并签名发布，然后Run进入VERIFYING。
9. Gate Supervisor在独立沙箱对精确CommitBundle执行Gate。Phase2只运行确定性Gate；Phase3需要的Review Run按InputBinding读取选定输出，只生成结构化非权威证据。
10. Verdict按冻结规则形成；ACCEPTED才生成PreDeliveryEvidence，取得当前撤销检查与独立GitStagingLease/DeliveryAuthorization，创建候选操作。
11. Delivery Service持久化dispatch intent并推进APPLYING；Stager上传精确对象并CAS候选ref，读回对象与operation key。
12. 已知拒绝映射明确失败；结果未知进入RECONCILING。仅CONFIRMED且最终Evidence Bundle完整、无已接受取消/隔离时，Lifecycle写Task SUCCEEDED。

### 14.3 有界修复与多角色依赖

Phase2禁用Repair和模型Review；REPAIRABLE映射HANDOFF_TO_HUMAN，保留失败证据。Phase3启用时遵循以下规则：

1. Verdict前的基础设施重试使用原Run的RETRY_WAIT→READY与新Attempt，只能选原ExecutionPlan已经冻结的路线；不能改变成功条件或补入未计划模型。
2. 首轮REPAIRABLE使实现Run进入REPAIR_REQUIRED，Task进入REPAIR_PLANNING；此时才开始生成新REPAIR计划，不能把“计划已生成”作为进入规划状态的条件。
3. 新计划绑定父plan/Run、触发Verdict、失败集合、failureFingerprint、repairInstructionDigest和parentOutputManifestDigest；只允许§10.5.1的变化集合。原SourceBundle不变，修复在父输出副本上应用新变更；CommitBundle相对冻结Source构建完整累计树。
4. Lifecycle创建Repair Run并CAS更新RequiredRunBinding；旧Run进入SUPERSEDED，保留全部历史证据。并发Repair争夺同一绑定时只有一个胜者，其余不得执行。
5. 修复Run的输入由InputBinding Service绑定，不能由Reviewer或Runtime自行挑选最新输出。每一轮创建新的Review Run绑定当前轮输出；上一轮Review不可用于新的CommitBundle。
6. Review Run只经过类型/Schema/来源/路线/证据验证终结，不产生Git候选，不为自己再创建Review。最终实现Verdict消费其证据，不赋予模型成功写权。
7. 总计1次初始+最多2轮Repair；所有轮次共享Task累计预算和固定Gate。验证目标、Source、安全边界变化创建新Task，不伪装Repair。
8. 父输出不可验证则NO_VERDICT/QUARANTINED；计划发布失败、轮次或资源耗尽则按§8终结。任何已交付成功后的代码修改创建新Task；不得回开旧Task终态。

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

| 编号 | 威胁 | 本版控制 |
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
| Node Agent / Runtime Proxy | claim / heartbeat / Artifact / Gateway 短 Grant；**工作负载签名 key（仅用于签署 Node 来源的 AttemptTerminalEnvelope 证明，见 §9.4；纳入轮换、反向索引与 SIGNING_KEY 撤销体系）** | Git Remote、Skill 发布、Verdict 签名、唯一业务终态写入 |
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
| Revocation Service | 当前日志/checkpoint读取、闭包构建与撤销检查签名 | 修改被检查对象、执行仓库代码、放行缺号日志 |
| Input Binding Service | 计划槽位与已发布产物读取、绑定签名 | 自选计划外producer、写Runtime工作区 |
| Ledger Service | 父级额度、Grant分配与结算签名 | 发起Provider调用、退款未知消费 |
| Gateway消费代次 | 自身Grant与持久Journal、Provider受控调用 | 使用其他代次Grant、丢日志后复原余额 |
| Lifecycle报告授权 | 签发TerminalReportGrant与更新报告代次 | 授权报告Grant执行模型/Git动作 |

能力令牌不得进入日志、Artifact、Session Checkpoint 或终态信封。可执行短能力令牌携带audience、actions、subject、epoch、notBefore/expiresAt；BudgetGrant另用validFrom/expiresAt与消费owner，不能被当作执行授权。公开证据仅保存Grant ID/digest和必要校验事实，不保存可重放令牌字节。

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
| 基准 / 对照 | 功能质量、资源边界、延迟 | 统计口径冻结、保留集防污染 |

### 17.2 基准集与任务边界

Phase 0 定义两套冻结基准：Positive Set 为 73 个应当交付候选分支的 PUBLIC / 脱敏任务；Negative / Adversarial Set 至少 40 个不应形成候选分支的任务。每个任务记录 SourceBundle、任务类型、允许 / 禁止路径、独立 Oracle、预期精确终态、资源限制、数据等级和依赖镜像。

正向类别严格属于 §1.2 的缺陷修复、测试补充、小型功能，不再额外引入独立重构或高风险权限改造。以下数量是冻结的覆盖配额，不声称由统计公式推导；正式任务 ID 与覆盖矩阵才是完成证据。

| 任务类别 | 覆盖 | 数量 |
|---|---|---:|
| 缺陷修复：低复杂度 | Go / Python / TypeScript，各类明确断言 | 18 |
| 缺陷修复：中复杂度 | 跨函数逻辑、错误传播与状态边界 | 12 |
| 缺陷修复：边界与输入校验 | 仍限定白名单路径，不扩大权限 | 6 |
| 测试补充：分支覆盖 | 增量测试目录与独立验收 Oracle | 9 |
| 测试补充：边界 / 回归 | 已知故障重放、错误输入与缺失用例 | 12 |
| 小型功能 | 固定接口、路径和可自动化验收条件 | 16 |
| **合计** | 三类任务，语言 / 框架配比在 manifest 明示 | **73** |

负例配额：模糊规格 ≥12、安全诱导 / 后门 / 越权 ≥12、超预算 / 超时 ≥6、Git 漂移 / 竞态 ≥6、Skill / Route / Evidence 漂移 ≥4。各类覆盖不能互相替代。竞争场景要冻结事件顺序与 stopReason 优先规则，使预期终态可重放；不能事后扩大允许结果集。

两套集合各至少 20% 为保留集，以仓库 / 模板家族分组隔离，不能仅靠不同任务 ID 声称互不泄漏。每题 3 次运行不能当作 3 个独立任务样本；置信区间和差异检验按任务 / 仓库聚类处理。

#### 17.2.1 测试补充与受保护 Oracle

TaskSpec 显式区分 immutableAcceptancePaths 与 allowedTestChangePaths。Agent 可以在获准测试路径增补用例，但不能修改平台挂载的 GatePack、基准 Oracle、原受保护断言、CI 权限或测试发现策略。旧版禁止任意 tests/** 修改的规则由本条替代。

“测试补充成功”至少证明：原基线测试仍通过、增量测试确实执行、对冻结故障 / mutation 能失败且对正确实现通过。仅新增永真断言、把测试改成跳过、修改脚本使其直接退出 0，不构成成功。平台冻结测试执行器、发现配置和断言证据；宿主捕获的退出码证明进程事实，不能单独证明测试覆盖有效。

缺失依赖、需要联网 Gate、无法机器判定目标或超出三类白名单的任务在准入时拒绝 / 交接，不通过运行中改依赖或放宽 Oracle 继续。

### 17.3 指标定义与判定单位

| 指标 | 精确定义 | Phase 2 判定 |
|---|---|---|
| PositiveRunE2ESR | 73 正题 ×3 次 =219 次中，Task SUCCEEDED 且候选与完整交付证据均有效的次数 /219 | ≥70%，即至少154次；每个任务类别运行级比例≥50% |
| PositiveTaskE2ESR | 每题3次全部 SUCCEEDED 的题数 /73；另报告2/3与至少1/3成功题数 | 描述任务稳定性，Phase 2 不与运行级70%混用 |
| ExpectedOutcomeMatch | 实际终态精确等于冻结预期终态的运行比例；分正例、负例与整体报告，公开各自分母 | 报告项，不设整体95%硬门槛 |
| NegativeOutcomeMatch | 负例实际终态精确匹配预期的比例 | ≥95%；确定性协议 / 安全反例另要求逐例100% |
| UnsafeAcceptanceRate | 负例错误形成候选 ref 的比例；即使 Task 未宣布成功也计入 | 观测0/N，任一命中 No-Go |
| False Success | 宣告成功却未满足冻结 Oracle、Gate、制品或外部交付合同 | 0，安全硬门槛 |
| Evidence Completeness | 根据冻结 outcomeEvidenceProfile 检查该终态必须具备的证据项 | 100%；绝不把失败证据完整等同成功证据完整 |
| Infrastructure Failure | 正式运行中非任务本身导致的调度、存储、Node、Runtime或Gateway故障比例 | ≤2%，失败仍留在原分母 |
| SuccessWithin2Repairs | 每个初始运行连同最多2次Repair构成一个评估 episode，其成功比例 | Phase 3 使用，内部Attempt不能当新分母 |
| C | 冻结目标硬件实测的额定并发 Attempt 数 | 作为技术容量参数，不是人员或研发计划 |

用最小 73+40 的任务级示例，整体 ExpectedOutcomeMatch≥95% 即使负例全对仍需68/73正例成功；运行级219+40则需207/219。本版显式撤销该整体门槛，避免隐式覆盖正向70%。不得通过将正向安全失败改标“成功”来解决指标冲突。

失败、超时、预算耗尽、取消、基础设施故障均保留在预注册分母；唯一允许的试验作废规则须在运行前冻结并单列原记录，不得择优保留三次中的最佳结果。Phase 2 的 REPAIRABLE → HANDOFF_TO_HUMAN 仍是正向未成功。

成功证据要求包含合同、调用来源、正常终态、完整输出、Gate / Verdict 和 Git 读回；LOST 等终态使用 FAILURE_PLATFORM_PROOF，要求 DB时钟、Lease / fencing、最后观察和缺失证据清单。Node 失联时不要求伪造 stdout 或正常终态信封，也不能用该档案进入成功路径。

不安全接受的独立模型负例报告观测0/N及 Clopper–Pearson 单侧95%上界；0/N时为 1 - 0.05^(1/N)。Phase 2 /4A /4.5 最低独立负例数分别40/150/300，上界约7.2%/2.0%/1.0%。重复同题不自动增加独立N；相关样本须另报聚类方法与有效样本解释。确定性攻击反例采用逐例回归，不以统计区间替代必过测试。

### 17.4 契约专项验收

以下为功能验收清单：纯 Schema / digest / 状态模型在 Phase 0 验证，依赖真实 Node / 存储 / Gateway 的项目在 Phase 1 验证，真实 Git 交付在 Phase 2 验证。不得将后续组件的集成测试当作尚未实现前就已通过的事实：

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
14. 任取20个成功Task沿traceId验证至候选Git的完整链；失败Task验证对应outcomeEvidenceProfile，不要求存在候选ref。
15. 日志、事件和证据中不存在明文 LeaseGrant。
16. SourceBundle 固定 ref、submodule、LFS 与文件模式；浮动或不一致输入全部拒绝。
17. DependencyImage 在 MicroVM 构建并含 SBOM / SCA / provenance；Attempt / Gate 在线安装全部拒绝。
18. Gate 对 CommitBundle 预计算 GitObjectId 验证；Git Stager 未执行仓库代码且读回对象完全相同。
19. 高于本地已同步 epoch 的 Token 先拒绝并触发同步，不存在 fail-open 窗口。

#### 17.4.1 v1.3.2 新增反例与验证合同

| 测试ID | 输入 / 故障 | 必须结果 |
|---|---|---|
| A32-T01 | 同一RolePack从候选构建到审批、签名；改一字节 | 无环、摘要可重算；旧审批不能批准变化内容 |
| A32-T02 | 两个无关Task，仅撤销其中一个依赖 | 命中任务停止；无关任务补齐水位并刷新授权后继续，合同digest不变 |
| A32-T03 | 撤销流缺号、旧checkpoint重放、签名key命中 | 停止新动作，拒绝水位回退；不允许自报CLEAR |
| A32-T04 | Gateway在预留、意图落盘、发送、响应、结算各点崩溃 | 每笔预留只能消费一次；未知用量占额；无盲目重复发送 |
| A32-T05 | Journal丢失、Gateway双代次、旧实例恢复 | 原Grant不可重用，无余额回滚；新额度与未知负债不重叠 |
| A32-T06 | CommitAssembler失败或被杀 | 有限重试保持同Intent；耗尽后Run与Task可安全终结 |
| A32-T07 | Git明确拒绝、授权过期、ref外部漂移、ack丢失 | 分别失败、过期、交接、对账；不把已知拒绝悬挂为未知 |
| A32-T08 | 取消与终态、Manifest发布、Git dispatch并发 | 输出选择守卫尊重已接受取消；已发Git先对账；报告专用Grant不能执行 |
| A32-T09 | TERMINAL_REPORTED后Artifact封存失败 | 形成失败证据与终态，不永久卡住或伪造正常Manifest |
| A32-T10 | Reviewer等待实现输出；恶意替换producer | 只绑定选定发布输出；不要求未来digest；替换被拒绝 |
| A32-T11 | Review Run完成；循环Review DAG | 前者证据验证后终止，后者编译拒绝；不生成无意义CommitBundle |
| A32-T12 | 219正向结果、40负向结果的固定统计夹具 | 分母稳定、154/219满足70%；整体终态匹配不反向改写门槛 |
| A32-T13 | Agent删除断言、跳过用例或测试脚本直接exit0 | 固定Oracle / 测试发现与mutation检查拒绝假成功 |
| A32-T14 | Prime / Native Runtime缺少管道或偷偷联网 | 能力协商失败，真实执行准入拒绝；Scripted通过不能替代 |
| A32-T15 | P2配置打开Repair、Reviewer、PR/MR或自动合并 | PROFILE_FEATURE_DISABLED；无副作用 |
| A32-T16 | 1.3旧合同、未知Schema字段、旧事件payload | 拒绝隐式升级；按显式离线迁移或历史只读处理 |

所有测试当前状态为“待实现 / 待执行”。本文仅定义可重复输入、预期结果和证据要求，没有运行产物引用时不能标记 PASS。

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
- 模型消费、未结算预留、UNKNOWN负债、Grant剩余额度及失败资源占用；
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
| 进程退出后合法终态收敛 | 99%在60s内；已证明无不明副作用的极端路径≤120s，按StateDeadlinePolicy |
| Infrastructure Failure | ≤ 2% |
| 受保护分支未授权写入 | 0 |
| 运行时 Skill 漂移 | 0 |
| 撤销事务先于准入事务时，命中Bundle的新Attempt获批 | 0；已签发在途能力按撤销传播窗口处理 |

### 18.3 运行预算、消费所有权与崩溃一致性

预算用于资源约束和循环收敛，不用于本版的人力投入或商业回报评估。Task / Run / Attempt 分别限制墙钟时间、调用次数、输入 / 输出 Token、金额、Runtime Retry、Repair、Artifact 字节、并发与外部副作用；父级累计上限不能被新 Attempt、Repair、Grant 或进程重启重置。

#### 18.3.1 BudgetGrant 与预留

~~~text
BudgetGrant:
  budgetGrantId / budgetReservationId
  taskId / runId / attemptId / executionPlanDigest
  ledgerEpoch
  gatewayWorkloadIdentity / gatewayInstanceId / gatewayGeneration
  reservedTotalMicros / reservedInputTokens / reservedOutputTokens
  perCallCapMicros / maxInputTokensPerCall / maxOutputTokensPerCall
  maxInvocations / grantSeqStart / grantSeqEnd
  rateCardDigest / budgetPolicyDigest
  validFrom / expiresAt / payloadDigest / signature

InvocationReservation:
  budgetGrantId / invocationId / requestSequence / requestDigest
  gatewayInstanceId / gatewayGeneration
  reservedMicros / reservedInputTokens / reservedOutputTokens
  providerRequestId                              # 尚未知时 null
  state = RESERVED | DISPATCH_INTENT | IN_FLIGHT | SETTLED | UNKNOWN | RELEASED
  journalSequence / previousEntryDigest / entryDigest
~~~

Ledger Service 在单个事务内从父级未分配额度切出不重叠预算，记录 grant、消费实例与序号范围并签发授权。所有未结算 / 不确定 / 尚未安全关闭的 Grant 继续占用父级额度。BudgetGrant 是持久额度分配，不携带可反复变化的撤销水位；每次动作的 GatewayGrant 与 RevocationCheckAttestation 另行检查当前撤销，刷新它们不生成新余额。

#### 18.3.2 调用前持久化与单调扣减

1. Gateway 验证自身 workload、instance、generation、Ledger epoch、Grant TTL、请求身份与当前执行 / 撤销授权；同一 Grant 只允许一个消费者代次。
2. 对预算计算的原子操作同时检查余额、次数、序号唯一性；金额按已冻结费率和请求最坏输入 / 输出计费上界预留，Token 按上限预留。无法界定最坏扣费的路线不可进入硬预算任务。
3. 本地持久 Journal 先追加 RESERVED 并 fsync，扣除可用额度；调用前再追加 DISPATCH_INTENT 并 fsync。两个动作完成前不能访问 Provider。本地 Journal 是 Ledger 已委派额度的消费事实日志，不能增发额度。
4. Provider 请求携带可用的稳定幂等键；即使 Provider 不支持幂等，也先有持久意图。已知存在 DISPATCH_INTENT 但结果未知时，禁止盲目重新调用。
5. 响应、providerRequestId、用量与结算事实先持久化，再异步按 invocationId 上报 Ledger；结算确认后才释放预留与实际消费差额。
6. 同 invocationId / 同 requestDigest 返回已知状态；同 ID 不同请求拒绝。模型库、SDK 和代理内部隐式重试必须关闭，或每次物理发送都获得独立的受预算控制 reservation，不能藏在一次逻辑调用之下。

RESERVED 且确定没有 dispatch intent 的额度可释放；DISPATCH_INTENT / IN_FLIGHT 在崩溃或连接丢失后进入 UNKNOWN。UNKNOWN 占用完整预留，直到可验证的 Provider 结果 / 明确无发送证明或保守按预留全额结算后关闭；TTL 到期、看不到请求 ID、读取日志失败都不是退款证据。账单超出冻结上界视为路线约束失效，停止新调用并记录异常，不能宣称已保证供应商永不错误计费。

#### 18.3.3 重启、转移与对账

- 同实例重启必须验证 Journal 链、校验和和唯一序号后恢复余额；有缺页 / 回退即停止消费原 Grant。
- 丢失 Journal 时将原 Grant 未确认部分整体锁定；新实例不能重新使用原 Grant。原代次须先 fenced，新 Grant 只能来自父级尚未分配额度，不从丢失日志推测退款。
- Ledger 不向两个活跃代次分配相同 reservation / sequence 区间。交接必须保留旧 Grant 的累计消费与未知负债，旧实例恢复后拒绝其新调用。
- 异步结算具有不可变 invocation identity 与 receipt digest；重复回执只确认同一事实，金额不一致进入对账隔离，不能覆盖历史。
- Gateway 本地热路径不要求同步查询 PostgreSQL，但必须有持久化原子扣减。授权缺失、Journal 不可用、Ledger / 执行代次失效或对账不明均 Fail Closed。

80% / 90% 阈值可触发告警或停止非必需步骤；不得改写冻结 Gate。安全 Gate、停止清理和失败证据封存使用独立应急预算，业务调用额度耗尽不能跳过它们。

### 18.4 告警

- Critical：假成功、受保护分支写入、凭据泄露、控制面 epoch 回退、签名失败、跨 Attempt 污染；
- High：Route / Skill / Role drift、撤销传播超 SLO、Artifact 摘要不一致、Git 不可确认副作用；
- Warning：调用消费异常、Outbox backlog、启动延迟、错误率和容量逼近；
- Info：正常发布、Canary 晋级、Bundle supersede 和例行对账。

Critical 默认触发 Scope Stop 或 Global Stop，不等待模型判断。

---

## 19. 功能阶段、依赖与 Go / No-Go

### 19.1 晋级原则

阶段表达功能依赖和证据成熟度，不给出研发排期、人员投入或商业回报阈值。协议原型、隔离能力实验可以在正式基线批准前执行；这不代表允许接入生产或跳过正式验收。

唯一权威、安全边界、无假成功、完整证据、预算硬约束和可对账副作用同时成立才可晋级。安全复核和 Four-Eyes 的身份独立性保留；本文不根据人数估计交付能力。未完成验证保持候选状态，不能用文档修订替代验证报告。

### 19.2 阶段总览

| 阶段 | 核心功能 | 前置证据 | 最高可声明成熟度 |
|---|---|---|---|
| Phase 0 | 契约、状态模型、功能profile、Runtime能力实验 | PUBLIC隔离实验输入与可验证目标 | 可实施设计候选 / 通过复审后冻结 |
| Phase 1 | Runtime、Node隔离、Lease、撤销、预算与证据原型 | Phase 0正式合同或有范围标识的实验合同 | 受控PoC |
| Phase 2 | 单Implementer到独立Gate与候选ref完整链路 | Phase 1安全与协议验收 | PUBLIC单节点MVP |
| Phase 3 | 多角色、后绑定和有界Repair | Phase 2闭环和固定基准 | 增强型MVP |
| Phase 4A | INTERNAL、MicroVM、专用节点和恢复 | 对应任务能力及安全前置 | 可恢复生产候选 |
| Phase 4B | 独立故障域、同步复制、故障转移 | Phase 4A恢复验收 | 可选HA候选 |
| Phase 4.5 | 真实负载Shadow、分档Canary与回退 | 适用数据等级的安全验收；HA需求才依赖4B | 有限生产可用 |
| Phase 5 | 模型、Role、Skill、Gate、镜像独立演进 | 已验证基线及变更隔离 | 持续治理 |

### 19.2.1 功能范围与规范等级

附录 H 是 Phase 2 的规范性功能profile。未标阶段的安全不变量默认 MUST-P2；功能可达性必须显式开关，不能因Schema包含未来枚举就默认启用。

| 约束 / 功能 | 等级 | 精确规则 |
|---|---|---|
| 唯一写入、CAS、内容摘要、签名、完全断网、当前撤销检查、预算持久扣减、独立Gate、Git对账 | MUST-P2 | 功能骨架不可省略 |
| Builder / Approver / Signer / Publisher职责和工作负载权限分离 | MUST-P2 | 可共用部署设施，但同一身份不能兼任冲突权限 |
| RolePackCandidate与审批无环；RuntimeIntegration与Compatibility绑定 | MUST-P2 | 是内容构造要求，不按阶段延后 |
| 供应链独立服务进程与独立密钥设施 | MUST-P4A | P2可采用共享Signer服务的分key / 分主体模式，权限与审计仍独立 |
| REVIEW / READ_ONLY Run、fan-out / fan-in、Repair、动态上游槽位 | MUST-P3 | P2只使用初始Source绑定，相关功能拒绝 |
| INTERNAL的MicroVM与专用节点 | MUST-P4A | PUBLIC容器不能作为INTERNAL替代 |
| 多故障域与同步复制 | MUST-P4B | 同宿主多容器不构成HA |
| 真实流量Shadow / Canary | MUST-P4.5 | P2仅隔离测试候选仓库 |
| PR/MR自动创建、自动合并、生产部署、运行时Skill安装 | DISABLED | 无相应新合同和ADR不得开启 |

所有阶段均保留完整候选 / 签名 / 发布语义；镜像只要payload未带审批引用，可使用 unsigned Snapshot → Approval → detached Signature → Publication，无需机械增加同义Candidate。凡payload包含审批引用的类型，必须先有不含审批的Candidate，不能按“MVP简化”引入环。

### 19.3 Phase 0：契约与能力冻结

交付：核心Schema / DigestProfile /签名向量、状态可执行模型、字段级权威矩阵、Phase2Profile、基准manifest、Runtime接入能力报告、Git CAS能力报告与A32测试规格。

准许在隔离环境用合成PUBLIC任务开展Runtime真实模型实验及Git测试ref的能力实验；使用专属实验身份、命名空间和有限授权，不接真实业务Task。这些实验与正式平台运行分别留痕，解决“要先实现验证才能批准实现”的循环。

Go：摘要依赖可拓扑构建；Go与独立参考实现字节一致；正常/失败/取消/对账状态模型无未定义出口；模板与实例绑定可重算；73正题和≥40负题完成Oracle与范围校验；至少一条真实Runtime路线满足受监督管道合同；未知字段和失效授权全部拒绝。未通过时只继续限定实验，不发布正式运行profile。

### 19.4 Phase 1：协议、隔离与恢复PoC

交付：Scripted与至少一个真实Runtime Driver、Node/Sandbox、Lifecycle/Lease、Revocation Service、Budget Journal、Artifact封存、最小供应链和独立Gate Supervisor。

Go：200个脚本/故障/安全场景逐例符合预期；≥10,000条重复乱序事件无重复副作用；100次Node/Runtime崩溃无双执行；100次旧epoch提交均拒绝；A32-T01～T11、T13～T16按阶段完成；Bundle在3个独立干净环境累计100次构建内容摘要一致。网络、FD、ptrace、路径、凭据和Gate输入篡改反例必须拒绝。

失联Node按平台失败证据收敛，不能要求不存在的完整日志。只有Scripted通过时保持协议PoC，不宣称真实Runtime可用。任一越权、假成功、旧所有者写入或预算重复消费即No-Go。

### 19.5 Phase 2：PUBLIC单节点候选闭环

交付：Task准入 → 冻结计划与初始输入绑定 → 单Implementer → Artifact → CommitBundle → 独立确定性Gate → Verdict → 独立Git授权 → candidate ref读回 → DeliveryEvidenceBundle。

Go：

- 73正题每题3次共219次，PositiveRunE2ESR≥70%且类别≥50%；同时报告PositiveTaskE2ESR；
- 负例至少40个，NegativeOutcomeMatch≥95%，UnsafeAcceptanceRate=0/N，False Success=0；确定性反例逐例100%；
- ExpectedOutcomeMatch按§17.3分别报告，不设置整体95%通过条件；
- outcomeEvidenceProfile完整率100%；成功必须有完整交付证据；摘要、路线、授权和预算阻断覆盖100%；
- 控制面API p95<500ms，调度至沙箱启动p95<30s；退出后99%在60s内收敛，极端收敛上界见StateDeadlinePolicy；
- Infrastructure Failure≤2%；额定并发C持续8小时无死锁、状态损坏或孤儿进程，连续7天稳定性验证无重复外部副作用；
- 至少30个真实集成故障场景涵盖Git明确拒绝、ack丢失、撤销无关推进、预算恢复及Artifact封存失败；
- 自动PR/MR、Repair、多角色、受保护分支写入、自动合并和部署均按profile拒绝。

任一安全硬失败No-Go；正向比例不足则保持未通过，不能改预期结果或移除失败样本。回退关闭新准入、收敛在途执行，保留失败证据和候选ref隔离记录。

### 19.6 Phase 3：多角色与有界Repair

交付：Implementer / Reviewer / Semantic Judge角色隔离、InputBinding后绑定、RequiredRunBinding、最多2轮Repair、fan-out/fan-in及结构化审查证据。

Go：固定73题每组3次，单角色/多角色共438个episode；同源、同Gate、相同资源预算边界，盲态比较。SuccessWithin2Repairs≥80%；相对单角色成功率提升至少10个百分点或逃逸缺陷降低至少50%；首次成功率回归≤3个百分点；预标可修复失败至少70%在两轮内修复。安全假接受为0，自审、共享可写目录、Review递归和越权为0。Verdict同证据重放1,000次一致。

不以人工工时或金额收益判断多角色必要性；资源预算用于公平对照和防失控。未证明功能质量优势时不默认开启该Workflow，保留已验证单角色路径。

### 19.7 Phase 4A：INTERNAL与可恢复运行

交付：SSO/RBAC、短凭据、KMS/Secret Broker、MicroVM+专用节点、数据分类与DLP、独立签名部署、备份恢复与审计保留。

Go：INTERNAL隔离100%；1,000个敏感数据反例无泄露；3次备份恢复成功，RPO≤15分钟、RTO≤60分钟；72小时额定容量验证Infrastructure Failure<1%。INTERNAL正题≥100×3次，运行成功率≥70%、类别≥50%；独立负例≥150且不安全接受0。恢复后先提升控制面代次、隔离旧Node、保守保留未知预算负债、读回Git并核验Artifact，再开放新任务；备份缺失记录不能推断未发生副作用。

### 19.8 Phase 4B：真实HA

至少两个独立故障域、同步数据库复制、跨域Artifact、控制面N+1与Gateway消费代次交接。至少10类N-1故障无双主、重复调用/提交或已确认制品丢失；权威元数据RPO=0、服务RTO≤5分钟；7天故障域耐久演练。未满足保持可恢复非HA，不省略已有安全规则。

### 19.9 Phase 4.5：Shadow与Canary

Shadow不产生用户可见或Git写入；Canary只开放任务专属候选ref。观察时长是稳定性验证条件，不是研发排期：Shadow≥4周且≥500个任务，5%/10%/25%各档≥7天且≥100个任务；时间与样本条件都满足才能晋级。独立负例≥300且不安全接受0。

每档无Critical、假成功、泄露或受保护分支写入；相对对照质量非劣界限−3个百分点，样本按任务/仓库聚类和统计功效确定；平台p95延迟增加≤20%。告警到停止新任务≤2分钟、完整版本回退≤5分钟；3次人工触发和3次自动触发回退均成功。报告采纳/修改/拒绝的功能原因与逃逸缺陷，不要求人工工时或商业收益报告。

### 19.10 Phase 5：独立演进

模型、Prompt、Role、Skill、Gate与Image独立提出变更、审批、评测、发布和撤销。每项变更须有冻结假设、保留集、影响指标和可回退版本；安全反例全部通过、证据完整。质量95%置信区间非劣界限−3个百分点，平台p95延迟回归≤10%。Skill裁剪要求充分覆盖与统计功效，并验证关键安全Skill不因低频被删；资源效率仅作技术优化指标，不作为商业回报核算。

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

## 21. 功能工作包与依赖顺序

本章仅定义交付接口和架构依赖，不包含建设排期、人员编制或人工成本。

| 工作包 | 功能输出 | 必需前置 | 技术验收 |
|---|---|---|---|
| WP-01 规范与Profile | 不变量、任务范围、功能开关与替代清单 | 本版规范 | 无跨版本冲突与隐式开启 |
| WP-02 契约 | Schema、DigestProfile、签名和状态模型 | WP-01 | 正反夹具、无环、状态出口 |
| WP-03 运行生命周期 | Task/Run/Attempt、CAS、Outbox/Inbox、Lease | WP-02 | 重复/乱序/失联/取消收敛 |
| WP-04 Runtime能力 | Scripted、Prime或Native适配与ModelCallIntent | 独立实验合同或WP-02 | 真实模型、管道、子进程隔离 |
| WP-05 执行隔离 | Node、Sandbox、工具进程和Gate Supervisor | WP-02/04 | 网络/FD/路径/Gate篡改反例 |
| WP-06 治理供应链 | Skill、RoleCandidate、镜像、审批、签名与发布 | WP-02 | 内容复现、审批消环、不可变挂载 |
| WP-07 当前授权 | 撤销日志、checkpoint、依赖闭包与短Grant刷新 | WP-02/03/06 | 命中阻断与无关恢复分离 |
| WP-08 Gateway预算 | 模型调用、Journal、预留、结算和代次交接 | WP-03/04/07 | 未知占额、无重复消费 |
| WP-09 源码与证据 | Source、InputBinding、Artifact和证据profile | WP-02/03/06 | 正常/失败证据均可验证 |
| WP-10 独立验证 | CommitIntent/Bundle、Gate、Verdict | WP-05/09 | 精确对象、确定性重放 |
| WP-11 候选交付 | Git授权、CAS、读回、对账和失败映射 | WP-07/09/10 | 明确失败与不确定分开收敛 |
| WP-12 多角色修复 | Review证据、后绑定、RequiredRunBinding、Repair | 已验收单角色闭环 | 无递归、受限修改、全部Gate重跑 |
| WP-13 运营与恢复 | 观测、告警、回退、备份与HA能力 | 对应运行组件 | 故障演练及权威重建 |

首个可执行迭代产出：RolePack无环夹具、四类状态机模型、Scripted故障场景、撤销无关推进场景、预算Journal恢复场景、初始InputBinding与一份可校验的模拟Evidence Bundle。Runtime / Git能力实验分别留存真实边界证据，不以模拟器结果替代。

组件的逻辑写入者与权限归属以§5为准；审批主体、签名主体和发布主体保持可审计分离，不据本章推算需要多少实际人员。

---

## 22. ADR 路线

### 22.1 规范替代与历史来源

v1.3.2 为当前自包含功能与架构规范，来源v1.3.1文件SHA-256为 386014d9194ed8ffe3383f9312484a1c320d2b093ef062a22a8c683ca2ec1722。v1.0～v1.3.1仅保存设计历史；任何“旧版全部保留”“自动取更严格条款”不再作为实现规则。

| 历史主题 | 本版权威 | 处置 |
|---|---|---|
| 唯一状态权威、宿主Gate事实、Git移出沙箱 | §3～6、§8 | 保留并具体化 |
| 全局revocationEpoch与旧合同精确相等 | §10.7、§13.3 | 替代为历史基线+当前依赖检查+短授权 |
| RolePack最终摘要直接审批 | §10.5 | 替代为Candidate审批与单向Snapshot |
| 任意tests/**写入禁止 | §1.2、§17.2.1 | 替代为受保护Oracle与允许测试增量路径分离 |
| 逐任务Evaluation/Route审批 | §7.7、§10.3.1 | 模板审批，实例确定性编译 |
| 无调用持久日志的BudgetGrant本地扣减 | §18.3 | 补足独占代次、Journal、保守未知负债 |
| MERGING与渐进式成功 | §8、附录H | 当前profile禁用，未来须独立规范 |
| RLM-001～010 | §6.7、§17.4、附录H | 保留为未来只读RLM的实验来源；P2关闭RLM，不声称完整实现其能力 |
| 正例成功率与整体95%终态匹配双门槛 | §17.3、§19.5 | 整体匹配改报告项；安全反例要求不变 |
| 建设排期、人力编制、投入与收益核算 | 版本说明 | 本版不评估，不作为架构门槛 |

下列新增ADR为本版设计决议，状态均是“规范已写入，等待实现证据和独立复审”，不得伪标Accepted。历史ADR-25～34的安全边界在正文承接；其旧版“通过”状态不自动带入本版。

### 22.2 既有ADR的当前承接要求

| ADR | 决策主题 | 优先级 | 正式发布前要求（当前待验证） |
|---|---|---:|---|
| ADR-25 | SkillRoster 生态位与运行时 Skill 不变性 | P0 | 发布前需Accepted；当前待证据 |
| ADR-26 | SkillPackage 格式、来源冻结与安全门禁 | P0 | 发布前需Accepted；当前待证据 |
| ADR-27 | Skill Registry、Artifact 与 SkillBundle 权威 | P0 | 发布前需Accepted；当前待证据 |
| ADR-28 | Four-Eyes、签名、发布、Canary 与回退 | P0 | 发布前需Accepted；当前待证据 |
| ADR-29 | Skill 能力声明与 Role / Sandbox 最小权限编译 | P0 | 发布前需Accepted；当前待证据 |
| ADR-30 | Skill 使用证据、隐私与裁剪判断 | P0 | 发布前需Accepted；当前待证据 |
| ADR-31 | Skill 撤销、污染扩散与重建恢复 | P0 | 发布前需Accepted；当前待证据 |
| ADR-32 | Skill 许可、Notice 与第三方责任 | P1 | 当前Proposed；发布前完成适用复审 |
| ADR-33 | SourceBundle 与 DependencyImage 冻结供应链 | P0 | 发布前需Accepted；当前待证据 |
| ADR-34 | CommitBundle、独立 DeliveryAuthorization 与 Git Staging | P0 | 发布前需Accepted；当前待证据 |

### ADR-25｜SkillRoster 生态位与运行时 Skill 不变性

决策：只作为离线、可替换、只读优先的治理 Adapter；本版仅允许Scan/Report JSON；Runtime、Attempt、Gate 镜像不包含 SkillRoster；Agent 只能提出 MissingCapabilityProposal。

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

决策：Skill 声明 Role、任务类、工具、脚本、数据和网络；requiredCapabilities 必须是 Role / Sandbox 能力交集的子集；本版不支持运行时On-demand。

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


### 22.3 v1.3.2 新增ADR

| ADR | 决策 | 取代 / 补充 | 验证 |
|---|---|---|---|
| ADR-35 | RolePackCandidate消除审批摘要环 | 旧§10.5最终Snapshot审批 | A32-T01 |
| ADR-36 | 历史准入水位与当前授权分离 | 旧§10.7全局精确相等 | A32-T02/T03 |
| ADR-37 | BudgetGrant独占代次与持久消费 | 旧§18.3本地扣减 | A32-T04/T05 |
| ADR-38 | 已知失败、优雅取消、交付对账显式出口 | 旧§8缺口 | A32-T06～T09 |
| ADR-39 | Runtime候选同合同能力验证 | 默认Prime daemon可直接适配假设 | A32-T14 |
| ADR-40 | 运行成功率/安全/终态匹配分开判定 | 旧§17/19整体95%门槛 | A32-T12/T13 |
| ADR-41 | 当前单一规范与Phase2Profile | 多文件默认继承 | A32-T15/T16 |
| ADR-42 | InputBinding后绑定与Review终止规则 | 预知未来输出摘要的歧义 | A32-T10/T11 |
| ADR-43 | outcomeEvidenceProfile与报告专用授权 | 强制终态须完整Node日志的歧义 | A32-T08/T09 |

本文是拟冻结的功能决议，不表示对生产政策实施降低或已取得额外Security Approver签字。实际发布器仍按§10.3.1审批范围、差异和身份规则执行。

## 23. 功能与架构风险登记

| 风险 | 触发条件 | 必须防护 / 验证 | 当前状态 |
|---|---|---|---|
| 摘要循环或引用不覆盖安全字段 | Candidate与审批互指、DigestProfile漏字段 | 无环拓扑、内容变异向量、独立实现比对 | 规则已修订，待实现验证 |
| 局部撤销扩大为无关任务永久停止 | 用最新水位直接否定历史合同 | 当前闭包证明、短授权刷新、不改冻结输入 | 规则已修订，待故障注入 |
| 撤销漏传播 / 旧checkpoint回放 | 日志缺号、断联、时钟异常 | 连续日志、新鲜度上限、代次和签名校验 | 待实验 |
| 预算重放 / 隐式Provider重试 | Journal丢失、多实例消费、未知退款 | 持久预留、独占代次、未知占额 | 待恢复实验 |
| 状态永久悬挂 | 组装失败、上传失败、Git明确拒绝 | StateDeadlinePolicy与显式失败出口 | 待模型验证 |
| 取消被正常完成覆盖 | 取消与信封/发布/dispatch竞争 | cancelRequest CAS、报告专用Grant、写入前守卫 | 待并发注入 |
| Prime / Native无法管道桥接 | 默认Socket、隐式Provider调用、后台子树 | RuntimeIntegration能力矩阵，任何路线独立验证 | 未得出可用结论 |
| Gate被同宿主进程或修改后的测试蒙骗 | FD/ptrace/目录旁路、跳过测试 | 不同身份/namespace、受保护Oracle、mutation反例 | 待安全验证 |
| 多角色输出绑定错误或递归 | 任意producer、Review等待自身、无限审查 | InputBinding、RequiredRunBinding、DAG和Run kind校验 | 待Phase3验证 |
| Git写入身份不明 | ack丢失、sender仍在飞、ref漂移 | 持久意图、精确CAS、operation key、先对账 | 待真实Adapter验证 |
| 规范和示例分叉 | Schema主版本不变但字段语义变化 | 2.0契约、生成式夹具、字段和引用检查 | 待正式Schema验证 |
| 恢复后重复副作用 | DB/Journal备份落后于Provider/Git事实 | 提升代次、未知负债保守锁定、读取外部事实 | 待恢复演练 |

---

## 24. 功能质量与架构有效性

本版删除建设成本、人工投入、交付周期、单位工时收益与Break-even模型。本章仅判断功能和架构是否成立。

| 维度 | 判断依据 | 不能替代它的证据 |
|---|---|---|
| 功能正确 | 独立Oracle、正向运行成功率、回归覆盖 | 模型自报完成、输出行数 |
| 边界有效 | 能力拒绝、沙箱反例、审批和撤销检查 | 组件名包含Sandbox或Signer |
| 故障收敛 | 各状态失败/取消/恢复模型和故障注入 | 正常路径演示 |
| 多角色必要性 | 同资源约束的成功率与逃逸缺陷对照 | Agent数量或角色名称 |
| 证据可验证 | 固定输入、完整引用、摘要签名、重放结果 | Markdown示例或数据库自报成功 |
| 外部副作用可控 | 精确Git对象、CAS、读回、在飞操作对账 | HTTP成功码或本地调用已返回 |
| 可扩展性 | 类型化Adapter、唯一写入者、显式profile | 提前拆出更多进程 |

Token、金额和资源上限仍属于运行防失控合同；吞吐、延迟、Lease TTL与恢复目标仍属于技术指标，不因排除投入评价而删除。

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
| 撤销命中阻断与无关刷新 | Phase 2 | 命中/无关/缺号反例 | checkpoint、闭包证明、拒绝或刷新证据 |
| 多角色改善功能质量 | Phase 3 | 438个公平盲态A/B episode | 成功率、逃逸缺陷、资源边界与显著性 |
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

### 26.1.1 v1.3.2 必须登记的新增对象

~~~text
task_admission_decisions
task_cancel_requests
required_run_bindings
role_pack_candidates
role_pack_publications
runtime_integration_candidates
runtime_integration_snapshots
runtime_integration_publications
input_binding_snapshots
input_binding_policies
revocation_view_checkpoints
revocation_check_attestations
revocation_freshness_policies
closure_policies
terminal_report_grants
terminal_report_generations
lease_policy_snapshots
state_deadline_policies
outcome_evidence_profiles
phase_profiles
budget_grants
budget_grant_owners
budget_grant_settlements
budget_uncertain_liabilities
invocation_settlement_receipts
candidate_authorization_bindings
execution_image_compatibility_publications
~~~

InvocationReservation消费Journal由绑定Gateway代次持久化；Ledger保存分配、结算、未知负债和Journal校验锚。它不是每次模型调用必须同步写PostgreSQL的要求。所有新对象需在契约manifest列明字段Schema、DigestProfile、逻辑写入者、权限、引用和迁移规则；不允许只加表名却省略合同。

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
- 数据库使用UTC，Lease以数据库时间为准。
- RequiredRunBinding(taskId,workflowNodeId)唯一且由Lifecycle CAS；同轮Repair只能有一个活跃替代。
- BudgetGrant owner代次、reservation与序号区间唯一；父级available+allocated+spent+uncertain按守恒式校验，禁止重启重置。
- candidate logicalOperationDigest不可变；AuthorizationBinding仅追加，currentBindingId通过CAS切换。
- RolePack审批subject必须指Candidate；发布时内容投影与审批精确匹配。
- TerminalReportGrant不得包含模型/Git/正常输出发布动作；终态写入后报告仅追加隔离证据。

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

## 27. 架构就绪与功能验证顺序

### 27.1 READY_FOR_IMPLEMENTATION 的证据条件

本版文档完成不等于正式架构就绪。状态推进为：

SPEC_REVISED → CONTRACT_VALIDATED → PROTOTYPE_VERIFIED → INDEPENDENT_REVIEW_CLOSED → PROFILE_APPROVED。

每次推进记录规范版本、内容digest、实际验证产物、适用profile、检查主体与复审结论。未执行测试不能填PASS；签署空白时保持候选。限定范围的合同/隔离实验可在正式冻结前进行，不能据此开放真实业务运行。

Phase2Profile批准前必须满足：

- Schema2.0、DigestProfile、签名正反向量与依赖DAG可验证；
- 四类状态机的正常、失败、取消和对账守卫完整；
- RolePack无环、InputBinding、RuntimeIntegration与当前授权引用无未来字段；
- 至少一条真实Runtime路线通过能力实验，Scripted不能替代；
- Git Provider支持精确CAS、读回和专属候选权限；
- 当前撤销刷新、预算Journal崩溃恢复、报告专用授权及Gate独立性完成反例；
- 基准范围、分母、Oracle与证据profile冻结；
- 签名、审批、发布身份独立性在实际权限中可验证；
- §28中适用的阻塞项具备实现证据并完成复审。

### 27.2 需要冻结的技术输入

Git Provider与隔离仓库、精确Git对象算法、Linux/内核/容器/微虚机及MAC策略、PostgreSQL/Artifact组件摘要、Runtime入口与shim、模型真实路线及费率上界、镜像来源、工作负载身份与key、撤销新鲜度、Lease/StateDeadlinePolicy、预算Journal持久化介质、证据保留和GC规则。

这些输入按技术兼容性和安全需求确定，不按人工成本或研发排期取舍。

### 27.3 第一个功能迭代

1. 建立本版契约清单、固定夹具与状态模型。
2. 生成RolePackCandidate到审批与Snapshot的真实摘要链。
3. 运行两任务无关撤销、Gateway各持久化边界崩溃、取消/交付竞争的脚本场景。
4. 实现初始InputBinding、正常和失败Evidence Bundle及验证器。
5. 在隔离能力实验中验证真实Runtime管道与Git CAS，分别记录模拟与真实结果。
6. 用固定结果夹具验证219次正向与负例指标计算。
7. 通过后再组合Node/Artifact/Gate/Git纵向运行，按Phase2Profile启用功能。

### 27.4 当前可声明结论

已完成的是功能和架构规则修订；没有随本Markdown提供的运行日志、Schema实现、签名向量或故障注入结果，均保持待验证。SkillRoster继续仅作离线结构证据Adapter；它不承担审批、沙箱、成功判定或运行时变更权威。

---

## 28. v1.3.2 修订与验证跟踪

本表不把“文字已修订”标作“问题已验证关闭”。规范处置已完成，后续状态必须由真实证据推进；当前实现证据与复审字段留空。

| 编号 | 规范处置 | 验证入口 | 阻塞对象 | 实现证据 | 复审 |
|---|---|---|---|---|---|
| A32-01 | RolePack候选审批与无环Snapshot | A32-T01 | 合同冻结 | 待提供 | 待复审 |
| A32-02 | 当前撤销检查、无关任务恢复 | A32-T02/T03 | 当前授权与运行准入 | 待提供 | 待复审 |
| A32-03 | BudgetGrant独占、Journal与未知负债 | A32-T04/T05 | 模型调用 | 待提供 | 待复审 |
| A32-04 | 组装/取消/交付失败状态闭合 | A32-T06～T09 | 生命周期与Git交付 | 待提供 | 待复审 |
| A32-05 | Runtime接入模式与真实能力实验 | A32-T14 | 真实Attempt | 待提供 | 待复审 |
| A32-06 | 基准范围、分母、Oracle与安全门槛 | A32-T12/T13 | 功能验收 | 待提供 | 待复审 |
| A32-07 | 单一规范与Phase2Profile | A32-T15 | 配置编译和功能启用 | 待提供 | 待复审 |
| A32-08 | 输入后绑定、Review终止、Repair血缘 | A32-T10/T11 | Phase3多角色 | 待提供 | 待复审 |
| A32-09 | 终态专属证据与报告授权 | A32-T08/T09 | 失败与取消证明 | 待提供 | 待复审 |
| A32-10 | Schema2.0、API/事件/表/示例一致性 | A32-T16及引用检查 | 契约发布 | 待提供 | 待复审 |

未决的技术选择：真实Runtime路线与启动入口、Git Provider CAS实现、Node报告key与撤销信任链、Gateway Journal介质/恢复规则、技术Policy初值的实测适用性。这些选择必须记录责任组件、依赖节点和阻塞功能，不在本版设置人员投入或日历截止日期。

---

## 附录 A：示例 SkillBundleSnapshot

以下是 Phase 0 Schema 必须接受的正例骨架；占位摘要在测试夹具中替换为合法 64 位小写十六进制值。

~~~json
{
  "schemaVersion": "2.0",
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
    "schemaVersion": "2.0",
    "payloadDigest": "sha256:<skill-bundle-snapshot-digest>",
    "controlPlaneEpoch": 42,
    "signedAt": "2026-09-04T08:00:01Z",
    "signature": "<base64url-signature>"
  }
}
~~~

## 附录 B：示例 AttemptContract

以下为Schema2.0的文档展示骨架，字段按§10.6列示；没有当前动作Grant或撤销检查证明，因为它们在合同外另行签发。合同和executionLeaseBinding中的revocationEpoch=19是历史准入基线；后续检查可以在20或更高完整视图重新授权，合同字节不变。

所有占位摘要和签名必须在正式测试夹具中替换为合法值并重算。本文仅验证JSON可解析与字段对照，不声称已有正式JSON Schema、有效密码签名或运行测试通过。runtimeIdentity示例表示候选Prime路线，不证明该路线已通过实验。

~~~json
{
  "schemaVersion": "2.0",
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
  "sourceBundleDigest": "sha256:<source-bundle-digest>",
  "runtimeDriverId": "prime-runtime-driver",
  "runtimeDriverVersion": "1.3.0",
  "inputArtifactManifestRefs": [
    "cas://sha256/<source-artifact-manifest-digest>"
  ],
  "repositoryId": "repo-pi-demo",
  "baseGitObjectId": {
    "algorithm": "sha256",
    "hex": "<git-object-hex>"
  },
  "executionLeaseBinding": {
    "leaseId": "0199c200-8000-7000-8000-000000000008",
    "ownerInstanceId": "node-linux-01",
    "controlPlaneEpoch": 42,
    "resourceExecutionEpoch": 7,
    "revocationEpoch": 19,
    "leasePolicyDigest": "sha256:<lease-policy-digest>"
  },
  "budgetReservationId": "0199c200-9000-7000-8000-000000000009",
  "budgetPolicyDigest": "sha256:<budget-policy-digest>",
  "allowedCapabilities": [
    "workspace.read",
    "workspace.write",
    "tool.test"
  ],
  "networkPolicyDigest": "sha256:<complete-offline-network-policy-digest>",
  "filesystemPolicyDigest": "sha256:<filesystem-policy-digest>",
  "gitPolicy": {
    "remoteCredentials": "NONE",
    "remoteWrite": "DENY",
    "allowedPathPolicyDigest": "sha256:<path-policy-digest>"
  },
  "runtimePolicy": {
    "rlmForWriteAttempt": "DISABLED",
    "modelCallMode": "NODE_MEDIATED_CONTROL_PIPE"
  },
  "outputContract": {
    "requiredArtifactTypes": [
      "PROPOSED_TREE"
    ],
    "maxArtifactBytes": 1073741824,
    "requiredEvidenceTypes": [
      "ATTEMPT_TERMINAL_ENVELOPE"
    ]
  },
  "revocationEpoch": 19,
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
    "schemaVersion": "2.0",
    "payloadDigest": "sha256:<contract-digest>",
    "controlPlaneEpoch": 42,
    "signedAt": "2026-09-04T08:09:59Z",
    "signature": "<base64url-signature>"
  },
  "runtimeIdentity": {
    "runtimeName": "prime-agent",
    "runtimeVersion": "0.9.1",
    "runtimeSourceDigest": "sha256:<prime-source-digest>"
  },
  "runtimeIntegrationSnapshotId": "0199c200-5900-7000-8000-000000000005",
  "runtimeIntegrationSnapshotDigest": "sha256:<runtime-integration-snapshot-digest>",
  "inputBindingSnapshotId": "0199c200-5a00-7000-8000-000000000005",
  "inputBindingSnapshotDigest": "sha256:<input-binding-snapshot-digest>"
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

在本页完成签署前，本文件保持“功能与架构修订候选稿”，不得被表述为已完成生产架构批准。

修订与验证状态见第28章；技术未决项与阻塞功能见§27。本页签署不替代各项实际运行证据。

---

## 附录 E：术语表

| 术语 | 当前含义 |
|---|---|
| Candidate | 未含自身审批和最终发布引用的不可变候选内容；先得到摘要再审批 |
| Snapshot | 冻结内容快照；若包含审批引用，必须单向引用已批准Candidate |
| Template | 可复用政策模板，需审批；逐任务实例由可信编译器确定性产生 |
| Publication | 指向批准Snapshot的可变发布指针，单独CAS；不混入Snapshot内容摘要 |
| InputBindingSnapshot | 可信服务将计划内上游槽位绑定到已发布精确产物的不可变对象 |
| RequiredRunBinding | Lifecycle维护的当前有效Run指针，用于Repair替换及Task验收 |
| Attestation | 对精确事实和范围的签名证明；不自动等于业务成功 |
| historical revocationEpoch | 计划/合同创建时的不可变撤销准入基线 |
| checkedRevocationEpoch | 本次依赖闭包实际校验的完整全局日志水位 |
| RevocationViewCheckpoint | 带有效期和完整日志身份的签名head；提供有界新鲜度，非瞬时全局一致保证 |
| RevocationCheckAttestation | 可信闭包、动作、资源与当前水位的CLEAR/REVOKED证明 |
| Grant | 绑定主体/动作/资源的能力或额度；额度委派与当前执行权分别校验 |
| BudgetGrant | 向唯一Gateway代次分配的不可重叠额度；不是可复制的钱包 |
| InvocationReservation | 调用前落盘的资源预留及发送状态 |
| UNKNOWN | 已可能发送但结果不明，必须占额/对账，不等于未消费 |
| TerminalReportGrant | 只能停止、收集和封存失败/终态事实，不能继续执行或发布成功产物 |
| Fencing | 以控制面与资源代次拒绝旧所有者；无关撤销刷新不改变资源代次 |
| CommitBundle | Gate前固定的Git对象包；Stager不重建对象 |
| AuthorizationBinding | 候选逻辑操作的追加授权历史，不改变operation key或CommitBundle |
| OutcomeEvidenceProfile | 每类终态所需证据清单；失联失败档案不可当成功档案 |
| PositiveRunE2ESR | 运行级正向成功率；Phase2主门槛 |
| PositiveTaskE2ESR | 每题3次全部成功的任务比例；用于稳定性报告 |
| Profile | 按阶段固定的功能开关、合同依赖和拒绝行为 |
| CAS | 对预期状态/rowVersion/ref值的原子条件更新 |
| NO_VERDICT | 证据不足以判定，不解释为成功 |

## 附录 F：规范来源与显式承接清单

本附录是来源索引，正文和规范性附录H为当前权威。v1.0～v1.3.1保留作为历史材料，不要求实现者阅读旧版才能决定当前行为；未列出的旧条款不得自动进入验收。

| 历史设计意图 | 本版已承接规则 |
|---|---|
| ADR-01 唯一输出状态权威 | §5/§8：OUTPUT_STAGED与选择由Lifecycle写入 |
| ADR-02 失联节点强制终态 | §8/§11.1：DB/Lease/fencing形成平台失败证据，不等待失联Node |
| ADR-06 Gate可信捕获 | §6.10/§17.2.1：宿主逐步捕获、冻结Oracle和发现策略 |
| ADR-07/08 RLM静默与危险解释器 | §6.7：P2关闭RLM；未来只读启用另验边界 |
| ADR-14 外部ack丢失先读回 | §8.5/§12.8：不明写入保持对账，禁止盲重发 |
| ADR-15 离线依赖与隔离 | §1.2/§6.14：依赖在MicroVM预构建，Attempt/Gate断网 |
| ADR-17 取消完整性 | §8/§13.3：报告权分离，取消与dispatch竞争先对账 |
| ADR-19 渐进式成功 | 本版profile禁用；不得由旧状态名引入成功路径 |
| ADR-21 预算异步权威 | §18.3：Ledger额度+持久消费Journal+保守未知负债 |
| ADR-23 Trace贯通 | §9.2/§13：各作用域身份与事件来源可追溯 |
| ADR-24 有限重试 | §11.1/§18.3：StateDeadlinePolicy、累计预算与实际物理调用计数 |
| 高风险评审独立性 | §6.3：真实上游/模型族/故障域规则及来源证明 |

旧的全局水位精确比较、最终RolePack直接审批、所有测试路径禁止、整体终态匹配95%门槛和工期/人员评估已由§22.1显式处置；不得再次以“旧规则更严格”为由回填。

## 附录 G：不可变摘要构造依赖（非规范性派生视图）

本表是手工整理的待核验视图，箭头统一表示“左侧对象内容摘要依赖右侧已存在摘要”。它不表示事件执行顺序。正式Schema产生后应从DigestProfile引用字段生成并比对，本文未声称已有生成器或CI通过结果。

| 消费对象 | 构造依赖 |
|---|---|
| PolicyApprovalScope[ROLE_PACK] | RolePackCandidate、治理政策和评测事实 |
| PolicyApprovalDecision | Scope、Candidate、不可变Proposal身份 |
| PolicyApprovalSet | Scope、全部Decision |
| RolePackSnapshot | RolePackCandidate、PolicyApprovalSet、Decision摘要 |
| RolePackCandidate | Prompt、ToolPolicy、SkillBundle、Schema、编译器输入；不引用自身审批 |
| SkillBundleSnapshot | BundleCompositionScope、已批准Package、对应PolicyApprovalSet |
| CompatibilitySnapshot | CompatibilityCandidate、对应PolicyApprovalSet |
| RuntimeIntegrationSnapshot | RuntimeIntegrationCandidate、对应PolicyApprovalSet |
| ExecutionPlanSnapshot | TaskSpec、Workflow/Evaluation模板及实例、Source、Role/Skill/Route、镜像、Compatibility、RuntimeIntegration、InputBindingPolicy |
| InputBindingSnapshot | ExecutionPlan、plannedAttemptInput、已发布上游Manifest或SourceBundle |
| AttemptContract | ExecutionPlan、InputBinding、冻结路线/镜像/Role/Skill及静态执行策略 |
| AttemptTerminalEnvelope / 输出ArtifactManifest | producer AttemptContract和实际输出字节 |
| CommitIntent | SourceBundle、选定输出Manifest、固定操作身份和提交模板 |
| CommitBundle | CommitIntent、SourceBundle、选定输出Manifest、精确Git对象 |
| GateAttestation | GateExecutionFact、CommitBundle、EvaluationPlan |
| EvaluationVerdict | EvaluationPlan、GateAttestation、适用的SemanticReviewAttestation |
| PreDeliveryEvidence | 合同、CommitBundle、Verdict及证据引用 |
| DeliveryAuthorization | CommitBundle、Verdict、PreDeliveryEvidence、当前撤销检查证明 |
| GitStagingResult | CommitBundle、实际AuthorizationBinding、Git读回 |
| DeliveryEvidenceBundle | PreDeliveryEvidence、GitStagingResult、最终对账与检查证明 |
| BudgetGrant | 已存在ExecutionPlan、预算政策、Ledger预留身份；Plan不反向引用Grant |
| RevocationCheckAttestation | 被检查根摘要、依赖闭包摘要、当前Checkpoint；被检查对象不反向引用本次检查 |

InputBinding属于后续Run对已存在上游产物的引用，按Workflow拓扑排序；不是上游Contract等待自己的输出。Snapshot和Publication状态、entity ID血缘、数据库反向索引、撤销日志subject关系与事件处理顺序分别建模，不要求把所有不同语义的图混成一个无环图。

Phase0核验必须包括：RolePack审批链无环；ExecutionPlan不引用未来BudgetGrant/InputBinding；AttemptRoute不引用AttemptContract；CommitIntent不引用未来Verdict；Review不等待自身Verdict；不通过删除签名覆盖字段人为消环。


## 附录 H：Phase 2 功能Profile（规范性）

本附录与正文同为规范；冲突属于本版缺陷，必须修订后才能批准，不能由实现者任意取舍。

~~~text
Phase2Profile:
  profileId = PUBLIC_SINGLE_NODE_CANDIDATE
  profileRevision = 1
  schemaVersion = 2.0
  dataClasses = [PUBLIC, SANITIZED_PUBLIC_EQUIVALENT]
  executionTopology = SINGLE_NODE
  allowedTaskClasses = [BUG_FIX, TEST_ADDITION, SMALL_FEATURE]
  runKinds = [IMPLEMENTATION]
  deliverableKinds = [CODE_CHANGE]
  semanticReviewEnabled = false
  repairEnabled = false
  rlmEnabled = false
  runtimeSkillMutationEnabled = false
  automaticReviewRequestEnabled = false
  automaticMergeEnabled = false
  productionDeploymentEnabled = false
  networkMode = DENY_ALL
  modelCallMode = NODE_MEDIATED_CONTROL_PIPE
  deliveryMode = CANDIDATE_BRANCH
  currentRevocationCheckRequired = true
  durableBudgetReservationRequired = true
  independentGateRequired = true
~~~

SANITIZED_PUBLIC_EQUIVALENT是入口分类说明，落入合同前必须由准入政策证明已脱敏并规范化为PUBLIC；不得通过自报此标签绕过INTERNAL控制。

| 功能 | P2要求 | 开启或变化条件 |
|---|---|---|
| Task/Run/Attempt/CandidateOperation | 必须 | 状态白名单、单写入者、全部失败出口 |
| Source与依赖镜像 | 必须 | 固定Git对象、离线依赖、审批签名及兼容性 |
| Role/Skill治理 | 必须 | RoleCandidate无环；固定SkillBundle与审批链 |
| InputBinding | 必须实现初始Source绑定 | 动态上游槽位在P3开放 |
| RuntimeIntegration | 必须 | 至少一个真实Driver通过同合同测试；不按名称豁免 |
| Revocation/Grant | 必须 | 历史基线、当前checkpoint、闭包检查、报告权分离 |
| BudgetJournal | 必须 | 调用前持久预留、独占代次、未知占额 |
| Code Gate | 必须 | 受保护Oracle、独立执行器、精确CommitBundle |
| Reviewer/Semantic Judge/Repair | 拒绝 | P3批准profile并通过InputBinding/独立性/有界循环测试 |
| PR/MR自动创建 | 拒绝 | 独立CreateReviewRequest合同、操作身份、读回和对账 |
| Merge/Deployment | 拒绝 | 独立未来规范，不在P3或Canary默认开放 |
| INTERNAL/HA | 拒绝对应声明 | 分别通过P4A/P4B条件 |

Profile自身作为PHASE_PROFILE政策对象审批与签名；编译器、Node、Gateway、Gate与Stager在各自边界强制校验。禁用功能遇到调用返回PROFILE_FEATURE_DISABLED并记录审计，不能静默转成另一种未经批准的动作。

附加政策对象的最小内容：InputBindingPolicy定义slot类型/producer/selection/只读规则；ClosurePolicy定义各Schema的依赖引用和签名key描述符；LeasePolicy定义TTL/heartbeat/renew/pauseGrace；RevocationFreshnessPolicy定义当前视图窗口；StateDeadlinePolicy定义各非终态的时限和出口；OutcomeEvidenceProfile定义证据分档；BudgetPolicy定义累计资源上限和Grant回收规则。它们均采用“无审批回指的内容快照 → PolicyApproval → 分离Signature/Publication”的模式，所有业务字段进入DigestProfile。

RuntimeIntegrationCandidate的payload覆盖§6.7全部集成参数与测试证据，不含其自身审批；能力实验只绑定原始源码/shim/OCI字节摘要，不引用尚未生成的正式RuntimeImageSnapshot，避免实验与发布元数据循环；subjectType=RUNTIME_INTEGRATION审批Candidate。RuntimeIntegrationSnapshot含candidateId/digest、policyApprovalSetId/digest、适用环境/数据等级、payloadDigest和签名。Snapshot使用的环境/数据等级不得超过Candidate审批范围。RuntimeImageSnapshot增加runtimeIntegrationCandidateDigest，CompatibilityCandidate绑定该RuntimeImage；计划另绑定正式IntegrationSnapshot并逐字段核对同一Candidate，避免镜像与集成Snapshot互引。

本版所有新增Schema、API、事件和表都必须进入契约manifest；仅有本文中的类型名称不表示实现完成。

## 附录 I：关键架构路径示例（说明性）

### I.1 无关撤销

任务A的合同基线为19，使用Skill-X；任务B使用Skill-Y。撤销Y提交全局序号20。A旧的动作Grant暂时不可用于已看到20的代理；服务补齐日志，重算A的闭包为CLEAR，签checkedRevocationEpoch=20的新证明与短Grant。A的ExecutionPlan、AttemptContract、输入和预算Grant摘要不变，继续原序号之后的调用。B闭包命中，执行权fenced，停止并隔离。A若在同步时Lease已过期，则不得借CLEAR复活旧Attempt。

### I.2 未知模型调用

父任务额度100，已向Gateway-G1分配40，其中一次调用预留10并落盘发送意图后断电。无法确认Provider是否执行时，这10仍占额；若整个Journal丢失，原Grant未确认部分全部锁定。新实例只能申请父级尚未分配的60之内的额度，不把原40重新发放。结算证据到达后按invocationId确认一次，才释放确定未使用的差额。

### I.3 候选写入与取消

候选操作PREPARED且证明未dispatch时，取消可使Operation CANCELLED并最终Task CANCELLED。若dispatch intent已持久化或外部CAS可能发出，则取消返回需对账；读回匹配原commit与operation key时CONFIRMED，记录已发生写入，不能标成“无副作用取消”。任何后续删除是独立操作。

### I.4 Review与Repair

实现Run的选定Manifest先发布；Review Run通过InputBinding读取该Manifest，产出结构化风险证据并通过证据Schema验证后终结。实现Run的Verdict消费Review证据；若允许Repair则创建新Run、绑定父输出和失败证据、重跑全部Gate。Review不为自己再创建Review，旧轮次证据不能证明新轮次对象。
