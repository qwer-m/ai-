# generation 测试目录说明

这个目录是 RAG / test generation 链路的 pytest 回归与契约测试目录，不是生产运行入口，也不应承载针对某个文档、某个页面或某次生成的特调逻辑。

新增测试优先按链路边界分流：

- `test_generation_quality_governance.py`：最终用例质量治理，如优先级、可断言期望、截断文本、推理泄漏、最终用例过滤。
- `test_execution_plan_quality_contracts.py`：workflow blueprint、main smoke、execution group、fixture 选择等执行计划契约。
- `test_core_flow_backfill*.py`：core flow 覆盖、backfill 计划、候选生成与应用。
- `test_generation_route_quality_errors.py`：路由层对空结果、低质量结果等生成状态的 HTTP 错误映射。
- `test_generation_diagnostics.py` / `test_*diagnostic*`：GEN_DIAG、导出、稳定性归因等诊断输出契约。

维护规则：

- 不新增 `from test_x import *` 形式的测试转发壳，避免 pytest 重复收集。
- 公共测试运行器放到非 `test_*.py` helper 文件，例如 `quality_governance_harness.py`。
- 针对真实生成问题新增回归时，先保留能解释问题的数据链路信息，再抽象成通用契约；不要把某一篇文档或某一次生成固化成生产规则。
- 如必须替代外部模型、数据库或文件系统，命名应表达 deterministic / in-memory 语义，并只用于隔离外部副作用，不应改变被测业务路径。
