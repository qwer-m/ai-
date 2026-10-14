# 取消后旧 worker 回写修复与真实数据验证

Run 41 于 2026-09-28 06:42:10 UTC 自行失败、worker 退出后，经主线程授权应用。生产改动仅在 `backend/modules/agent_platform/runtime.py`，未触及同时核对的 source/support 文件；当前已冻结。

## 修复

- 删除 `_mark_node_cancelled` 及其 10 处调用。旧 worker 看到取消后只回滚、停止并向上传播取消；不补写节点终态、输出、SDK 快照或租约。统一 `transition_run` 仍是 Run 终态及节点预算收尾入口。
- `_refresh_run_is_cancelled` 保留完整 Run 刷新，放入 `no_autoflush`；确认取消后 rollback，清除尚可被后续 commit 写回的脏快照。完整刷新没有退化成仅刷新状态，后续 artifacts 合并仍读取最新已持久账本。
- `_renew_progress_lease` 在 `no_autoflush` 内捕获原 claim、锁定刷新租约字段并核验；取消或失权立即回滚。普通节点和串行 map 的异常分支也直接传播失权，不将其当作业务重试失败。
- 并行 `cancel_pending_instances` 等待本地任务退出后、写任何取消事件前复用 claim 校验；不会跨任务 await 持有新 Run 锁。并行 fatal_error 保存失败详情前也执行同一校验。
- 已取消的工作流异常收尾只 rollback 并返回取消状态，不再查询节点后补写终态；状态、claim、租约不属于当前 worker 时同样回滚。

前端无需补兼容逻辑：取消 API 响应带完整节点快照，工作台取消后还会重新 GET Run；运行中也按 2 秒间隔读取完整详情。节点展示使用节点 status，不靠 `node_cancelled` 事件驱动。

## 真实回放

基线：[run40-terminal-stale-worker-baseline-real-data.json](2026-09-28-run40-terminal-stale-worker-baseline-real-data.json)。旧入口在与生产相同的 autoflush=False，以及额外的 True 配置下，均可把已经收尾的 pending=0 / interrupted=1 覆盖回真实旧快照 pending=1 / interrupted=0。

修复后完整报告：[run40-terminal-stale-worker-final-real-data.json](2026-09-28-run40-terminal-stale-worker-final-real-data.json)，结果 **passed**：

1. 真实 Run 40 / node 994 的相同原始 SDK 状态，在隔离 SQLite 中先经过统一取消事务，再迟到进入节点完成、工作流异常处理、完整刷新及续租校验四个生产入口。
2. 四入口 × 两种 autoflush 配置共 8 例，入口期间和随后显式 commit 都没有发出任何 Node UPDATE。最终完整 SDK hash 仍为已收尾状态，pending=0 / interrupted=1；charged、reported_usage、Run usage 保持原值。
3. 原有正常取消、预先标记 failed 的当前节点、重复终态幂等、事务回滚、journal 旧租约拒绝全部继续通过。
4. 使用 Run 40 的真实成功节点 993 与监控中真实早期用量，在两种会话配置下执行正常 `_complete_node_execution`。内存放入真实旧用量 0 次响应 / 6 次准入，数据库保留实际最终 118 次响应 / 119 次准入；完成提交后完整 run_context 与当前已持久账本完全相等，原节点输出不变。证明完整刷新保留，且不先把旧账本自动 flush 回去。
5. 原线上 Run / Node 指纹与基线完全相同，验证后重新读取确认未变。模型请求 0，线上写入 0。Python 编译和修改文件空白检查通过。

运行命令（backend 目录）：

```powershell
..\.venv\Scripts\python.exe scripts/qa/verify/verify_terminal_budget_real_data.py --run-id 40 --stale-worker-expectation protected --usage-history ../output/qa/2026-09-28-crm-workbench-run40-monitor.json --output ../output/qa/2026-09-28-run40-terminal-stale-worker-final-real-data.json
```

已生成报告不会覆盖，重跑需换输出路径。原基线保留，不改写为修复后通过。

## 范围与证据限制

以上为真实历史行、真实成功输出和真实早晚用量的隔离数据库回放；只在副本安排生命周期时序，没有制造模型响应或业务事实，不是线上 Run 40 曾触发该竞态的证明。未调用模型，未实施线上取消或 MySQL 并发实验。并行事件 guard 的位置已做代码核对；该报告未声称模型执行中的并行 map 取消被实际跑通。

冻结时 runtime.py SHA256：`68fd7f178679017739d216df714ea87c7b2de74f56648ac805aa22b444bb4583`。
