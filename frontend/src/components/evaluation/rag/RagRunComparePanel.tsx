import { useMemo, useState } from 'react';
import { Alert, Badge, Button, Form, Modal, Table } from 'react-bootstrap';
import { getRagEvalRunCompare, translateError } from '../state/evaluationService';

type Props = {
  onLog: (msg: string) => void;
  currentRunId: number | null;
};

type DiffRow = {
  key: string;
  value: number;
};

function DiffBadge({ value }: { value: number }) {
  if (value > 0) return <Badge bg="success">{value.toFixed(4)}</Badge>;
  if (value < 0) return <Badge bg="danger">{value.toFixed(4)}</Badge>;
  return <Badge bg="secondary">0.0000</Badge>;
}

export function RagRunComparePanel({ onLog, currentRunId }: Props) {
  const [runA, setRunA] = useState<number | ''>('');
  const [runB, setRunB] = useState<number | ''>(currentRunId ?? '');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [compareData, setCompareData] = useState<any>(null);
  const [detail, setDetail] = useState<{ title: string; content: string } | null>(null);

  const metricRows = useMemo<DiffRow[]>(() => {
    const metricDiff = compareData?.metric_diff || {};
    return Object.entries(metricDiff).map(([k, v]) => ({
      key: String(k),
      value: Number(v || 0),
    }));
  }, [compareData]);

  const runCompare = async () => {
    if (!runA || !runB) {
      setError('请先输入 run_a 和 run_b');
      return;
    }
    if (Number(runA) === Number(runB)) {
      setError('run_a 与 run_b 不能相同');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const resp = await getRagEvalRunCompare(Number(runA), Number(runB));
      setCompareData(resp);
      onLog(`RAG 运行对比完成: run_a=${runA}, run_b=${runB}`);
    } catch (e) {
      setError(await translateError(e));
    } finally {
      setLoading(false);
    }
  };

  const renderSampleTable = (rows: any[], title: string) => {
    return (
      <div className="ui-section-card mt-3">
        <div className="ui-section-title">{title}</div>
        <div className="table-responsive rag-report-table scroll-table-md">
          <Table striped bordered hover size="sm" className="mb-0">
            <thead>
              <tr>
                <th>sample_id</th>
                <th>query</th>
                <th>correct A→B</th>
                <th>failure A→B</th>
                <th>rank A→B</th>
                <th>answer_correctness Δ</th>
                <th>faithfulness Δ</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={8} className="text-center text-muted">暂无</td>
                </tr>
              ) : rows.map((row) => (
                <tr key={row.sample_id}>
                  <td>{row.sample_id}</td>
                  <td className="rag-report-query-cell">{String(row.query || '').slice(0, 100)}</td>
                  <td>{String(row.from_correct)} {'→'} {String(row.to_correct)}</td>
                  <td>{String(row.from_failure_reason || 'pass')} {'→'} {String(row.to_failure_reason || 'pass')}</td>
                  <td>{row.first_hit_rank_a ?? '-'} {'→'} {row.first_hit_rank_b ?? '-'}</td>
                  <td>{Number(row.answer_correctness_delta || 0).toFixed(4)}</td>
                  <td>{Number(row.faithfulness_delta || 0).toFixed(4)}</td>
                  <td>
                    <Button
                      size="sm"
                      variant="outline-secondary"
                      onClick={() => setDetail({
                        title: `sample ${row.sample_id}`,
                        content: JSON.stringify(row, null, 2),
                      })}
                    >
                      查看
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      </div>
    );
  };

  return (
    <div className="d-flex flex-column gap-3 rag-report-subpanel">
      {error ? <Alert variant="danger" className="mb-0">{error}</Alert> : null}

      <div className="ui-section-card">
        <div className="ui-section-title">评测运行对比</div>
        <div className="grid grid-cols-4 gap-3 rag-report-grid rag-report-grid-wide control-grid-lr">
          <Form.Group className="control-field">
            <Form.Label className="small text-muted">对比基线（run_a）</Form.Label>
            <Form.Control
              type="number"
              min={1}
              value={runA}
              onChange={(e) => setRunA(e.target.value ? Number(e.target.value) : '')}
            />
          </Form.Group>
          <Form.Group className="control-field">
            <Form.Label className="small text-muted">目标运行（run_b）</Form.Label>
            <Form.Control
              type="number"
              min={1}
              value={runB}
              onChange={(e) => setRunB(e.target.value ? Number(e.target.value) : '')}
            />
          </Form.Group>
          <div className="d-flex align-items-end rag-report-actions rag-col-span-2">
            <Button variant="outline-primary" disabled={loading} onClick={() => void runCompare()}>
              {loading ? '对比中...' : '开始对比'}
            </Button>
          </div>
        </div>
      </div>

      {compareData ? (
        <>
          <div className="ui-section-card">
            <div className="ui-section-title">指标变化</div>
            <div className="grid grid-cols-5 gap-3 small rag-report-kpi-grid">
              {metricRows.map((m) => (
                <div key={m.key} className="ui-kpi-card">
                  <div className="ui-kpi-title">{m.key}</div>
                  <DiffBadge value={m.value} />
                </div>
              ))}
            </div>

            <div className="grid grid-cols-4 gap-3 small rag-report-kpi-grid mt-3">
              <div className="ui-kpi-card">
                <div className="ui-kpi-title">improved</div>
                {compareData?.summary?.improved_samples ?? 0}
              </div>
              <div className="ui-kpi-card">
                <div className="ui-kpi-title">regressed</div>
                {compareData?.summary?.regressed_samples ?? 0}
              </div>
              <div className="ui-kpi-card">
                <div className="ui-kpi-title">unchanged_correct</div>
                {compareData?.summary?.unchanged_correct ?? 0}
              </div>
              <div className="ui-kpi-card">
                <div className="ui-kpi-title">unchanged_incorrect</div>
                {compareData?.summary?.unchanged_incorrect ?? 0}
              </div>
            </div>
          </div>

          {renderSampleTable(compareData?.improved_samples || [], '错误 -> 正确 (improved)')}
          {renderSampleTable(compareData?.regressed_samples || [], '正确 -> 错误 (regressed)')}

          <div className="ui-section-card">
            <div className="ui-section-title">分维度差异</div>
            <div className="grid grid-cols-3 gap-3 rag-report-grid rag-report-grid-wide control-grid-lr">
              <Form.Group className="control-field">
                <Form.Label className="small text-muted">标签维度差异</Form.Label>
                <Form.Control
                  as="textarea"
                  rows={8}
                  readOnly
                  value={JSON.stringify(compareData?.by_tag_diff || {}, null, 2)}
                />
              </Form.Group>
              <Form.Group className="control-field">
                <Form.Label className="small text-muted">难度维度差异</Form.Label>
                <Form.Control
                  as="textarea"
                  rows={8}
                  readOnly
                  value={JSON.stringify(compareData?.by_difficulty_diff || {}, null, 2)}
                />
              </Form.Group>
              <Form.Group className="control-field">
                <Form.Label className="small text-muted">失败原因维度差异</Form.Label>
                <Form.Control
                  as="textarea"
                  rows={8}
                  readOnly
                  value={JSON.stringify(compareData?.by_failure_reason_diff || {}, null, 2)}
                />
              </Form.Group>
            </div>
          </div>
        </>
      ) : null}

      <Modal show={Boolean(detail)} onHide={() => setDetail(null)} size="lg">
        <Modal.Header closeButton>
          <Modal.Title>{detail?.title}</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          <Form.Control as="textarea" rows={16} readOnly value={detail?.content || ''} />
        </Modal.Body>
      </Modal>
    </div>
  );
}
