# 托管规划专业会话的验收错误边界审阅

2026-09-21；只读审阅生产代码，真实模型执行期间不修改生产文件。

## 已确认正确的 SDK 链路

`sdk_adapter.run_agent_async` 的 `enable_managed_submission` 仅派生本次专业调用定义。已有 `submit_source_semantics` 继续作为终止工具，没有添加第二个 `submit_result`。子调用复用父级 `shared_budget_hooks`，不会创建第二份额度；首次预留及每次请求变化仍回写父 journal。

终止工具原始输出经 `AutonomousToolLoop.wrap_tool` 调用完整 `AgentOutputContract.validate`，包括原始输出 Schema、定义 postprocessor、map item postprocessor、领域 candidate validator。只有 accepted 回执才终止。返回时 `skip_output_postprocessor=True`，父 `invoke_agent` 优先取 `validated_output`，不会重复坐标平移；自由正文不能代替验收。

`_planning_feedback` 没有在 `_runner_input` 中被投影掉，来源、草案、全局规划均收到完整问题和真实历史候选。vision 输入在实际加载图片时继续校验文档及页面资产指纹。

## 已确认的反馈断点

`postprocess_agent_map_output` 把原业务后处理异常包装为 `OutputPostprocessingError(ModelBehaviorError)`。自主循环只沿 SDK 错误 cause 解包，并只恢复显式 `OutputValidationError` 等类型。最终 cause 为普通 `ValueError` 时正确维持硬失败。

但以下业务后处理目前把真实输入错误和模型候选错误混用为 `ValueError`，导致本应修正的候选也无法返回专业会话：

- `test_generation_semantics.postprocess_source_semantics_item`（约 2396 行）：未知 block_id、候选 document/page 不匹配、候选坐标超界、重复 fact_id、候选 governed_by 错误。真正的输入错误包括缺失/无效 source kind、原始页/块/作用域/坐标映射损坏；这些必须继续硬失败。
- `test_generation_planning.validate_business_plan_draft_output`（约 566 行）：候选缺 module_candidates、重复模块、遗漏/未知 fact_id。真正的输入错误是当前 planning_scopes/facts 结构、空事实或原始 fact_id 重复。
- `test_generation_planning._normalize_planning_route_repair`（约 1318 行）：候选错 module_index、缺失或重复 decision、引用输入外 fact_id、supported/unsupported 与事实引用不一致。真正输入错误是 prepared module_index、缺口 design indexes、candidate fact catalog 无效。

领域 `validate_candidate` 在上述后处理之后执行，因此在它里面包装错误不能修复这个断点。不能把通用 ValueError 或全部 OutputPostprocessingError 加入可恢复集合。

## 最小分类调整方案

1. 草案：抽出 `_business_plan_draft_source(input_payload)`，在输出校验前硬校验 scope/fact 结构并返回真实 expected fact IDs。候选校验单独接收这些已编译数据，仅在候选违反约束时抛 `OutputValidationError`，附 missing/unknown fact IDs 或冲突模块位置。外部函数名和返回结果不变。

2. 缺口复核：抽出 prepared 输入校验，硬校验真实 module index、非空且唯一的缺口设计索引、真实候选 fact IDs。其余候选分支抛 `OutputValidationError`，附 module index / missing design indexes / unknown fact IDs。保留原严格判断，不接受 unsupported 为完成。

3. 来源：在 `postprocess_source_semantics_item` 的候选转换前，复用 `_hydrate_source_semantics_item` 得到真实完整来源，并显式预检其解析所需的 source kind、页身份、块目录、作用域及坐标映射。此预检在候选错误捕获范围之外；内联来源校验实际 requirement hash 与坐标范围，文档来源校验每个页/块/作用域的实际身份和跨度。

   随后候选转换和单项合并使用该已验证来源；候选错误转换为 `OutputValidationError`。既有 `_merge_source_semantics_records` 的分流数量、记录序号和全量来源输入错误仍保持硬失败，避免放宽常规 merge 路径。`TypeError`/`KeyError` 等程序或未覆盖平台错误仍不吞掉。

4. 不修改 SDK 的全局可恢复类型，不增加异常消息关键词规则，不放宽既有 source/coverage/route 校验。模型运行结束后再修改生产代码，执行身份变化后使用新的独立诊断/Run，不改旧 checkpoint 的 identity。

## 验证边界

优先提取真实历史中原始无效候选、其完整实际输入及来源 artifacts，在同一原 AgentOutputContract 和 AutonomousToolLoop 工具链中回放，确认返回 rejected 且父状态不变。同步用真实来源/草案输入的损坏副本确认仍硬失败；这些副本仅用于输入完整性反例，不伪造业务事实或模型成功数据。全部历史有效输出要求逐字段不变。

如果没有某类自然无效候选，只报告静态分类及有效样本复验，不宣称该自然失败分支已执行。
