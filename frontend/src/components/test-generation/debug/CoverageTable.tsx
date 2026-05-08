import { useMemo, useState } from 'react';
import { Badge, Button, Form } from 'react-bootstrap';
import { useRagDebugStore } from './debugStore';

type Props = {
  onRuleClick?: (ruleId: string, ruleText?: string) => void;
  activeRuleId?: string | null;
};

function toArray(input: unknown): string[] {
  return Array.isArray(input) ? input.map((x) => String(x)) : [];
}

function countValue(input: unknown): number {
  if (Array.isArray(input)) return input.length;
  const n = Number(input);
  return Number.isFinite(n) ? n : 0;
}

export function CoverageTable({ onRuleClick, activeRuleId }: Props) {
  const coverage = useRagDebugStore((s) => s.coverage);
  const bizKeys = useRagDebugStore((s) => s.bizKeys);
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [bizFilter, setBizFilter] = useState('all');

  const diagnostics = useMemo(() => {
    const list = Array.isArray(coverage?.rule_diagnostics) ? coverage.rule_diagnostics : [];
    return list.filter((item) => {
      const covered = Boolean(item?.covered);
      const itemBiz = String((item as any)?.biz_key || 'global');
      if (onlyMissing && covered) return false;
      if (bizFilter !== 'all' && itemBiz !== bizFilter) return false;
      return true;
    });
  }, [coverage, onlyMissing, bizFilter]);

  return (
    <div className="rag-debug-card rounded-2xl shadow-md p-4 border bg-white dark:bg-slate-900">
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h6 className="mb-0 fw-bold">覆盖诊断</h6>
        <div className="d-flex align-items-center gap-2">
          <Form.Check
            type="switch"
            id="only-missing-rules"
            label="仅看未覆盖规则"
            checked={onlyMissing}
            onChange={(e) => setOnlyMissing(e.target.checked)}
          />
          <Form.Select size="sm" value={bizFilter} onChange={(e) => setBizFilter(e.target.value)} style={{ width: 170 }}>
            <option value="all">全部 biz_key</option>
            {bizKeys.map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </Form.Select>
        </div>
      </div>

      <div className="small text-muted rag-debug-muted mb-2">
        total_rules: {countValue(coverage?.total_rules)} / covered_rules: {countValue(coverage?.covered_rules)} / missing_rules: {countValue(coverage?.missing_rules)}
      </div>

      <div className="table-responsive">
        <table className="table table-sm align-middle mb-0">
          <thead>
            <tr>
              <th>Rule ID</th>
              <th>状态</th>
              <th>已覆盖</th>
              <th>缺失</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {!diagnostics.length ? (
              <tr>
                <td colSpan={5} className="text-center text-muted py-4">
                  暂无覆盖诊断数据
                </td>
              </tr>
            ) : null}
            {diagnostics.map((item, idx) => {
              const ruleId = String(item?.rule_id || `rule-${idx + 1}`);
              const ruleText = String((item as any)?.rule_text || '');
              const covered = Boolean(item?.covered);
              const coverageTypes = toArray((item as any)?.coverage_types);
              const missingTypes = toArray((item as any)?.missing_types);
              return (
                <tr key={`${ruleId}-${idx}`} className={activeRuleId && activeRuleId === ruleId ? 'table-warning' : undefined}>
                  <td className="fw-semibold">{ruleId}</td>
                  <td>
                    <Badge bg={covered ? 'success' : 'danger'}>{covered ? '已覆盖' : '未覆盖'}</Badge>
                  </td>
                  <td>
                    {!coverageTypes.length ? <span className="text-muted">-</span> : null}
                    <div className="d-flex flex-wrap gap-1">
                      {coverageTypes.map((tag) => (
                        <Badge key={`${ruleId}-cov-${tag}`} bg="success-subtle" text="success">
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  </td>
                  <td>
                    {!missingTypes.length ? <span className="text-muted">-</span> : null}
                    <div className="d-flex flex-wrap gap-1">
                      {missingTypes.map((tag) => (
                        <Badge key={`${ruleId}-miss-${tag}`} bg="danger-subtle" text="danger">
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  </td>
                  <td>
                    <Button variant="outline-secondary" size="sm" onClick={() => onRuleClick?.(ruleId, ruleText)} disabled={!onRuleClick}>
                      查看关联
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
