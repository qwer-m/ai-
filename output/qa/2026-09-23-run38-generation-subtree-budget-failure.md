# Run38 局部请求预算失败的真实证据

Run38 未通过。node978 在第二次 `generate_cases` 行动中，因为 item8 的生成专家与其咨询子树耗尽 6 次请求额度，整阶段终止。不是父 160 次请求预算耗尽，也不是 504。

## 已通过与尚未验证

冷启动第一个行动是初始 `rebuild_business_plan`（7906），没有先盲目 accept。独立规划缺口审查后接受规划（7935），10 条 authority 回执随后真实应用成功（7947），已自然进入生成。初始布局为 21 批、50 条用例。

没有提交 `planning_feedback`，没有问题驱动的上游修订，没有进入独立批次审查；唯一 revision_history 是初始规划从 0 到 1。不得把这些结构进展称为语义收敛或强 Agent 路线全部完成。

## 精确失败位置

- 父行动：`call_00_ywU0JDFXetcbmxvZPlOL2733`，参数选择 `[5,6,7,8,9]`。
- 用真实 `prepare_generation_action` 构建请求，与落库 `input_hash` 精确匹配，失败为 item8 / `M003-B003` / `续费办理流程与场景分流`。
- 该批 16 条真实事实、目标 3 条用例；请求哈希 `d7e1d121764c0317c003043d392deffd210ec8e141c0cbe3cd41e9b7fa9e39f1`。
- 事件 7959 为行动失败，7960 为节点失败，7961 为 Run 失败。
- 异常：`AgentSubtreeRequestBudgetExceeded`，`attempted_requests=6/6`，原持久回执 `recoverable=false`。

## 三次咨询与可取原文

| 咨询 | 调用 ID | 真实结果 |
| --- | --- | --- |
| 第 1 次 | `call_00_awe5M33QD3QhdplqJNRH8882` | advice 额外字段 `scenario_groups.0.precondition_fact_ids_placeholder` 被 schema 拒绝；参数 1734 字符 |
| 第 2 次 | `call_00_6TbvwPmVXQuaByd2jQ0T2695` | advice 额外字段 `scenario_groups.5.precondition_fact_ids_note` 被 schema 拒绝；参数 2571 字符 |
| 第 3 次 | `call_00_oks1auRvVVy5wLY1AcyW0438` | 成功返回 advice，1535 字符，recommended_case_count=3；含 cross_lifecycle/cross_role/fact_overload 提醒 |

第三次咨询的完整请求和成功 advice 全文均可从 `node978.sdk_state.tool_calls` 的失败 `agent:test_case_generator` 条目取得：`submissions` 内的 `agent:test_generation_scenario_designer.arguments.input` 与 `submit_scenario_design_guidance.arguments`。已原样存入本报告 JSON。

前两次没有完整 raw 留在当前持久记录；`tool_observations` 只有请求长度/哈希、诊断和截断候选片段。已保存所有可得原证据，不能冒充已取得三次完整 raw。成功 advice 是建议而非范围报告，它仍建议 3 条用例，不能自动推导为容量不足，更不能自动批准重规划或加预算。

该失败专家没有一次 `submit_generation_batch` 或 `report_generation_scope_problem` 提交。三个生成者咨询调用与三个子咨询响应合计触达 6 次局部上限，第三次得到建议后没有请求余量继续提交。

## 原子状态与成功回执

父行动前后 state hash 完全相同：`8320d28a64f37cf8f14f575ac129caaf6057bcebcc847efc2b5181b6e918ad8d`。未把半批输出伪装为整个行动成功。

8 个生成专家成功回执均已持久化：items0—4 共 14 条候选已应用到父 generation_records；items5—7 共 8 条候选来自第二次行动，可信回执已保存，但因该行动随后失败而尚未应用。item9 尚未执行到。JSON 保存全部 8 回执及逐项 applied 标记；这三条未应用回执不能称为丢失，也不能称父状态已包含它们。

## 用量与语义风险

Run 汇总已报告 23 请求、输入 97324 / 输出 36656；失败父 checkpoint 另有 61 请求、输入 605225 / 输出 36097，未结算进入该汇总。合并已报告 84 请求、输入 702549 / 输出 72753，共 775302 token。父 pending_requests=0，failed_model_requests=[]。

先前保存的 item1 无合同前提与后续多状态步骤未闭合风险仍未接受独立审查，见 `2026-09-23-run38-initial-generation-context-risk.json`；本轮失败不能证明它被审查纠正，也不能把所有模型建议当作已经证实的缺陷。

全部取证为 MySQL READ ONLY，纯准备函数只构建请求并验 hash；没有运行模型、修改数据库、重置预算或更改生产代码。
