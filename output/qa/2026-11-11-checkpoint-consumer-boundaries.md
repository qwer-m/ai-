# 检查点消费边界复核

本记录只说明消费者与存储边界；不代表新业务 schema 已经真实生成通过。没有发送模型请求，没有创建或启动 Run。

| 消费者 | 数据来源 | 是否需要展开正文 | 原因 |
| --- | --- | --- | --- |
| action journal 的预算保存与控制查询 | v3 小头、分支、Run.usage 小投影 | 否 | 仅变更预算与已记账游标；JSON_SET 更新同一个权威 Run.run_context.usage 字段 |
| usage.sync_action_usage_projection | 节点小预算及 action_usage 游标 | 否 | 按真实累计用量差入账，随后 expire ORM run_context，防止旧副本覆盖增量 |
| 重试 action checkpoint | input_ref/state_ref/journal_ref | 否 | 复制固定引用和读取游标，首次日志写入分叉；旧 v2 直接拒绝，需显式迁移 |
| 重试 map 修复候选检测 | 原有 parallelism.total_instances、item_index、输入哈希 | 否 | 这里只筛选候选；真正执行仍读取冻结输入并核对逐项哈希/schema |
| API 节点详情与 UI 进度 | projection、budget、输入引用头 | 否 | input_payload_is_reference 明确标记引用，前台没有 node.input_payload 正文需求 |
| 只读 timing audit 的 v3 统计 | projection 与小回执头 | 否 | 专业次数、规划进度和动作状态不能因改协议静默变成 0 |
| 用户确认应用 | 当前 state_ref | **仅领域状态** | 用户答案实际改变来源权威、下游失效和确认记录；不读全部专业输出及回执，写回新 CAS 引用 |
| 运行时明确读取输入 | 仓库 read_node_input / restore_node_input | 是 | 执行业务前需要完整冻结输入、schema 与哈希核验 |
| 完整审计导出 | 明确调用完整 hydrate | 是 | 调用方明确要求完整历史正文；不允许作为预算、诊断、工具可用性检查入口 |

唯一权威性：领域 JSON 正文由内容寻址产物保存；节点小头只引用其哈希及摘要。动作与专业结果为追加记录及引用，UI 摘要是派生信息。Run.usage 仍是同一字段，新增 SQL 投影入口没有建立永久双写的第二张用量表。生命周期用量逻辑共用 action_usage.recorded 游标。

保留边界：Run.run_context 的最终 artifacts 和节点最终 output_payload 仍沿既有生命周期复制与输出协议。本轮没有承诺全链路零复制，也没有把旧业务状态自动转换为新业务 schema。

历史工具兼容性：只读 audit_generation_timing_real_data.py 同时明确标注 v3 与 legacy-inline-archive。原 v2 存档 transport 回放脚本可以继续读取存档原文，但其它直接访问 cp.state / cp.receipts 的历史实跑脚本尚未全部迁移，不能直接用于 v3 现库，也不能据此继续花费模型 API。新消费者验收使用 verify_checkpoint_consumers_real_data.py 的显式迁移及真实回滚路径。

审批验收边界：旧脚本把问题 reason 当用户 answers 的做法已删除。Run 27 / Approval 7 的真实表单只用于只读检查及旧协议拒绝验证；没有用户真实答案，因此有效新回答事务未验证，不伪造“确认成功”或恢复执行。

## 已完成的真实证据

- Run 47 / Node 378 的真实 MySQL 回滚验证 23 项通过；使用已记录 response_received 序号 1 的真实用量 6059 输入 / 461 输出 Token 重放，无新请求。
- 检查点 JSON 为 748,957 → 2,100 bytes，迁移后整个 SDK 状态为 24,980 bytes；不把残余正常技术元数据说成零字节。
- 捕获到的热入账 SQL 只有 Run.usage 的 JSON_EXTRACT、JSON_SET 和 Node.sdk_state 更新；没有完整 Run.run_context SELECT，没有产物正文读取。前台序列化及恢复 helper 零 SQL。
- 二次同步零 SQL、不重复记账；ORM 缓存失效后，后续生命周期读取的用量仍是已入账真实值。
- 原 Run / Node 状态及 JSON 指纹、四张新存储表计数、三张模型账户/价格/流水表指纹全部恢复原值；0 模型调用，0 持久业务改动。
- 证据：2026-11-11-checkpoint-consumers-with-spend-boundary.json；首次缺表失败保留在 2026-11-11-checkpoint-consumers-attempt1-failure.json，未掩盖失败过程。
- 前端 npm run build、后端定向 5 项身份/拒绝协议测试及 1 项重试候选测试通过。根侧另做整体集成验收。
