import { useRagDebugStore, selectTotalCaseCount } from './debugStore';

type TokenUsageRow = {
  batch_index?: number;
  attempt?: number;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  token_source?: string;
};

const EMPTY_TOKEN_USAGE_ROWS: TokenUsageRow[] = [];

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

function compactJson(value: unknown): string {
  if (!value || typeof value !== 'object') return '-';
  const entries = Object.entries(value as Record<string, unknown>);
  if (!entries.length) return '-';
  return entries.slice(0, 4).map(([key, val]) => `${key}:${String(val)}`).join(' / ');
}

function stageStatus(ok: boolean): string {
  return ok ? '已流通' : '未收到';
}

function formatTokenCount(value: unknown): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return '-';
  if (n >= 10000) return `${Math.round(n / 100) / 10}万`;
  return n.toLocaleString('zh-CN');
}

function tokenSourceLabel(value: unknown): string {
  return String(value || '').trim().toLowerCase() === 'provider' ? '模型返回' : '估算';
}

function TokenUsagePanel({ rows }: { rows: TokenUsageRow[] }) {
  const visibleRows = rows.filter((row) => Number(row.batch_index || 0) > 0).slice(-12);
  const totals = visibleRows.reduce(
    (acc, row) => {
      acc.input += Number(row.input_tokens || 0);
      acc.output += Number(row.output_tokens || 0);
      acc.total += Number(row.total_tokens || 0);
      return acc;
    },
    { input: 0, output: 0, total: 0 }
  );
  const maxTotal = Math.max(1, ...visibleRows.map((row) => Number(row.total_tokens || 0)));
  const source = visibleRows.some((row) => tokenSourceLabel(row.token_source) === '模型返回') ? '模型返回' : '估算';

  return (
    <div className="tg-token-usage-panel border rounded-2 p-3">
      <div className="d-flex justify-content-between align-items-center gap-2 mb-2">
        <div className="fw-semibold small">每轮输入/产出</div>
        <span className="tg-token-source-pill">{source}</span>
      </div>
      <div className="tg-token-summary-grid mb-2">
        <div>
          <div className="small text-muted rag-debug-muted">输入</div>
          <div className="fw-semibold">{formatTokenCount(totals.input)}</div>
        </div>
        <div>
          <div className="small text-muted rag-debug-muted">产出</div>
          <div className="fw-semibold">{formatTokenCount(totals.output)}</div>
        </div>
        <div>
          <div className="small text-muted rag-debug-muted">合计</div>
          <div className="fw-semibold">{formatTokenCount(totals.total)}</div>
        </div>
      </div>
      {visibleRows.length ? (
        <div className="tg-token-bars">
          {visibleRows.map((row) => {
            const input = Number(row.input_tokens || 0);
            const output = Number(row.output_tokens || 0);
            const total = Math.max(1, Number(row.total_tokens || input + output || 0));
            const totalWidth = Math.max(6, Math.round((total / maxTotal) * 100));
            const inputWidth = Math.round((input / total) * 100);
            const outputWidth = Math.max(0, 100 - inputWidth);
            return (
              <div key={`${row.batch_index}-${row.attempt || 1}`} className="tg-token-row">
                <div className="tg-token-row-label">第{Number(row.batch_index)}轮</div>
                <div className="tg-token-track" title={`输入 ${formatTokenCount(input)}，产出 ${formatTokenCount(output)}`}>
                  <div className="tg-token-stack" style={{ width: `${totalWidth}%` }}>
                    <span className="tg-token-input" style={{ width: `${inputWidth}%` }} />
                    <span className="tg-token-output" style={{ width: `${outputWidth}%` }} />
                  </div>
                </div>
                <div className="tg-token-row-value">{formatTokenCount(total)}</div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="small text-muted rag-debug-muted">暂无批次 token 数据</div>
      )}
      <div className="tg-token-legend small text-muted rag-debug-muted mt-2">
        <span><i className="tg-token-dot tg-token-dot-input" />输入</span>
        <span><i className="tg-token-dot tg-token-dot-output" />产出</span>
      </div>
    </div>
  );
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
  const tokenUsageRows = (useRagDebugStore((s) => s.streamBatchTokenUsageRows) || EMPTY_TOKEN_USAGE_ROWS) as TokenUsageRow[];

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

  return (
    <div className="rag-debug-card rounded-2xl shadow-md p-4 border bg-white dark:bg-slate-900">
      <h6 className="mb-3 fw-bold">生成概览</h6>
      <div className="tg-overview-main">
        <div className="tg-overview-metrics row g-3">
        <div className="col-md-6">
          <div className="small text-muted rag-debug-muted mb-1">模式</div>
          <div className="fw-semibold">{generationMode || '-'}</div>
        </div>
        <div className="col-md-6">
          <div className="small text-muted rag-debug-muted mb-1">当前业务</div>
          <div className="fw-semibold">{currentBizKey || '-'}</div>
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
          <div className="small text-muted rag-debug-muted mb-1">漏斗摘要 (raw → review候选 → review后 → judge输入 → judge拒绝/待定 → final)</div>
          <div className="fw-semibold">
            {resultState?.previewCaseCount ?? '-'} {' → '} {Number.isFinite(reviewCandidateCount) ? reviewCandidateCount : '-'} {' → '} {Number.isFinite(reviewSelectedCount) ? reviewSelectedCount : '-'} {' → '} {Number.isFinite(judgeInputCount) ? judgeInputCount : '-'} {' → '} {Number.isFinite(judgeRejectedOrPending) ? judgeRejectedOrPending : '-'} {' → '} {Number.isFinite(finalCount) ? finalCount : '-'}
          </div>
        </div>
        </div>
        <TokenUsagePanel rows={tokenUsageRows} />
      </div>

      <div className="mt-4">
        <h6 className="mb-3 fw-bold">闭环治理阶段流通</h6>
        <div className="row g-3">
          <div className="col-md-3">
            <div className="p-3 border rounded-2 h-100">
              <div className="small text-muted rag-debug-muted mb-1">上下文 / RAG</div>
              <div className="fw-semibold">{stageStatus(!!generationContextCompression || !!generationQualityLedger)}</div>
              <div className="small text-muted rag-debug-muted mt-2">
                snapshot: {boolLabel(Boolean(ledgerContext.snapshot_used))}
                {' / '}
                fusion: {String(ledgerContext.fusion_mode || '-')}
              </div>
              <div className="small text-muted rag-debug-muted">
                压缩率: {numberLabel(generationContextCompression?.compression_ratio ?? ledgerContext.compression_ratio)}
              </div>
            </div>
          </div>
          <div className="col-md-3">
            <div className="p-3 border rounded-2 h-100">
              <div className="small text-muted rag-debug-muted mb-1">样本池 / Pattern 回流</div>
              <div className="fw-semibold">{stageStatus(!!feedbackControlState)}</div>
              <div className="small text-muted rag-debug-muted mt-2">
                applied: {boolLabel(feedbackControlState?.control_state_applied ?? Boolean(ledgerControl.control_state_applied))}
              </div>
              <div className="small text-muted rag-debug-muted">
                preferred/forbidden: {numberLabel(feedbackControlState?.preferred_patterns_count)} / {numberLabel(feedbackControlState?.forbidden_patterns_count)}
              </div>
              <div className="small text-muted rag-debug-muted">
                low-confidence skipped: {numberLabel(sourceMeta.retrieval_low_confidence_sample_count)}
              </div>
              <div className="small text-muted rag-debug-muted">
                grain: {compactJson(sourceMeta.pattern_grain_distribution)}
              </div>
            </div>
          </div>
          <div className="col-md-3">
            <div className="p-3 border rounded-2 h-100">
              <div className="small text-muted rag-debug-muted mb-1">Review / Judge</div>
              <div className="fw-semibold">{stageStatus(!!reviewDecisionSummary || !!judgeSummary)}</div>
              <div className="small text-muted rag-debug-muted mt-2">
                review: {numberLabel(reviewDecisionSummary?.candidate_total)} → {numberLabel(reviewDecisionSummary?.retained_total)}
              </div>
              <div className="small text-muted rag-debug-muted">
                compact rows: {reviewDecisionTableCompactRows?.length ?? 0}
              </div>
              <div className="small text-muted rag-debug-muted">
                judge input: {numberLabel(judgeInputCount)}
              </div>
              <div className="small text-muted rag-debug-muted">
                judge reject/pending: {numberLabel(judgeRejectedOrPending)}
              </div>
            </div>
          </div>
          <div className="col-md-3">
            <div className="p-3 border rounded-2 h-100">
              <div className="small text-muted rag-debug-muted mb-1">质量账本</div>
              <div className="fw-semibold">{stageStatus(!!generationQualityLedger)}</div>
              <div className="small text-muted rag-debug-muted mt-2">
                final: {numberLabel(generationQualityLedger?.final_count ?? finalCount)}
              </div>
              <div className="small text-muted rag-debug-muted">
                coverage: {percentLabel(ledgerCoverage.coverage_rate)}
              </div>
              <div className="small text-muted rag-debug-muted">
                missing/non-blocking: {numberLabel(ledgerCoverage.missing_rules_count)} / {numberLabel(ledgerCoverage.non_blocking_rules_count)}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
