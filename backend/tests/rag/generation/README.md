# Agent 生成相关测试

本目录只保留仍被新链路使用的窄范围回归：

- `test_generation_model_selection.py`：校验 Agent 运行时的模型选择。
- `test_rag_single_debug_display.py`：校验 RAG 调试结果展示。

Agent 工作流、Run、节点执行和 Artifact 契约统一放在 `backend/tests/agent_platform`；评测链契约放在 `backend/tests/orchestration`。不要在这里恢复旧测试生成路由、日志诊断或转发壳。
