# 动作回执顺序真实缺陷、修复与旧 QA 结论纠正

日期：2026-09-28。真实对象：Run 39 / node 986；另对运行中的 Run 40 / node 994 做过只读快照审计。本文不把 MySQL JSON 对象成员顺序当作时间顺序。

## 结论

Run 39 的最后一次 `route_planning_scopes` 失败被旧失败投影错误隐藏，已确认是实际缺陷。旧实现反转 `receipts.values()`，把 JSON 对象成员顺序误当动作先后顺序。MySQL 返回的成员序使较早的同参数成功先进入 `seen`，进而隐藏真实较晚的失败。

经 root 确认 Run 40 已正常诊断取消、worker 已退出后，修复已应用并冻结。新动作保存现有事件仓库分配的真实启动事件 `sequence`；全部依赖动作先后的读取按该序号排序。旧检查点没有可信序号，直接拒绝，不自动补写历史身份。

## 真实证据

只读事务审计文件：[receipt-order-event-audit.json](2026-09-28-receipt-order-event-audit.json)。事务标识为 `READ ONLY`，保存事件 ID、真实 `sequence`、节点、工具调用 ID、参数摘要和状态摘要。

| 真实动作 | 启动事件 | 终态事件 | 终态 |
| --- | --- | --- | --- |
| `call_00_c6R7nAT4KszxlVaH2Mvv1278` / `route_planning_scopes` | id 8163 / sequence 202 | id 8190 / sequence 229 | completed，10:19:35 |
| `call_00_RniWnkfNr9VCQb7NJaFZ6371` / `route_planning_scopes` | id 8213 / sequence 252 | id 8221 / sequence 260 | failed，10:27:54 |

两者的 `arguments_hash` 都是 `33a19b98316b943f4eb5ccfe22dd6d4a9758292d06072588263f083ee5af497d`。最后失败为 `AgentExecutionBudgetExceeded`，原错误消息为 `本轮父子 Agent 共享预算耗尽: attempted_requests=161/160`。

审计已逐项查找该失败之后同工具、同参数的实际成功事件，结果为空。旧生产函数却返回空失败列表。将完全相同的真实回执按事件顺序排序后，失败正确可见，且原始错误、参数、状态关系和七次已验收路由子调用都保留。

Run 40 审计快照包含 25 个回执，JSON 成员序同样不等于事件顺序。当时没有失败回执，因此这里只能证明顺序来源不可靠，不能声称 Run 40 已发生失败隐藏。

## 必须撤回的旧 QA 时序结论

以下既有报告中的 `actual_later_success_still_hides_previous_failure: true`，以及“真实历史前缀”“实际后续成功消解旧失败”的相关文字，均撤回：

- [duplicate-json-keys-with-failure-projection-real-data.json](2026-09-28-duplicate-json-keys-with-failure-projection-real-data.json)
- [duplicate-json-keys-final-real-data.json](2026-09-28-duplicate-json-keys-final-real-data.json)

原因是旧 QA 脚本也误用了 JSON 成员序构造所谓历史前缀；这个前提不能证明时间关系。旧报告保留原样以记录审计过程，不静默改写。重复键拒绝、原始参数摘要、无效参数投影为 `null`、合法历史响应完整契约验收等与顺序无关的结论未因此被推翻，并已在修正后的 QA 中重新验证。

QA 脚本 `verify_duplicate_json_keys_real_data.py` 现要求真实事件审计文件，按真实调用 ID、节点、工具、参数摘要、前置状态摘要关联原有启动事件。已删除错误的“后续成功”断言，新报告明确写出纠正理由。

## 实现边界

- `action_journal.py`：检查点版本改为 2；新增统一 `ordered_action_receipts`，要求每个回执有唯一、正整数的 `started_sequence`，并核对成员键与 `tool_call_id`。旧版、缺序号、重复序号、布尔值冒充整数、身份不匹配均拒绝。
- 首次 `begin` 复用 `AgentRunRepository.append_event` 返回的实际事件序号，写入回执；恢复已开始动作保留最初序号。没有工具 ID 排序、时间推断、兼容兜底或线上历史迁移。
- `project_action_failures` 按该顺序取最新结果。同参数较早成功不能隐藏较晚失败。
- `sdk_adapter.py` 的专业调用历史错误选择和已完成终态恢复共用该 helper。只做集合、计数或唯一正在执行动作检查的读取不需要人为排序。

事务说明：`append_event` 会 `flush`，但首次 `begin` 此时仅修改了局部检查点副本，尚未改变节点预算或状态。事件先 flush 后，`_save` 在同一事务中保存序号、同步用量；最终统一提交。任意保存或 claim 校验失败都回滚整笔事务。`_finish` / `save_budget` 仍沿用现有先 `_save` 同步入账、再事件 flush 的顺序。usage 负责 agent 已独立只读核对该边界。

## 验证结果与限制

新报告：[receipt-order-production-real-data.json](2026-09-28-receipt-order-production-real-data.json)，结果 `passed`：

1. 原始 Run 39 完整检查点 hash 与只读数据库审计值一致，11 个真实回执全部绑定到已有且唯一的真实启动事件。
2. 当前生产函数显示最后一次真实失败；反转 JSON 成员序、按成员键重排后结果完全一致。
3. 五种检查点协议异常明确拒绝；两个生产文件编译通过。
4. 原始检查点和审计文件字节/hash 完全未改。

重复键回归：[duplicate-json-keys-event-order-real-data.json](2026-09-28-duplicate-json-keys-event-order-real-data.json)，结果 `passed`：真实重复键响应在原始、合法二次序列化、结构化正文及控制字符处理入口都被拒绝；真实无重复键历史响应仍通过当前完整输出契约。现有工具提交回路先返回 rejected，再接受合法原响应；错误参数投影为 `null` 并保留诊断和真实原始参数摘要。

这里将真实事件序号接到历史回执副本上，是明确标记的离线协议验证输入，不是生产迁移、缓存恢复或重新执行旧规划。错误参数接入真实回执结构也只属于派生边界验证，不声称该组合曾在线上持久化。

本次无模型请求、无业务数据库查询或写入。应用导入会初始化数据库/缓存连接，不据此声称覆盖了实际新动作的事务执行。新鲜运行的持久化、取消、重启恢复仍需后续真实运行验证；本次未用 mock 或写测试数据库伪装该覆盖。

应用清单及生产 SHA256：[receipt-order-staged/application.json](2026-09-28-receipt-order-staged/application.json)。此前暂存报告中的 `production_applied: false` 描述暂存时点，不代表最终状态；本报告及 application / production-real-data 报告记录已应用状态。
