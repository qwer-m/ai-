# 有效工作与未完成项审计

有效工程工作存在，但已证在线效果仅是局部输入缩小、批准/恢复/预算保留等。完整70条端到端成功仍不存在，不能用代码行数、418项回归或提前失败证明产品目标完成。

本次仅查当前代码和保存的真实证据；未运行模型、未写业务数据库。

| 已改工作 | 已证效果 | 证据级别 | 尚不能声称 |
| --- | --- | --- | --- |
| 拆除固定多层生成专家链，主Agent直接写用例，仅留一个独立评审 | 当前目录为2个生成Agent；Run64全部8请求、Run65全部8边界都只来自主coordinator，没有再走旧81次前置专家链。 | 源码+真实在线调用身份 | 两轮仍未到用例编写，因此不是同样70条交付的前后成本对照，也不能宣称已达SLO。 |
| 模型输入去掉重复初始状态、工具回执整目录、逐项技术hash及二次JSON字符串编码 | Run64首请求input10623，Run65首请求input6330；源码确认当前状态只在尾部一份、read_source不再返回整catalog。Run64实际重复catalog字节片段171490B已定位。 | 源码+真实单请求局部观察 | 两轮批准输入从3条变4条，并非控制实验；不能将197116→124640视为相同完整任务节省，因为Run65还有未知504用量且均无交付。 |
| 覆盖目标按ID增改，删除显式remove_coverage_ids；编号pattern公开并与领域共用 | 真实Run64单项修订造成19→1的历史参数，当前协议CPU重放为19→19，另外18项完整JSON不变；Run65未再发生编号格式拒绝，最终保存20目标。 | 真实失败参数CPU回归+有限在线不复现 | Run65没有人工暂停后的局部修订场景，故不能将其当作在线19→19成功或完整70条交付。 |
| 引用匹配区分东亚版面空白与真实文字变化 | Run65真实13处失败引用中12处仅版面空白，修后34测试接收该12处；AP15“学前自测→学前前测”改字继续拒；读范围、raw SHA、英文数字/韩文词界及硬边界保持。 | 真实失败引用CPU正负回归 | 修复发生在Run65终态后，尚未用新模型在线再次验证引用轮次减少。此前将全部引文失败泛称模型问题不准确。 |
| 真实批准冻结、同项目/来源边界、人工确认原位恢复 | Run65实际冻结AP13两条/AP15一条/AP16一条，无重复人审；Run64用户真实AP16答复正式API生效，同Run恢复，143.106107秒等待排除，原预算保留。 | 真实在线审批/恢复+CPU拒绝边界 | 这不是用户最终用例质量验收，也不是长期反馈采用闭环；RAG正文当前仍未接入独立评审引用链。 |
| 完整请求诊断和同Run预算恢复，单次HTTP/整会话期限拆分 | Run65保留8边界=7真实报告+1未知504；同Run Node420→421恢复后87011未知预留未退款。另修复180秒HTTP期限兼作整主会话期限，3项冻结配置回归通过。 | 真实在线账本/恢复+期限CPU回归 | 本次504在120.069秒由上游返回，不能归因于本地180秒期限；期限修复没有证明上游504消失或长会话在线完成。 |
| 真实图像传输、无损共享引用目录及只附必要图像块 | 真实doc287原图/PDF高清局部到SDK/Chat转换的像素和SHA CPU回归通过；同一旧70候选评审输入540267→186359字节，引用可逐字还原。 | 真实资产传输CPU+同数据无损容量实验 | Run64/65未调用图片或独立评审；该旧候选含图保守上界仍591395，超过30万，所以不能把65.5%字节下降当在线token达标。 |
| 拆分真实报告量、未知预留和下一请求投影；保留费用上限防失控 | Run64报告197116，下一投影305315；Run65报告124640+未知预留87011=已占用211651，下一投影310625。第9请求两轮均未发，历史Run未改。 | 真实在线额度账本+门禁 | 额度上界按UTF8字节和协议/图像保守预留，明显高于已测输入token；它可以提前拒绝。尚未证明30万真实token不足70条；也不能把门禁值当供应商实计费。 |

## 旧Run63的已对账消耗

| 分组 | 请求数 | 输入token | 输出token | 合计 |
| --- | ---: | ---: | ---: | ---: |
| 主协调器 | 41 | 743,987 | 1,633 | 745,620 |
| 生成含场景咨询 | 136 | 999,944 | 79,502 | 1,079,446 |
| 前置规划/治理/归属/支持/来源 | 81 | 1,032,468 | 56,624 | 1,089,092 |
| 审查/修复 | 25 | 271,859 | 5,903 | 277,762 |

四组累计283次、3,191,920 token。精确角色分组来自完整会话用量；早期逐请求时序只保留尾200，不能补造前83请求。

## 关于“模型过度思考”

Run64/65所有请求metadata reasoning_effort=low，reasoning_tokens均null。SDK reasoning item count为0不证明模型内部没思考。输入占比分别97.5%和93.8%，当前证据不支持将主要费用归于过度思考；504未知请求的内部生成/思考用量无法由现有数据分解。

## 仍未完成

- 没有完整70条在线交付，当前未验证进入生成后的吞吐/审查成本
- 当前独立评审仍整份来源+完整70候选，旧候选全图保守容量仍超30万
- 整份成功source读取会继续出现在每个后续主请求中；未解决失败草稿及反馈仍留历史，非所有重复均已消除
- 预算UTF8字节上界显著保守，不等于真实token；未知504预留必须保留但不能被叫实耗
- RAG检索正文与真实反馈未接入独立评审引用链；长期反馈闭环仍未完成
- 最近的版面匹配/会话期限修复尚无修后新在线整轮证据，上游504服务端原因未被证明解决

## 文件索引

- 拆除固定多层生成专家链，主Agent直接写用例，仅留一个独立评审：backend/modules/agent_platform/test_generation_builtin_specs.py:77；output/qa/2026-11-16-direct-agent-live/terminal-snapshot.json；output/qa/2026-11-16-direct-agent-rerun/terminal-snapshot.json
- 模型输入去掉重复初始状态、工具回执整目录、逐项技术hash及二次JSON字符串编码：backend/modules/agent_platform/sdk_adapter.py:2174；backend/modules/agent_platform/test_generation_agent.py:320；output/qa/2026-11-16-direct-agent-live/independent/context-analysis.md
- 覆盖目标按ID增改，删除显式remove_coverage_ids；编号pattern公开并与领域共用：backend/modules/agent_platform/test_generation_agent.py:408；backend/modules/agent_platform/test_generation_builtin_specs.py:23；output/qa/2026-11-16-direct-agent-live/coverage-incremental-tests.xml
- 引用匹配区分东亚版面空白与真实文字变化：backend/modules/agent_platform/test_generation_agent.py:60；output/qa/2026-11-16-direct-agent-rerun/quote-layout-fix-verification.json；output/qa/2026-11-16-direct-agent-rerun/independent/all-commit-reference-audit.json
- 真实批准冻结、同项目/来源边界、人工确认原位恢复：output/qa/2026-11-16-direct-agent-rerun/independent/initial-approved-scope.json；output/qa/2026-11-16-direct-agent-live/approval-response.json；docs/单主Agent架构与改造方案.md:106
- 完整请求诊断和同Run预算恢复，单次HTTP/整会话期限拆分：output/qa/2026-11-16-direct-agent-rerun/independent/timeout-and-resume-evidence.json；backend/modules/agent_platform/sdk_adapter.py:2328；backend/modules/agent_platform/runtime.py:3245；backend/tests/agent_platform/test_persistent_session_timeout_real_data.py
- 真实图像传输、无损共享引用目录及只附必要图像块：output/qa/2026-11-13-agent-cost-rerun/legacy-audit/frontend-projection/images-real-verification.json；output/qa/2026-11-13-agent-cost-rerun/direct-review-input-capacity.after.json；output/qa/2026-11-13-agent-cost-rerun/direct-review-reference-roundtrip.json
- 拆分真实报告量、未知预留和下一请求投影；保留费用上限防失控：output/qa/2026-11-16-direct-agent-live/independent/cost-profile.json；output/qa/2026-11-16-direct-agent-rerun/independent/cost-profile.json；backend/modules/agent_platform/autonomous_budget.py
