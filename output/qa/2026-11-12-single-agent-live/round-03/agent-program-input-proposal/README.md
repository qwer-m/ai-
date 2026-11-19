# 顶层 AgentProgram 输入边界修复建议

本目录为待审核补丁，不修改在途 Run52 的任何后台代码、冻结契约或检查点。

推荐 `runtime-program-input.patch`：依据已经冻结并已在执行入口验证的 workflow.definition.execution_mode 区分顶层 AgentProgram 与普通 DAG。只有顶层 Program 的领域输入改为 Run 原始业务 payload；运行和项目身份继续由 ToolExecutionContext 提供。DAG 内 agent_network 仍使用其原有映射和依赖规则。

## 当前证据与限制

`current-reference-proof.json` 使用真实 Run50/51 的同一业务 payload、真实 Node393 的检查点和纯输入投影。两份投影仅 run_id 不同，输入 hash 已不同；把新输入引用与旧检查点组合时，现有 compact_checkpoint 确定拒绝。这是输入引用组合证据，不是实际创建了父子重试 Run。当前数据库 parent_run_id 非空记录为 0，没有历史真实跨 Run 重试成功或失败可供回放。

确定的源码链路：service._restore_checkpoint_node 继承旧 input_ref 和检查点；runtime._prepare_node_execution 为失败的恢复节点重算 _node_input(新 Run)，写入新 input_ref；checkpoint_storage.compact_checkpoint 要求节点 input_ref 与检查点相等，因此当前会拒绝包含新 run_id 的投影。

不建议仅在有检查点时复用旧输入：虽然改动小，但会继续让新建 Program 的领域输入携带执行编号，并形成新建与恢复两种边界。推荐方案从新 Program 的首次创建就消除执行标识对业务输入 hash 的影响。

## 应用与隔离验证

1. 等当前真实 Run52 到达可停机边界后，再审核应用补丁；不要在运行中修改源码或冻结签名。
2. 把同目录 `test_agent_program_input_real_data.py` 复制到 `backend/tests/agent_platform/`，运行定向 pytest。生产数据库只读；读取真实 Run51 的任务、来源资产与真实 Run48 的 DAG 映射输入。
3. 测试覆盖同业务输入跨执行编号保持 hash、空 payload 保持 `{}`、DAG 工具映射和 DAG 内 agent_network 映射不变、当前执行身份仍来自 context。
4. 恢复测试只在独立 SQLite 内存库使用真实任务/来源快照与真实 Repository、Journal、输入存储和运行时准备函数。新协议的初始状态由真实文档资产确定性生成，不制造专业结果或人工答案；父子执行编号、租约和失败标志是明确的隔离控制元数据。检查 input_ref、完整 state_hash、journal.init 与来源观察均可恢复。
5. 运行当前 Agent 平台回归后，同步冷启动 API 与 Worker，再创建新版本真实任务验证。不能静默重写 Run51/52 旧输入 hash 或给旧状态补别名来冒充新协议恢复；旧在途状态应遵守既定的版本与生命周期边界。

尚未应用这个补丁，尚未运行其正向 SQLite 恢复测试；不能把建议测试内容写成已通过。
