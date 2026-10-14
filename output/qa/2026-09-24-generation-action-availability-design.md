# 统一生成 Agent 的当前合法行动目录

本次为实现准备。真实模型仍在运行，未编辑生产文件，未请求模型或联网。

## 改动目标与边界

当前统一协调者始终看到 17 个工具。已有领域 guard 能拒绝不合法动作，但拒绝发生在模型请求之后。例如全局审查的前置条件未满足、全部批次已有结果后再次生成，均会浪费父级请求。

让领域层在同一份当前观察中提供 `available_actions`，SDK 使用其设置原生 `FunctionTool.is_enabled`。可见性表示“当前至少存在一个合法调用目标”，并不表示该动作最优，更不规定下一步只能做一个动作。原文取证、主动修订草案、主动重建计划继续保持可选。

`is_enabled` 看不到参数，不能单独保证选择了正确索引、事实、批次或有效的新 guidance。参数及完整业务验收仍由既有 guard 执行。

## 最小领域接口

观察新增一个普通字典，不新增状态表、持久字段、组件或 registry 类型：

```python
"available_actions": {
    "generate_cases": {"item_indexes": [3, 8]},
    "inspect_generation_scope": {"item_indexes": [0, 1, 2, 3], "problem_ids": ["真实问题 ID"]},
    "repair_review_batch": {"batch_ids": ["真实终审批次 ID"]},
    "read_planning_evidence": {},
    "rebuild_business_plan": {"problem_ids": ["真实问题 ID"]},
}
```

只包含当前可用动作，字典中不存在即不可用；每项保留实际目标目录。动作名来自当前已有工具 key，SDK 不推断阶段或业务名字。不需要再加 `enabled`、`recommended`、`phase` 等平行字段。

在 `test_generation_agent.py` 提取小函数 `_available_actions(state, planning_observation, review_observation)`。它只读已验证 state 以及已算出的规划/审查观察，不调用模型、不准备完整候选、不写 artifacts。`observe_generation_state` 现有规划及审查观察只算一次，传给它生成目录。

增加 `_require_action(context, action_key)` 或将其融入 `_mutable(context, action_key=...)`：读当前已验证 state，构造相同目录，动作缺席时抛 `OutputValidationError` 并带当前行动目录；返回该 state 和当前动作目标，供参数校验使用。不要让执行 guard 再把 17 项启用条件抄写一次。

统一层各入口复用目录；原规划/审查子领域的候选契约、状态身份、依赖重算与结果验收照旧，仍可独立调用。重叠的统一层“尚未 ready”“重复接受”布尔判断迁入目录后删除，具体参数及内容约束保留。

## 17 个工具逐项核对

所有修改动作仍要求统一 state 未完成。`inspect_generation_scope` 当前为只读工具，本身允许读取已完成状态；保持该行为。恢复已完成终止回执走原直接复验，不能要求终止工具在完成后的目录中仍可见。

规划子领域也有 `completed` guard。所有规划读取/修改工具的目录必须同时满足 `planning.completed=false`。当前统一 `accept_generation_plan` 只调用规划 `_resolved_output`，不会调用 `finish_planning`；因此正常接受计划不关闭主动重规划。2026-09-23 Run38 两份真实隔离检查点均确认 `accepted_plan` 非空而 `planning.completed=false`，无需新增“重新打开”逻辑。历史单独规划节点已完成的状态则不能冒充可修改；旧 Run36 诊断脚本的显式重绑定不是正常恢复路径。

| 工具 | 现有 guard 与位置 | 目录条件及目标 | 保留的参数/业务验收 |
|---|---|---|---|
| `read_planning_evidence` | 统一 `_mutable`；规划 read 的 evidence ID、offset/max_chars | 未完成即保留，已有原始目录 | 真实 evidence_id 或 null、原文分页越界、来源及输入指纹 |
| `reinspect_planning_source` | 统一入口禁止 `generation_requests` 非空；规划 prepare 校验 kind/index/question | 未完成且生成未请求过；targets 为 source_items 中真实 kind/index | kind、index、非空 question、同源事实锚点；不能仅因“已接受计划”就禁用 |
| `revise_planning_draft` | `_planning_action`；有 problem_ids 时 `_feedback`；规划 prepare 当前 index/question | 未完成且存在当前规划批次；targets 包含所有当前草案索引，problem_ids 为当前问题 ID | 不只允许 needs_revision=true；允许模型主动纠正事实支持但意图不准的草案；当前问题 ID、完整事实覆盖 |
| `rebuild_business_plan` | `_planning_action`；有 problem_ids 时 `_feedback`；内部自动补 pending drafts | 未完成且存在当前规划输入；附当前 problem_ids | 不以“plan 已存在”禁用；保留主动重规划；完整 catalog、预算、规划容量及候选身份 |
| `route_planning_scopes` | 规划 prepare 要求 plan 非空且有 pending route records | 复用 planning.plan_available 与 planning.pending_route_indexes；无参数 | 规划 prepare 继续从同一当前列表构造全部必要输入，提交前版本复验 |
| `review_planning_route_gaps` | 规划 prepare 要求 pending gap records | planning.pending_gap_indexes 非空；无参数 | 同一输入已有真实结论不能重复抽取，supported/unsupported 原判断 |
| `accept_generation_plan` | `planning._resolved_output`；拒绝 accepted_plan 与 resolved 相同 | planning.can_finish=true 且当前未接受；无参数 | 保留完整 `_resolved_output`，目录可见不替代正式验收；上游变化当前已会清空 accepted_plan |
| `reconcile_generation_authority` | prepare 要求 accepted_plan；`_indexes` 只收 pending | accepted_plan 非空且 authority.pending_indexes 非空；item_indexes 为这份真实列表 | 非空/唯一索引、严格权威结论和完整输入 hash |
| `generate_cases` | prepare 要求 effective_facts；`_indexes`；request hash 不得已执行 | effective_facts 非空且 generation.pending_indexes 非空；item_indexes 同该列表 | 同一个 pending 批次可能已有失败请求；允许新具体 guidance。因此不能因 request 失败过就隐藏整个 generate；继续检查完整 request hash |
| `review_generated_cases` | prepare 要求 generation 非空；`_indexes` 当前 review records | 当前 generation 已完整且 pending_initial_review_indexes 非空；targets 是审查索引，不是生成索引 | 严格独立初审；每批次用完整审查身份验收 |
| `revise_generation_layout` | effective_facts 非空；`_feedback` 要求当前真实问题；changes 非空、模块合法、layout 必須改变 | effective_facts 非空且当前 problem_ids 非空；附真实 problem_ids 与所有合法 module_indexes | 不以审查差异类型推断必须改布局；事实完整且仅分配一次、总预算不变、版本及 no-op 拒绝 |
| `inspect_generation_scope` | 参数 XOR；index 当前范围或 `_feedback` 当前 problem ID | 有 generation_inputs 或当前 problems；附全体当前 generation item_indexes 与 problem_ids | 保留 XOR、实际 index/problem 校验及当前 review_scopes 投影；读取已完成批次是合法的，不能只给 pending 索引 |
| `read_review_evidence` | `_mutable` + `_review_context`；子 read 校验 fact ID/分页 | review state 已存在且未完成 | 原始事实取证不能被视作审查通过，保留事实与页范围校验 |
| `repair_review_batch` | `_review_context`；子 prepare 禁止 pending_recheck、已完成；确定性 repair plan 不能为空 | review.repairable_batch_ids 非空；targets 为这些批次 | 仍由原 prepare 精确验证可修复范围；其他批次、scope版本及无变化候选严格拒绝 |
| `recheck_review_batch` | 子 prepare 要求对应 pending_rechecks 且 scope_sha256 一致 | review.recheck_batch_ids 非空；targets 为这些批次 | 当前修改必须经过独立复审，版本校验不能由可用目录替代 |
| `review_all_cases` | 子 `_resolved_batch_output`；已经审过的全局可见候选拒绝重复 | 直接复用 review.can_review_all_cases；无参数 | 不根据“初审已结束”自行推导；原完整局部审计、复审与全局历史候选校验保留 |
| `finish_generation` | `_resolved_output` 重算计划、权威、分批依赖，并调用 review.resolve_review_output | 直接复用最外层 can_finish，且统一 state 尚未 completed | 完整交付验收与原子终止回执全部保留 |

## 避免重复前置条件

1. 行动可见性用已有 observer 的 ready、pending、can_* 结果；不能另建基于阶段编号的条件表。
2. `_prepare_action` 的三个 kind（authority/generation/review）统一从对应行动目录取得合法索引，再由 `_indexes` 检查参数是否为其非空唯一子集。准备与 apply 都调用同一检查，防止迟到提交。
   `_apply_action` 的顺序必须保持为先核对 `action.state_hash`、实际完整 `items` 和输出数量，再核对当前行动目录/目标。版本不匹配继续抛原 `ValueError` 硬失败；不能因为目录先返回 disabled，把迟到候选或状态损坏降级成模型可修复反馈。
3. `_planning_action` 和 `_review_action` 两个已有封装接收精确 action_key，统一调用 `_require_action`。其余直接工具在现有入口检查。不要动态从中文描述、函数名字符串片段或错误消息猜测动作。
4. 子领域现有细校验照留：例如 full source hash、旧候选、不完整覆盖、索引内容不符，与工具是否存在合法目标是不同层次，并非冗余策略。
5. 不尝试执行每个 prepare 函数来判断工具可用，更不捕获所有异常作为 False。prepare 可能组装大输入、依赖参数或触发业务错误，状态损坏必须保持硬失败。

## SDK 接入与重复检查成本

本地 SDK `FunctionTool.is_enabled` 支持同步/异步回调。`Agent.get_all_tools` 会并发检查所有工具，每轮重新调用；工具执行器也可能再次解析可用函数工具。

复用当前 `_PersistentStateInputFilter`，通过显式 `runtime_config.state_action_catalog="available_actions"` 启用，由它提供只读 `tool_is_enabled(tool_key)`。该配置仅用于持久 Agent；SDK 层只核对工具名是否属于对应观察目录，并校验目录 key 均属于实际已绑定工具。业务工具的执行 guard 仍在领域层。

不要在 17 个 callback 中各跑一次 `observe_generation_state`。以完整 `action_state + action_input` hash 缓存目录，领域每次行动 `_save` 原子替换状态后自然失效；缓存不进入持久状态。`filter.observe` 每次刷新模型输入仍执行真实 observer 并刷新缓存，不跳过来源完整性校验。状态输入过滤器刷新给模型的当前观察与 is_enabled 读取应共用这份快照。计算 hash 可重复，但全量 DB/来源/审查复验不应重复 17 次。

原始输入、上下文来源 artifacts 在父会话内已冻结。执行 guard 和 journal 仍负责实际调用及提交时的来源/claim/state校验；可见性快照不能授予执行权限。目录缺失、非法工具名或 observer 异常应明确报错，不通过默认全开/全关掩盖。

父级终止工具平时不可见不影响 SDK 构建：现有 `_tool_use_behavior` 在构建时校验的是全部已绑定工具名，实际动态工具列表在每轮发送前过滤。恢复已完成提交回执仍直接走原 `on_invoke_tool` 及完整验收，不发新请求。

## inspect 参数的最小修正

不改成新 `target` 字段。本次只在原 Schema 追加两个互斥分支的 `anyOf`，保留原参数名和所有历史合法调用形状：

```json
"anyOf": [
  {
    "properties": {
      "item_index": {"type": "integer", "minimum": 0},
      "problem_id": {"type": "null"}
    }
  },
  {
    "properties": {
      "item_index": {"type": "null"},
      "problem_id": {"type": "string", "minLength": 1}
    }
  }
]
```

两个分支类型互斥，因此 `anyOf` 已表达恰好选择一个；无需 oneOf/not/if 等更多组合。现有顶层 required 与 additionalProperties=false 保持不变。平台 `_parse_function_arguments` 的 JSON Schema 校验立即能拒绝 null/null 和双填。

本地 SDK `ensure_strict_json_schema` 明确递归支持 anyOf，并在分支发现 properties 时保留两个字段 required。上线前用本地实际 FunctionTool 构建检查转换后 Schema，以及真实历史合法工具参数全量回放。无需改旧日志、旧回执或新增兼容解析。新源码/Schema 仍改变执行身份，不恢复混用旧检查点；历史报告可继续只读。

已先在内存中的真实工具定义副本完成本地 SDK 构建验证：`FunctionTool(strict_json_schema=True)` 保留这两个 anyOf 分支并补齐 required；`Draft202012Validator` 对旧 Run38 三条真实 inspect 回执逐项重放，两条合法 `(8, null)` 均接受，实际失败的 `(null, null)` 拒绝。未编辑生产 Schema，未执行工具或调用模型。

## 真实数据验证范围

- 用 Run38 隔离 SQLite 的真实初审后状态，确认只能看见现有合法行动；过早全局审查、无待生成项的生成应不可见，仍能取证、查看已完成范围、修复、重规划。
- 用该运行多个真实行动前后状态验证目录随完成、修复、待复审、重新分组而变化，不修改业务事实。
- 对每个历史合法工具参数重放 Schema；用已发生的 null/null、过早全局审查、重复 completed index 验证早拒绝及正确当前目标反馈。
- 验证模型输入快照和 `get_all_tools` 的工具名集合一致；工具过滤不额外产生模型请求、不改 journal/domain/budget。
- 确認已完成 finish 回执恢复仍零模型请求；被隐藏工具通过旧直接回放入口执行时仍受原完整 guard 约束。

此目录只约束合法性，不证明模型一定选择最优行动或在现有预算内收敛。

## 已核实的真实 Run38 数据与回放链

不需要为了测试阶段而手动填写输出、置空成功记录或改写审查判定。下列现有持久产物足够重放生成、初审、修复的自然状态变化。

| 来源 | 可直接使用的内容 | 读取方式 |
|---|---|---|
| `output/qa/2026-09-23-run38-local-failure-diagnostic-prepared.checkpoint.json` | 真实失败重验后的起始领域：21 个生成输入，已完成 0..7，待处理 8..20；计划已接受；无初审；父/规划均未完成 | `checkpoint.state`，原始完整 `domain_sha256=0c04b7e22619e201c41901401036b351c9a7552384380582f78c8f533c45c80a` |
| 对应 `.sqlite` 和 `.json` | 原 Run、节点完整输入、来源 artifacts、初始预算及失败来源说明 | 复用 `verify_persistent_input_filter_real_data.sqlite_engine` 的只读连接；不恢复或改写 journal |
| `output/qa/2026-09-23-run38-local-failure-real-model.checkpoint.json` | 同一起点的 22 条真实行动回执、27 个已验收专业候选；事件 ID 给出真实顺序 | `events` 筛选 `agent_action_completed/failed` 后通过 `tool_call_id` 查 `checkpoint.receipts` |
| 对应 `.sqlite` 和 `.json` | 精确候选输入/输出、源身份、共享预算；末态为生成及 11 批初审完成，3 批待复审 | 与 checkpoint 交叉校验，结束前后验证文件 hash、DB 无 dirty/new/deleted |
| `output/qa/2026-09-24-run38-versioned-memory-real-model.sqlite` | 新一轮真实模型的自然后续状态 | 当前仍在执行；必须等模型停止及 `.checkpoint.json` 落盘后才作为静态回放证据，不以运行中半截数据断言最终结果 |

生产 guard 与候选验收的回放步骤：

1. 从 prepared 检查点复制完整 state；从只读 SQLite 取原 `AgentRun`、`AgentNodeRun`，恢复 `action_input/run_input/artifacts`。确认原始 state hash；不要按测试目标清空或伪造记录。
2. 按旧真实诊断持久事件顺序重放 13 次成功 `generate_cases`：调用 `generation.prepare_generation_action`，按 `parent_tool_call_id + agent_key + 完整 input_hash` 找 `validated_agent_calls`，经 `_revalidate_managed_agent_call` 和原 `AgentOutputContract` 的 `testing.postprocess_generation_batch_item` 重新完整验收，然后 `generation.apply_generation_action`。确认每次 pre/post hash 与原回执完全一致。
3. 第 44 号事件完成生成项 20 后，实际 state hash 为 `ae9df67f4628f9fa45ece1338554232069021c63b8f1bad855bdacf6ff57fbf3`，当前 21 项生成均完成、11 项初审全部待处理。此时 `generate_cases` 应隐藏，`review_generated_cases.item_indexes` 精确为 0..10，全局审查不应出现；主动规划/read/inspect 仍保留。
4. 第 57 号事件 `review_generated_cases` 的原参数为 0..10。依原 `prepare_initial_review_action` 构造真实输入，逐条通过 `testing.postprocess_final_review_batch_item` 重新验收对应 11 个专业候选，然后 `apply_initial_review_action`。post hash 必须等于 `1b4780cd453a259f85eb2c9148b7aaf3eb8015e1dc6b89e5b489c43868c07c8c`；真实可修复批次是 R-001/R-002/R-004/R-006/R-007/R-008/R-009/R-010/R-011，尚无待复审，全局不可用。
5. 第 66/69/72 号事件分别成功修复 R-001/R-002/R-004。复用 `verify_state_snapshot_filter_real_data.py` 已实现的原候选回放：`review.prepare_review_repair` → 完整 `AgentOutputContract` 与 `validate_review_repair_candidate` → `apply_review_repair` → 外层 `_save`。修复批次从可修复目录消失、加入 `recheck_batch_ids`；其他真实差异仍可修复。该现有脚本已经验证三次 post hash 和原观察完全相同。
6. 旧真实诊断还提供三个自然无效参数，不需人工制造：第 8 号事件重复请求已完成生成索引 2..8；第 59 号事件 inspect 的两个目标都为 null；第 63 号事件在 9 批差异未修复时调用全局审查。前者在仍有 pending 时工具仍可见，但参数应被共同目标 guard 拒绝；后两者分别由最小 Schema 与目录可见性提前约束。

目录上线后，领域 state 不新增持久字段，预期真实 replay 的 pre/post hash 保持一致。新观察多出 `available_actions`，所以比较历史 observation 时只投影删除这个新增展示字段；不得更改历史回执或直接把旧观察当作新的状态。

每个自然阶段用真正的 SDK `Agent.get_all_tools(RunContextWrapper(context))` 比较工具名称集合与当前 `available_actions.keys()`，连续读取应不改 state/budget/receipts。对已有合法参数仍调用真正 prepare/guard；不以 mock observer、mock model 或人工输出替代。真实旧轨迹未达到全局可审及完成状态，这两项只能在新轨迹实际到达后追加验证，不能把差异清空来宣称通过。
