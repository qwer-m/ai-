import { useRagDebugStore, selectTotalCaseCount } from './debugStore';

export function GenerationOverview() {
  const generationMode = useRagDebugStore((s) => s.generationMode);
  const currentBizKey = useRagDebugStore((s) => s.currentBizKey);
  const totalCases = useRagDebugStore(selectTotalCaseCount);

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
      </div>
    </div>
  );
}
