import { useRagDebugStore, selectTotalCaseCount } from './debugStore';

function resultSourceLabel(source?: string): string {
  if (source === 'streaming_preview') return '流式预览结果';
  if (source === 'final_persisted') return '最终持久化结果';
  return '-';
}

function boolLabel(value?: boolean): string {
  if (value === true) return '是';
  if (value === false) return '否';
  return '-';
}

function numberLabel(value: unknown): string {
  const n = Number(value);
  return Number.isFinite(n) ? String(n) : '-';
}

function percentLabel(value: unknown): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return '-';
  return `${Math.round(n * 1000) / 10}%`;
}

function stageStatus(ok: boolean): string {
  return ok ? '已流通' : '未收到';
}

function gateStatus(passed?: boolean): string {
  if (passed === true) return '已通过';
  if (passed === false) return '已阻断';
  return '未收到';
}

function qualityGateStatus(passed?: boolean, blocked?: boolean): string {
  if (passed === true) return '已通过';
  if (passed === false && blocked === true) return '未通过并阻断';
  if (passed === false) return '未通过（观察模式）';
  return '未收到';
}

function enumLabel(value: unknown, labels: Record<string, string>, emptyLabel = '-'): string {
  const key = String(value || '').trim();
  if (!key) return emptyLabel;
  return labels[key] || key;
}

function generationModeLabel(value?: string): string {
  return enumLabel(value, {
    multi_pass: '多轮生成',
    single_pass: '单轮生成',
    full_functional_regression: '全功能回归',
    standard_regression: '标准回归',
    expanded_regression: '扩展回归',
    main_smoke: '核心冒烟',
    append: '追加补齐',
    repair: '修复生成',
  });
}

function businessKeyLabel(value?: string): string {
  const key = String(value || '').trim();
  if (!key) return '-';
  if (key === 'unknown') return '未识别';
  return enumLabel(key, {
    global: '全局',
    current: '当前业务',
    default: '默认业务',
    org_close_rule: '机构关闭规则',
    org_open_rule: '机构开通规则',
    learning_flow: '学习流程',
    inventory_flow: '库存流程',
    lesson_flow: '课程流程',
    schedule_time: '排课/时间规则',
    learning_plan: '学习计划',
  });
}

function fusionModeLabel(value: unknown): string {
  return enumLabel(value, {
    rag: 'RAG',
    snapshot: '快照',
    'snapshot+rag': '快照+RAG',
    snapshot_rag: '快照+RAG',
    none: '未融合',
    unknown: '未识别',
  });
}

function sourceLabel(value: unknown): string {
  return enumLabel(value, {
    requirement_semantics: '需求语义',
    document_extracted: '文档抽取',
    sample_pool: '样本池',
    priority_sample_pool: '优先级样本池',
    pattern: '模式',
    final_case_learning: '终稿用例学习',
    fallback: '兜底',
    none: '无',
    unknown: '未识别',
  });
}

function patternGrainLabel(value: string): string {
  return enumLabel(value, {
    pattern: '模式',
    anti_pattern: '反模式',
    case: '用例',
    rule: '规则',
    source: '来源',
  });
}

function compactLabeledCounts(value: unknown, labeler: (value: string) => string): string {
  if (!value || typeof value !== 'object') return '-';
  const entries = Object.entries(value as Record<string, unknown>);
  if (!entries.length) return '-';
  return entries.slice(0, 4).map(([key, val]) => `${labeler(key)}:${String(val)}`).join(' / ');
}

export function GenerationOverview() {
  const generationMode = useRagDebugStore((s) => s.generationMode);
  const currentBizKey = useRagDebugStore((s) => s.currentBizKey);
  const totalCases = useRagDebugStore(selectTotalCaseCount);
  const resultState = useRagDebugStore((s) => s.resultState);
  const reviewDecisionSummary = useRagDebugStore((s) => s.reviewDecisionSummary);
  const generationConvergence = useRagDebugStore((s) => s.generationConvergence);
  const judgeSummary = useRagDebugStore((s) => s.judgeSummary);
  const generationSummary = useRagDebugStore((s) => s.generationSummary);
  const generationContextCompression = useRagDebugStore((s) => s.generationContextCompression);
  const feedbackControlState = useRagDebugStore((s) => s.feedbackControlState);
  const generationQualityLedger = useRagDebugStore((s) => s.generationQualityLedger);
  const reviewDecisionTableCompactRows = useRagDebugStore((s) => s.reviewDecisionTableCompactRows);
  const persistenceGate = useRagDebugStore((s) => s.persistenceGate);
  const caseQualityGate = useRagDebugStore((s) => s.caseQualityGate);

  const reviewCandidateCount = Number(
    reviewDecisionSummary?.candidate_total ?? generationConvergence?.candidate_count_before_review
  );
  const reviewSelectedCount = Number(generationConvergence?.review_selected_count ?? reviewDecisionSummary?.retained_total);
  const judgeRejectedOrPending = Number(judgeSummary?.rejected_out_count ?? judgeSummary?.reject_count)
    + Number(judgeSummary?.pending_out_count ?? judgeSummary?.pending_count);
  const ledgerJudge = generationQualityLedger?.judge && typeof generationQualityLedger.judge === 'object'
    ? generationQualityLedger.judge as Record<string, unknown>
    : {};
  const passCount = Number(judgeSummary?.confirmed_pass_out_count ?? judgeSummary?.pass_count);
  const repairableCount = Number(judgeSummary?.repairable_count ?? judgeSummary?.repaired_pass_out_count);
  const judgeInputFallback = (Number.isFinite(passCount) ? passCount : 0)
    + (Number.isFinite(repairableCount) ? repairableCount : 0)
    + (Number.isFinite(judgeRejectedOrPending) ? judgeRejectedOrPending : 0);
  const judgeInputCount = Number(ledgerJudge.total ?? judgeInputFallback);
  const finalCount = Number(generationSummary?.final_count ?? resultState?.displayCaseCount ?? 0);
  const sourceMeta = feedbackControlState?.source_meta || {};
  const ledgerCoverage = generationQualityLedger?.coverage || {};
  const ledgerContext = generationQualityLedger?.context || {};
  const ledgerControl = generationQualityLedger?.control || {};
  const executionPlanValidation = persistenceGate?.execution_plan_validation;
  const executionPlanMetrics = executionPlanValidation?.metrics || {};
  const persistenceGateFailureReasons = executionPlanValidation?.failure_reasons || [];
  const effectiveCaseQualityGate = caseQualityGate || generationQualityLedger?.case_quality_gate;
  const caseQualityMetrics = effectiveCaseQualityGate?.metrics || {};
  const caseQualityFailureReasons = effectiveCaseQualityGate?.failure_reasons || [];
  const judgeRejectClusters = ledgerJudge.reason_clusters && typeof ledgerJudge.reason_clusters === 'object'
    ? ledgerJudge.reason_clusters as Record<string, unknown>
    : {};

  return (
    <div className="rag-debug-card rounded-2xl shadow-md p-4 border bg-white dark:bg-slate-900">
      <h6 className="mb-3 fw-bold">生成概览</h6>
      <div className="tg-overview-main">
        <div className="tg-overview-metrics row g-3">
        <div className="col-md-6">
          <div className="small text-muted rag-debug-muted mb-1">模式</div>
          <div className="fw-semibold">{generationModeLabel(generationMode)}</div>
        </div>
        <div className="col-md-6">
          <div className="small text-muted rag-debug-muted mb-1">当前业务</div>
          <div className="fw-semibold">{businessKeyLabel(currentBizKey)}</div>
        </div>
        <div className="col-md-6">
          <div className="small text-muted rag-debug-muted mb-1">阶段候选数</div>
          <div className="fw-semibold">{totalCases}</div>
        </div>
        <div className="col-md-6">
          <div className="small text-muted rag-debug-muted mb-1">预览 / 最终</div>
          <div className="fw-semibold">{resultState?.previewCaseCount ?? '-'} / {resultState?.finalCaseCount ?? '-'}</div>
        </div>
        <div className="col-md-6">
          <div className="small text-muted rag-debug-muted mb-1">当前展示数</div>
          <div className="fw-semibold">{resultState?.displayCaseCount ?? '-'}</div>
        </div>
        <div className="col-md-6">
          <div className="small text-muted rag-debug-muted mb-1">结果来源</div>
          <div className="fw-semibold">{resultSourceLabel(resultState?.resultSource)}</div>
        </div>
        <div className="col-md-6">
          <div className="small text-muted rag-debug-muted mb-1">生成记录 ID</div>
          <div className="fw-semibold">{resultState?.generationId ?? '-'}</div>
        </div>
        <div className="col-md-6">
          <div className="small text-muted rag-debug-muted mb-1">最终结果已加载</div>
          <div className="fw-semibold">{boolLabel(resultState?.isFinalResultLoaded)}</div>
        </div>
        <div className="col-md-12">
          <div className="small text-muted rag-debug-muted mb-1">漏斗摘要（原始 → 复核候选 → 复核后 → 判定输入 → 判定拒绝/待定 → 最终）</div>
          <div className="fw-semibold">
            {resultState?.previewCaseCount ?? '-'} {' → '} {Number.isFinite(reviewCandidateCount) ? reviewCandidateCount : '-'} {' → '} {Number.isFinite(reviewSelectedCount) ? reviewSelectedCount : '-'} {' → '} {Number.isFinite(judgeInputCount) ? judgeInputCount : '-'} {' → '} {Number.isFinite(judgeRejectedOrPending) ? judgeRejectedOrPending : '-'} {' → '} {Number.isFinite(finalCount) ? finalCount : '-'}
          </div>
        </div>
        </div>
      </div>

      <div className="mt-4">
        <h6 className="mb-3 fw-bold">闭环治理阶段流通</h6>
        <div className="row g-3">
          <div className="col-md-3">
            <div className="p-3 border rounded-2 h-100">
              <div className="small text-muted rag-debug-muted mb-1">上下文 / RAG</div>
              <div className="fw-semibold">{stageStatus(!!generationContextCompression || !!generationQualityLedger)}</div>
              <div className="small text-muted rag-debug-muted mt-2">
                快照：{boolLabel(Boolean(ledgerContext.snapshot_used))}
                {' / '}
                融合模式：{fusionModeLabel(ledgerContext.fusion_mode)}
              </div>
              <div className="small text-muted rag-debug-muted">
                压缩率: {numberLabel(generationContextCompression?.compression_ratio ?? ledgerContext.compression_ratio)}
              </div>
            </div>
          </div>
          <div className="col-md-3">
            <div className="p-3 border rounded-2 h-100">
              <div className="small text-muted rag-debug-muted mb-1">样本池 / 模式回流</div>
              <div className="fw-semibold">{stageStatus(!!feedbackControlState)}</div>
              <div className="small text-muted rag-debug-muted mt-2">
                已应用：{boolLabel(feedbackControlState?.control_state_applied ?? Boolean(ledgerControl.control_state_applied))}
              </div>
              <div className="small text-muted rag-debug-muted">
                偏好/禁用模式：{numberLabel(feedbackControlState?.preferred_patterns_count)} / {numberLabel(feedbackControlState?.forbidden_patterns_count)}
              </div>
              <div className="small text-muted rag-debug-muted">
                低置信跳过：{numberLabel(sourceMeta.retrieval_low_confidence_sample_count)}
              </div>
              <div className="small text-muted rag-debug-muted">
                模式粒度：{compactLabeledCounts(sourceMeta.pattern_grain_distribution, patternGrainLabel)}
              </div>
              <div className="small text-muted rag-debug-muted">
                事实/项目画像：{sourceLabel(feedbackControlState?.fact_profile_source || reviewDecisionSummary?.fact_profile_source)} / {sourceLabel(feedbackControlState?.project_profile_source || reviewDecisionSummary?.project_profile_source)}
              </div>
            </div>
          </div>
          <div className="col-md-3">
            <div className="p-3 border rounded-2 h-100">
              <div className="small text-muted rag-debug-muted mb-1">复核 / 判定</div>
              <div className="fw-semibold">{stageStatus(!!reviewDecisionSummary || !!judgeSummary)}</div>
              <div className="small text-muted rag-debug-muted mt-2">
                复核：{numberLabel(reviewDecisionSummary?.candidate_total)} → {numberLabel(reviewDecisionSummary?.retained_total)}
              </div>
              <div className="small text-muted rag-debug-muted">
                压缩明细行：{reviewDecisionTableCompactRows?.length ?? 0}
              </div>
              <div className="small text-muted rag-debug-muted">
                流程缺失/顺序异常：{numberLabel(reviewDecisionSummary?.flow_missing_stage_count)} / {numberLabel(reviewDecisionSummary?.flow_misordered_count)}
              </div>
              <div className="small text-muted rag-debug-muted">
                最终流程缺失/顺序异常：{numberLabel(reviewDecisionSummary?.final_flow_missing_stage_count)} / {numberLabel(reviewDecisionSummary?.final_flow_misordered_count)}
              </div>
              <div className="small text-muted rag-debug-muted">
                场景重复簇：{numberLabel(reviewDecisionSummary?.scenario_duplicate_cluster_count)} 组
              </div>
              <div className="small text-muted rag-debug-muted">
                最终重复簇/用例：{numberLabel(reviewDecisionSummary?.final_scenario_duplicate_cluster_count)} / {numberLabel(reviewDecisionSummary?.final_scenario_duplicate_case_count)}
              </div>
              <div className="small text-muted rag-debug-muted">
                已裁剪/已重排：{numberLabel(reviewDecisionSummary?.scenario_duplicate_pruned_count)} / {reviewDecisionSummary?.flow_reordered ? '是' : '否'}
              </div>
              <div className="small text-muted rag-debug-muted">
                事实拒绝依据：{numberLabel(reviewDecisionSummary?.fact_profile_forbidden_count)} 禁用 / {numberLabel(reviewDecisionSummary?.fact_profile_pending_count)} 待确认
              </div>
              <div className="small text-muted rag-debug-muted">
                判定输入：{numberLabel(judgeInputCount)}
              </div>
              <div className="small text-muted rag-debug-muted">
                判定拒绝/待确认：{numberLabel(judgeRejectedOrPending)}
              </div>
            </div>
          </div>
          <div className="col-md-3">
            <div className="p-3 border rounded-2 h-100">
              <div className="small text-muted rag-debug-muted mb-1">质量账本</div>
              <div className="fw-semibold">{stageStatus(!!generationQualityLedger)}</div>
              <div className="small text-muted rag-debug-muted mt-2">
                最终数：{numberLabel(generationQualityLedger?.final_count ?? finalCount)}
              </div>
              <div className="small text-muted rag-debug-muted">
                初评得分：{numberLabel((generationQualityLedger as any)?.initial_quality_score ?? (generationQualityLedger as any)?.quality_score)}
              </div>
              <div className="small text-muted rag-debug-muted">
                覆盖率：{percentLabel(ledgerCoverage.coverage_rate)}
              </div>
              <div className="small text-muted rag-debug-muted">
                缺失/非阻断：{numberLabel(ledgerCoverage.missing_rules_count)} / {numberLabel(ledgerCoverage.non_blocking_rules_count)}
              </div>
            </div>
          </div>
          <div className="col-md-12">
            <div className={`p-3 border rounded-2 h-100 ${persistenceGate?.passed === false ? 'border-danger' : ''}`}>
              <div className="small text-muted rag-debug-muted mb-1">执行计划 / 落库门禁</div>
              <div className={persistenceGate?.passed === false ? 'fw-semibold text-danger' : 'fw-semibold'}>
                {gateStatus(persistenceGate?.passed)}
              </div>
              <div className="small text-muted rag-debug-muted mt-2">
                模式：{persistenceGate?.gate_mode || '-'}
                {' / '}
                失败码：{persistenceGate?.failure_code || '-'}
              </div>
              <div className="small text-muted rag-debug-muted">
                主链/P0：{numberLabel(executionPlanMetrics.main_smoke_count)} / {numberLabel(executionPlanMetrics.p0_count)}
                {' / '}
                状态字段覆盖率：{percentLabel(executionPlanMetrics.state_field_coverage)}
                {' / '}
                状态冲突：{numberLabel(executionPlanMetrics.state_conflict_count)}
              </div>
              <div className="small text-muted rag-debug-muted">
                线性可执行：{boolLabel(executionPlanMetrics.linear_executable as boolean | undefined)}
                {' / '}
                可信蓝图数：{numberLabel(executionPlanMetrics.trusted_workflow_contract_count)}
                {' / '}
                蓝图来源：{String(executionPlanMetrics.workflow_blueprint_source || '-')}
              </div>
              <div className="small text-muted rag-debug-muted">
                失败原因：{persistenceGateFailureReasons.length ? persistenceGateFailureReasons.join(', ') : '-'}
              </div>
            </div>
          </div>
          <div className="col-md-12">
            <div className={`p-3 border rounded-2 h-100 ${effectiveCaseQualityGate?.passed === false ? 'border-warning' : ''}`}>
              <div className="small text-muted rag-debug-muted mb-1">用例质量门禁</div>
              <div className={effectiveCaseQualityGate?.passed === false ? 'fw-semibold text-warning' : 'fw-semibold'}>
                {qualityGateStatus(effectiveCaseQualityGate?.passed, effectiveCaseQualityGate?.blocked)}
              </div>
              <div className="small text-muted rag-debug-muted mt-2">
                模式：{effectiveCaseQualityGate?.mode || '-'}
                {' / '}
                最终数/最低数：{numberLabel(caseQualityMetrics.final_count)} / {numberLabel(caseQualityMetrics.min_acceptable_final)}
                {' / '}
                评分：{numberLabel(caseQualityMetrics.quality_score)} ({String(caseQualityMetrics.quality_score_grade || '-')})
              </div>
              <div className="small text-muted rag-debug-muted">
                最终重复/乱序：{numberLabel(caseQualityMetrics.final_scenario_duplicate_case_count)} / {numberLabel(caseQualityMetrics.final_flow_misordered_count)}
                {' / '}
                Judge 拒绝：{numberLabel(caseQualityMetrics.judge_rejected_count)}
                {' / '}
                泄漏/角色错配：{numberLabel(caseQualityMetrics.reasoning_leak_count)} / {numberLabel(caseQualityMetrics.role_mismatch_count)}
              </div>
              <div className="small text-muted rag-debug-muted">
                拒绝聚类：{compactLabeledCounts(judgeRejectClusters, (value) => value)}
              </div>
              <div className="small text-muted rag-debug-muted">
                失败原因：{caseQualityFailureReasons.length ? caseQualityFailureReasons.join(', ') : '-'}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
