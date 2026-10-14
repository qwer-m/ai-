# 跨阶段取证与重规划的真实起点

来源为真实成功 Run 31；本次仅查询 MySQL，只读事务，无模型调用，无业务数据库写入。刷新后核对涉及的 16 个节点完整快照哈希未变。

## 推荐起点：无事实支撑的规划意图被剪掉，但目标仍残留

| 节点 | 真实记录 | 观察结果 |
| --- | --- | --- |
| plan_batches | 862，item 3 | 从审批字段展示提炼出“提交时各字段的采集与传递”主题 |
| prepare_plan_consolidation | 863，CG-0018 | 覆盖组保留该主题 |
| plan | 864，module_index 6 | 模块与测试点目标都包含字段采集传递 |
| prepare_plan_route_repairs | 867，item 0 | 该设计项没有原始事实路由 |
| plan_route_repairs | 868，item 0 | 专家判为 unsupported：候选事实支持身份记录、审批字段展示，未直接定义提交过程中的字段采集与传递 |
| routed_plan | 869，module_index 6 | coverage_item 被删除，模块与测试点目标未更新 |
| prepare_generation | 873，item 12 | 残留目标继续进入 coverage_focus 与 business_module.objective |

原模块目标为“记录提交渠道与提交人身份映射，采集传递续约申请的完整字段”。剪枝后该目标未变化，而唯一剩余覆盖项是“验证小程序提交记录为销售、OPS提交记录为运营的身份映射规则”。

原文第 7 页 EV-0021 的 DOC278-P0007-S447-E1020-F0011 描述“列表/详情展示”字段；DOC278-P0007-S447-E1020-F0004 描述提交人身份记录。二者不构成后台字段组装、传输协议的直接定义。合理的跨阶段动作应回读这些原文、调整受影响计划目标与设计，再重建路由；不能为了保留旧设计补造事实。

这一问题不意味着缺少原文。它证明旧流程单独剪掉覆盖项后，仍可能让上游错误意图通过其他字段进入生成阶段。

## 备用起点：真实文本与图片语义冲突

Run 31 authority 节点 871 / item 5：DOC278-P0006-12 说明 OPS 仅办理免费续约，DOC278-P0006-21 从图片提取出“提交免费/付费续约申请”按钮。旧治理将后者标为 uncertain，并移出 effective_facts。可以用它验证重新读取真实页面文本和图片，但不能预先断言图片读取错误或指定强制修正答案。

## 专项验证边界

默认 original 模式只使用 Run 31 原始来源和初始草案，按新正式节点输入映射构建，不注入历史下游缺口。不因本次诊断而强制工具顺序，也不植入错误候选。若将来单独增加 diagnostics 模式，必须披露额外使用了真实下游反馈，并使用不同输入哈希，不能将其描述为原始启动自然触发。

验证脚本为 [verify_planning_agent_real_model.py](../../backend/scripts/qa/verify/verify_planning_agent_real_model.py)，默认只准备，显式 `--execute` 才调用真实模型。来源 MySQL 保持只读；动作与预算仅写入新建隔离 SQLite。该专项运行不是新建正式 AgentRun，也不证明 HTTP/Celery 入口已经覆盖。

当前规格准备检查已通过：[准备报告](../../backend/artifacts/verification/planning_agent_run31_original_current_specs_prepared.json)。它读取 4 个文本来源项、6 个视觉来源项与 12 个初始规划草案，初始化 40 次父子共享请求预算，未请求模型。6 个真实专业 Agent 的当前提示和运行配置仅通过 `set_committed_value` 同步到只读会话对象；输出契约及真实模型路由保持一致，报告记录同步前后哈希，刷新后确认线上定义未变。原始输入 SHA256 为 `b4f2b4e39db08d9fda5a83371f83c9ccf5d5c38116faae14f3640f10a771c29e`。

完整节点哈希、实际缺口、专家原输出、原文片段和可复用完整输入保存在 [JSON 证据](2026-09-21-cross-stage-real-origin-evidence.json)。JSON 文件 SHA256：`e370dbd65991734a2c502067a640c32b99478b201cb52ccc6eb498709ad67817`。

需求文件为 document_id 278《门店续费功能产品 (1).pdf》，8 页，源文件 SHA256：`1edc1f74f899c51832d4c5d5141e3e1de3d6f9c3f8b664748c4dfe8984f3004f`。
