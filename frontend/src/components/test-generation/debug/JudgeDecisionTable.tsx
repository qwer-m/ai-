import { useMemo, useState } from 'react';
import { Badge, Form } from 'react-bootstrap';
import { judgeStatusLabel } from './debugLabels';
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
  hitVague: boolean;
  hitReuseRisk: boolean;
  hitDuplicate: boolean;
  duplicateOf: string;
};

const EMPTY_ROWS: Array<Record<string, unknown>> = [];

function toBool(value: unknown): boolean {
  return value === true;
}

function toText(value: unknown): string {
  return String(value ?? '').trim();
}

function toFiniteNumber(value: unknown): number | undefined {
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

function extractRow(input: Record<string, unknown>): JudgeRow {
  const beforeCase = (input.before_case_snapshot && typeof input.before_case_snapshot === 'object'
    ? (input.before_case_snapshot as Record<string, unknown>)
    : input.before_case && typeof input.before_case === 'object'
    ? (input.before_case as Record<string, unknown>)
    : {}) as Record<string, unknown>;
  const afterCase = (input.after_case_snapshot && typeof input.after_case_snapshot === 'object'
    ? (input.after_case_snapshot as Record<string, unknown>)
    : input.after_case && typeof input.after_case === 'object'
    ? (input.after_case as Record<string, unknown>)
    : {}) as Record<string, unknown>;
  const chosenCase = Object.keys(beforeCase).length ? beforeCase : afterCase;

  const module = toText(chosenCase.test_module) || '-';
  const priority = toText(chosenCase.model_priority_current) || toText(chosenCase.priority) || '-';
  const scene = toText(chosenCase.description) || '-';
  const judgeStatus = toText(input.judge_status) || toText(input.status) || '-';
  const rejectReason = toText(input.reject_reason) || toText(input.pending_reason) || '-';
  const hitConfirmedFact = toBool(input.violates_confirmed_fact)
    || (Array.isArray(input.confirmed_fact_hits) && input.confirmed_fact_hits.length > 0);
  const hitPending = toBool(input.contains_pending_logic)
    || (Array.isArray(input.pending_hits) && input.pending_hits.length > 0);
  const hitVague = Array.isArray(input.vague_or_unconfirmed_hits)
    && input.vague_or_unconfirmed_hits.length > 0;
  const hitReuseRisk = Array.isArray(input.reuse_risk_hits) && input.reuse_risk_hits.length > 0;
  const hitDuplicate = toBool(input.is_semantic_duplicate);
  const duplicateOf = toText(input.duplicate_of_case_id) || '-';

  return {
    caseId: toText(input.case_id) || '-',
    module,
    priority,
    scene,
    judgeStatus,
    rejectReason,
    hitConfirmedFact,
    hitPending,
    hitVague,
    hitReuseRisk,
    hitDuplicate,
    duplicateOf,
  };
}

export function JudgeDecisionTable() {
  const rawRows = useRagDebugStore((s) => s.judgeDecisionTableRows) ?? EMPTY_ROWS;
  const meta = useRagDebugStore((s) => s.judgeDecisionTableMeta);
  const judgeSummary = useRagDebugStore((s) => s.judgeSummary);
  const [statusFilter, setStatusFilter] = useState<JudgeStatus>('ALL');

  const rows = useMemo(() => {
    const mapped = rawRows
      .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
      .map(extractRow);
    if (statusFilter === 'ALL') return mapped;
    return mapped.filter((row) => row.judgeStatus === statusFilter);
  }, [rawRows, statusFilter]);

  const sampledSummary = useMemo(() => {
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

  const summary = useMemo(() => {
    const pass = toFiniteNumber(judgeSummary?.pass_count);
    const repairable = toFiniteNumber(judgeSummary?.repairable_count);
    const reject = toFiniteNumber(judgeSummary?.reject_count ?? judgeSummary?.rejected_out_count);
    const pending = toFiniteNumber(judgeSummary?.pending_count ?? judgeSummary?.pending_out_count);
    const hasFullSummary = [pass, repairable, reject, pending].some((value) => value !== undefined);
    if (!hasFullSummary) return sampledSummary;
    return {
      total: Number(pass || 0) + Number(repairable || 0) + Number(reject || 0) + Number(pending || 0),
      PASS: Number(pass || 0),
      REPAIRABLE: Number(repairable || 0),
      REJECT: Number(reject || 0),
      PENDING: Number(pending || 0),
    };
  }, [judgeSummary, sampledSummary]);

  const totalRows = Number(meta?.rowCountTotal);
  const displayedRows = rawRows.length;
  const isSampled = Boolean(meta?.rowsScope && meta.rowsScope !== 'full');

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
          <option value="REJECT">仅拒绝 REJECT</option>
          <option value="PENDING">仅待确认 PENDING</option>
          <option value="REPAIRABLE">仅可修复 REPAIRABLE</option>
          <option value="PASS">仅通过 PASS</option>
        </Form.Select>
      </div>

      <div className="small text-muted rag-debug-muted mb-2 d-flex flex-wrap gap-2">
        <Badge bg="secondary">全量 {summary.total}</Badge>
        <Badge bg="secondary">当前表格 {sampledSummary.total}</Badge>
        {Number.isFinite(totalRows) && totalRows > 0 ? (
          <Badge bg={isSampled ? 'warning' : 'success'} text={isSampled ? 'dark' : undefined}>
            明细 {displayedRows}/{totalRows}
          </Badge>
        ) : null}
        {isSampled ? <Badge bg="warning" text="dark">因数据量大已抽样</Badge> : null}
        <Badge bg="success">通过 {summary.PASS}</Badge>
        <Badge bg="warning" text="dark">可修复 {summary.REPAIRABLE}</Badge>
        <Badge bg="danger">拒绝 {summary.REJECT}</Badge>
        <Badge bg="info">待确认 {summary.PENDING}</Badge>
      </div>

      <div className="table-responsive">
        <table className="table table-sm align-middle mb-0">
          <thead>
            <tr>
              <th>用例编号</th>
              <th>模块</th>
              <th>优先级</th>
              <th>场景</th>
              <th>Judge 状态</th>
              <th>拒绝/待定原因</th>
              <th>命中确认事实</th>
              <th>命中待确认逻辑</th>
              <th>命中模糊依据</th>
              <th>命中复用风险</th>
              <th>命中重复</th>
              <th>重复来源</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={12} className="text-center text-muted py-4">
                  暂无 Judge 决策明细数据
                </td>
              </tr>
            ) : null}
            {rows.map((row, idx) => (
              <tr key={`${row.caseId}-${idx}`}>
                <td className="fw-semibold">{row.caseId}</td>
                <td>{row.module}</td>
                <td>{row.priority}</td>
                <td>{row.scene}</td>
                <td title={row.judgeStatus}>{judgeStatusLabel(row.judgeStatus)}</td>
                <td>{row.rejectReason}</td>
                <td>{row.hitConfirmedFact ? '是' : '否'}</td>
                <td>{row.hitPending ? '是' : '否'}</td>
                <td>{row.hitVague ? '是' : '否'}</td>
                <td>{row.hitReuseRisk ? '是' : '否'}</td>
                <td>{row.hitDuplicate ? '是' : '否'}</td>
                <td>{row.duplicateOf}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
