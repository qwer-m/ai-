# 测试生成修复与真实运行验证

**最终结论：Run 27 已使用本 PDF 完成真实模型生成、确定性审计、终审修复与持久化，状态为 success，50 条用例已入库。** 详情接口和 Excel 导出均返回 HTTP 200，接口结果与持久化结果一致，导出的 50 条输入数据与用例一致。运行耗时 17 分 51 秒。

交付：[实际接口导出的测试用例 Excel](D:/Qoder/测试开发平台/output/qa/门店续费功能产品-Run27-测试用例.xlsx)、[结构化验收记录](D:/Qoder/测试开发平台/output/qa/run27-delivery-verification.json)、[完整节点及事件快照](D:/Qoder/测试开发平台/.cache/run27-completed-evidence.json)。

本报告记录上一轮审计之后的新增修复。使用用户提供的原始 PDF、数据库中的实际 Run 和原文件页面资产；未构造 mock 文档、模型结果或仓储数据。此前的重构遗漏及历史回放证据保留在[原审计报告](D:/Qoder/测试开发平台/output/qa/2026-09-14-agent-refactor-audit.md)，保留其当时的失败结论，并增加本报告入口。

## 实际来源与运行身份

| 项目 | 实际值 |
| --- | --- |
| 原文件 | [门店续费功能产品 (1).pdf](<C:/Users/Administrator/Downloads/门店续费功能产品 (1).pdf>) |
| 文档 SHA256 | `1edc1f74f899c51832d4c5d5141e3e1de3d6f9c3f8b664748c4dfe8984f3004f` |
| 项目 / 文档 / 用户 | 9 / 277 / 1 |
| 文档资产 | 8 页、19 个真实图片块；第 8 页只有 5 张界面截图，没有正文字符 |
| 生成输入 | 50 条用例、批次上限 5、开启上下文压缩、压缩预算 1,800 tokens |
| 最终验证运行 | Run 27，task `90d186ee-16ed-4b56-a847-b05e51917a73`，success |

| 运行 | 真实进展与停止原因 | 证据 |
| --- | --- | --- |
| Run 23 | 第 8 页没有正文锚点，确定性准备阶段失败，未请求模型 | [原阻塞快照](D:/Qoder/测试开发平台/.cache/run23-before-visual-generation.json) |
| Run 24 | 完成 4 个文本项和 6 个视觉页；第 8 页首个 JSON 错误经第二次请求成功。Windows 热更新中断服务后，通过业务服务取消；任务 `0f9bd8ad-5328-4128-8585-af5ee6ea3b8b` | [节点与事件快照](D:/Qoder/测试开发平台/.cache/run24-before-high-resolution.json)、[服务日志](D:/Qoder/测试开发平台/.cache/visual-generation-dev.stdout.log) |
| Run 25 | 高清输入改善了来源识别，来源、规划与原协调流程完成；但协调第 5 项收到“未达标免费”截图事实与“未达标付费”正文规则后仍提交空 decisions，两者均为 effective。发现后取消运行，修复治理规则 | [冲突修复前快照](D:/Qoder/测试开发平台/.cache/run25-before-source-conflict-fix.json) |
| Run 26 | 正文项完成 2/4 后，主机 Modern Standby 导致阶段预算耗尽；不是本轮代码超时参数错误 | [失败前检查点](D:/Qoder/测试开发平台/.cache/run26-before-standby-retry.json)、[主机电源事件](D:/Qoder/测试开发平台/.cache/run26-host-standby-events.json) |
| Run 27 | 正常 retry 创建时 parent_run_id=26，恢复 2/4 个正文检查点；后续来源、规划、20 个生成批次、审计与终审全部完成，persist 节点 774 成功，50 条用例落库 | [重试创建记录](D:/Qoder/测试开发平台/.cache/standby-retry-generation-start.json)、[完成快照](D:/Qoder/测试开发平台/.cache/run27-completed-evidence.json) |

Run 27 完成后触发既有历史清理，Run 26 被删除，因此当前 `parent_run_id` 为 null；重试关系以创建时保存的记录为准。

Windows 电源事件记录：2026-09-14 **12:40:27 进入、13:31:57 恢复**（北京时间，事件 506/507），约 51 分 30 秒的主机待机跨过了 Run 26 阶段截止时间。没有通过放宽阶段预算掩盖此次主机中断；恢复沿用平台业务重试和持久化检查点。

## 新增修复与原因

| 问题与影响 | 修复后的行为 | 代码位置 |
| --- | --- | --- |
| 证据目录只收录正文，纯图页被判断为没有有效来源 | 从真实 manifest 收录图片块，使用 `image_region`、真实块 ID、文档及页面 SHA；文本证据和图片证据分别保留自己的来源边界 | [证据目录](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_batching.py) |
| 压缩把空正文图片当作无效片段，恢复时也缺少图片范围 | 图片不进入正文噪音筛选，全部保留；压缩输入及恢复后的页面都核验允许引用的真实块和来源身份 | [上下文压缩](D:/Qoder/测试开发平台/backend/modules/agent_platform/context_compression.py)、[来源准备与恢复](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_semantics.py) |
| 来源契约要求所有事实都有字符坐标，图片无法通过 | 将正文锚点与图片区域建模为各自的合法结构；图片事实只能绑定唯一真实图片块，不能伪造 `quote` 或 `source_span` | [来源 Schema](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_schemas.py)、[锚点验证](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_semantics.py) |
| 图片事实误用正文治理值切片 | 图片事实的 `governed_value_spans` 必须为空，平台校验图片不能声明正文切片；固定规则和动态配置仍由来源语义判断 | [事实归一化](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_semantics.py) |
| 整页缩小后的界面细字发生误读 | 同一次页面模型请求内同时发送整页全景和标注真实块 ID 的高清局部图；局部图从原 PDF 按真实嵌图像素密度重渲染，保留遮罩和批注，不放大已丢失细节的页面 PNG | [原 PDF 高清局部图](D:/Qoder/测试开发平台/backend/modules/knowledge_base_components/document/document_asset_service.py)、[SDK 视觉输入](D:/Qoder/测试开发平台/backend/modules/agent_platform/sdk_adapter.py) |
| 原型中的实例状态被推广为通用要求，整张流程图被合成一条事实 | 提示模型区分单次实例和通用规则，保留可读前提、状态、数值关系与结果；流程图按独立条件分支拆分；图文冲突分别留存锚点，交既有跨页治理处理 | [内置来源 Agent 提示](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_builtin_specs.py) |
| 权威协调提示只允许显式“替代、修订”等措辞触发状态变化，真实图文矛盾因此被保留 | 比较相同条件下的行为和结果；规范性规则明确时，将混入的实例观察降为 reference_only 或 uncertain；优先权不明的冲突不得同时 effective。不改 assertion、不伪造 replaces，并保留禁止激活原非 effective 事实等约束 | [权威协调提示](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_builtin_specs.py) |
| 同页图片与正文被合并为一个 source_positions，只有图像冲突信号时可能不触发 review | 图片块 ID 纳入独立来源位置；包含图片事实的多来源模块进入协调，不必等待文本中的显式修订关键词 | [协调触发与来源位置](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_semantics.py) |
| 旧缓存可能遮蔽新证据与治理行为 | 文本缓存为 `source-text-semantics-v4-conditional-facts`，视觉缓存为 `source-vision-semantics-v3-region-conditional-facts`，权威协调缓存为 `authority-reconciliation-v2-source-conflicts`；全部禁用旧缓存兼容 | [缓存配置](D:/Qoder/测试开发平台/backend/modules/agent_platform/test_generation_builtin_specs.py) |
| Windows 下 API 热重载的控制台中断影响同组 Worker，正在执行的来源阶段未正常收尾 | 各开发服务使用独立隐藏控制台启动，通过管道汇总日志；停止时按保存的服务 PID 清理进程树，避免解释器子进程残留 | [开发服务启动](D:/Qoder/测试开发平台/backend/start_dev.py)、[进程启动与停止封装](D:/Qoder/测试开发平台/backend/start_dev_helpers.py) |

这些规则面向文档中的通用图文来源、条件分支和实例数据，没有针对本 PDF 的业务词语或某一种版式增加特调。

## 确定性真实数据验证

使用新增的[真实来源验证脚本](D:/Qoder/测试开发平台/backend/scripts/qa/verify/verify_visual_source_real_data.py)，从 Run 的实际工作流定义读取入参映射，经当前注册工具重放证据读取和来源准备。数据库事务设为 `READ ONLY`，不调用模型，不生成或覆盖业务记录。

从仓库根目录可复核：

```powershell
& 'D:/Qoder/测试开发平台/.venv/Scripts/python.exe' 'D:/Qoder/测试开发平台/backend/scripts/qa/verify/verify_visual_source_real_data.py' --run-id 27
```

| 检查 | 实际结果 |
| --- | --- |
| 证据目录 | 26 条：7 条正文证据 + 19 条图片证据 |
| 文本模型工作项 | 4 个，覆盖第 1 页与第 7 页的实际正文范围 |
| 视觉模型工作页 | 6 页：2、3、4、5、6、8 |
| manifest / 模型输入 / 恢复后图片块 | 均为 19 个，集合完全一致 |
| 正文锚点校验 | 162 次通过，引用和字符范围与真实页面一致 |
| 图片锚点校验 | 19 次通过，区域和双 SHA 与真实资产一致，无伪正文坐标 |
| 压缩来源恢复 | 7 个页面恢复记录；分段事实命名空间按实际来源范围区分 |
| 契约与幂等性 | 工具入参、出参、来源输入 Schema 均通过；再次归一化同一锚点结果不变 |
| 写库 / 模型调用 | 0 / 0 |

上述命令已在完成的 Run 27 上实际通过。上表中的“162 次正文锚点校验”是来源块验证次数，**不是模型生成的事实数或测试用例数**。初步证据目录结果另见[来源预检记录](D:/Qoder/测试开发平台/.cache/visual-source-preflight.json)。

## 真实来源语义检查

Run 24 输出 68 条文本事实、93 条视觉事实，其中第 8 页 7 条。20 条图片事实引用 18 个真实图片块，区域与双 SHA 均匹配；未引用的第 4 页小型批注头像不是遗漏的业务界面。第 8 页 `ToolArgumentsJSONError` 经第二次请求成功，因此 10 个完成工作单元不等于只有 10 次模型请求。

以下历史发现推动了高清输入、条件事实和权威协调修复；最终结果已按 Run 27 核对：

| 实际证据 | 历史发现 | 最终处理与验证 |
| --- | --- | --- |
| `DOC277-P0008-F002` | 把“当前第 2 页、共 3 页”列为固定有效事实，原图实际上高亮第 1 页 | 高清局部图帮助核对字形与状态；最终用例未将当前记录数、页码等截图实例推广为固定要求 |
| `DOC277-P0008-F001/F006/F005` | 界面字段与标题出现文字误读 | 最终用例正确保留“是否完成低消”；旧“经济”等误读未进入最终用例 |
| `DOC277-P0005-18/P0005-21` | 原型中的按钮与正文规则有冲突，且截图操作被省略适用条件后推广 | 保留条件与来源；最终 TC-017 的互斥前置条件经终审修复，TC-019 免费审批生效限定为到期达标，未达标分支仅描述金额展示及提交申请 |
| `DOC277-P0002-8` | 整张流程图被压成一条很长的事实 | 按可独立验证的条件分支分别生成事实，允许多条事实锚定同一真实图片块 |
| 第 8 页未单独提取拒绝原因的若干规则 | 仅看第 8 页会误判为规则缺失 | 第 7 页文本事实已包含“原因非必填、向提交人展示、允许重新提交”，不把单页缺失误报为全文遗漏 |

Run 25 已证明仅改善送图和来源提取提示还不够：权威协调收到相互矛盾的事实仍给出空补丁，因此本轮同时修复协调提示与触发链路。Run 27 已将缺少资格前提的实例观察降为参考信息。对最终 50 条用例逐项筛查“未达标”与“免费、免交、0 元、生效”等相关断言，未发现“未达标无需缴费即可续约生效”的明确结论，付费规则与最低缴费要求仍保留。截图上的“提交申请”与“续约生效”分别核对，未仅凭按钮名称认定规则冲突。

模型执行中仍出现过非法 JSON、错误事实 ID 及互斥前置条件等输出问题，分别由既有重试、确定性校验和终审修复流程处理。实际错误事实 ID 被校验拒绝，修复后才继续；没有放宽验证器或填入人工伪造结果。终审通过及覆盖率描述本次链路验收，不表示每条自然语言措辞都已穷尽人工业务评审。

## 原 PDF 高清视觉输入验证

6 个视觉页面在 SDK 输入装配中全部通过，19 个原 PDF 图片块均生成有效高清局部图：总计 6 张全景和 19 张局部图，按原有 6 个页面工作项发送，**未增加页面模型请求数量**。第 2、3、4、5、6、8 页分别携带 2、2、3、8、4、6 张图片；实际模型用量须以最终运行记录为准。

第 8 页 `P0008-I0002` 在原整页 PNG 中约为 242 × 237 像素；按原 PDF 重渲染后为 639 × 627 像素。验证检查真实图片块范围、原文件指纹及页面指纹，图像增强不修改来源锚点。

证据：[SDK 高清输入预检](D:/Qoder/测试开发平台/.cache/high-resolution-sdk-preflight.json)、[第 8 页 I0002 高清局部图](D:/Qoder/测试开发平台/.cache/document277-page8-I2-detail.png)。源 Agent 规格直接导入和修改文件的差异格式检查通过。

## Run 27 最终验收记录

以下结果来自完成后的数据库读取、真实业务 HTTP 接口和接口返回的 Excel 文件。

| 验收项 | Run 27 结果 | 持久化证据 |
| --- | --- | --- |
| Run 身份与失败恢复 | Run 27，task `90d186ee-16ed-4b56-a847-b05e51917a73`；从 Run 26 恢复 2/4 正文检查点 | [重试创建记录](D:/Qoder/测试开发平台/.cache/standby-retry-generation-start.json) |
| Worker 与开发服务 | 真实 API 热重载后健康检查恢复 200，launcher、Worker、Beat 保持运行；Run 27 完成。临时防待机请求已释放 | [控制台隔离验证](D:/Qoder/测试开发平台/.cache/start-dev-console-probe/result.json)、[服务日志](D:/Qoder/测试开发平台/.cache/high-resolution-dev.stdout.log) |
| 来源工作项与图片事实 | 4/4 正文项、6/6 视觉页完成；19 个图片块在证据、模型输入、恢复环节一致，第 8 页纯图页成功 | [完成快照](D:/Qoder/测试开发平台/.cache/run27-completed-evidence.json) |
| 字段准确性与实例状态 | “低消”识别正确，截图记录数、页码及个人样例未被固定成通用规则 | 完成快照中的来源事实及最终 50 条用例 |
| 条件分支与图文冲突 | TC-017 的互斥前置已修复；TC-019 区分未达标申请与达标免费生效 | 完成快照中的终审、approved_cases 769、持久化产物 |
| 规划与生成 | 20 个生成批次完成，产出目标 50 条用例 | 完成快照及真实结果契约回放 |
| 确定性审计 | 有效事实覆盖 212/212，设计项覆盖 145/145；最终修复后审计 765 通过 | 完成快照中的 audit_terminal_repaired |
| 终审 | `approved=true`，`differences=[]`；必要修复及复审已完成 | [最终审查及交付记录](D:/Qoder/测试开发平台/output/qa/run27-delivery-verification.json) |
| 数据库持久化 | persist 774 成功；50 条、`target_met=true`，与 approved_cases 完全一致 | 完成快照与交付记录 |
| 前端读取接口 | 已认证的 `GET /api/agents/runs/27` 返回 200；`test_generation_result` 与数据库产物完全一致 | 交付记录；前端直接消费该字段 |
| Excel 导出 | `GET /api/agents/runs/27/test-cases.xlsx` 返回 200；50 条数据加 1 行表头，输入值与落库用例逐项一致 | [实际导出文件](D:/Qoder/测试开发平台/output/qa/门店续费功能产品-Run27-测试用例.xlsx) |
| 最终状态与耗时 | success；北京时间 2026-09-14 13:37:01 至 13:54:52，17 分 51 秒 | 交付记录及完成快照 |
| 运行记录用量 | requests=130，attempted_requests=136；输入 866,452、输出 136,650、合计 1,003,102 tokens | 交付记录 usage；属于平台记录口径，含恢复的已完成来源请求，不等同供应商账单或全部尝试估算 |

运行 `verify_refactor_contracts.py --run-id 27` 通过：实际 50 条用例、20 个批次可由当前校验链读取；缺失事实仍被拒绝；输入不被校验改写；展示和导出输入一致；数据库未被回放修改。来源真实数据验证命令也在 Run 27 上通过。

此前已完成后端未定义名称检查、独立导入及调用契约审计、真实需求入口测试。全量 Pyright 仍存在既有 ORM/类型问题，本报告不声称全量类型检查通过。本次没有完成登录后的浏览器视觉验收；已验证的是前端使用的真实认证接口及其返回数据。开发服务保持运行，可直接打开项目 9 查看 Run 27。
