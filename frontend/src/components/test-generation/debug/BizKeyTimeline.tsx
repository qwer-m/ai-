import { useMemo } from 'react';
import { Badge } from 'react-bootstrap';
import { useRagDebugStore } from './debugStore';

const STAGE_META: Record<string, { label: string; className: string }> = {
  primary: { label: '主生成', className: 'bg-primary-subtle text-primary' },
  gap: { label: '差距分析', className: 'bg-warning-subtle text-warning' },
  review: { label: '复核回填', className: 'bg-success-subtle text-success' },
};

export function BizKeyTimeline() {
  const stages = useRagDebugStore((s) => s.stages);

  const grouped = useMemo(() => {
    const map = new Map<string, typeof stages>();
    for (const row of stages) {
      if (!map.has(row.bizKey)) map.set(row.bizKey, []);
      map.get(row.bizKey)!.push(row);
    }
    for (const rows of map.values()) {
      rows.sort((a, b) => {
        const order = ['primary', 'gap', 'review'];
        return order.indexOf(a.stage) - order.indexOf(b.stage);
      });
    }
    return Array.from(map.entries());
  }, [stages]);

  return (
    <div className="rag-debug-card rounded-2xl shadow-md p-4 border bg-white dark:bg-slate-900">
      <h6 className="mb-3 fw-bold">biz_key 执行过程</h6>
      {!grouped.length ? <div className="text-muted rag-debug-muted small">暂无执行事件</div> : null}
      <div className="d-grid gap-3">
        {grouped.map(([bizKey, rows]) => (
          <div key={bizKey} className="border rounded-3 p-3">
            <div className="fw-semibold mb-2">{bizKey}</div>
            <div className="d-flex flex-column gap-2">
              {rows.map((row) => {
                const meta = STAGE_META[row.stage] || STAGE_META.primary;
                return (
                  <div key={`${row.bizKey}-${row.stage}`} className="d-flex align-items-center justify-content-between">
                    <div className="d-flex align-items-center gap-2">
                      <span className="text-success">✓</span>
                      <Badge pill bg="light" text="dark" className={meta.className}>
                        {meta.label}
                      </Badge>
                    </div>
                    <div className="fw-semibold">{row.caseCount}</div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
