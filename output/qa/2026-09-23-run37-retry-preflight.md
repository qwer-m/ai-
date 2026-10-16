# Run37 正式重试身份校验

结论：当前代码不能通过正式 `retry_run` 恢复 Run37。只读实测得到 `run_version_mismatch`，没有调用重试、写库、迁移检查点、重置预算或发出模型请求。后续应创建新的正式验证 Run。

## 实际身份

- Run：37，状态 `failed`；失败节点：970 / `resolve_generation`。
- Run 持久签名：`2ac66ff93a032fd5508925642cde71e1939ecca279297d79304a95bbf270a40f`。
- 当前新进程签名：`5528260b1d7b79c55061c4d8e14c98d827dc53d035bfdb882f6c1007dfa6e975`。
- 采样时间：`2026-09-23T10:25:56.835257+00:00`。后续源码改动会再次改变当前签名。
- 比较沿用 `service.retry_run` 的实际前置谓词。未调用服务方法，以免产生新的 Run 或触发后台任务。

`runtime_registry_signature` 覆盖全体内置 Agent、工具、工作流、处理器注册和执行源码。authority 格式边界修复属于此身份范围。因此即使来源文档未变，也不能跨这个代码版本复用旧 Run 检查点。

## 检查点仍完整保留

真实纯函数 `_restorable_checkpoints` 识别 8 个候选：节点 963—969 共 7 个已成功节点，以及 node970 的失败自主行动检查点。这证明数据还在，不表示当前版本已获恢复许可；正式服务会先被版本谓词拒绝，根本不会开始复制这些候选。

在同版正常重试中，成功节点输出会复用，失败父节点会恢复原 sdk_state、状态、行动回执及共享预算。实际 SDK 还会校验父 Agent、托管子 Agent、模型路由、工具与源码的 `execution_semantic_hash`。已存在身份不一致时抛出 `ActionStateConflict`，不会把旧身份自动改成新身份。本次没有调用 journal 绑定，也没有绕过服务门禁去试跑父节点。

## 真实用量分开展示

| 证据位置 | 已报告请求 | 输入 token | 输出 token | 合计 token |
| --- | ---: | ---: | ---: | ---: |
| Run37 `run_context.usage`，与成功节点 usage 之和相同 | 22 | 94855 | 34485 | 129340 |
| node970 `action_checkpoint.budget.reported_usage`，未计入上述汇总 | 50 | 428490 | 18180 | 446670 |
| 两个不重叠证据相加 | 72 | 523345 | 52665 | 576010 |

父检查点 `action_usage_recorded` 未设置，成功节点之和与 Run 汇总精确相等，因此不能把 22 次称为本轮全部用量。父检查点 `pending_requests=0`、`completed_requests=50`、`interrupted_requests=0`。Run 汇总另有 `attempted_requests=24`，其口径不同，不用它替换 22 次已报告请求。

父共享额度仍为 160 次、2,000,000 输入 / 800,000 输出 / 2,800,000 合计 token，已消费 50 次、428,490 输入 / 18,180 输出。原预算与回执均保持原样。

## 失败边界与后续路径

11 个 authority 子调用均已有可信回执；父 `reconcile_generation_authority` 应用结果时抛出 `governed_by.directive_fact_id不能为空`。证据为事件 7831 的行动开始、7843 的行动失败和 7844 的节点失败；尚未进入生成和独立审查。不是生成语义通过，也没有证据将此失败归因于 504。

安全路径：完成真实 11 回执回放验证修复后，由根线程调用正式 `service.create_run`，用修复后的新鲜运行时执行新的验证 Run。保留 Run37 全部历史、用量、回执和已记录的 supported/reason 语义矛盾，不篡改签名，不移植旧父状态。新 Run 应明确记为新验证，不称为跨版本续跑，不宣称已验证生成质量。

JSON 同名文件保留实际身份、候选节点、失败事件、检查点与输出哈希、用量和代码定位。MySQL 事务显式 READ ONLY，结束时 ORM 的 dirty/new/deleted 均为空，随后 rollback；模型请求数为 0。
