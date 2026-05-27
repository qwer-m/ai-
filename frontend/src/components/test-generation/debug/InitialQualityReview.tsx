import { useMemo } from 'react';
import { Badge } from 'react-bootstrap';
import { buildRows, extractCaseArray } from './PriorityDebugTable.helpers';
import type { ResultSource } from './PriorityDebugTable.helpers';
import { coverageTypeLabel, judgeStatusLabel, qualityAssessmentLabel } from './debugLabels';
import { useRagDebugStore } from './debugStore';

type Props = {
  result?: any;
  resultSource?: ResultSource;
};

type IssueLevel = 'danger' | 'warning' | 'info' | 'success';

type QualityIssue = {
  title: string;
  count: number;
  level: IssueLevel;
  detail: string;
  examples?: string[];
};

type ScoreDeduction = {
  key: string;
  label: string;
  count: unknown;
  points: number;
};

const WEAK_EXPECTED_RESULT_PATTERNS = [
  '正常显示',
  '显示正常',
  '页面正常',
  '操作成功',
  '系统正常',
  '符合预期',
  '无异常',
];

function toFiniteNumber(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function toOptionalFiniteNumber(value: unknown): number | undefined {
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

function toText(value: unknown): string {
  return String(value ?? '').trim();
}

function percentLabel(value: number): string {
  if (!Number.isFinite(value)) return '-';
  return `${Math.round(value * 1000) / 10}%`;
}

function normalizeDeductions(value: unknown): ScoreDeduction[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
    .map((item) => ({
      key: String(item.key || '').trim(),
      label: String(item.label || item.key || '扣分项').trim(),
      count: item.count,
      points: toFiniteNumber(item.points),
    }))
    .filter((item) => item.points > 0)
    .slice(0, 8);
}

function confidenceLabel(value: string): string {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized === 'high') return '高';
  if (normalized === 'medium') return '中';
  if (normalized === 'low') return '低';
  return value;
}

function scoreSourceLabel(value: string): string {
  if (value === 'backend_diagnostic_v1') return '后端诊断评分 v1';
  return value;
}

function issueBadgeVariant(level: IssueLevel): string {
  if (level === 'danger') return 'danger';
  if (level === 'warning') return 'warning';
  if (level === 'success') return 'success';
  return 'info';
}

function scoreVerdict(score: number, riskCount: number): { text: string; variant: string } {
  if (score >= 85 && riskCount === 0) return { text: '可直接采用', variant: 'success' };
  if (score >= 70) return { text: '建议复核后采用', variant: 'warning' };
  return { text: '高风险，建议补生成或人工复核', variant: 'danger' };
}

function signalVerdict(dangerCount: number, warningCount: number): { text: string; variant: string } {
  if (dangerCount > 0) return { text: '高风险，建议人工复核', variant: 'danger' };
  if (warningCount > 0) return { text: '存在风险，建议复核', variant: 'warning' };
  return { text: '未发现明显风险', variant: 'success' };
}

function extractCaseId(item: any, index: number): string {
  return String(item?.id || item?.case_id || item?.caseId || `TC-${index + 1}`).trim();
}

function extractExpectedResult(item: any): string {
  return toText(item?.expected_result || item?.expectedResult || item?.expected || item?.assertion);
}

function extractDescription(item: any): string {
  return toText(item?.description || item?.title || item?.name || item?.test_module);
}

function buildCaseExamples(rows: any[], predicate: (item: any, index: number) => boolean, limit = 5): string[] {
  return rows
    .map((item, index) => ({ item, index }))
    .filter(({ item, index }) => predicate(item, index))
    .slice(0, limit)
    .map(({ item, index }) => `${extractCaseId(item, index)}：${extractDescription(item) || '-'}`);
}

export function InitialQualityReview({ result, resultSource = 'none' }: Props) {
  const coverage = useRagDebugStore((s) => s.coverage);
  const reviewDecisionSummary = useRagDebugStore((s) => s.reviewDecisionSummary);
  const judgeSummary = useRagDebugStore((s) => s.judgeSummary);
  const judgeDecisionRows = useRagDebugStore((s) => s.judgeDecisionTableRows) || [];
  const generationQualityLedger = useRagDebugStore((s) => s.generationQualityLedger);
  const resultState = useRagDebugStore((s) => s.resultState);

  const cases = useMemo(() => extractCaseArray(result), [result]);
  const priorityRows = useMemo(() => buildRows(result, resultSource), [result, resultSource]);

  const review = useMemo(() => {
    const missingRules = Array.isArray(coverage?.missing_rules)
      ? coverage?.missing_rules.length
      : toFiniteNumber(coverage?.missing_rules ?? generationQualityLedger?.coverage?.missing_rules_count);
    const totalRules = Array.isArray(coverage?.rule_diagnostics)
      ? coverage?.rule_diagnostics.length
      : toFiniteNumber(coverage?.total_rules);
    const coveredRules = totalRules > 0 ? Math.max(0, totalRules - missingRules) : toFiniteNumber(coverage?.covered_rules);
    const coverageRate = totalRules > 0 ? coveredRules / totalRules : Number(generationQualityLedger?.coverage?.coverage_rate ?? 0);
    const duplicateClusters = toFiniteNumber(reviewDecisionSummary?.scenario_duplicate_cluster_count);
    const duplicateCases = toFiniteNumber(reviewDecisionSummary?.scenario_duplicate_case_count);
    const flowMissing = toFiniteNumber(reviewDecisionSummary?.flow_missing_stage_count);
    const flowMisordered = toFiniteNumber(reviewDecisionSummary?.flow_misordered_count);
    const rejectCount = toFiniteNumber(judgeSummary?.reject_count ?? judgeSummary?.rejected_out_count);
    const pendingCount = toFiniteNumber(judgeSummary?.pending_count ?? judgeSummary?.pending_out_count);
    const repairableCount = toFiniteNumber(judgeSummary?.repairable_count ?? judgeSummary?.repaired_pass_out_count);
    const priorityMismatch = priorityRows.filter((row) => row.displayFinalMismatch || row.rawFinalMismatch).length;
    const p0Count = priorityRows.filter((row) => row.finalPriority === 'P0' || row.displayPriority === 'P0').length;
    const weakExpectedRows = cases.filter((item) => {
      const expected = extractExpectedResult(item);
      if (!expected) return true;
      return WEAK_EXPECTED_RESULT_PATTERNS.some((pattern) => expected.includes(pattern));
    });
    const judgeDuplicate = judgeDecisionRows.filter((row) => Boolean((row as any).is_semantic_duplicate)).length;
    const confirmedFactHits = judgeDecisionRows.filter((row) => Boolean((row as any).violates_confirmed_fact)).length;
    const pendingHits = judgeDecisionRows.filter((row) => Boolean((row as any).contains_pending_logic)).length;
    const vagueHits = judgeDecisionRows.filter((row) => Array.isArray((row as any).vague_or_unconfirmed_hits) && (row as any).vague_or_unconfirmed_hits.length > 0).length;
    const reuseRiskHits = judgeDecisionRows.filter((row) => Array.isArray((row as any).reuse_risk_hits) && (row as any).reuse_risk_hits.length > 0).length;

    const issues: QualityIssue[] = [];
    if (missingRules > 0 || flowMissing > 0 || flowMisordered > 0) {
      issues.push({
        title: '主流程/规则覆盖不足',
        count: missingRules + flowMissing + flowMisordered,
        level: missingRules + flowMissing > 0 ? 'danger' : 'warning',
        detail: `未覆盖规则 ${missingRules} 个，流程缺失 ${flowMissing} 个，顺序异常 ${flowMisordered} 个。`,
        examples: (coverage?.rule_diagnostics || [])
          .filter((item) => !item?.covered)
          .slice(0, 5)
          .map((item) => `${item.rule_id || '-'}：${item.rule_text || '缺少可追溯规则文本'}`),
      });
    }
    if (duplicateClusters > 0 || duplicateCases > 0 || judgeDuplicate > 0) {
      issues.push({
        title: '重复意图偏多',
        count: Math.max(duplicateCases, judgeDuplicate, duplicateClusters),
        level: 'warning',
        detail: `复核阶段发现重复簇 ${duplicateClusters} 组、重复用例 ${duplicateCases} 条；判定明细命中重复 ${judgeDuplicate} 条。`,
      });
    }
    if (weakExpectedRows.length > 0) {
      issues.push({
        title: '预期结果断言偏弱',
        count: weakExpectedRows.length,
        level: 'warning',
        detail: '部分预期结果为空或只描述“正常/成功”，缺少可断言的页面、数据或状态变化。',
        examples: buildCaseExamples(cases, (item) => weakExpectedRows.includes(item)),
      });
    }
    if (confirmedFactHits > 0 || pendingHits > 0 || vagueHits > 0 || reuseRiskHits > 0) {
      issues.push({
        title: '需求外假设/事实风险',
        count: confirmedFactHits + pendingHits + vagueHits + reuseRiskHits,
        level: confirmedFactHits > 0 ? 'danger' : 'warning',
        detail: `命中已确认事实冲突 ${confirmedFactHits} 条、待确认逻辑 ${pendingHits} 条、模糊依据 ${vagueHits} 条、复用风险 ${reuseRiskHits} 条。`,
      });
    }
    if (rejectCount > 0 || pendingCount > 0 || repairableCount > 0) {
      issues.push({
        title: '判定拒绝/待修复',
        count: rejectCount + pendingCount + repairableCount,
        level: rejectCount > 0 ? 'danger' : 'warning',
        detail: `拒绝 ${rejectCount} 条，待确认 ${pendingCount} 条，可修复 ${repairableCount} 条。`,
      });
    }
    if (priorityMismatch > 0) {
      issues.push({
        title: '优先级展示或判定不一致',
        count: priorityMismatch,
        level: 'info',
        detail: '原始优先级、调试优先级或最终展示优先级存在不一致，需要确认是否为预期纠偏。',
      });
    }
    if (!p0Count && cases.length > 0) {
      issues.push({
        title: 'P0 主链锚点不足',
        count: 1,
        level: 'danger',
        detail: '当前结果中未识别到 P0 用例，主链闭环可能没有被稳定保留。',
      });
    }

    const riskCount = issues.filter((issue) => issue.level === 'danger' || issue.level === 'warning').length;
    const dangerCount = issues.filter((issue) => issue.level === 'danger').length;
    const warningCount = issues.filter((issue) => issue.level === 'warning').length;
    const explicitScore = toOptionalFiniteNumber(
      (generationQualityLedger as any)?.initial_quality_score
      ?? (generationQualityLedger as any)?.quality_score
      ?? (generationQualityLedger as any)?.quality?.score
    );
    const scoreDeductions = normalizeDeductions((generationQualityLedger as any)?.quality_score_deductions);
    const scoreSource = String((generationQualityLedger as any)?.quality_score_source || '').trim();
    const scoreConfidence = String((generationQualityLedger as any)?.quality_score_confidence || '').trim();

    return {
      score: explicitScore,
      scoreText: explicitScore === undefined ? '未接入' : String(explicitScore),
      scoreNote: explicitScore === undefined
        ? '当前只展示规则信号，不生成前端假分数'
        : `后端诊断评分${scoreConfidence ? `，置信度 ${confidenceLabel(scoreConfidence)}` : ''}`,
      scoreSource,
      scoreDeductions,
      riskCount,
      verdict: explicitScore === undefined ? signalVerdict(dangerCount, warningCount) : scoreVerdict(explicitScore, riskCount),
      caseCount: cases.length || resultState?.displayCaseCount || generationQualityLedger?.final_count || 0,
      coverageRate,
      coverageEvidence: totalRules > 0 ? `规则命中 ${coveredRules}/${totalRules}` : '暂无规则诊断',
      p0Count,
      issueCount: riskCount,
      signalCount: issues.reduce((sum, issue) => sum + issue.count, 0),
      issues,
    };
  }, [coverage, generationQualityLedger, reviewDecisionSummary, judgeSummary, judgeDecisionRows, priorityRows, cases, resultState]);

  const hasAnySignal = Boolean(
    review.caseCount
    || coverage
    || reviewDecisionSummary
    || judgeSummary
    || judgeDecisionRows.length
    || generationQualityLedger
  );

  return (
    <div className="rag-debug-card rounded-2xl shadow-md p-4 border bg-white dark:bg-slate-900">
      <div className="d-flex flex-wrap justify-content-between align-items-start gap-3 mb-3">
        <div>
          <h6 className="mb-1 fw-bold">质量初评</h6>
          <div className="small text-muted rag-debug-muted">
            基于当前批次的覆盖、复核、判定、优先级和用例文本信号聚合；后续可接入模型初评结果。
          </div>
        </div>
        <Badge bg={review.verdict.variant}>{review.verdict.text}</Badge>
      </div>

      {!hasAnySignal ? (
        <div className="text-muted rag-debug-muted small">暂无可用于初评的生成诊断数据</div>
      ) : (
        <>
          <div className="tg-initial-quality-grid mb-3">
            <div className="tg-initial-quality-metric">
              <div className="small text-muted rag-debug-muted">初评得分</div>
              <div className="fw-bold fs-4">{review.scoreText}</div>
              <div className="small text-muted rag-debug-muted">{review.scoreNote}</div>
            </div>
            <div className="tg-initial-quality-metric">
              <div className="small text-muted rag-debug-muted">可见用例</div>
              <div className="fw-bold fs-4">{review.caseCount}</div>
            </div>
            <div className="tg-initial-quality-metric">
              <div className="small text-muted rag-debug-muted">规则覆盖率</div>
              <div className="fw-bold fs-4">{percentLabel(review.coverageRate)}</div>
              <div className="small text-muted rag-debug-muted">{review.coverageEvidence}</div>
            </div>
            <div className="tg-initial-quality-metric">
              <div className="small text-muted rag-debug-muted">P0 数量</div>
              <div className="fw-bold fs-4">{review.p0Count}</div>
            </div>
            <div className="tg-initial-quality-metric">
              <div className="small text-muted rag-debug-muted">风险类型</div>
              <div className="fw-bold fs-4">{review.issueCount}</div>
              <div className="small text-muted rag-debug-muted">命中信号 {review.signalCount}</div>
            </div>
          </div>

          {review.scoreDeductions.length ? (
            <div className="tg-initial-quality-issue border rounded-3 p-3 mb-3">
              <div className="fw-semibold mb-2">评分扣分依据</div>
              <div className="d-flex flex-wrap gap-2">
                {review.scoreDeductions.map((item) => (
                  <Badge key={`${item.key}-${item.points}`} bg="light" text="dark">
                    {item.label} -{item.points}
                  </Badge>
                ))}
              </div>
              {review.scoreSource ? (
                <div className="small text-muted rag-debug-muted mt-2">评分来源：{scoreSourceLabel(review.scoreSource)}</div>
              ) : null}
            </div>
          ) : null}

          <div className="d-grid gap-3">
            {review.issues.length ? review.issues.map((issue) => (
              <div key={issue.title} className="tg-initial-quality-issue border rounded-3 p-3">
                <div className="d-flex flex-wrap align-items-center gap-2 mb-2">
                  <Badge bg={issueBadgeVariant(issue.level)} text={issue.level === 'warning' ? 'dark' : undefined}>
                    {issue.count}
                  </Badge>
                  <span className="fw-semibold">{issue.title}</span>
                </div>
                <div className="small text-muted rag-debug-muted">{issue.detail}</div>
                {issue.examples?.length ? (
                  <ul className="small mb-0 mt-2 tg-initial-quality-examples">
                    {issue.examples.map((example) => <li key={example}>{example}</li>)}
                  </ul>
                ) : null}
              </div>
            )) : (
              <div className="tg-initial-quality-issue border rounded-3 p-3">
                <div className="d-flex align-items-center gap-2 mb-2">
                  <Badge bg="success">0</Badge>
                  <span className="fw-semibold">未发现明显质量风险</span>
                </div>
                <div className="small text-muted rag-debug-muted">当前诊断信号未命中覆盖缺失、重复、弱断言、事实风险或优先级异常。</div>
              </div>
            )}
          </div>

          {Array.isArray(coverage?.rule_diagnostics) && coverage.rule_diagnostics.length ? (
            <div className="small text-muted rag-debug-muted mt-3">
              覆盖类型说明：
              {' '}
              {Array.from(new Set(coverage.rule_diagnostics.flatMap((item) => [...(item.coverage_types || []), ...(item.missing_types || [])])))
                .slice(0, 8)
                .map((item) => coverageTypeLabel(item))
                .join('，')}
            </div>
          ) : null}
          {generationQualityLedger?.quality_assessment ? (
            <div className="small text-muted rag-debug-muted mt-2">
              质量账本结论：{qualityAssessmentLabel(generationQualityLedger.quality_assessment)}
            </div>
          ) : null}
          {judgeDecisionRows.length ? (
            <div className="small text-muted rag-debug-muted mt-2">
              判定状态：{['PASS', 'REPAIRABLE', 'REJECT', 'PENDING'].map(judgeStatusLabel).join(' / ')}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
