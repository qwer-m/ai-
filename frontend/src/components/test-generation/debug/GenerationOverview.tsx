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

export function GenerationOverview() {
  const generationMode = useRagDebugStore((s) => s.generationMode);
  const currentBizKey = useRagDebugStore((s) => s.currentBizKey);
  const totalCases = useRagDebugStore(selectTotalCaseCount);
  const resultState = useRagDebugStore((s) => s.resultState);
  const reviewDecisionSummary = useRagDebugStore((s) => s.reviewDecisionSummary);
  const generationConvergence = useRagDebugStore((s) => s.generationConvergence);
  const judgeSummary = useRagDebugStore((s) => s.judgeSummary);
  const generationSummary = useRagDebugStore((s) => s.generationSummary);

  const reviewCandidateCount = Number(
    reviewDecisionSummary?.candidate_total ?? generationConvergence?.candidate_count_before_review
  );
  const reviewSelectedCount = Number(generationConvergence?.review_selected_count ?? reviewDecisionSummary?.retained_total);
  const judgeRejectedOrPending = Number(judgeSummary?.rejected_out_count ?? judgeSummary?.reject_count)
    + Number(judgeSummary?.pending_out_count ?? judgeSummary?.pending_count);
  const finalCount = Number(generationSummary?.final_count ?? resultState?.displayCaseCount ?? 0);

  return (
    <div className="rag-debug-card rounded-2xl shadow-md p-4 border bg-white dark:bg-slate-900">
      <h6 className="mb-3 fw-bold">生成概览</h6>
      <div className="row g-3">
        <div className="col-md-4">
          <div className="small text-muted rag-debug-muted mb-1">模式</div>
          <div className="fw-semibold">{generationMode || '-'}</div>
        </div>
        <div className="col-md-4">
          <div className="small text-muted rag-debug-muted mb-1">当前业务</div>
          <div className="fw-semibold">{currentBizKey || '-'}</div>
        </div>
        <div className="col-md-4">
          <div className="small text-muted rag-debug-muted mb-1">总用例数</div>
          <div className="fw-semibold">{totalCases}</div>
        </div>
        <div className="col-md-4">
          <div className="small text-muted rag-debug-muted mb-1">预览 / 最终</div>
          <div className="fw-semibold">{resultState?.previewCaseCount ?? '-'} / {resultState?.finalCaseCount ?? '-'}</div>
        </div>
        <div className="col-md-4">
          <div className="small text-muted rag-debug-muted mb-1">当前展示数</div>
          <div className="fw-semibold">{resultState?.displayCaseCount ?? '-'}</div>
        </div>
        <div className="col-md-4">
          <div className="small text-muted rag-debug-muted mb-1">结果来源</div>
          <div className="fw-semibold">{resultSourceLabel(resultState?.resultSource)}</div>
        </div>
        <div className="col-md-4">
          <div className="small text-muted rag-debug-muted mb-1">生成记录 ID</div>
          <div className="fw-semibold">{resultState?.generationId ?? '-'}</div>
        </div>
        <div className="col-md-4">
          <div className="small text-muted rag-debug-muted mb-1">最终结果已加载</div>
          <div className="fw-semibold">{boolLabel(resultState?.isFinalResultLoaded)}</div>
        </div>
        <div className="col-md-12">
          <div className="small text-muted rag-debug-muted mb-1">漏斗摘要 (raw → review → judge → final)</div>
          <div className="fw-semibold">
            {resultState?.previewCaseCount ?? '-'} {' → '} {Number.isFinite(reviewCandidateCount) ? reviewCandidateCount : '-'} {' → '} {Number.isFinite(reviewSelectedCount) ? reviewSelectedCount : '-'} {' → '} {Number.isFinite(judgeRejectedOrPending) ? judgeRejectedOrPending : '-'} {' → '} {Number.isFinite(finalCount) ? finalCount : '-'}
          </div>
        </div>
      </div>
    </div>
  );
}
