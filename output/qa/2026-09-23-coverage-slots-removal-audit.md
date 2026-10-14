# coverage_slots 只读链路审查

本次没有修改生产代码、调用模型或写入 MySQL。当前统一 Agent 的真实模型诊断仍在进行，本文不评价其尚未完成的独立审查结论。

## 已证实的职责错位

`prepare_test_case_batches` 先按规划路由形成批次，在 `_build_case_fact_contract` 中再给每个用例分配 `coverage_slots`。这段分配只读取事实 ID、设计项 ID、事实到设计项的路由和数量；先均分设计项，再按槽位容量与路由交集贪心分配事实，不读取事实条件、角色或生命周期语义。

`_validate_generation_batch_output` 检查输入槽位的数量、编号、合法 ID 和全批并集。之后输出验收只检查实际用例数量、事实绑定、模块、事实全集与设计项全集。局部变量 `coverage_slots` 在构建后不再读取，也没有将各用例的绑定与对应槽位比较。

但槽位仍影响模型：生成器的 `input_projection` 明确透传逐槽位事实，提示词称其为“初始事实负载参考”；父协调器通过 `inspect_generation_scope` 取得完整输入，也会看到它。`generation_repair_targets` 又用同一槽位把全批缺失事实或设计项指定给固定 `test_cases_array_index`。因此它是未获语义验证的逐用例安排，不是实际输出必须满足的验收条件。

## Run 36 的真实证据

[只读证据 JSON](../../backend/artifacts/verification/run36_coverage_slots_readonly_audit.json)来自生成节点 951：

- 22 个已接受批次全部重新通过当前完整输出校验。
- 其中 41 条用例、17 个批次没有逐项满足对应槽位分配的事实或设计项。这不构成现有输出契约违规，恰好说明槽位不是输出约束。
- 50 个槽位中，19 个槽位的设计项不能由本槽位事实沿真实路由全部支持。当前输入校验也没有检查这层对应关系。item 14 不属于这 19 个槽位。
- 只在真实 item 14 输入副本中交换两个槽位的事实和设计项分配，保持编号、数量及全批全集不变，同一已接受输出仍完全相同地通过校验。
- 对同一个真实失败候选及相同的缺失集合，交换槽位会改变修复反馈的逐用例目标；问题本身没有变化。

这些证据支持删除槽位职责，并不证明删除后模型一定主动上报范围问题，也不证明现有两个用例在所有设计下都无法完成该批范围。

## 最小一致清理范围

1. `test_generation_batching.py`：从 `_build_case_fact_contract` 删除逐槽位均分/贪心算法及字段；保留数量编号、必须覆盖的事实与设计项全集、事实到设计项路由。删除 `_validate_generation_batch_output` 中只检查输入槽位的代码；保留所有实际输出覆盖、绑定与数量校验。将“事实槽位”错误文案改成真实的“事实路由”。
2. `test_generation_schemas.py`：从 `GENERATION_BATCH_ITEM_SCHEMA` 的属性与必需字段中删除 `coverage_slots`。这个 schema 被范围问题验证器实际使用，不能只改模型投影而留下旧要求。
3. `test_generation_builtin_specs.py`：删除槽位提示、投影以及逐用例 `repair_targets` 的相关提示；更新输入投影版本。父协调器的完整输入由准备器自然减少，无需另加隐藏字段或特殊规则。
4. `test_generation_repair.py`：删除依赖槽位的 `generation_repair_targets`。最小方案复用已有 `no_repair_targets`，保留 `generation_repair_feedback` 对缺失/非法事实和设计意图的完整文本关联。完整 `error.details` 已包含 `candidate_fact_ids_by_case`，无需再在修复 context 中复制，更不应把缺失项重新硬指派给某条用例。

`test_generation_escalation.py`、`test_generation_workflow.py` 的实质逻辑不用增加替代规则：它们已经使用事实全集、设计全集、路由和数量，没有使用槽位。当前缓存与行动执行身份包含完整输入及生产源码；不能把旧进行中的检查点改为新契约继续使用。应先保存当前诊断及其真实结果，再进行清理和新诊断。

## 历史回放策略

- 旧持久记录保持原样。新准备器产物与旧真实输入副本比较时，只显式删除旧 `case_fact_contract.coverage_slots` 后进行完整等值比较，禁止广泛忽略不同字段。这项转换只能出现在验收脚本，生产无需兼容旧槽位。
- 重验 Run 36 的 22 批自然接受提交，标准化输出应逐项相同；重验 Run 35 自然失败的 6 个候选，其核心拒绝类型、缺失/非法事实及设计集合不应因删除槽位改变。同会话反馈不再断言缺口必须落到原槽位用例。
- 更新 `verify_generation_layout_real_data.py`：依旧验证事实范围完整、来源/容量上限、精确总预算、路由完整，以及只使相关输入失效；删除只验证槽位均摊的断言。
- 更新统一领域及隔离模型诊断脚本：用当前准备器重建真实输入，与“仅删除槽位”的历史副本完整比较后，才将真实已验收候选送入当前 `apply_generation_action`。不能冒充相同完整输入缓存命中；这是显式契约迁移后的真实结果再验收。
- `verify_generation_repair_feedback_real_data.py` 和旧 `verify_output_repair_real_data.py` 需去掉槽位目标预期，继续验证完整错误集合、真实事实文本、完整候选重验和错误不被掩盖。
- 旧单元测试中固定槽位平衡和固定分配的断言应删除或改成剩余真实职责断言；不为了覆盖删除代码而增加 mock 测试。

运行新真实模型诊断后，分别记录完整验收是否通过、是否自然上报范围问题、是否实际调整规划/布局、调整后独立审查是否通过。只删除槽位不能等同于这些行为已发生。
