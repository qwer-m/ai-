# 终态 pending 收尾独立只读审查

审查范围：`autonomous_budget.py`、`usage.py`、`run_repository.py`、`lifecycle.py` 的最新修改，以及它们在 `runtime.py` / `service.py` 中的直接调用与取消出口。生产处于冻结状态，Run 41 已启动；本审查没有修改生产、调用模型或读写业务数据库。

## 结论

新终态收尾在统一 `transition_run` 路径内的计数和事务顺序正确；已预先标为 failed 的当前节点也能被显式选中，未发现这些新增函数本身的阻断问题。但旧 `runtime._mark_node_cancelled` 仍可在服务端终态事务后提交 worker 内存中的 `sdk_state` / `output_payload`，这会绕过 journal 的执行围栏。因此，“终态后旧 worker 不能覆盖检查点”的保证目前只覆盖 journal 入口，不能扩展为所有 runtime 写入入口。

当前确认的是生产代码中可调用的无围栏写路径；尚未证明 Run 40 在线上实际触发 pending 回退。本次真实终态 QA 使用的 Run 40 取消快照在原线上状态中确有一个 pending，但回放该旧快照覆盖新终态属于明确标记的派生执行边界。usage 负责 agent 已补充隔离 SQLite 回放，不能把它称为线上竞态复现。

## 新增四文件的链路核对

1. `autonomous_budget.interrupt_pending_budget`（133）：复用 `_validated_budget_state` 校验完整预算，返回深拷贝；只将 pending 归零并累加到 interrupted。charged、reported_usage、completed 均保持不变，重复执行不再次累加。收尾不重新执行额度准入，因此实际已超额账本也仍可合法终止。
2. `usage.interrupt_node_pending_requests`（16）：处理当前 action_checkpoint 预算、节点 budget_usage 和当前 items 的预算，不改历史 retry_history。新状态写回后沿原游标 `sync_action_usage` 入账，未建立第二种计费规则。
3. `run_repository.finalize_unfinished_node_runs`（295）：由调用者先持有 Run 锁；先 flush 事务内最新检查点及预标失败状态，再查轻量主键；逐节点锁定并刷新 sdk_state，预算收尾后标终态。当前明确指定且已与目标同状态的节点也被选中，解决异常处理器先标 failed 导致漏收尾的问题。其他历史终态节点不会被扫入。
4. `lifecycle.transition_run`（74）：状态、租约、审批失效、节点预算、节点终态和事件都由同一调用者事务提交，内部没有新增 commit。`service.cancel_run` 先 `get_run_for_update` 再调用；`runtime._handle_workflow_exception` 先 rollback、锁 Run、核对 claim / lease，再调用。

现有 [run40-terminal-budget-real-data.json](2026-09-28-run40-terminal-budget-real-data.json) 的结论与脚本一致：真实副本覆盖取消、预标失败节点、重复终态、事务回滚、journal 旧 claim 拒绝；agent_map 只有 18 项已完成预算，不覆盖正在请求的 map 项，也不覆盖 MySQL 并发取消。

## 实质缺陷：旧取消写入口仍提交内存快照

`runtime.py:274` 的 `_mark_node_cancelled` 在任何 claim / lease 校验前执行以下操作：

- 有参数时直接赋值 `node_run.output_payload` 和 `node_run.sdk_state`。
- 即使节点已经 cancelled，也仍会保存传入 JSON、清租约并 commit。
- 事件函数 `_event` 只调用 `append_event`；后者加 Run 锁并 flush，但不会核对旧 worker 的执行身份，锁本身不是身份围栏。

冻结源码的 AST 扫描共找到 10 处调用，不以手工计数遗漏调用：

| 位置 | 调用者 | 当前取消后行为 |
| --- | --- | --- |
| 1721 | `_execute_node_with_retry` | 为看到的 running 节点再标取消并提交 |
| 2094 | 并行 map 的 `persist_progress` | 捕获续租检查的取消后，反而提交本地输出与状态 |
| 2645 | `_execute_agent_map_parallel_async` 尾部 | 提交并行 map 本地输出与状态 |
| 2819、2891、3018、3039 | `_execute_agent_map` | 开始前、异常、完成进度和尾部的取消分支都可提交本地状态 |
| 3204 | `_execute_agent_node` | 请求前的取消分支再次写节点 |
| 3399 | `_complete_node_execution` | 完成后发现取消，仍提交刚算出的输出与状态 |
| 3639 | `_handle_workflow_exception` | 已取消分支仍可能补写节点 |

正常 action_network 在模型等待时收到取消，一般由 `_run_standard_agent_async` 取消任务后直接抛 `_RunCancelled`，服务端已标终态则异常处理器不再调用旧入口。所以不能仅凭存在 `_mark_node_cancelled` 就声称 Run 40 的常规取消一定回退 pending。问题更广的是此入口明确允许终态后覆盖已经持久化的快照，尤其 map 的取消路径会主动传入本地快照。

## 两个关联写入时序必须一起处理

并行 map 的 `cancel_pending_instances`（2138）先取消并 await 子任务，然后写 `map_item_cancelled` 事件，直到末尾 `persist_progress` 才校验当前 claim。事件写入已有显式 flush，因此不能只删除最终 `_mark_node_cancelled` 而保留前面的失权写入。应在 await 完成后、任何事件或节点修改前复用现有 `_renew_progress_lease`；外部取消或失权立即 rollback 并停止。校验应在 await 后，避免持有 Run 锁等待网络任务收尾。内部 sibling_failed / deadline 取消仍可在当前有效 claim 内保留诊断。

并行 map 的 fatal_error 分支（2653—2680）直接赋值 sdk_state 并 commit；上面的 `_refresh_run_is_cancelled` 是普通状态读取，不能构成提交围栏。应在该本地保存事务开始处复用现有 `_renew_progress_lease`，再保存失败详情；失权时回滚。异常最终仍由统一 Run 终态入口完成节点终止。

## refresh / autoflush 的证据和界限

当前 `core/db/database.py:84` 的正式 SessionLocal 明确为 `autoflush=False`、`expire_on_commit=False`。因此不能声称当前默认生产会话的 `refresh()` 已自动把旧脏状态 flush。

安装中的 SQLAlchemy `orm/session.py:3141` 确实在 `Session.refresh` 查询前调用 `_autoflush()`；`runtime.run_agent_workflow` 又允许调用者传入 Session。因此，围栏函数本身应显式保证查询身份之前不 flush，而不是依赖默认 Session 配置。

- `_refresh_run_is_cancelled`（266）当前整行 refresh，既不锁定也不检查 claim。应将原完整 refresh 放入 `db.no_autoflush`；一旦确认 cancelled，rollback 并停止写入。完整刷新还负责读取最新已持久 `run_context`，不能为减少字段而改成只刷状态（见下方复核）。它不能代替提交处的锁定 claim 检查。
- `_renew_progress_lease`（321）已在锁定读取前保存 expected claim，且刷新 status / claim / heartbeat / expiry 四字段，随后验证 token 与租约。应将锁定 refresh 放在 `no_autoflush` 内，并在 `_RunCancelled` / `RunLeaseLost` 时 rollback 再抛出，确保旧脏 Node/Run 不留给后续 commit。
- 对轮询状态为“未取消”的结果，不可据此允许直接写；并发终态可能在检查后发生，必须由写事务的锁定 claim 校验决定。

## 后续最小实施方案（尚未应用）

1. 删除 `_mark_node_cancelled` 及唯一关联的 runtime `release_run_lease` 导入。所有旧 worker 取消出口只 rollback、停止任务、向上传播取消，不补写输出/节点状态/租约。
2. 收紧现有两个状态/续租 helper 的查询边界；不要新增另一套终态组件或恢复兜底。
3. 并行子任务取消在 await 后、写事件前复用同一 claim 检查；fatal_error 本地保存也先检查。
4. Run / 节点终止及 pending 收尾只由现有 `transition_run → finalize_unfinished_node_runs` 提交。
5. 用真实 Run 40 副本扩展旧快照写入边界回放；保留原新终态 QA 的回滚、幂等、预标失败节点及 journal 失权覆盖。任何外部 Session autoflush 检查都应明确是会话配置边界，不冒称默认生产已发生自动 flush。

Run 41 运行期间维持源码冻结，不做线上取消或竞态实验。实施授权和应用时点由主线程在真实运行终止后决定。

## 补充复核：完整账本刷新必须保留

撤回本文初稿“`_refresh_run_is_cancelled` 只刷新所需状态字段”的建议。`runtime._complete_node_execution:3398` 先调用该函数，接着 `_renew_progress_lease`，随后在 3410 从 `run.run_context` 深拷贝完整账本，只替换 artifacts，再整体写回 Run。当前 `_renew_progress_lease` 只刷新租约相关四字段，因此若把前一步也缩成 status 字段，后续合并将失去刷新完整 run_context 的保证，可能带回旧 usage / agent_instance_quota_usage。

现有 journal 逐请求保存路径通过 `get_run_for_update().populate_existing()` 读取完整 Run，在 `_save → sync_action_usage` 中提交最新累计账本；并行专业调用也会使用独立 worker Session。不能假设节点开始时加载的 Run 实例已经包含所有后续持久变化。新方案保留完整刷新，在外面加 `no_autoflush` 和取消后的 rollback，不削弱账本读取范围。

若后续把节点完成阶段的“先读状态再续租”合并成一次读取，应在该短提交事务内锁定、完整刷新 Run，再核对原 claim，最后合并 artifacts；不能只刷新租约字段，也不能把持有 Run 锁的查询随意放到会跨模型 await 的轮询处。本次只读审计没有更改这些入口。

## 补充复核：前端更新不依赖 node_cancelled 事件

现有工作台没有订阅 `run_cancelled` / `node_cancelled` 事件来推进节点状态，而是使用完整 Run 快照：

- `useAgentWorkspace.ts:323` 在 pending / running 期间每 2 秒调用 `openRun → getAgentRun`；`openRun:179` 接收完整 Run 后更新 activeRun。
- `cancelRun:531` 调用取消 API，537—540 先 `commitRun(response.run)`，再 `openRun` 重新读取详情。因此本页面发起取消不依赖下一次轮询或节点取消事件。
- 后端 `routers/orchestration/agents.py:39` 的 `_run_payload` 调用 `list_node_runs`，由 `serialize_run` 输出完整 `nodes`；取消路由在 service 的终态事务提交完成后调用该序列化入口。
- `AgentWorkspace.tsx` 使用 Run / Node 的 status 映射中文状态和业务阶段；没有根据 `node_cancelled` 事件修改节点状态的 reducer。
- `/runs/{run_id}/events` 只是按权限返回历史事件列表。本轮搜索未发现生产前端或后端以特定取消事件消费结果投影状态。

因此，删除旧 worker 的 `node_cancelled` 补写不会要求 UI 兼容逻辑。节点 cancelled/failed 状态已由统一终态事务更新，取消响应和详情 GET 会自然带回。上述结论来自代码流通审查，未冒称本次做过浏览器点击取消验证。

## 补充真实数据基线回放结果

[run40-terminal-stale-worker-baseline-real-data.json](2026-09-28-run40-terminal-stale-worker-baseline-real-data.json) 已由 usage 负责 agent 生成。使用真实 Run 40 原 SDK/预算，在隔离 SQLite 中先执行当前统一终态收尾，再调用旧入口：

- `_mark_node_cancelled` 在 autoflush=False（与正式 Session 配置相同）和 True 两种会话中，都发出 UPDATE 并 commit，将 pending=0 / interrupted=1 覆盖回 pending=1 / interrupted=0，完整 SDK 状态恢复成旧 hash；Run usage 未变。
- 两个 refresh helper 在 False 时读取期间未发出 UPDATE，但旧 Node 仍 dirty，随后 commit 会覆盖终态；在 True 时读取期间就因自动 flush 提前 UPDATE。

此报告把可调用代码缺陷提升为真实快照上的数据库持久化复现；它仍不证明原线上 Run 40 发生过同样时序。后续修复验收需检查入口期间和随后一次 commit 都不改变已持久终态，不能只验证函数抛出了取消异常。
