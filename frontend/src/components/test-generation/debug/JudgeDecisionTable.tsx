import { useMemo, useState } from 'react';
import { Badge, Form } from 'react-bootstrap';
import { useRagDebugStore } from './debugStore';

type JudgeStatus = 'ALL' | 'PASS' | 'REPAIRABLE' | 'REJECT' | 'PENDING';

type JudgeRow = {
  caseId: string;
  module: string;
  priority: string;
  scene: string;
  judgeStatus: string;
  rejectReason: string;
  hitConfirmedFact: boolean;
  hitPending: boolean;
  hitReuseRisk: boolean;
};

const EMPTY_ROWS: Array<Record<string, unknown>> = [];

function toBool(value: unknown): boolean {
  return value === true;
}

function toText(value: unknown): string {
  return String(value ?? '').trim();
}

function extractRow(input: Record<string, unknown>): JudgeRow {
  const beforeCase = (input.before_case && typeof input.before_case === 'object'
    ? (input.before_case as Record<string, unknown>)
    : {}) as Record<string, unknown>;
  const afterCase = (input.after_case && typeof input.after_case === 'object'
    ? (input.after_case as Record<string, unknown>)
    : {}) as Record<string, unknown>;
  const chosenCase = Object.keys(beforeCase).length ? beforeCase : afterCase;

  const module = toText(chosenCase.test_module) || '-';
  const priority = toText(chosenCase.model_priority_current) || toText(chosenCase.priority) || '-';
  const scene = toText(chosenCase.description) || '-';
  const judgeStatus = toText(input.status) || '-';
  const rejectReason = toText(input.reject_reason) || toText(input.pending_reason) || '-';
  const hitConfirmedFact = toBool(input.violates_confirmed_fact)
    || (Array.isArray(input.confirmed_fact_hits) && input.confirmed_fact_hits.length > 0);
  const hitPending = toBool(input.contains_pending_logic)
    || (Array.isArray(input.pending_hits) && input.pending_hits.length > 0);
  const hitReuseRisk = Array.isArray(input.reuse_risk_hits) && input.reuse_risk_hits.length > 0;

  return {
    caseId: toText(input.case_id) || '-',
    module,
    priority,
    scene,
    judgeStatus,
    rejectReason,
    hitConfirmedFact,
    hitPending,
    hitReuseRisk,
  };
}

export function JudgeDecisionTable() {
  const rawRows = useRagDebugStore((s) => s.judgeDecisionTableRows) ?? EMPTY_ROWS;
  const [statusFilter, setStatusFilter] = useState<JudgeStatus>('ALL');

  const rows = useMemo(() => {
    const mapped = rawRows
      .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
      .map(extractRow);
    if (statusFilter === 'ALL') return mapped;
    return mapped.filter((row) => row.judgeStatus === statusFilter);
  }, [rawRows, statusFilter]);

  const summary = useMemo(() => {
    const all = rawRows
      .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
      .map(extractRow);
    const byStatus = {
      PASS: all.filter((x) => x.judgeStatus === 'PASS').length,
      REPAIRABLE: all.filter((x) => x.judgeStatus === 'REPAIRABLE').length,
      REJECT: all.filter((x) => x.judgeStatus === 'REJECT').length,
      PENDING: all.filter((x) => x.judgeStatus === 'PENDING').length,
    };
    return {
      total: all.length,
      ...byStatus,
    };
  }, [rawRows]);

  return (
    <div className="rag-debug-card rounded-2xl shadow-md p-4 border bg-white dark:bg-slate-900">
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h6 className="mb-0 fw-bold">Judge 决策明细</h6>
        <Form.Select
          size="sm"
          style={{ width: 160 }}
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as JudgeStatus)}
        >
          <option value="ALL">全部状态</option>
          <option value="REJECT">仅 REJECT</option>
          <option value="PENDING">仅 PENDING</option>
          <option value="REPAIRABLE">仅 REPAIRABLE</option>
          <option value="PASS">仅 PASS</option>
        </Form.Select>
      </div>

      <div className="small text-muted rag-debug-muted mb-2 d-flex flex-wrap gap-2">
        <Badge bg="secondary">total {summary.total}</Badge>
        <Badge bg="success">pass {summary.PASS}</Badge>
        <Badge bg="warning" text="dark">repairable {summary.REPAIRABLE}</Badge>
        <Badge bg="danger">reject {summary.REJECT}</Badge>
        <Badge bg="info">pending {summary.PENDING}</Badge>
      </div>

      <div className="table-responsive">
        <table className="table table-sm align-middle mb-0">
          <thead>
            <tr>
              <th>case_id</th>
              <th>module</th>
              <th>priority</th>
              <th>scene</th>
              <th>judge_status</th>
              <th>reject_reason</th>
              <th>hit_confirmed_fact</th>
              <th>hit_pending</th>
              <th>hit_reuse_risk</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={9} className="text-center text-muted py-4">
                  暂无 judge_decision_table 数据
                </td>
              </tr>
            ) : null}
            {rows.map((row, idx) => (
              <tr key={`${row.caseId}-${idx}`}>
                <td className="fw-semibold">{row.caseId}</td>
                <td>{row.module}</td>
                <td>{row.priority}</td>
                <td>{row.scene}</td>
                <td>{row.judgeStatus}</td>
                <td>{row.rejectReason}</td>
                <td>{row.hitConfirmedFact ? 'Y' : 'N'}</td>
                <td>{row.hitPending ? 'Y' : 'N'}</td>
                <td>{row.hitReuseRisk ? 'Y' : 'N'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
