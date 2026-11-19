# 生成链清理依赖地图

2026-11-13 静态只读核对。未导入生产模块、未接触DB、未启动Run、未请求模型。完整函数、导入位置、源码SHA见 `cleanup-dependency-map.json`。行号是本次快照，后续编辑可能移动。

## 可以整体替换的边界

旧生成域共16文件、20,036行，当前定义25个公开工具、13个Agent、1个workflow。正式用例评测、长期记忆、Excel导出均不依赖旧来源治理、规划、路由、分批或终审实现。外部生产显式导入只有：

- `registry.py:257` 导入生成规格，`:262` 导入注册器，`:300` 调用生成注册器。新入口在此替换。
- `conflict_confirmation.py:23` 读取旧支持结论，`:100–116` 通过旧root/planning应用人工口径。这部分必须随新状态重写，不能留旧链让确认继续可用。
- 无import的耦合：`checkpoint_storage.py:444 state_projection` 硬读旧planning/authority/generation/review数组；只改投影，不删除ArtifactStore与日志引擎。

| 文件 | 行数 | 清理建议 |
| --- | ---: | --- |
| test_generation_planning.py | 1768 | 删除旧草案→归属→支持路由链；`:93 resolve_requirement_evidence` 的来源绑定职责移入新入口或已有document工具后再删除，避免连来源权限/指纹一起丢弃 |
| test_generation_planning_agent.py | 1422 | 可整体删除；依赖仓、模块所有权、定向支持、全规划重建仅旧链使用 |
| planning_dependencies.py | 396 | 可整体删除；生产消费者仅旧planning/planning_agent/workflow |
| test_generation_review.py | 2002 | 可整体删除或以新独立审核实现替换；正式test_case_evaluation不依赖它 |
| test_generation_review_agent.py | 800 | 可整体删除；旧局部审查/全局审查/修复调度，不是人工文档对比评测 |
| test_generation_source_agent.py | 322 | 可整体删除；旧先抽取全量事实再治理再草案的调度；已有document工具和资产服务另行保留 |
| test_generation_source_context.py | 415 | 可整体删除旧state/专家raw迁移协议；同项目来源身份/审批追溯职责由新协议明确承接，不能复活旧Run状态 |
| test_generation_repair.py | 162 | 可整体删除3个生成专用repair策略；通用output_repair引擎保留 |
| test_generation_escalation.py | 191 | 可删除旧问题分类/反馈工具协议，以新状态的确定问题/人工请求替代；不牵连人工评测反馈 |
| test_generation_agent.py | 1197 | 整体重写统一主Agent领域state与tools；保留公开产物边界，不保留旧20个必经阶段工具 |
| test_generation_builtin_specs.py | 1386 | 整体重写生成定义/工具/提示词/workflow；不删registry内automation/evaluation的独立规格 |
| test_generation_schemas.py | 2642 | 旧生成schema可整体替换；外部生产仅旧generation域与planning_dependencies读取，无评估/记忆schema共用 |
| test_generation_workflow.py | 501 | 整体替换注册器/交付验收；旧persist函数不是评测/导出的依赖，但最终artifact字段需延续 |
| test_generation_batching.py | 2550 | 旧布局、事实→意图→额度、执行链算法可删；`:677 build_planning_evidence_catalog` 是旧来源输入依赖，可改用通用document资产读取后整删；不为它保留2500行分批体系 |
| test_generation_semantics.py | 3633 | 旧逐批抽取/全量治理/支持refs/重检迁移可删；没有外部生产消费者。精确锚点/表格证据保护若新协议继续需要，应收敛到已有文档资产读取，不能让其成为继续强制全量专家抽取的理由 |
| test_generation_facts.py | 649 | 非评测/导出共享；旧设计义务/事实绑定体系可替换。新协议需要的文本非空/来源引用校验写入新验收器，不保留旧整套design IDs前置依赖 |

“可删”指新入口已替代且registry/conflict/projection已解除引用之后；不是在入口仍依赖时直接删文件。上述20,036行是旧域体积，不承诺最终净删同样行数。

## 必须保留的有效业务与函数

| 业务 | 实际代码位置 | 必须保持的行为 |
| --- | --- | --- |
| 已交付生成结果读取 | `results.py:16 persisted_test_generation_result` | `artifacts.test_generation` 是对外结果入口；历史只读不是旧执行链继续运行 |
| 来源身份 | `sources.py:36 SourceSnapshot`、`:103 persisted_source_snapshot`、`:135 latest_successful_run_for_source`、`:168 assert_same_source` | 项目/用户/来源SHA准确；新生成仍冻结 `requirement_source`，不能靠当前文档回推历史来源 |
| 真实人工文档评测 | `test_case_evaluation.py:17 create_test_case_evaluation_run`；`test_case_evaluation_feedback.py:20 evaluation_source_inputs`、`:36 evaluation_input_identity` | 必须由成功源Run的真实需求、已交付用例、人工参考文档形成三份输入；不依赖旧review_agent |
| 反馈沉淀 | `test_case_evaluation_feedback.py:81 difference_candidate_contents`、`:110 stage_evaluation_feedback`、`:151 validate_evaluation_feedback_record`；`test_case_evaluation_workflow.py:114 persist_test_case_evaluation` | 差异先待人工确认，校验源Run/评测Run/项目/用户/版本，不以模型分数自动变业务规则 |
| API结果/评估展示 | `serialization.py:228 serialize_run`、`:246 serialize_run_summary`、`:281 summarize_run_evaluation`；`service.py:579 get_run_evaluation_summary`、`:646 get_test_case_export_data` | 已交付产物、运行概要、反馈数量可读；主Agent内部状态收缩不要求重写评测业务 |
| Excel导出 | `excel_export.py:94 build_test_cases_excel`、`:156 test_cases_export_filename`；`routers/orchestration/agents.py:523 export_run_test_cases` | 只消费最终test_cases与来源文件名，不消费plan/fact/support/执行链 |
| 通用文档按需工具 | `document_agent_tools.py:19 _owned_document`、`:60 get_manifest`、`:65 search_document`、`:97 get_page_text` | 权限/项目/解析完成校验以及页内真实块和表格上下文；直接复用，不再复制来源解析组件 |
| 文档资产 | `document_asset_service.py:947 prepare_document_assets`、`:1054 load_document_manifest`、`:1085 document_page_text`、`:1094 document_page_layout`、`:1180 document_table_layouts`、`:1372 search_document_pages` | 原文、页图、坐标、表格与SHA属于通用知识库，不能跟旧semantics一起删除 |
| RAG/短期状态 | `context_agent_tools.py:30 retrieve_context`；`context_provider.py:42 ShortTermStateContextSourcePort`、`:220 OnDemandContextProvider`、`:263 build_context_provider` | 显式按需来源选择、完整manifest、项目/用户隔离；均不导入旧generation域 |
| 长期记忆端口 | `ports/context_memory_port.py:244 ContextSourcePort`、`:253 LongTermMemoryPort`、`:272 ShortTermStatePort`、`:303 assemble_context` | 可插拔接口与预算；不删除用户偏好、项目知识、长期反馈功能 |
| 记忆版本/确认 | `context_memory_repository.py:86 ContextMemoryRepository`（`:188 retrieve`、`:286 create_pending`、`:382 revoke`、`:436 confirm_pending`、`:495 record_feedback`） | validated_feedback仅项目作用域，确认/撤销/版本保持真实证据链 |
| 通用agent执行设施 | `action_journal.py:58 AgentActionJournal`、`checkpoint_storage.py:53 ArtifactStore`、runtime/sdk_adapter/autonomous_tools/autonomous_budget/retry_policy/output_repair | 保留日志、租约、原子行动、中断恢复、请求预算、模型输出修复、合法工具控制；不因旧生成变更而删除通用DAG/Agent引擎 |
| 人工确认控制流 | `conflict_confirmation.py:9 ConflictConfirmationRequired`、`:69 apply_conflict_decision`、`:134 confirmation_instructions` | 保留真实请求/整表答复/项目归属/原子更新/预算不重置/等待不耗执行期限；删除其对旧planning形态的假设 |

新生成最终至少需要保持：`requirement`、`test_cases`、`evidence.source`（或独立requirement_source）、`case_count/target_count/target_met`；正式成功后才供评测。当前Excel用例字段由 `excel_export.py:70 _project_case` 定义。`execution_plan/final_review/case_fact_bindings`是旧生成交付验收的自有字段，正式评测与Excel不读取；可在新协议内替换，而不是为导出保留旧链。

## 规格和测试清理

25工具、13Agent的名称及精确行号已逐项列在JSON `generation_specs`。需要收缩的是这些定义与实际调用链；只把它们隐藏在UI或只删DAG不改变成本。`test_generation_scenario_designer`也是旧生成器付费工具，Run63真实调用43次，不能忽略这条子链。

显式依赖旧域的测试文件36个、QA脚本25个，完整列表在JSON `dependent_file_groups`。其中以下文件混合通用平台保护，不能整文件删除以求通过：

- `test_agent_platform_contracts.py`：保留程序/DAG解析、SDK工具、来源API/序列化/配额等通用合同；删除旧13Agent/25工具、routing/support/schema强制断言，改为新协议回归。
- `test_runtime_retry.py`：大部分是通用runtime/SDK/并发/重试保护；仅删除其旧generation/planning/final-review专用repair策略断言（如311/947/963/991/1014/1057/1338），不要删除通用重试验证。
- `test_agent_program_input_real_data.py`：79/93/98/112的程序原始输入、空payload、请求预算、DAG原始映射仍有效；117的旧生成source检查点迁移需按新协议重写。
- `test_artifact_read_cost_real_data.py`：75/83/89的真实大状态解码、无共享别名、跨读取独立保留；102/116/132的旧source/planning视图测试随旧域删除。
- `test_agent_context_cost_real_data.py`：合法工具投影、完整观察缓存失效、实时预算属于通用保护；旧compact_planning/layout/source receipt形状断言改写，不保留旧planning模块满足测试。
- `test_context_compression.py`：通用开关/候选预算属于独立组件；旧`_compression_model_catalog`相关断言不能强留semantics。
- `test_native_test_generation.py`：真实输入/最终产物/唯一性/导出契约重写；旧planner/router/schema和registry集合断言删除。

其他draft/planning/support_refs/authority_baseline/source_context/default_layout/batching/preflight/chain/review专用测试随旧协议整组删除或用新真实协议回归替代。`real_planning_protocol_projection.py`、`real_support_reference_projection.py`是旧fixture适配器，也应随无消费者的旧测试移除，不能演化成生产兼容层。

现有评测反馈、Excel交付、来源身份、项目记忆、租约/预算/日志等测试继续保留，已核对具体文件：`test_test_case_evaluation_workflow.py`、`test_test_case_evaluation_feedback.py`、`test_run_evaluation_summary.py`、`test_excel_export.py`、`test_artifact_source_identity.py`、`test_checkpoint_protocol_real_data.py`、`test_shared_budget_concurrency_real_data.py`、`test_retry_progress_journal_real_data.py`、`test_recovery.py`、`test_automation_evaluation_workflow.py`。QA中 `verify_test_case_feedback_real_data.py`、`verify_lifecycle_sources_real_data.py`、`verify_compact_journal_real_data.py`、`verify_checkpoint_history_real_data.py`、`verify_checkpoint_consumers_real_data.py`、`verify_autonomous_budget_real_data.py`、`verify_agent_excel_delivery_real_data.py`保护共享业务边界；其中若有字符串断言旧state字段，应只改对应断言而不删除整类验证。

历史Run59–63原始证据位于output/qa，保留真实费用、失败与候选/交付区别；它们不是生产代码，不把删除历史JSON/压缩fixture计为生产清理成果。QA旧协议脚本从当前验收入口移除，新的默认验收不得再导入被删模块。

## 已发现但不在本次只读任务修改的风险

1. `service.py:646 get_test_case_export_data`当前只查artifact，没有校验run.status=success；新入口确保只在最终完成原子发布artifact，避免失败候选能导出为最终成果。可以另加正式成功状态校验。
2. `conflict_confirmation.py`虽名为通用模块，实际要求所有gap_records完成才提问，又在答复后重置旧authority/cache；新协议不能沿用这套旧阶段前置。
3. `checkpoint_storage.state_projection`是重写状态时最容易遗漏的隐式耦合；保留运行时存储实现，替换领域展示字段即可。
