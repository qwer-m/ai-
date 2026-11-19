# Agent 平台数据库与持久化核查

核查日期：2026-11-11。对象为当前工作区代码、真实本机 MySQL schema、保留的真实运行、Run 46 导出快照。全程未调用模型、未创建或重跑任务、未修改数据库或生产代码。下文把已发生的问题、代码可达的设计缺口、容量风险分开；不以“使用 JSON”或“表少”本身作为缺陷。

## 结论与证据边界

本次收费浪费不能只归因于数据库。数据库侧已经确证的主要问题是：失败历史自动物理删除、已付费中间成果和成本账本依附可变运行快照、控制事务反复处理整份大 JSON。另有持久化后投递的崩溃窗口以及时间字段混存。不能据此断言 Run 46 的租约过期就是数据库锁造成的；尚无对应时段的锁等待证据。

当前 Run 47 已失败（父任务核查为 HTTP 402），本审计不尝试恢复它，不消耗剩余 API。

### 真实数据库读取得到的数据

- `AGENT_RUN_HISTORY_LIMIT=1`，与 `backend/core/settings/config.py:122-124` 的默认值一致。
- 当前保留 Run：13、15、24、26、27、47。Run 42～46 均不在数据库；Run 46 文件快照仍在 `output/qa/2026-11-11-crm-run46-source-snapshot.json`。
- 62 个节点的 `sdk_state + input_payload + output_payload` JSON 长度合计 **30,081,906 bytes**。最大 `sdk_state`：节点 123 为 **3,744,750 bytes**，145 为 3,627,976，226 为 3,405,738。该结果来自无排序读取长度标量后在 Python 排序，未读取或输出凭据。
- Run 46 节点 370 的紧凑 JSON 快照：领域状态 1,376,269 bytes，已验收子调用目录 337,526 bytes，动作回执 75,338 bytes。领域状态中 `planning` 926,177 bytes、`accepted_plan` 329,271 bytes、`authority_inputs` 119,888 bytes。85 个验收子调用保存在同一个检查点中。
- Run 46 三个 `resolve_generation` 尝试 368、369、370 各有约 1,790,856 bytes 的 `sdk_state`，说明恢复尝试会复制完整快照。这里是逻辑 JSON 体积，不等同实际网络、redo 或磁盘写入量。
- MySQL `@@session.time_zone=SYSTEM`、`@@global.time_zone=SYSTEM`、隔离级别 `REPEATABLE-READ`；同次查询 `NOW()=13:57:28`、`UTC_TIMESTAMP()=05:57:28`。
- Run 47：`created_at=2026-11-11 13:44:37`，`started_at=2026-11-11 05:44:37`，`finished_at=2026-11-11 05:52:19`。创建时间为数据库本地时间，执行时间为应用 UTC。

## D01 · P1 · 失败历史清理删除已付费证据与可复用成果（已发生）

**位置：** `backend/core/settings/config.py:122-124`；`modules/agent_platform/retention.py:14-34,37-59`；`modules/agent_platform/run_repository.py:290-357`。

终态每次完成就执行清理；默认同源只留一个历史。仓储在 341～355 行依次断开子 Run 的父指针、删除审批、事件、节点和 Run。只对特定 `AgentContextRecord` 引用做保留保护。事件仅保存删除数量，没有被删 Run ID、费用摘要、失败类型或外部审计归档地址。外层 58～59 行还直接吞掉清理异常。

**影响：** 新失败会删除前一次失败的模型用量、验收子结果、重试状态、原始错误和父子关系。现在 Run 42～46 已从 DB 消失，不能再靠运行查询核对整段充值消耗；只能依赖本次手工导出的少量报告。新一轮从头运行时，本应能安全复用的已验收事实和审查结果也可能随之消失。把“界面仅显示最新一条”实现为“删除整份业务审计”是设计错误。

**修正：** 展示策略与保留策略分开。费用流水、失败摘要、运行冻结身份、来源版本和已付费验收产物独立保留；大块原始响应/过期可变快照可按 TTL 归档。清理前先生成可核对的归档清单，失败可见，不能静默吞掉；保留 FK 与依赖引用保护。不要通过把默认 1 简单改成更大数字来替代生命周期设计。

## D02 · P1 · 可变执行快照承载成本账本与验收缓存，整个快照成为控制事务的热点（已证实设计缺口）

**位置：** `backend/core/db/model_defs/agent_platform.py:155-157,201-203`；`modules/agent_platform/action_journal.py:135-175,184-193,314-351,363-420`；`modules/agent_platform/runtime.py:280-326`；`run_repository.py:47-52,605-642`。

每次 `AgentActionJournal` 事务先锁完整 Run，再锁 Node；`_checkpoint` 深拷贝全量检查点、计算完整领域及输入 hash；`_save` 将整份检查点重新赋给 `node.sdk_state`。`save_budget`、读取已验收调用、保存每个子调用等都走相同边界。预算、领域状态、所有历史动作、所有已验收输出不是独立实体，而是同一多 MB JSON 的不同键。

**影响：** 仅更新请求预算或保存一个新子回执也要装载、校验、拷贝并在保存时重写整个检查点。数据随已完成工作增加，后续每一次操作都更贵；多次尝试还复制这些大块状态。续租、取消、事件序号也争用同一 Run 行。独立心跳线程解决事件循环阻塞不等于消除这些锁/数据耦合。

另一个读放大点：`runtime._current_run_status` 每秒以新 Session `db.get(AgentRun, ...)` 读取整行，虽然只需要 `status`；`_renew_run_lease` 的 `get_run_for_update` 同样装载含产物的大 `run_context`。当前 Run JSON 可达 0.6～1.5 MB，属于不必要的控制面数据传输。没有测量实际传输量或锁等待，因此不宣称其是本次租约丢失的唯一原因。

**最小修正：** 保留 Run 状态机、原有 Run 锁与 fencing，不把全部业务字段拆表。先独立持久化每次真实模型请求的身份、预留/结算、供应商 request ID、token/cache usage 和明确“已知/未知”的费用；再让已验收产物成为按内容与执行契约寻址的不可变记录/对象，检查点仅保存引用和当前游标。预算变动不再复制业务领域，缓存读不再锁整个领域快照。状态轮询投影 `status`，续租只投影所有权/租约字段并以当前 token 条件更新。

## D03 · P1 · Run 提交与队列投递不原子，恢复器覆盖不到孤儿 pending（代码路径确证，未制造故障）

**位置：** `modules/agent_platform/service.py:506-512,754-756,848-851`；`dispatcher.py:35-59`；`recovery.py:25-39,76-83`。

创建、重试和批准流程均先提交 pending，再执行 `_start_worker`。恢复器也先提交 pending、再投递。若进程在两步间崩溃，或者 broker 已接受但响应丢失，数据库和队列就不一致。投递包装能处理被当前 Python 捕获的异常，不能处理进程消失的窗口。恢复器只查 `running` 的过期租约，不扫描未投递或长时间未认领的 pending。

**影响：** 前台可能永久显示排队，用户反复点击；DB 状态不等于任务真正进入执行队列。当前未观测到此类孤儿，不能把 Run 47 的 402 归因于它。

**修正：** 在创建/批准/恢复的同一事务写入最小调度 outbox，调度器以业务运行身份幂等投递并确认；扫描过期的未确认投递和未认领 pending。Worker 仍由现有 claim token 认领，不新增第二套执行所有权。不靠无条件重发所有 pending 解决，避免重复调用。

## D04 · P2 · 时间字段混存、部分输出错误标注 UTC（已发生）

**位置：** `core/db/model_defs/agent_platform.py:164-167,205-208,225,245-247`；`core/db/database.py:57-64`；`modules/agent_platform/serialization.py:33-43,65-73,144-146,158,170-171,200-202`。

数据库 `func.now()` 默认按当前本地时区写入 created/updated/requested 时间；Python `datetime.utcnow()` 写入 started/finished/decided 时间。连接初始化没有统一 session time_zone。序列化有的直接返回 naive 本地时间，有的强行补 UTC；`serialize_context_record` 甚至把数据库生成的 naive created/updated 当 UTC 标记 Z。

**影响：** 同一记录创建与开始时间相差 8 小时、事件/审批时间语义不一致，影响诊断排序和界面展示。租约主要使用同一套应用 UTC，不能因为此处混存便断言所有租约比较都错。

**修正：** 统一写入和 API 时间契约为 UTC，迁移时逐列区分历史数据库本地字段与应用 UTC 字段；禁止把历史所有 naive 时间统一加减 8 小时。连接初始化明确 time_zone；API 输出显式时区。

## D05 · P2 · 最终测试用例依附运行 JSON，缺少独立成果版本生命周期（产品设计限制，非本次失败直接原因）

**位置：** `modules/agent_platform/test_generation_workflow.py:372-417`；`results.py:16-22`；`service.py:621-641`；`run_repository.py:324-355`。

当前所谓“落库”是把测试用例、事实绑定、执行主链和终审结果保存在 Run 的 `run_context.artifacts.test_generation`；不是独立用例或成果版本实体。这一实现可以正确保存批量结果，不因 JSON 形式就判为错误。问题是成果与运行保留周期绑定，新成功清理旧成功时，可同时删除旧成果版本。缺少独立稳定的成果版本 ID/引用；导出和读取都必须装载运行大 JSON。

**修正：** 引入最小不可变成果记录（source revision、artifact hash、schema/contract version、review decision、run provenance、case count），Run 只引用成果。先保留整体 JSON artifact，再按确有查询和人工编辑需求拆分用例；不先将每个测试字段关系化。已确认反馈、评测和人工修订引用固定成果版本，旧 Run 的清理不删除成果。

## D06 · P1 · 长期上下文的作用域与所有者字段缺少一致性检查（静态可达风险，无本次多用户泄漏证据）

**位置：** `routers/orchestration/agents.py:224-245`；`modules/knowledge_base_components/context/context_memory_repository.py:94-125,259-317`；`core/db/model_defs/context_memory.py:23-40`。

创建接口只验证 `request.project_id` 的所有权，却原样传入 `scope_id`；仓储只有 `validated_feedback` 强制 scope_id==project_id，普通项目记录没有此约束，用户作用域也没有强制 scope_id==user_id。检索主要按 scope/scope_id（另加反馈专用项目约束），没有统一校验记录 owner/tenant 一致性。普通用户还能创建和确认 global 记录，其检索对所有查询开放。

**影响：** 恶意或错误的 scope_id 可把记录放入别的用户/项目的检索命名空间；global 的含义若不是平台共享知识，还存在跨用户污染。当前表无可用于多用户验证的记录，本审计未插入测试记录，不能声称已经发生泄漏或把它归因于 API 消耗。

**修正：** 写入从当前身份推导作用域 ID；项目 scope 强制绑定已授权 project，用户 scope 强制绑定当前 user。global 区分平台公共知识与用户跨项目知识，公共发布需平台权限。检索同时校验 scope 与 owner/tenant；唯一身份也包含必要的 tenant，迁移旧数据先隔离而不猜归属。

## D07 · P2 · 索引未按主要查询组合设计（容量风险，当前小数据未证明是瓶颈）

实际数据库已有单列索引及下述关键唯一约束，不是无约束数据库：

- `agent_node_runs(run_id,node_key,attempt)` 唯一。
- `agent_run_events(run_id,sequence)` 唯一。
- agent/tool/workflow 定义版本有作用域与 key 的唯一约束。

Run 的同源活动唯一性由项目行锁和锁内查询保证；该策略应保留。当前 `EXPLAIN` 过期租约筛选使用 status 索引后 filesort，pending 查询使用 status/task_id index_merge。没有组合索引 `(status,lease_expires_at,id)`、调度状态组合索引；同源身份藏在 JSON，无法直接索引命中。当前只有 6 个 Run，这不是现阶段烧费的主要原因。

迁移中在主查询稳定后按真实 EXPLAIN 增加少量组合索引；来源规范化版本/逻辑任务标识可投影为真实列，配合现有创建锁，不删除锁来赌唯一键。不要先给每个 JSON 路径建索引。

## 已有有效设计，不能在重构时删掉

- Run 行锁、claim token、租约过期校验和迟到写入 fencing 是正确性边界。
- 动作开始/完成、领域 hash、验收回执与预算在同一事务绑定，避免部分提交。
- Node attempt、事件 sequence 的唯一约束已在真实 MySQL 存在。
- 审批决策先锁 Run、刷新 approval、验证 waiting/pending；来源冲突还校验检查点身份。
- 最终产物必须有独立终审 `approved=True`；不能为了节省请求直接绕过审查或伪造成功。
- 大 JSON 查询已多处采用“轻量 ID 排序，再读取正文”，当前节点列表也先无排序读取再 Python 排序；不能为了精简代码恢复整行 filesort。

## 最小迁移顺序

1. **止损与保留：** 停止自动付费重跑，冻结当前运行、账本与证据；把列表显示与物理清理分开。历史已删的真实费用不能靠估算补成“准确账单”。
2. **每请求流水：** 在现有请求开始/结束预算钩子写独立追加记录，保留真实 request ID、usage 原字段、缓存命中/未命中、route/model 和本次预算预留/结算。先迁移已有可验证数据，缺项明确 unknown。
3. **不可变成果/缓存：** 迁移已验收产物和来源级事实，检查点只保存引用。按具体步骤的输入与契约版本寻址；保持来源、用户/项目、模型与校验器的隔离，不能拿不兼容历史结果冒充命中。
4. **缩小可变状态：** 将当前行动游标和预算聚合与巨大领域正文解耦。先双读校验/受控切换，验证前后状态 hash、累计请求与 token 不变，再移除旧嵌入副本，避免永久双写胶水。
5. **投递 outbox：** 用已有调度器承接事务内 outbox；保留 worker claim/fencing；离线验证提交后崩溃、重复投递、响应丢失和取消的路径。
6. **时间与索引迁移：** 明确各列历史时区再迁移；根据真实查询选择组合索引。保留 FK、尝试身份、来源冻结与审计事件。

本次没有进行模型验收或重跑，也没有实施上述迁移。代码检查、schema 检查和旧真实快照分析不能替代后续零付费的确定性验证及经用户明确授权的受预算限制模型验证。
