# Run 36 网关失败与恢复证据

2026-09-23。数据来自真实 MySQL 节点和追加事件，只读采集报告见 [Run 36 监控](2026-09-23-run36-readonly-monitor.json)。

| 字段 | 实际记录 |
|---|---|
| 运行 / 节点 | Run 36 / `resolve_planning`，首次 attempt 节点 943 |
| 失败位置 | 协调 Agent 的第一个请求，尚未开始领域行动 |
| 模型 | `main` 路由，deepseek-v4-flash |
| HTTP 状态 | 502；没有将其记为 504 |
| 时间 | 2026-09-23 15:34:51 至 15:34:54，Asia/Shanghai |
| 请求耗时 | 2.995 秒 |
| 上下文体积 | 20917 字节，SDK 上下文 JSON 口径，不是 HTTP 线上报文体积 |
| 单请求超时设置 | 900 秒 |
| 错误响应 | application/json，326 字节；正文只记录摘要 |
| 错误事件 | 7535，`node_failed`，`failure_kind=upstream_server`，允许原有传输重试 |

该请求未返回模型用量，首次失败检查点保留 `pending_requests=1` 和原预留额度。Runtime 自动进入第二次 attempt 节点 944；恢复后的共享预算将旧请求计入 `interrupted_requests=1`，没有释放或重置其额度。节点 944 的第一个协调请求已成功返回，并调用 `rebuild_business_plan`。此时的 `pending_requests=1` 对应新的规划专家请求，已完成请求数为 1、累计尝试数为 3。

因此本次 502 没有发生“旧 pending 阻塞后续任务”。约 3 秒返回也不能证明长 JSON 触发了超时。

## 随后捕获到真实 504

节点 944 的 `rebuild_business_plan` 行动调用 `test_business_planner`，实际模型为 `review` 路由 deepseek-v4-pro。请求于 15:36:08 发起，在 **120.135 秒**后收到 HTTP 504。本地专业调用超时配置为 **360 秒**，SDK 上下文为 **53173 字节**，输出预留上限为 12000 tokens。响应是 `server=stgw`、`content-type=text/html`、159 字节，标题为 `504 Gateway Time-out`。详见[当时的三个 attempt 及请求诊断](2026-09-23-run36-gateway-failure-evidence.json)。

领域行动失败事件 7539、节点失败事件 7540 已落库；行动前后状态摘要相同，没有把未完成的规划写入正式状态。第三次 attempt 节点 945 已从原检查点继续，再次调用规划专家。当时共享预算为累计尝试 5、完成 2、interrupted 2、pending 1，旧失败额度未重置。

可以确认是网关先于本地请求超时返回了 504，且失败后确实继续调度，没有停留在 pending。120 秒接近网关固定时限是一种解释，但没有网关内部日志，不能据此确认固定配置或认定大 JSON 是根因。

对照当日[已成功的规划专项](2026-09-23-planning-agent-run31-actionable-state-real-model.json)，同一模型曾处理 55770、72754、89766、102541 字节的 SDK 上下文，请求分别在 53.81、36.006、38.983、37.869 秒返回。这些输入比本次失败请求更大，不能从字节数单独推断超时原因。

15:40 的持久快照进一步确认节点 945 已完成规划重建，计划版本从 0 变为 1，开始事实路由。共享预算累计尝试 8、完成 5、interrupted 2、pending 1。

## 规划阶段最终恢复结果

节点 945 后续完成第一次规划、17 批路由、缺口复核、草案 4 修订、第二次规划和 16 批路由。在第二次缺口复核中触发协调会话 900 秒本地硬超时，异常链为 `TimeoutError → TimeoutError → CancelledError`，没有 HTTP 504 状态；不能把它与前面的网关超时混为一谈。本次 `failed_model_requests` 为空，不能报告具体被取消子请求的耗时或上下文字节数。

节点 946 从检查点恢复，第二版 16 批已完成路由保留，继续复核与取证后成功完成规划。其最终版本为来源 0、草案 1、计划 2；累计 61/64 次尝试、58 次返回模型用量、3 次 interrupted、**pending 0**。已返回用量为输入 538522、输出 36399、总计 574921 tokens；保留失败预留后的计费占用总量为 728437。两者口径不同。

恢复前后真实节点和检查点见[本地超时及最终恢复证据](2026-09-23-run36-coordinator-timeout-evidence.json)。在线强杀恢复没有执行；这里验证的是实际网关故障、本地会话超时后的 Runtime 重试恢复。后续完整流程的结果以[正式运行报告](2026-09-23-agent-workflow-e2e-review-generation.json)为准。
