# agent_platform 冗余与重复规则审计

日期：2026-09-17。范围：`backend/modules/agent_platform` 当前工作区，35 个 Python 文件、25,072 个物理行。本轮只做代码审计与只读验证，未修改生产代码；既有未提交改动保留。

结论：存在可删除的旧定义和导出，也存在已经出现行为差异的重复实现。优先收尾现有模块拆分、复用已有校验和生命周期入口，再处理运行时的重复执行流程。文件大、函数同名或跨边界再次校验，本身不足以判定为冗余。

## 一、可优先清理的定义与依赖

### 1. 工作流拆分留下旧导出和反向依赖

- [test_generation_workflow.py:64](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_workflow.py:64) 导入 75 个 Schema 名字，其中 70 个本地未使用，其余 5 个只服务于本文件残留的 Schema 定义。
- [同文件:143](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_workflow.py:143) 导入的 planning 私有函数中，16 个本地未使用。只有 `_summary_item_text`、`_business_planning_limits` 仍被旧测试通过此处引用，其余 14 个没有这种消费者。多数函数在 planning 内仍活跃，应删除转发导入，保留实现。
- [同文件:532](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_workflow.py:532) 到 849 行仍放着校验、批次、执行链的静态契约和 `_map_record_schema`；[builtin_specs:82](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_builtin_specs.py:82) 因此反向导入业务工作流模块。
- [workflow:852](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_workflow.py:852) 的 `__getattr__` 又惰性转发三组 `BUILTIN_*_SPECS`，仓库内已无旧出口消费者。[registry:96](D:/Qoder/测试开发平台/backend/modules/agent_platform/registry.py:96) 已直接读取规格模块。
- [builtin_specs:5](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_builtin_specs.py:5) 的 75 个 Schema 导入中，40 个本地未用，也没有调用方通过该模块读取这些 Schema。

处理：把残留静态契约归入已有 schemas；迁移旧测试及真实校验脚本的导入，再删除无消费者导出和惰性转发。`_required_text`、`_identity` 仍有业务调用，不能随旧导出一起删除。无需新增兼容层。

涉及消费者包括 `test_agent_platform_contracts.py`、`test_native_test_generation.py`、`test_test_generation_batching.py`、`test_test_generation_chain.py`、`test_test_generation_semantics.py`，以及 `backend/scripts/qa/verify/verify_output_repair_real_data.py`。迁移导入不等于删除这些测试覆盖。

### 2. 用例评估保留一组未接入执行链的旧报告契约

[test_case_evaluation_workflow.py:26](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_case_evaluation_workflow.py:26) 的 `METRICS_SCHEMA`、`DEFECT_ANALYSIS_SCHEMA`、`REQUIREMENT_BASELINE_SCHEMA` 只供 `TEST_CASE_EVALUATION_SCHEMA` 引用，而后者没有代码消费者。第 26–96 行形成无入口的定义链。

实际链路是第 344 行 Agent 输出 `EVALUATION_EVIDENCE_SCHEMA` → 第 304 行持久化工具接收证据 → 第 138 行 `_build_evaluation_result` 生成报告。

处理：删除这四个旧定义；保留活动契约仍在使用的 `REPORT_SUMMARY_SCHEMA`、`TEXT_LIST_SCHEMA` 和 `REQUIREMENT_POINT_LIST_SCHEMA`。这是删除未使用定义，不是撤掉运行中的校验。

### 3. 图片区域与文本跨度重复定义

- [batching:383](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_batching.py:383) 的 `_image_evidence_region` 与 [semantics:108](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_semantics.py:108) 的 `_image_region` 重复维护同一套严格规则：字段集合、拒绝布尔值、有限数值、正宽高、页面边界及 `1.000001` 容差。错误文案不同。
- 真实资产文档 277、278 各有 8 页、19 个图片区域；38 个实际区域通过两套校验，返回值全部一致。这支持合并候选，但不代表覆盖了全部非法输入。
- [schemas:1141](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_schemas.py:1141)、1150、1159 的三份 span 对象与 [SOURCE_SPAN_SCHEMA:1304](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_schemas.py:1304) 完全相等，已通过当前模块实际对象比较确认。

处理：把区域校验收敛到现有来源契约边界；提前定义现有 `SOURCE_SPAN_SCHEMA` 并复用。`right_range` 多一个必填 `head_end`，不能替换为普通 span。资产服务的像素坐标转换与严格区域校验用途不同，不能混为一个函数。

## 二、同一调用链里的重复处理

### 4. 审批拒绝重复结束节点

[service:723](D:/Qoder/测试开发平台/backend/modules/agent_platform/service.py:723) 调用 `transition_run(..., "failed")`，后者经 [lifecycle:78](D:/Qoder/测试开发平台/backend/modules/agent_platform/lifecycle.py:78) 调用仓储批量结束未完成节点。

[service:729](D:/Qoder/测试开发平台/backend/modules/agent_platform/service.py:729) 随后又查询审批节点，重复修改状态、错误和结束时间，并重新生成时间戳。正常审批节点由 [runtime:487](D:/Qoder/测试开发平台/backend/modules/agent_platform/runtime.py:487) 置为 `waiting_approval`，已在批量更新范围内。

处理：核对历史审批节点状态约束后，删除服务层这次查询和手工更新，沿用生命周期入口；保留审批决定、事件和事务提交。无需新增 helper。本轮没有执行拒绝审批操作。

### 5. 文档复用候选重复查询来源文档

[service:453](D:/Qoder/测试开发平台/backend/modules/agent_platform/service.py:453) 查询文档检查存在，随后第 466 行调用 `latest_successful_run_for_source`，经 [sources:146](D:/Qoder/测试开发平台/backend/modules/agent_platform/sources.py:146)、[run_repository:74](D:/Qoder/测试开发平台/backend/modules/agent_platform/run_repository.py:74) 再次查询同一项目文档。

此外，[run_repository:56](D:/Qoder/测试开发平台/backend/modules/agent_platform/run_repository.py:56) 与已有 [KnowledgeDocumentRepository:194](D:/Qoder/测试开发平台/backend/modules/knowledge_base_components/repositories/knowledge_document_repository.py:194) 重复实现 `id + project_id` 查询。

处理：复用已有知识库仓储，让已解析的 `SourceSnapshot` 沿调用链传递。保留项目过滤，以及“文档不存在”和“解析未完成/类型不支持”的不同错误。来源解析还服务于创建和重试，需要一起审查调用点。

### 6. 事实绑定索引重复实现并反复构建

[test_generation_facts.py:307](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_facts.py:307) 在 `validate_case_fact_bindings` 中手写数组、对象、空/重复 case_id 校验与索引构造；同文件 [binding_index:458](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_facts.py:458) 已实现这套规则，只是错误字段名称固定。

[review:603](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_review.py:603) 对每个 difference 调用 `_difference_fact_ids`；后者在 [第 103 行](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_review.py:103) 每次对相同输入重新调用 `binding_index`。存在 D 条涉及用例的差异、B 条绑定时，此处会重复遍历 D 次 B 条绑定。

处理：让现有 `binding_index` 接收必要的字段上下文；在单次终审批次中构建一次索引并传给差异解析。保留事实引用完整性和跨模块校验，不把跨工具或恢复边界的重新校验一并删除。这里确认的是重复计算，未做性能压测。

## 三、重复实现已经产生规则差异

### 7. agent_map 的顺序与并行流程没有完全共用生命周期

[runtime:2789](D:/Qoder/测试开发平台/backend/modules/agent_platform/runtime.py:2789) 分流后，两条路径分别维护恢复、请求、重试、后处理、事件和落盘。

已发现的具体差异：

- 顺序分支 [第 2926 行](D:/Qoder/测试开发平台/backend/modules/agent_platform/runtime.py:2926) 直接同步调用 `run_agent`，请求期间没有并行分支的取消轮询和续租管理。
- 顺序成功检查点 [第 3094 行](D:/Qoder/测试开发平台/backend/modules/agent_platform/runtime.py:3094) 重新手写 `sdk_state`，遗漏现有 `_AgentMapCheckpoint.state()` 包含的 `failure_counts`；若该时点中断并恢复，累计失败统计存在丢失风险。

真实配置核对：4 个内置工作流和 104 条 MySQL 工作流目前均采用并行映射；数据库 364 个 `agent_map` 节点的并发度为 2、3、4、6，没有 1。上述风险存在于仍受契约支持的顺序路径，不能表述为当前真实任务已发生故障。

处理：先复用已有 `_AgentMapCheckpoint`，再收敛单项执行和请求期间的取消/续租。并行独立 Session、并发安全工具约束和稀疏完成顺序必须保留，不直接删除顺序分支。

### 8. 同一配置或去重规则有两种解释

| 位置 | 已确认差异 | 收敛方向 |
| --- | --- | --- |
| [runtime:1047](D:/Qoder/测试开发平台/backend/modules/agent_platform/runtime.py:1047) / [sdk_adapter:929](D:/Qoder/测试开发平台/backend/modules/agent_platform/sdk_adapter.py:929) | `max_turns=0` 在预留层变成默认值，在 SDK 层报错；两层对是否包含子 Agent 工具的判断不同 | 共用有效工具能力和轮次解析，保留额度预留与执行职责 |
| [context_compression:59](D:/Qoder/测试开发平台/backend/modules/agent_platform/context_compression.py:59) / 同文件第 130 行 | 相同转换和限幅写两遍；0 分别成为最小值 128 和默认值 1800 | 共用标量预算归一化；正式链先归一化，尚未证实触发差异 |
| [planning:53](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_planning.py:53) / [batching:48](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_batching.py:48) | 标题与模块去重的 `_identity` 一份有 NFKC，一份没有；前者由 workflow 第 410 行使用，后者由合并第 1988 行使用 | 统一用例身份规范；不要直接把其中一个当无用函数删掉 |

当前数据库未发现 `max_turns=0`，配置子 Agent 的 Agent 同时有直接提交工具。Unicode 差异由代码确认，本轮没有声称真实数据已因此漏检。

## 四、次级清理项

- [service:189](D:/Qoder/测试开发平台/backend/modules/agent_platform/service.py:189) 筛选可恢复检查点时已判断修复状态和部分 Map 输出；第 587 行为事件统计再次计算。可返回携带两个独立标志的恢复计划，两个标志允许同时为真。
- [serialization:117](D:/Qoder/测试开发平台/backend/modules/agent_platform/serialization.py:117) 与第 146 行复制 13 个公共标量字段。可提取公共响应字段；摘要不能先完整序列化再删字段，否则可能破坏 `load_only()` 并加载大 JSON。
- [sdk_adapter:1310](D:/Qoder/测试开发平台/backend/modules/agent_platform/sdk_adapter.py:1310) 的 `_run_sdk_agent_sync` 只剩测试调用；[runtime:1934](D:/Qoder/测试开发平台/backend/modules/agent_platform/runtime.py:1934) 的同步替身分支主要服务 monkeypatch。迁移测试入口后可删除测试专用兼容，保留生产仍调用的公开同步入口和真正的取消管理。
- [batching:270](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_batching.py:270) 的 `_compact_fragment_text` 在后端扫描中只有定义；同文件导入 `index_effective_facts` 后未使用。删除前再做全仓引用复核即可。
- planning/review 的 `_required_text` 函数体完全相同，semantics 版本仅错误文案有差异。适合随所属校验边界整理，不值得为几行函数单独增加一层通用工具包。

## 五、不应作为冗余删除的部分

- 生成后处理与后续合并都调用同一个验证器：存在工具、持久化恢复等输入边界，本轮不建议直接撤掉后一层验证。
- runtime 的顶层 SDK 调用使用 `skip_output_postprocessor=True`，SDK 与 runtime 后处理不是重复执行。
- 评估结果回写源 Run，与 runtime 写当前评估 Run，目标不同。
- `recovery` 查询候选后在锁内再次验证租约是并发保护；retention 服务与仓储的同名方法分别决定策略和执行删除。
- batching/semantics 的 `_fact_source_order` 对文档和 inline 的排序语义不同，不能因为同名就合并。
- 四个 `submit_*` handler 都返回提交参数，但工具身份和契约不同；不能认定为四项重复功能。

## 六、建议实施顺序与验证

1. 清理无消费者的导入、旧报告 Schema；完成 schemas/specs/workflow 的依赖收尾。检查全部调用点、导入可达性和内置规格序列化结果。
2. 复用区域、span、事实绑定索引；删除审批重复写，沿来源链传递已有对象。用真实资产、现存运行产物回放，数据库保持只读。
3. 统一 `max_turns` 和预算解析，再逐步合并 agent_map 单项生命周期。顺序路径当前无真实运行样本，实施时明确补足验证证据，不用 mock 结果替代真实验证。

本轮验证：源码与 AST 扫描、全项目调用引用核对、真实工作流配置只读查询、真实文档 38 个图片区域对照、3 个 span Schema 实际对象比较。未运行模型、未操作审批、未写数据库、未运行 mock 测试，也未进行完整端到端测试。
