# AI测试开发（RAG + Multi-pass）面试精简版 v2

## 1. 一句话总括
我们把系统拆成两条闭环：检索闭环负责“找全、选准、喂好”，生成闭环负责“生成、评审、裁决、修复、落库”，并通过统一诊断事件做全链路观测。

## 2. 检索闭环（项目真实说法）
- 链路：`multi-lane recall -> rerank(base+bonus) -> diversity selection -> compression fidelity -> retry/gate`。
- Multi-lane：`original/rewrite + raw/summary + keyword_docs`，并支持 `biz_key` 关联扩召。
- Rerank：`base_score + bonus_score -> final_score`，bonus 来自关键词命中、长度、query/chunk 来源偏置。
- 选择：先做文档覆盖，再做“分数增益+信息增益”，限制单文档 chunk 上限，避免霸榜。
- 压缩：有 token budget，也有 fidelity 保真检测；保真不足时会触发轻回退/强回退，而不是盲目截断。
- 稳定性：低相关门控 + 有界重试（只重试可恢复错误）。

## 3. 生成闭环（项目真实说法）
- 链路：`multi-pass generation -> review decision -> judge/repair/training gate -> final persist`。
- Multi-pass 不是“多跑几次模型”，而是每轮都看新增有效用例、重复率、覆盖增益，再决定继续或停止。
- Early-stop 触发核心：新增价值下降、重复率高、覆盖已满足、信息增益不足。
- Judge 状态：`PASS / REPAIRABLE / REJECT / PENDING`。
- Repair + Gate：可修复项补齐后再分桶输出（confirmed pass / repaired pass / rejected / pending）。

## 4. 你可以直接背的高频回答

### Q1：你们的 RAG 不就是 Recall + Rerank 吗？
不是。我们在线上是五段：召回、重排、多样性选择、压缩保真、重试门控。这样质量、稳定性和成本更可控。

### Q2：为什么 Recall 要放宽？
漏召回不可恢复，噪声可在后续重排和选择阶段过滤。

### Q3：你们怎么避免单文档霸榜？
重排后不直接截断，会先保证多文档覆盖，再按分数+信息增益补齐，并限制每文档上限。

### Q4：Compression 怎么防止“压坏”？
我们做保真检测，观察约束保留率；触发告警会启用回退策略扩大预算或保留更多原文。

### Q5：Multi-pass 怎么停？
不是固定轮次。看边际收益：新增有效用例、重复率、覆盖满足度、信息增益；收益变低就停。

### Q6：Judge 在你们系统里算后处理吗？
不只是后处理。Judge 是评估与反馈核心，会驱动 repair 和最终分流，是闭环关键节点。

## 5. 观测与复盘（加分点）
- 我们把关键阶段统一打成 `GEN_DIAG` 事件。
- 前端直接看漏斗：`raw -> review -> judge -> final`。
- 复盘时重点看：召回 lane 命中、重排分数分布、压缩保真、judge 拒绝/待定原因、最终留存率。

## 6. 避免说错的三句话
- 不要说“fallback 就是重新 recall 一轮”。我们主要是降级检索、低相关软门控和压缩回退。
- 不要说“样本池只是离线资产”。我们有在线开关参与反馈控制。
- 不要把检索闭环和生成闭环混成一条线讲。
