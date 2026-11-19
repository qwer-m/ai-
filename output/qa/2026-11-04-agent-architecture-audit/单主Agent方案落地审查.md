# 单主 Agent 方案落地审查

审查日期：2026-11-04（Asia/Shanghai）  
审查范围：`docs/单主Agent架构与改造方案.md`、Agent Platform 前后端代码、ORM 模型、真实 MySQL 结构与运行数据。  
审查目标：判断方案在当前项目中能否直接实现，区分架构遗漏、过渡设计问题、过度设计和设计矛盾，并给出可验证的迁移顺序。

## 一、结论

方案关于“目标驱动、结构化状态、工具选择、反馈循环、失败重规划、人工介入和可量化评估”的方向正确；当前代码也已经具备一部分可复用基础：`ActionJournal` 的原子检查点、`AgentRunEvent` 事件、`sdk_state`、知识库召回管线、文档证据定位和已有冲突确认。

但方案不能按第六节的顺序直接切换为顶层 `AgentProgramDefinition`。当前实现存在五个直接阻断：

1. 顶层 Agent 的重试没有完整恢复现有状态，容易把失败任务变成从头重跑。
2. 运行契约和定义会被同版本原地覆盖，不能保证旧任务继续使用旧指令、工具和模型配置。
3. 持久状态 Agent 要求绑定工具满足 `state_only`，而当前源准备、文档读取和交付能力仍依赖外层节点与 `context.artifacts`。
4. 运行详情接口把完整状态、节点输入和 `sdk_state` 一次返回，真实运行已测得约 5--19 MB；前端每 2 秒轮询该结构，无法作为长期主读模型。
5. 顶层 `agent_network` 运行时前端已按真实入口 Agent 和交付产物显示自主执行阶段；冲突确认入口已经独立可达，但按目标拆分的细阶段、人工暂停后的回答和恢复还没有用真实任务完成验收。

因此第一阶段应先做“运行契约、任务身份、只读详情、人工介入和数据库迁移”基础工程；第二阶段在保留薄 Workflow 壳的情况下接入单主循环；第三阶段才接入长期记忆和更多 RAG 来源；完成真实验收后才删除旧 DAG。单主 Agent 是目标形态，不是第一步的实现形态。

## 二、真实代码与数据库证据

### 2.1 运行入口与状态链路

`backend/modules/agent_platform/runtime.py` 的 `_execute_workflow_nodes` 会把 `AgentProgramDefinition` 包成一个 `agent_network` 节点执行；`run_agent_workflow` 仍是完整的 `AgentRun` 生命周期入口。因此把定义类型改成 AgentProgram 并不会自动获得新的任务生命周期、重试语义或持久化协议。

`backend/modules/agent_platform/service.py` 的 `retry_run` 只对 `WorkflowGraph` 分支复制和恢复节点检查点。顶层 AgentProgram 失败后没有等价的 action checkpoint 恢复路径。与此同时，`runtime.py` 会把 `run_id` 注入节点输入；若用节点输入计算幂等哈希，重试产生的新 Run 会自然得到不同输入身份。

`action_journal.py` 的原子提交保存 action state、回执、预算和事件，但不保存 `context.artifacts`。现有源准备和交付阶段把重要产物放在外层节点完成结果中。若将这些能力直接搬进主 Agent，进程中断后不能假设内存中的 artifacts 仍存在，必须把产物引用或可重建的版本化清单写入持久状态。

### 2.2 定义版本与执行版本

`definition_repository.py` 的 `sync_builtin_agent` 和 `sync_builtin_workflow` 会更新同一版本记录的 instructions、definition 等内容，再启用当前记录并停用旧记录。数据库虽然有 `(scope, key, version)` 唯一约束，但运行本身没有完整的不可变执行契约快照。`semantic_identity.py` 又会按注册表签名拒绝旧运行环境。

这与方案中“旧任务按旧契约完成、新任务使用新契约”的要求冲突。必须在创建 Run 时保存 agent/workflow/tool/model/runtime 的执行快照或不可变契约哈希，并建立发布、排空、切换和旧检查点兼容规则。

### 2.3 工具状态契约

`sdk_adapter.py` 的持久状态模式会校验绑定工具必须是 `state_only`。当前源准备、文档读取、交付等真实能力并非全部满足该约束；而 `test_generation_workflow.py` 只把现有 17 个生成动作注册为 `state_only`。`FunctionTool` 当前还设置 `needs_approval=False`，SDK 内部审批中断也没有映射到平台审批节点。

这意味着“让 Agent 自由选择所有现有工具”当前不可行。必须先为每个动作定义输入、输出、状态写入、幂等键、外部副作用和审批策略，再决定它能否作为主循环工具。

### 2.4 真实数据库快照

只读脚本连接真实 MySQL，以只读事务读取元数据和聚合数据，结果保存在 `database-readonly.json`。关键观察如下：

- 数据库版本为 MySQL 8.0.44；当前 AgentRun 共 4 条，状态为 `cancelled=1`、`failed=1`、`success=2`。样本很小，不能把 50% 当成系统成功率。
- 回填前 `run_context` 最大约 1.22 MB，回填执行契约后当前最大约 1.41 MB；`agent_node_runs.sdk_state` 最大约 3.74 MB；按当前 `serialize_run` 对真实运行重建的完整响应约为 5.2 MB、6.9 MB、9.3 MB 和 19.3 MB。它是未压缩的序列化结构，不等于 HTTP 线上的压缩大小，但足以证明完整详情不应作为前端轮询主契约。
- 现有工作流中每种模式有 4 条带 `execution_mode` 的新定义，同时仍有 22 条 `execution_mode` 为空的历史定义。迁移必须覆盖历史定义、选择规则和回退行为。
- 同一项目内按 `content_hash` 聚合出 5 组重复需求来源，共 39 条文档。`SourceSnapshot.key` 只使用前缀和 content hash，而 `assert_same_source` 又额外检查 document id；来源身份和幂等身份不一致，不能只用内容哈希作为任务身份。
- 当前没有独立的 memory、feedback、preference 或 agent task 表。已有 `rag_eval_*` 表是检索数据集评估，不是 Agent 任务评估或长期记忆。
- 外键删除规则实际均为 `NO ACTION`。项目删除服务手工删除 Agent 运行数据和知识文档，但当前链路没有覆盖全部 RAG 评估资产，也没有把文档数据库、资产目录和向量索引作为一个可核验的删除事务。
- 数据库默认时间和服务端 Python UTC 时间混用；当前序列化只对部分 `started_at/finished_at` 加 UTC 标记，`created_at`、事件和审批时间仍可能保留数据库本地值。评估窗口、排队时长和跨时区页面需要先统一时间契约。

ORM 与实际数据库表的列结构已做 AST 对比，当前比较的 17 张表没有发现列差异；这只能证明现有结构与模型基本一致，不能代替未来迁移。项目启动的 `Base.metadata.create_all` 只负责创建缺失表，不会安全升级已有列、索引、约束或历史数据。

## 三、架构遗漏

### 3.1 缺少任务、尝试和幂等的明确实体

当前 `AgentRun.task_id` 是 Celery 任务 id，不是稳定的业务任务 id；`run_context.run_attempt` 可以通过 `reset_run_attempt` 重置，不适合作为评估分母或尝试身份。方案需要明确：

- `logical_task_id`：一次业务目标，跨重试保持不变；
- `attempt_id`：一次执行尝试，单调递增且不可重写；
- `run_id`：当前持久执行记录；
- `input_hash` 与 `execution_contract_hash`：分别表示业务输入和执行契约。

创建 Run 的幂等键至少要包含用户、项目、workflow、目标、数量/预算、来源文档身份和输入版本。当前按 `source.key + workflow` 合并活动 Run，会把同一来源但不同目标或数量的请求合并。

### 3.2 缺少不可变执行契约和检查点版本

方案提到版本化，但没有把契约快照、检查点 schema 版本、工具语义版本和迁移失败处理写成运行级协议。没有这些字段，旧 checkpoint 无法判断是否可恢复，也不能证明重试使用了同一组工具与模型。

### 3.3 缺少状态与产物的持久化边界

当前状态有 `run_context`、节点 input/output、`sdk_state.action_checkpoint` 和上下文 artifacts 多处载体。方案提出结构化 state，但没有明确哪个字段是唯一事实源、哪些是派生缓存、哪个提交点同时保证 state、artifact ref、receipt、budget 和 event 的原子性。

应先定义：状态只保存可重放的结构化事实和引用；大文本、文档片段、截图、模型输出通过不可变 artifact ref 关联；派生视图可删除并重建；任何外部副作用都先有幂等键和未知结果核验路径。

### 3.4 RAG 没有落到当前 Agent 能调用的工具协议

知识库已经有向量、关键词、混合召回和文档页文本工具，但当前 test_generation coordinator 的工具集合没有直接接入语义 RAG。方案描述了来源装配，却没有规定 Agent 如何提出检索意图、如何限制范围和预算、如何获得证据定位、如何区分无命中与来源故障。

至少要有 `retrieve_candidates`、`read_source`、`validate_evidence` 三类只读能力，并统一返回 `source_key、record_id、revision/content_hash、location、semantic_role、scope、validity、retrieval_route、score`。现有 debug 片段截断到约 1200 字且定位信息不完整，不能直接作为评估证据。

### 3.5 长期记忆的写入治理缺失

当前没有长期记忆表，也没有反馈、偏好、项目知识的授权、有效期、撤销和冲突模型。方案已经区分了三类内容，但还需要落地：来源主体、确认者、适用范围、有效版本、被替代关系、待核实状态，以及“用户纠正立即影响本轮、是否推广需单独确认”的写入协议。

### 3.6 人工介入的类型和前端读写契约不足

现有 `ApprovalDecision.conflict_answers` 是自由文本字典，不能表达未决定、延期、引用权威来源、仅对本任务有效或修改范围。动态工具审批也没有接入同一门禁。

需要把人工请求定义成类型化的 `human_action`：请求原因、影响范围、候选事实、所需答案 schema、有效期、回答者、证据引用和是否改变任务契约。前端需要能发现待处理请求、提交答案、恢复执行、查看答案是否已经被采用。

### 3.7 评估缺少数据保留和归因层

当前只有 AgentRun、节点运行、事件和 RAG 检索评估表，没有独立的任务级评估结果。仅按当前 Run 状态统计成功率会受到重试、reset、保留策略和任务合并影响。

评估至少要把任务成功、尝试终态、人工等待、预算消耗、动作拒绝、重规划、证据覆盖、交付验收和用户/规则反馈关联起来；指标分子分母必须按 logical task 或 attempt 明确选择，并保留未验证、取消、阻塞和部分交付。

## 四、过渡设计问题

### 4.1 不能先删除 DAG 再补能力

当前初始化规划 Agent 要求外层先准备完整的 source_text_inputs、source_vision_inputs、planning_inputs、evidence catalog；当前生成状态的下游失效也以预先完成的 planning/generation/review 记录为基础。直接把这些节点删除，主 Agent 拿到的不是“原始目标和可用工具”，而是一个缺少必要输入的状态。

过渡期应保留薄 Workflow 壳，只负责：用户/项目授权、来源快照和原始输入规范化、执行契约快照、最终交付提交。主 Agent 负责在壳内按需检索、规划、生成、复核和重规划。薄壳不应继续预先生成所有语义事实，否则只是把固定工作流换个名字。

### 4.2 顶层 AgentProgram 的重试必须延后

在 `retry_run` 能恢复 top-level action checkpoint、预算、artifact ref 和审批状态之前，不能把生产入口切到 AgentProgram。先为一条真实失败运行增加恢复测试：进程中断、工具超时、人工等待、外部结果未知、旧 checkpoint 版本不兼容分别验证。

### 4.3 定义发布要先从原地覆盖改为快照发布

先新增不可变 release 或 execution contract 记录，创建 Run 时绑定；seed 只能发布新版本，不能修改已被 Run 引用的内容。旧版本完成或被排空后再禁用。运行详情、节点详情和评估页面都读 Run 绑定的快照，不能再读取当前 catalog 解释历史运行。

### 4.4 前端必须先切换轻量读模型

先提供 `RunSummary`、`RunDetail`、增量事件或按 sequence 查询接口；运行列表和轮询只读 summary，节点、状态、审批和产物按需读取。把 `evaluationService` 和导出消费者从 `run_context.artifacts.test_generation` 的内部路径迁移到稳定的结果视图后，才能改变内部 state 结构。

### 4.5 人工介入入口必须先打通

在切顶层 Agent 前，继续验证 workspace 的 active run 按 workflow 过滤和顶层 `agent_network` 的自主执行阶段；冲突确认入口已经独立呈现，等待审批后页面仍需用真实任务验证请求刷新、类型化答案提交、恢复或明确阻塞原因。否则主 Agent 虽能发起人工请求，用户仍可能无法完成任务。

### 4.6 数据库迁移不能依赖 create_all

每一步需要独立 migration、回填、双读/双写窗口、数量校验、旧 checkpoint 兼容策略和回退条件。新增 memory/evaluation/source snapshot 表时，同时定义项目删除、用户删除、文档撤销、索引滞后和资产清理策略。不能先写模型再依赖启动脚本补表。

### 4.7 预算和并发语义要先固定

当前早期 agent_map 使用 per-agent-instance 配额，主协调器和托管子 Agent 又共享父任务预算；`ActionJournal` 同时只允许一个 started action，批量工具才在内部并发。迁移时应明确任务总预算、动作预算、子任务预算和人工等待是否计费，并先保持串行动作语义。没有预算和并发验收，开放工具选择会改变当前系统的资源边界。

## 五、过度设计

以下内容方向上有价值，但不应进入第一轮改造，否则会把当前可验证问题隐藏在抽象层后面：

- 一开始同时支持多个记忆后端、多个向量后端、事件总线、SSE、跨项目插件市场和通用连接器注册中心；先为现有 MySQL、现有知识库和现有事件接口定义最小适配协议。
- 为记忆、评估、审批各自创建新的 Agent。当前它们首先需要确定性服务和数据协议，不能通过增加 Agent 数量解决边界不清。
- 一开始追求多工具并发、动态 DAG、任意子 Agent 拆分。当前单动作 journal 和预算模型尚未定义并发提交冲突，先支持串行决策、批量工具内部并行。
- 为每一步都做模型评估或用一个总分代表成功率。先保存真实状态、证据、动作和反馈，再按任务类型增加离线评估。
- 在证据引用协议稳定前构造通用 Source Registry。当前 source hash、document id、资产 hash 和索引版本先统一即可。

## 六、设计矛盾

| 方案目标 | 当前实现 | 矛盾与处理 |
| --- | --- | --- |
| Agent 从原始目标自主规划 | 初始化函数要求完整语义事实、计划和证据清单 | 先拆出原始输入/来源绑定与动态规划；薄壳只做确定性准备 |
| 旧任务继续使用旧契约 | seed 同版本原地覆盖，运行按当前 registry signature 校验 | Run 创建时绑定不可变执行契约，发布采用新版本，不改旧版本 |
| Agent 自主选择工具 | 持久模式要求 state_only，部分工具仍依赖 outer node/artifacts | 先逐工具声明状态提交、幂等和副作用，再加入工具目录 |
| 状态与产物可恢复 | journal 保存 action state，artifacts 依赖节点完成 | artifact ref 纳入同一持久提交或定义可重建的 artifact store |
| content hash 可复用来源 | 同项目存在重复 hash 文档，`assert_same_source` 还检查 document id | 任务身份与来源内容身份分离，使用 document id + revision/hash + 作用域 |
| 有量化任务成功率 | task_id 是 Celery id，attempt 可 reset，Run 数量可被合并/清理 | 引入 logical task/attempt，明确保留、取消、阻塞、未验证的统计口径 |
| 存储可以插拔 | journal 依赖当前数据库事务和锁 | 先定义事务能力和 fencing 协议；不满足能力的适配器拒绝启用 |
| 所有工具都可人工审批 | FunctionTool 未启用 approval，SDK 中断未映射 | 统一工具门禁和类型化 human_action，固定节点审批不能代表动态工具覆盖 |
| RAG 按需提供证据 | 当前 Agent 未直接绑定语义 RAG，debug 缺少完整定位 | 先提供候选、回读、验证三段只读协议和 EvidenceRef |
| 历史运行可正确展示 | 前端节点详情读取当前 catalog | 详情必须读取 Run 快照和版本化结果视图 |
| 状态结构化且可观察 | 前端每 2 秒拉完整序列化结构 | 轻量 summary + 增量事件 + 按需详情，完整快照只用于审计/导出 |

## 七、建议的可执行迁移顺序

### 阶段 0：只读基线和契约冻结

不改变业务行为，完成以下工作并保存真实基线：

1. 为 Run 增加 logical task、attempt、原始输入 hash、执行契约 hash 和 checkpoint schema version 的设计与迁移草案。
2. 记录当前 source identity、workflow、agent/tool/model/runtime 的不可变快照。
3. 建立 RunSummary/RunDetail 的字段清单，量出真实运行的详情大小、轮询频率、事件数量和前端消费者。
4. 固定 UTC 存储、带时区 API 输出和历史数据解释规则。
5. 统计重试、审批、取消、reset、失败、未验证和部分交付，不把现有 4 条 Run 推导成系统成功率。

退出条件：旧运行仍能查看，schema/model/数据库校验通过，所有前端消费者列出迁移点。

### 阶段 1：兼容桥

1. 修复 active run 按 workflow、目标输入和来源身份判定；分离业务幂等和内容复用。
2. 实现 Run 级执行契约快照和版本发布；停止同版本原地覆盖已引用定义。
3. 实现 top-level AgentProgram 的 checkpoint 恢复测试，但暂不切换生产默认入口。
4. 提供轻量运行读模型、审批待办查询和类型化冲突回答。
5. 修复项目/文档/RAG 资产删除链路的完整性，明确索引滞后状态。

退出条件：真实失败运行可恢复；旧任务与新发布隔离；前端不再依赖完整 `serialize_run` 轮询；人工等待可闭环。

### 阶段 2：薄壳中的单主循环

保留 Workflow 壳，只把一个真实测试生成场景的规划、检索、生成、复核和重规划交给主 Agent。源准备只形成授权后的来源清单和不可变快照，最终交付由确定性提交动作完成。每个工具必须满足 state commit、幂等、证据回执和审批声明。

退出条件：至少覆盖成功、工具失败重规划、人工澄清、进程中断恢复、重复提交、来源无命中、来源故障和旧版本运行；每条都能从事件、状态、回执和结果视图复盘。

### 阶段 3：按需 RAG 与长期记忆

只在阶段 2 的证据协议稳定后加入三类长期记录：历史反馈、用户偏好、项目知识。每类记录有来源、范围、版本、确认状态、撤销关系和采用结果。RAG 先支持现有知识库和结构化任务记录，第二个存储或索引实现通过真实快照做替换验收后再开放。

退出条件：来源无命中、部分可用、权限拒绝、版本过期、撤销和索引滞后均能被 Agent 与用户区分；记忆不会改变本轮明确指令、工具权限或业务验收门槛。

### 阶段 4：删除旧路径

当阶段 2 的真实验收连续通过，并且新旧结果视图和关键指标完成对账后，再停用旧 DAG 节点、旧前端内部字段和旧 seed 分支。删除前保留历史运行的快照读取能力；任何旧 checkpoint 不能恢复时必须给出明确阻塞原因。

## 八、审查产物与限制

本审查包含真实数据库只读元数据、聚合统计、序列化大小测量和 ORM/数据库列对比。随后按只读预览结果执行了 `migrate_agent_run_contracts.py --apply`：真实库中 4 条历史 AgentRun 只补写逻辑任务身份、执行尝试身份、原始输入指纹、checkpoint schema version 和执行契约快照；没有修改运行状态、产物、审批或事件，也没有启动新 Worker。契约已升级到 schema version 3，回读预览已显示待更新 0 条。脚本和 JSON 结果位于当前目录的 `output/qa/2026-11-04-agent-architecture-audit/`。

数据库行数和序列化大小是 2026-11-04 的环境快照，不能替代长期监控；现有 AgentRun 样本也不足以代表成功率。下一步若要进入代码改造，应先按阶段 0 形成迁移 DDL、契约字段和前端读模型清单，再以一条真实任务做中断恢复和人工闭环验收。

## 九、本轮已执行的第一批改造

在上述审查基础上，本轮已完成一批可独立回退的阶段 0/1 改造：

- active run API 支持按 `workflow_key` 过滤，测试生成工作台不再把其他工作流的活动运行串入当前页面。
- 顶层 Agent 或没有展示阶段时，前端仍显示来源冲突的人工确认入口；回答会提交类型化字段并重新读取运行。
- `serialize_run_summary` 不再复制同一 Session 已加载的完整 `run_context`；前端保留对旧响应缺少新字段的兼容。
- 新 Run 在现有 `run_context` 中记录原始 `input_hash`、`logical_task_id`、`attempt_id`、`checkpoint_schema_version` 和执行契约快照；运行时优先回放快照，只有注册表/执行器版本签名或脱敏模型路由身份不一致才阻断，避免把当前 catalog 的变化误当成历史运行契约。
- 顶层 `AgentProgramDefinition` 的重试已投影为实际的单个 `agent_network` 节点，可复用 action checkpoint；仍需用真实进程中断、人工等待和必需产物场景验证完整恢复。
- Run 执行会优先按契约快照回放 workflow、Agent 和工具定义；已引用的 Agent/Workflow 定义发布会递增版本，不再同版本原地覆盖。工具定义尚未拥有独立发布审计字段，但完整工具快照已冻结在执行契约中，历史回放不会静默读取当前工具目录。
- 执行契约会递归捕获 `subagent_keys` 与 `managed_agent_keys` 的 Agent/工具闭包；普通 Agent、串行/并发 `agent_map`、结果缓存语义哈希和并发 worker 均复用该闭包，快照缺失时直接报错，不再静默回读当前定义。
- 新增按 Run id 的轻量 summary 接口，前端轮询先读取 summary，状态或节点变化及周期性刷新时才读取完整详情。
- summary 查询通过 JSON 路径投影运行身份字段，不加载整份 `run_context`，因此轻量响应仍能返回输入 hash、逻辑任务、尝试、checkpoint schema 和契约 hash。
- 运行事件接口支持 `after_sequence` 增量读取，并返回 `next_sequence/has_more`，前端不必反复传输同一批历史事件。
- 新增按 Run id 的 metrics 接口，从真实节点、事件、审批和评估 artifact 生成执行轨迹摘要；执行成功率明确标注为 `run_status_only`，缺少业务评估产物时返回 `unverified`。
- metrics 查询只加载运行状态、时间、`run_context` 和轨迹所需字段，不再把完整输入和运行产物带入评估接口。
- 阶段切换前新增可插拔上下文契约 `context_memory_port.py`：区分短期检查点、项目知识、用户偏好、长期记忆和已验证反馈；候选带来源版本/内容摘要/作用域/证据，按预算组装并记录拒绝原因，短期状态形状与绑定节点后的 `AgentActionJournal.load/init` 对齐。当时只提供纯契约和确定性组装；后续已接入当前 `retrieve_context`，仍没有伪造长期记忆数据。
- 已用真实 Run 13 做只读验证：15 条节点尝试、14 条成功、1 条失败、1 次重试、215 条事件且未截断；运行状态为 success，但业务评估仍为 `unverified`，没有把执行完成误报成业务正确。
- 已用真实 Run 24 回放冲突确认：收集到 3 条 `missing_support`，未完成答案会被拒绝，预算和回执保持不变，确认结果写入后不会重复弹出表单，等待时间不计入执行预算；验证事务已回滚，证据见 `conflict-confirmation-run24.json`。这证明了历史澄清回放和确定性门禁，尚未证明在线进程中断后的完整恢复。
- 重试链路测试替身已按生产持久化契约补齐 `no_autoflush`、节点输入读写和并发 Agent 输出契约/预算参数；局部修复提示明确保留正确候选内容，`test_runtime_retry.py` 当前 62 项全通过。
- 已执行历史回填：4 条真实 AgentRun 补齐任务身份、原始输入指纹和契约（当前契约 schema version 为 3，包含 Agent 绑定工具身份及脱敏模型路由身份），回读预览为 0 条待更新；没有修改状态、产物、审批和事件。
- 文档语义准备复用同一轮已校验的 manifest 解析表格布局，避免文本、布局和表格读取到不同版本；冲突确认探测脚本也限制最新节点只取一条，避免多次重试记录造成 `MultipleResultsFound`。

这些改造没有完成独立 task/attempt 表、工具级发布隔离、真实进程中断验收或 Agent 在线接入新的 RAG/长期记忆 Port；`agent_context_records` 只完成权威记录表和仓储边界，尚未产生任何虚构记忆数据。它们仍属于后续阶段，不能把当前 JSON 契约快照当成最终数据库设计。

## 十、本轮继续排查的修复与验证

- `ToolExecutionContext.invoke_agents` 已改为结构化 `asyncio.TaskGroup`。子 Agent 主动取消通过内部哨兵恢复为上层 `CancelledError`，未完成的兄弟调用会被收敛取消；局部预算耗尽仍作为局部失败回执保留，不重置共享预算。新增回归测试验证了取消传播和兄弟任务停止。
- 前端节点详情的模型显示已按“运行时记录 → 执行契约 `model_routes` → 执行契约定义 → 明确未记录”取值；契约自己的 `runtime_config.model_route` 优先于当前目录配置，历史运行不会因 Agent 目录改动而显示成另一条路由。
- required artifact 校验经只读审查保持为“键已持久化”。由于通用 `AgentProgramDefinition` 没有声明 payload schema，当前不能把空字典或非字典统一判为无效；具体 AgentProgram 的结构校验应留在其显式 artifact schema 中，避免错误收紧通用契约。
- 验证结果：运行契约、恢复、RAG Port、评估摘要、定义发布和重试聚焦套件 `86 passed`；并发取消/运行 Agent 上下文契约聚焦套件 `13 passed`；前端 `npm run build` 通过；真实数据库只读检查无 inspection error，Run 状态仍为 success 2、cancelled 1、failed 1，ORM/数据库列比较 `differences: []`。
- 全量选定的旧 `test_agent_platform_contracts.py` 仍有 12 项针对已升级 v11 事实支持、工具处理器键和逐设计项复核输入的旧断言；这些是测试夹具迁移项，不能通过增加生产别名或放宽来源/输入校验来消除。在线进程中断恢复、独立长期评估记录和长期记忆表仍未验收。

阶段切换后继续删除了运行兼容：工作流定义先将 88 条缺失模式的真实 DAG JSON 升级为显式 `execution_mode: dag`，解析器随后取消默认 DAG；活动运行查询强制提供 `workflow_key`；重试和 Worker 缺少 schema 3 执行契约时直接阻断，不再回读当前定义或重建旧契约。第三阶段已创建 `agent_context_records` 表，并接入真实知识库检索到 `ContextCandidate` 的来源适配器；这一阶段切换快照当时记载主 Agent 尚未接入，后续已由 `retrieve_context` 完成接入。当前记忆表为空，来源无记录和无精确锚点均明确返回 unavailable。

### 2026-11-05 阶段切换修订

前文“阶段 1：兼容桥”和“阶段 2：薄壳中的单主循环”属于 2026-11-04 的审查快照，不再作为当前实施顺序。当前阶段 2 的代码改造边界已经结束，旧输入归一化、未声明执行模式、缺契约回读当前目录、旧 Run 重建和旧测试别名均已删除；当前测试直接使用正式契约。

阶段 3 已开启：真实数据库已创建 `agent_context_records`，`ContextMemoryRepository` 只接受带证据的显式确认记录，`KnowledgeBaseContextSourcePort` 从真实知识库检索并按文档 `content_hash` 组装来源，`OnDemandContextProvider` 按查询范围选择来源并输出统一 manifest。当前表为空，缺少记录或精确 manifest 锚点会返回 `unavailable`，不会伪造记忆或把截断调试片段当作证据。`migrate_agent_run_contracts.py` 现为只读当前协议校验器，不再执行回填或旧契约重建。

本轮继续接入第三阶段闭环：主协调 Agent 新增 `retrieve_context` 当前工具，明确要求模型提交知识缺口和来源类型；真实知识库、用户偏好、长期记忆、已验证反馈和当前短期状态统一返回 `ContextManifest`，manifest 摘要、查询指纹、渲染正文摘要和拒绝原因写入本次运行 artifact。短期状态只从当前 `AgentActionJournal` 绑定的运行态显式注入，不写入长期记忆表。长期记录检索增加查询文本相关性与作用域筛选；知识库检索片段必须反查到唯一文档页面正文区间才可作为 `available`，否则保留 `unavailable` 原因。已对项目 2 的真实定义执行同步并核验 `retrieve_context` 可由当前协调 Agent 解析；真实数据库当前没有伪造记忆记录。

本轮最终验证：`backend/tests` 全量 **478 passed, 3 skipped**；前端 `npm run build` 通过；真实 MySQL 运行契约校验 4 条均有效，执行模式待升级 0 条，`agent_context_records` 已存在且当前记录数为 0；数据库只读快照 18 张表、ORM/数据库列比较 `differences: []`；项目 2 内置定义同步与隔离回放验证通过，当前协调 Agent 的 18 个工具中包含 `retrieve_context`。

### 2026-11-06 第三阶段收敛

- 撤回本轮使用外部“同步优学”及其他需求文档执行真实生成验收的方向；未创建新的 AgentRun，需求文档只保留原有来源记录，不作为当前第三阶段验收对象。
- `ContextManifest` 增加 `partial` 状态：同一查询存在精确可用来源和无法锚定、撤销或过期来源时，明确报告部分可用，避免把缺口误报为 `ready`。
- 长期上下文生命周期已收敛为候选 `pending` → 人工确认 `active` → 撤销或版本替代；`replaced_by_id` 保留替代关系。模型和运行反馈只能写入 `pending validated_feedback`，不能自动晋升长期事实。
- 新增 Agent 平台受控入口：长期记录查询、候选创建、确认、撤销、替代，以及按运行 manifest 提交上下文采用反馈；反馈来源必须属于该次真实检索清单，不能由请求方伪造来源身份。
- Run metrics 同时汇总上下文检索次数、manifest 的 `ready/partial/unavailable` 分布、被选来源数量和反馈状态；没有人工反馈时仍标记为 `unverified`，不把检索命中率当作业务成功率。
- 使用真实项目知识和真实 Run 15 做回滚式探针：返回 3 个精确来源锚点、1 个短期状态来源和 1 个无法精确锚定的候选，manifest 为 `partial`；探针前后 `agent_context_records` 均为 0，未写入伪造记忆。证据见 `output/qa/2026-11-06-stage3-context-real-data.json`。
- 当前第三阶段代码闭环已完成，剩余的是人工业务对检索采用结果的确认和真实长期记忆的业务沉淀；这两项不能由自动探针代替。
- 反馈语义已进一步收敛：反馈来自人工或独立评估者对本次运行产物是否被具体真实来源支持的判断；`validated_feedback` 允许跨任务复用，但只按产生反馈的项目作用域检索。Run 26 在主 Agent 构建前失败，没有产生检索 manifest、当前产物或反馈；真实库当前 `agent_context_records` 仍为 0。
