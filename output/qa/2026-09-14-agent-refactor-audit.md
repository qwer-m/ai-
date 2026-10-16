# Agent 测试生成真实运行与重构审计

> 后续修复已完成：Run 27 已真实生成并入库 50 条用例，终审、详情接口和 Excel 导出均通过，见[最终验证报告](D:/Qoder/测试开发平台/output/qa/2026-09-14-generation-verification.md)。下文保留 Run 23 当时的失败记录。

本次使用用户提供的原始 PDF，通过业务服务创建 Run 并投递 Celery Worker。运行在第二个确定性节点失败，尚未调用模型，也未生成新用例。随后使用真实历史记录检查后续工具链和重构接口，不使用 mock 文档、mock 模型或 mock 仓储。

## 本次真实执行

| 项目 | 实际值 |
| --- | --- |
| 原文件 | C:\Users\Administrator\Downloads\门店续费功能产品 (1).pdf |
| 文件大小 | 1,196,892 字节 |
| SHA256 | 1edc1f74f899c51832d4c5d5141e3e1de3d6f9c3f8b664748c4dfe8984f3004f |
| 来源一致性 | 原文件、知识库记录、页面资产三者指纹一致 |
| 项目 / 文档 / 用户 | 9 / 277 / 1 |
| 新运行 | Run 23 |
| Worker 任务 | 1c0b11dd-991c-477b-a778-5d875199397b |
| 输入配置 | 50 条用例、批次上限 5、开启上下文压缩、压缩预算 1,800 tokens |
| 文档资产 | 8 页；第 8 页为 5 张界面截图，正文字符数为 0 |
| evidence 节点 | 节点 693 成功；4,796 字符、7 条文本证据 |
| prepare_source_semantics 节点 | 节点 694 失败：页面没有可锚定的正文块，page_number=8 |
| 模型请求 / tokens | 0 / 0 |
| 新生成用例 | 0 |

[本次运行的节点与事件记录](D:/Qoder/测试开发平台/.cache/refactor-audit-live-run-result.json)。原 Run 22 被平台自带的失败历史清理规则淘汰；执行前已保存其[错误与节点输入快照](D:/Qoder/测试开发平台/.cache/run22-before-real-audit.json)。

## 已确认的问题与处理

| 问题 | 触发条件与影响 | 归因及处理 |
| --- | --- | --- |
| 需求规划模块漏导入两个名称 | 文档读取时先触发 load_document_manifest 的 NameError，后续还会遇到 SOURCE_ARTIFACT_KEY 缺失 | 最近的函数迁移遗漏；已补齐导入并清理旧文件残留，本次真实 evidence 节点通过 |
| 同步 SDK 入口漏接模型路由参数 | 串行 agent_map 传入 model_route_override，同步 run_agent 原先不接受该参数，请求模型前即抛 TypeError | 更早的 da1cbd9 变更漏同步接口；已补充参数并转发到 run_agent_async |
| 用例评测日志调用参数错误 | 评测 Run 已创建后，日志函数收到 workflow_kind、payload，而实际参数是 kind、details；接口会在返回成功响应前报错 | b77b36a 中的调用迁移遗漏；已改回实际函数契约 |
| 纯图片页被正文锚点校验拒绝 | 第 8 页有真实图片，但不存在 source_span 文本块，也未进入文本证据目录 | 本次拆分前已存在，c89cd9a 中的语义处理文件与修复前当前文件完全一致；本轮尚未补齐图片证据链，仍是整轮生成的阻塞点 |
| 提取后的局部变量残留 | _execute_node 留下 4 个不再使用的别名；用例校验保存了不再使用的正文返回值 | 已删除无用别名和赋值，保留需求非空校验调用 |

相关修改位置：

- [需求规划模块导入](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_planning.py:13)
- [同步 SDK 参数与转发](D:/Qoder/测试开发平台/backend/modules/agent_platform/sdk_adapter.py:1400)
- [评测接口日志调用](D:/Qoder/测试开发平台/backend/routers/orchestration/evaluation_execute_routes.py:110)
- [运行器节点入口](D:/Qoder/测试开发平台/backend/modules/agent_platform/runtime.py:3522)
- [用例非空校验](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_workflow.py:374)
- [图片页当前阻塞位置](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_semantics.py:1300)

## 重构完整性检查

| 检查 | 结果 |
| --- | --- |
| 原工作流顶层定义对照 | 重构前 40 个函数／类均找到对应实现；检查时全部 AST 一致，之后仅清理了校验函数的未使用赋值 |
| 原运行器顶层定义对照 | 79 个原定义均存在，75 个 AST 一致；4 个变化入口已结合提取函数、调用契约和真实检查点核查 |
| 独立进程导入 | 34 个 Agent 平台模块全部成功，不依赖提前导入 registry 的顺序 |
| 内置注册与契约 | 36 个工具、15 个 Agent、4 个工作流、58 个节点引用通过；工具签名、JSON Schema、Agent 工具引用、旧导出身份通过 |
| Python 显式函数调用契约 | 850 处当前模块全局函数调用检查通过；发现并修复同步 SDK 参数遗漏 |
| 工作流日志调用契约 | 10 个可静态分析的日志调用点通过；发现并修复评测接口参数遗漏 |
| IDE 定义跳转 | 8 个文件中的 136 处仓储／Session 调用，源目录配置两种模式均无未解析目标 |
| 后端未定义名称 | 全 backend 的 F821、F822、F823 检查通过 |
| 重构模块未使用局部变量 | Agent 平台和评测接口的 F841 检查通过 |
| 前端 | TypeScript 检查及 Vite 生产构建通过 |
| 真实文档入口测试 | test_requirement_evidence_reads_real_requirement_document_only 通过 |
| 差异格式 | git diff --check 通过 |

850 处调用检查针对可静态解析的全局函数和显式参数，不覆盖动态 getattr、任意 **kwargs 或全部实例方法。136 处跳转验证补充覆盖了明确的仓储与 Session 方法，但不等于逐个业务接口的端到端执行。

证据：[定义迁移](D:/Qoder/测试开发平台/.cache/refactor-audit-functions.json)、[运行器迁移](D:/Qoder/测试开发平台/.cache/refactor-audit-runtime-diff.json)、[独立导入](D:/Qoder/测试开发平台/.cache/refactor-audit-fresh-imports.json)、[内置注册](D:/Qoder/测试开发平台/.cache/refactor-audit-registry.json)、[函数调用](D:/Qoder/测试开发平台/.cache/refactor-audit-python-calls.json)、[日志调用](D:/Qoder/测试开发平台/.cache/refactor-audit-log-contracts.json)、[定义跳转](D:/Qoder/测试开发平台/.cache/refactor-audit-navigation.json)。

## 真实历史数据回放

Run 17 与 Run 19 共回放 66 个确定性工具节点，覆盖来源语义合并、业务规划、路由、权威事实、生成批次、审计、终审修复、批准、校验和执行链。工具输入取自实际持久化节点记录，数据库事务设为 READ ONLY。原本 4 处输出差异由回放时使用了最终修复轮次造成；从真实产物恢复各节点当时的轮次后，输出全部与历史记录一致。

- 4 个历史来源入口无法完整回放：文档 273 的页面资产已缺失，文档 276 的知识库记录和页面资产已缺失。没有构造替代文档或补写历史数据。
- 2 个写库 persist 节点不在只读回放中执行；最终已存用例、展示结果、Excel 导出使用既有真实契约验证。
- Run 19 的 50 条真实用例、18 个生成批次、测试输入导出、事实绑定、展示与持久化一致性检查通过。
- Run 19 的 agent_map 输出和 SDK 检查点可由提取后的 _AgentMapCheckpoint 完整重建，用例校验输出也与持久化结果一致。
- 定义同步、覆盖继承、退役、事务回滚以及生命周期、租约、历史清理使用真实行的隔离数据库副本验证通过；没有修改线上历史业务数据。
- 同步 SDK 参数验证使用 Run 17 / 节点 533 的真实输入和 turbo 路由，验证同步／异步签名一致及参数转发；未把该契约检查描述为真实模型请求成功。

证据：[工具回放](D:/Qoder/测试开发平台/.cache/refactor-audit-real-tool-replay.json)、[恢复修复轮次后的输出对比](D:/Qoder/测试开发平台/.cache/refactor-audit-replay-differences.json)、[检查点](D:/Qoder/测试开发平台/.cache/refactor-audit-checkpoints.json)、[SDK 路由契约](D:/Qoder/测试开发平台/.cache/refactor-audit-sdk-route.json)、[真实定义验证](D:/Qoder/测试开发平台/.cache/refactor-audit-definitions.json)、[生命周期验证](D:/Qoder/测试开发平台/.cache/refactor-audit-lifecycle.json)。

## 剩余边界与下一处修复重点

当前 PDF 的完整模型生成、生成用例质量与最终持久化仍未验证，因为 Run 23 在图片页预处理阶段停止。修复应贯通图片布局块、证据目录、视觉输入和事实来源校验，保留图片内容及可追溯坐标；跳过第 8 页或伪造正文会掩盖真实缺口。

全量 Pyright 类型检查并未通过；导入／未定义名称专项为零，仍存在 ORM 等类型诊断，详见[完整类型诊断](D:/Qoder/测试开发平台/.cache/agent-imports-backend-full.json)。不能将专项通过表述为全量类型检查通过。

运行时版本指纹目前主要覆盖注册处理器函数体、部分策略模块和定义，不能证明所有模块导入及 SDK 桥接代码一致。本次额外使用独立进程导入及实际 Worker 执行验证，没有只依赖指纹相等判断代码有效。

审批回放缺少真实审批记录，未编造记录；SQLite 隔离验证不覆盖 MySQL 并发加锁行为。审计中的一次自写整行 ORDER BY 查询触发 MySQL sort memory 错误，随后改用项目已有的无排序读取／Python 排序仓储方法完成读取；不将诊断脚本问题算作生成链路故障。

真实运行使用的服务已正常停止，日志包含 Stopping services / Services stopped；最后确认没有运行中的 Agent 任务。本轮未提交 Git commit。
