# 《AI测试开发（RAG + Multi-pass）面试背诵版》项目对齐清单

更新时间：2026-04-22  
对齐对象：`C:\Users\Administrator\Downloads\AI测试开发_RAG_MultiPass_面试背诵版.docx`

## 1. 建议补充（结合当前项目实现）

1. 补充“Stage2.5 检索链路”而非只写三段式  
当前实现是 `Recall -> Rerank -> Diversity Selection -> Compression -> Retry/Gate`，不只是 `Recall/Rerank/Compression`。  
代码锚点：
- `backend/modules/knowledge_base_components/context/context_helpers.py:159`
- `backend/modules/knowledge_base_components/retrieval/retrieval_selection.py:122`
- `backend/modules/knowledge_base_components/context/context_helpers.py:150`

2. 补充 multi-lane 的真实配置项与 lane 观测字段  
当前不仅有 `original/rewrite + raw/summary`，还有 `keyword_docs` lane、`biz_key` 扩召，以及 `lane_counts/lane_reasons/lane_topk/fusion_weights`。  
代码锚点：
- `backend/modules/knowledge_base_components/retrieval/pipeline/recall_pipeline.py:118`
- `backend/modules/knowledge_base_components/retrieval/pipeline/recall_pipeline.py:131`
- `backend/modules/knowledge_base_components/retrieval/pipeline/recall_pipeline.py:217`
- `backend/modules/knowledge_base_components/retrieval/pipeline/recall_pipeline.py:293`

3. 补充 rerank 的“base + bonus -> final”具体加权来源  
目前 bonus 明确来自：长度微调、关键词重叠、`query_source=original` 加成、`chunk_source=summary` 加成。  
代码锚点：
- `backend/modules/knowledge_base_components/retrieval/reranker.py:66`
- `backend/modules/knowledge_base_components/retrieval/reranker.py:71`
- `backend/modules/knowledge_base_components/retrieval/reranker.py:72`

4. 补充“多文档覆盖与去冗余”机制  
当前并非只按分数截断，先做文档覆盖轮（`doc_coverage_round`），再做“分数增益 + 信息增益”轮（`score_info_gain_round`），并限制每文档 chunk 上限。  
代码锚点：
- `backend/modules/knowledge_base_components/retrieval/retrieval_selection.py:220`
- `backend/modules/knowledge_base_components/retrieval/retrieval_selection.py:268`
- `backend/modules/knowledge_base_components/retrieval/retrieval_selection.py:150`

5. 补充低相关门控与重试判定（不是泛化“fallback”）  
当前有 `low_relevance_score(top1/topk)` 判定、`title_keyword_relaxed` 放宽、可重试错误白名单和 attempt 记录。  
代码锚点：
- `backend/modules/knowledge_base_components/retrieval/retrieval_retry.py:68`
- `backend/modules/knowledge_base_components/retrieval/retrieval_retry.py:175`
- `backend/modules/knowledge_base_components/context/context_retrieval_executor.py:19`
- `backend/modules/knowledge_base_components/context/context_retrieval_executor.py:53`

6. 补充 compression fidelity（保真）与降级模式  
当前有 `retention_ratio`、`warning`、`fallback_light/fallback_raw`，并保留 `dropped_over_budget_chunks`。  
代码锚点：
- `backend/modules/knowledge_base_components/context/context_compressor.py:184`
- `backend/modules/knowledge_base_components/context/context_compressor.py:333`
- `backend/modules/knowledge_base_components/context/context_compressor.py:373`

7. 补充 retrieval profile 的可观测输出  
当前有 `query_type`、`recall_lane_hits`、`raw_topk_scores`、`rerank_top_scores`、`stability`，可直接用于面试“如何观测”。  
代码锚点：
- `backend/modules/knowledge_base_components/retrieval/retrieval_profile.py:144`
- `backend/modules/knowledge_base_components/retrieval/retrieval_profile.py:149`
- `backend/modules/knowledge_base_components/retrieval/retrieval_profile.py:179`

8. 补充“生成链路的 early-stop 真实条件”  
当前不是抽象描述，而是可落地规则：`consecutive_low_new_valid_cases`、`duplication_rate_gt_50pct`、`coverage_satisfied`、`no_information_gain`。  
代码锚点：
- `backend/modules/test_generation_components/legacy/multi_pass_pipeline.py:102`
- `backend/modules/test_generation_components/legacy/multi_pass_pipeline.py:105`
- `backend/modules/test_generation_components/legacy/multi_pass_pipeline.py:107`
- `backend/modules/test_generation_components/legacy/multi_pass_pipeline.py:109`

9. 补充 Judge 闭环的真实状态机与输出分流  
当前 Judge 不是一句“评估”，而是 `PASS/REPAIRABLE/REJECT/PENDING` + repair + training gate（confirmed/repaired/rejected/pending 分桶）。  
代码锚点：
- `backend/modules/test_generation_components/judge/judge_types.py:9`
- `backend/modules/test_generation_components/judge/test_case_judge.py:503`
- `backend/modules/test_generation_components/judge/training_gate.py:30`

10. 补充“样本池反馈是在线可开关能力”  
当前由 `enable_sample_pool_feedback` 控制，并在 `build_feedback_control_state` 中与 `priority_sample_pool + memory_fabric` 联动。  
代码锚点：
- `backend/routers/automation/test_generation_generate_routes_split_helpers.py:97`
- `backend/modules/test_generation_components/control/build_feedback_control_state.py:1379`
- `backend/modules/test_generation_components/control/build_feedback_control_state.py:1409`

11. 补充前端调试漏斗与事件协议（GEN_DIAG）  
当前有固定事件种类：`generation_convergence/review_decision_summary/judge_summary/judge_decision_table/generation_summary`，并在前端展示 `raw -> review -> judge -> final` 漏斗。  
代码锚点：
- `backend/modules/test_generation_components/legacy/stream/persist.py:419`
- `backend/modules/test_generation_components/legacy/stream/persist.py:437`
- `backend/modules/test_generation_components/legacy/stream/persist.py:472`
- `frontend/src/components/test-generation/debug/diagParser.ts:109`
- `frontend/src/components/test-generation/debug/GenerationOverview.tsx:70`

## 2. 建议删除或改写（避免与现实现偏差）

1. 改写“统一单一 pipeline：Recall -> Rerank -> Judge -> Refine”  
建议拆成两条链路：
- 检索链路：`Recall -> Rerank -> Diversity -> Compression -> Retry/Gate`
- 生成链路：`Multi-pass Generation -> Review Gate -> Judge/Repair -> Final`

2. 删除“fallback 通常会重新 recall 一轮”这种确定性表述  
项目里 fallback 主要是：where 降级、低相关 soft gate、compression fidelity 回退；并非默认重跑 recall。

3. 改写“样本池主要是离线资产”  
当前系统已把样本池接入在线生成反馈控制（可开关），离线与在线并存。

4. 精简与项目实现弱相关的通用面试话术  
例如“系统设计题是不是现场写代码”这类内容可移到附录，不放主线。主线应优先保留“指标、开关、事件、stop 条件、故障降级”。

## 3. 建议替换的一段总括（可直接放进原文）

> 我们在项目里把能力拆成两条闭环：  
> 检索闭环是 `multi-lane recall -> rerank(base+bonus) -> 多文档去冗余选择 -> compression fidelity -> retry/low-relevance gate`；  
> 生成闭环是 `multi-pass generation -> review decision -> judge/repair/training gate -> final persist`。  
> 全链路通过 `GEN_DIAG` 事件输出 `coverage、convergence、judge、generation_summary`，并在前端形成 `raw -> review -> judge -> final` 漏斗观测。

## 4. 本次已落地到项目

- 新增本对齐文档：`docs/AI测试开发_RAG_MultiPass_项目对齐清单.md`
- 建议你下一步把本文件第 1、2、3 节回写进原 `docx`（主文保留结论，附录保留术语解释）。
