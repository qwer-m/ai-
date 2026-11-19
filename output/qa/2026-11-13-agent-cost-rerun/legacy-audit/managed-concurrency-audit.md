# 受管专家并发的过渡遗漏

本次只读审计，未修改运行中的代码或 Run60。冻结定义与设置的提取见 `managed-concurrency-static-evidence.json`。

## 已确认的问题

新主 Agent 的受管专家调用没有接入现有自适应并发策略。每次动作重新建立固定并发闸门：

```
test_generation_source_agent / planning_agent / generation_agent / review_agent
  -> ToolExecutionContext.invoke_agents
     -> asyncio.Semaphore(self.agent_concurrency)          registry.py:64
     -> SDK invoke_agent                                sdk_adapter.py:2241
        -> 命中已验收回执：复验后直接返回，0 新模型请求
        -> run_agent_async(... require_validated_submission=True)
```

`sdk_adapter.py:2201-2204` 每次父会话从冻结配置读取 `managed_agent_max_concurrency`，重新赋给 context。Run60 主 Agent439 冻结值是 6；草案 Agent441 和归属路由 Agent427 都走 `turbo`。`invoke_agents` 只对预算拒绝及暂态异常进行结构化收敛，没有记录上游压力状态，没有降低新的请求准入，并且下一次动作又回到 6。

当前成功回执复用是有效机制；它防止重复生成已经完成的专家结果，却不能替代上游压力控制。两者是不同问题。

## 旧 DAG 已有、但没有被新链路复用的能力

`runtime._execute_agent_map_parallel_async`：

- `runtime.py:1921-1953`：结合配置/平台上限、最小并发与上次 `parallelism.effective_concurrency` 恢复实际并发。
- `runtime.py:2049`：把有效并发与压力计数写入 sdk_state。
- `runtime.py:2094`：调整实际并发、清理相应窗口计数、保存调整轨迹并发出 `map_concurrency_adjusted`。
- `runtime.py:2477-2493`：实际成功后压力计数衰减；成功累计达到配置值，恢复一级并发。
- `runtime.py:2508-2521`：可重试压力类故障达到阈值后降一级并发。

现有配置为 max=6、min=2、pressure threshold=2、recovery successes=6。新 `invoke_agents` 没有读取这些设置。它甚至只约束配置 1..16，不使用 `AGENT_MAP_MAX_CONCURRENCY` 的平台上限。

压力类型已经统一定义于 `retry_policy.CONCURRENCY_PRESSURE_FAILURE_KINDS`。当前 `model_transport_failure_kind` 也已供新调用链使用。因此不需要新故障分类、第二套 retry 引擎或每个生成动作各自加规则。

## 最小修法草案（已生成，未应用）

`managed-concurrency-draft.patch` 包含 4 个生产文件与 1 个真实数据回归文件，净增加 287 行，未修改运行中的源码。二进制真实数据单独放在 concurrency-draft-tree，需按 manifest 复制。

1. `retry_policy.py` 抽出共享纯 `AdaptiveConcurrency`，DAG 与 managed 均使用。旧算法对一次成功就将 pressure 减一，真实交错 429 会被成功完全抵消。共同修正为：压力失败重置稳定成功数，窗口内压力累计；连续真实成功达到现有 recovery_successes 阈值后才清 pressure、恢复一级并发。保留 max/min/pressure/recovery 设置，不降低阈值为 1。
2. `runtime.py` 删除旧重复窗口算术，原 DAG 调度、重试、事件和使用量保持原职责，改调用共享纯策略并持久化实际控制计数。
3. `sdk_adapter.py` 在已验收回执提前返回之后、真实新 managed session 调用之前取得动态 Condition 许可。registry 原有批次 Semaphore、TaskGroup、输入输出索引、预算拒绝闩锁和严格首错闩锁保留；SDK 增加真实 provider/model 作用域的准入上限。降载不取消已付费兄弟，不引入重执行单项的循环。嵌套咨询不再次经过 managed invoke 闭包，因此不重复占同一许可。
4. `action_journal.py` 在当前轻量头按 scope_hash 保存 managed_parallelism，沿既有事务、分支、claim 验证和 _save 入口；不把控制计数放入领域 state，不修改领域 hash、已有回执、Run 配额或未知请求用量。
5. 成功反馈只在真实新专家执行并验收后记录；回执 replay 不增加恢复成功数。预算拒绝、取消、领域校验和租约失权不成为压力信号。严格运行保持有效并发 1，部署 max cap 也用于 managed 实际并发。

实际 route 作用域使用已有 `_resolved_agent_model` 与 AIClient 的 provider 指纹，包含真实 model、provider_class、wire_api。相同物理 provider/model 的不同 Agent 或路由别名共享压力；不按文档、动作名或 Agent key 硬编码。

## 真实交错序列对照

从 Run60 已终结节点 405/406/407 的持久诊断与新保存回执提取：**94 个真实成功会话、5 次真实 429、4 次取消未知请求**。策略序列只采用前两类共 99 个事件；取消单独保存，不退款、不当作压力。

44 个成功事件精确匹配最后响应的微秒时间；50 个投影请求哈希无法直接匹配的成功事件，保留 MySQL 秒级保存时间并按明确的服务器 UTC+8 转 UTC，精度已记录。429 前后的关键事件无秒级排序歧义。

旧 Runtime 算术直接从当前文件 AST 提取，仅移除 `_event` 数据库写入副作用，按同一真实模型作用域重放：**0 次调整**。修正的共享策略：

- 03:09:05.693145 UTC，草案第二次 429：6 → 5。
- 跨动作保留有效上限，03:14:58.851940 UTC 路由阶段完成稳定成功窗口：5 → 6。
- 支持复核两次 429 之间已有至少 6 次真实成功，本截点未触发其降载。

证据：`concurrency-policy-real-sequence-replay.json`。这证明策略差异，不能冒称已经减少在线 429 或 token。

隔离进程直接加载 4 文件草稿中的 retry_policy/sdk_adapter，**9 项真实数据测试通过**（1.36 秒）；包括交错失败、跨 JSON 恢复、无 429 维持并发、稳定恢复、严格并发 1、未知取消分类和新会话准入上限。Python 编译与 `git apply --check --ignore-space-change` 通过；整套后端回归与新 Run 在线验证留待正式整合后执行。

## 429 的证据边界

以上足以确认“新并发链缺少旧降载策略”，不能据此确认所有 429 由并发引起。当前 `ModelRequestDiagnostics` 对真实异常保存状态、白名单响应头（可含 retry-after）、body 哈希/字节数，不一定保存服务端 JSON error 的 RPM/TPM 分类。Run59 无 429；Run60 的 429 应从实际脱敏服务响应/已有错误 code 继续区分，不能凭 HTTP 429 给出 RPM/TPM 定论。

即便是 TPM，降低同一瞬间并发也不保证消除整个分钟窗口的速率限制。因此本次修法先恢复已有正式策略，不能宣称将所有请求限流彻底修复。若真实响应提供 retry-after，继续沿现有重试等待入口消费该信息，避免新增另一层重试调度。

## 一并发现的相关遗漏（单列，不混入并发结论）

受管 child 的 `run_agent_async` 调用未传 `model_route_override`。`transient_fallback_model_route` 在 SDK 中被校验，但旧 DAG 的实际压力计数/备用路由切换位于 `runtime._execute_agent_map_parallel_async`。新 managed 子调用的声明备用路由是否被正确采用需要单独验证；不能把父协调器的重试/路由切换冒称为子专家已经切换。

## 整合后的验证条件

- 先用真实 Run60 失败轨迹重放纯策略，显示每次降载的输入信号与期望有效并发；真实 replay 不计入恢复成功。
- 验证闸门降低后，已准入的付费兄弟正常收敛，排队中的项遵循新上限，输出仍逐输入索引一一对应。
- 预算拒绝仍停止新增请求，严格首错仍保持并发 1 与首次失败阻断。
- 控制头状态经真实存储往返后恢复，跨动作/父重试有效并发不重置；领域 state_hash、已有成功回执、使用量和来源不变。
- DAG 现有同类回归继续通过，共享策略前后使用同一真实故障/成功序列产生相同调整。
- 服务冷启动后用新 Run 做真实生成，单独报告 429 次数、并发调整、成功请求与未知请求；不把 CPU 重放当成在线改善结论。
