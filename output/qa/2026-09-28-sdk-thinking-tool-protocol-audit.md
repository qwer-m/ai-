# 推理与工具选择协议核查

已核查 Run 39 的真实评审路由：`deepseek-v4-pro`，`OpenAICompatibleProvider`，经项目配置的兼容网关使用 `chat_completions`。本机安装 `openai 2.52.0`、`openai-agents 0.19.2`。没有输出认证信息，没有额外模型调用。

## 已确认的协议问题及最小修改

DeepSeek 官方 [Chat Completions 参数文档](https://api-docs.deepseek.com/api/create-chat-completion/) 明确：推理模式不支持 `tool_choice=required` 和指定单个工具，会返回 400。当前失败请求配置是 `thinking=enabled`，本地 `_model_settings` 原先仍把 `force_terminal_tool_choice=False` 转成 `required`。这确认了组合存在协议冲突；本轮经过兼容网关，且错误仅为通用 `400001`，尚不能排除其他拒绝原因。

修改位置：`backend/modules/agent_platform/sdk_adapter.py:1102`。运行配置可显式选择 `tool_choice=auto/required/none`，优先于原有终止工具推导；未配置时保留旧行为。声明终止工具时禁止 `none`，无效选项提前报错。没有按模型名或供应商名增加分支，也没有关闭推理或替换模型。

完整提交验收仍在 `sdk_adapter.py:2103` 的 `autonomous_loop.require_submission()`；实际实现 `autonomous_tools.py:221` 只允许已通过契约的提交，自由正文不能通过。协议选择和领域提交验收职责保持分离。

## 当前 SDK 已有的推理内容支持

本地 SDK 文件均位于 `.venv/Lib/site-packages/agents/models/`：

- `openai_chatcompletions.py:526` 经 `chatcmpl_converter.py:75` 原样转换 `auto/required/none`，`openai_chatcompletions.py:577` 将结果放入请求；`extra_body` 在同文件第 590 行传给 OpenAI client。
- `chatcmpl_converter.py:137` 将响应中的真实 `reasoning_content` 保存为 `ResponseReasoningItem`，携带来源模型信息。
- `openai_chatcompletions.py:503` 将现有 `should_replay_reasoning_content` hook 交给消息转换器；未覆盖 hook 时，`reasoning_content_replay.py:36` 的 SDK 默认行为已经覆盖当前实际模型。
- `chatcmpl_converter.py:840` 核对推理项来源并在需要时回放，`chatcmpl_converter.py:559` 放入后续 assistant 消息。项目没有必要再复制此能力。
- `openai_chatcompletions.py:261` 保留供应商返回的 output token details；是否包含有效 reasoning token 统计仍以真实响应为准。

官方 [Thinking Mode 文档](https://api-docs.deepseek.com/guides/thinking_mode/) 要求携带工具的后续请求完整回传历史 `reasoning_content`。尚未取得新推理响应时，不能把阅读代码等同于已完成真实回传验收。

## 安全诊断与已完成验证

`backend/modules/agent_platform/request_diagnostics.py:142` 仅新增 `model_settings.tool_choice` 和 `model_settings.thinking_type`；没有复制扩展参数正文。第 163 行新增 `sdk_response_reasoning_item_count`，不记录推理正文。

`backend/scripts/qa/verify/verify_sdk_tool_choice_real_data.py` 离线回放保存的两次真实模型响应，保留原调用 ID、参数和 usage。第一轮遗漏意图 3 仍被拒绝，第二轮完整提交通过；显式 auto、旧配置默认 required、冲突配置拒绝、安全诊断和用量均通过。

结果见 `output/qa/2026-09-28-sdk-tool-choice-real-data.json`。历史响应的 reasoning item count 为 0，因此该报告明确未覆盖真实推理内容回放。没有合成 reasoning，没有改线上 Run，也没有把结构验收当作业务语义通过。
