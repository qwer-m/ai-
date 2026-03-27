import { useEffect, useState } from 'react';
import { Alert, Badge, Button, Form, Modal, Table } from 'react-bootstrap';
import {
  approveRagEvalCandidate,
  draftRagEvalCandidate,
  generateRagEvalCandidates,
  listRagEvalCandidates,
  rejectRagEvalCandidate,
  translateError,
} from '../state/evaluationService';

type Props = {
  onLog: (msg: string) => void;
  currentRunId: number | null;
};

function parseJsonArray(text: string): any[] {
  if (!text.trim()) return [];
  try {
    const parsed = JSON.parse(text);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function RagCandidatePanel({ onLog, currentRunId }: Props) {
  const [runId, setRunId] = useState<number | ''>(currentRunId ?? '');
  const [failureReasons, setFailureReasons] = useState('hallucination,wrong_version,incomplete_answer,low_rank');
  const [faithfulnessLt, setFaithfulnessLt] = useState<number | ''>('');
  const [correctnessLt, setCorrectnessLt] = useState<number | ''>('');
  const [targetType, setTargetType] = useState<'auto' | 'challenge' | 'regression'>('auto');

  const [status, setStatus] = useState('pending');
  const [filterFailureReason, setFilterFailureReason] = useState('');
  const [suggestedDatasetType, setSuggestedDatasetType] = useState('');

  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [rows, setRows] = useState<any[]>([]);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [detailOpen, setDetailOpen] = useState(false);
  const [active, setActive] = useState<any>(null);
  const [draft, setDraft] = useState<any>(null);

  const loadCandidates = async () => {
    try {
      const resp = await listRagEvalCandidates({
        status: status || undefined,
        failure_reason: filterFailureReason || undefined,
        suggested_dataset_type: suggestedDatasetType || undefined,
        page,
        page_size: pageSize,
      });
      setRows(resp?.items || []);
      setTotal(Number(resp?.total || 0));
    } catch (e) {
      setError(await translateError(e));
    }
  };

  useEffect(() => {
    void loadCandidates();
  }, [page, status, filterFailureReason, suggestedDatasetType]);

  const handleGenerate = async () => {
    if (!runId) {
      setError('请先填写 run_id');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const resp = await generateRagEvalCandidates({
        run_id: Number(runId),
        target_dataset_type: targetType === 'auto' ? null : targetType,
        filters: {
          answer_correct_false: true,
          failure_reasons: failureReasons.split(',').map((x) => x.trim()).filter(Boolean),
          faithfulness_lt: faithfulnessLt === '' ? null : Number(faithfulnessLt),
          answer_correctness_lt: correctnessLt === '' ? null : Number(correctnessLt),
        },
      });
      onLog(`候选回流生成完成: created=${resp?.created_count || 0}, skipped=${resp?.skipped_existing || 0}`);
      setPage(1);
      await loadCandidates();
    } catch (e) {
      setError(await translateError(e));
    } finally {
      setBusy(false);
    }
  };

  const openDraftModal = async (candidate: any) => {
    setError(null);
    try {
      const resp = await draftRagEvalCandidate(candidate.id);
      setActive(candidate);
      setDraft(resp?.draft || {});
      setDetailOpen(true);
    } catch (e) {
      setError(await translateError(e));
    }
  };

  const saveDraft = async () => {
    if (!active) return;
    try {
      const payload = {
        ...draft,
        gold_docs: typeof draft.gold_docs === 'string' ? parseJsonArray(draft.gold_docs) : (draft.gold_docs || []),
        gold_chunks: typeof draft.gold_chunks === 'string' ? parseJsonArray(draft.gold_chunks) : (draft.gold_chunks || []),
        answer_points: typeof draft.answer_points === 'string' ? parseJsonArray(draft.answer_points) : (draft.answer_points || []),
        tags: typeof draft.tags === 'string' ? parseJsonArray(draft.tags) : (draft.tags || []),
      };
      const resp = await draftRagEvalCandidate(active.id, payload);
      setDraft(resp?.draft || payload);
      onLog(`候选草稿已保存: candidate_id=${active.id}`);
      await loadCandidates();
    } catch (e) {
      setError(await translateError(e));
    }
  };

  const approve = async (candidate: any, forceType?: 'challenge' | 'regression') => {
    try {
      const resp = await approveRagEvalCandidate(candidate.id, {
        target_dataset_type: forceType || undefined,
        draft: draft && active?.id === candidate.id ? draft : undefined,
      });
      onLog(`候选已批准: candidate_id=${candidate.id}, sample_id=${resp?.target_sample_id}`);
      setDetailOpen(false);
      setActive(null);
      setDraft(null);
      await loadCandidates();
    } catch (e) {
      setError(await translateError(e));
    }
  };

  const reject = async (candidate: any) => {
    try {
      await rejectRagEvalCandidate(candidate.id, 'manual_reject');
      onLog(`候选已拒绝: candidate_id=${candidate.id}`);
      await loadCandidates();
    } catch (e) {
      setError(await translateError(e));
    }
  };

  return (
    <div className="d-flex flex-column gap-3 rag-report-subpanel">
      {error ? <Alert variant="danger" className="mb-0">{error}</Alert> : null}

      <div className="ui-section-card">
        <div className="ui-section-title">候选生成</div>
        <div className="grid grid-cols-5 gap-3 rag-report-grid rag-report-grid-tight">
          <Form.Group><Form.Label className="small text-muted">运行 ID</Form.Label><Form.Control type="number" min={1} value={runId} onChange={(e) => setRunId(e.target.value ? Number(e.target.value) : '')} /></Form.Group>
          <Form.Group className="rag-col-span-2"><Form.Label className="small text-muted">失败原因过滤</Form.Label><Form.Control value={failureReasons} onChange={(e) => setFailureReasons(e.target.value)} /></Form.Group>
          <Form.Group><Form.Label className="small text-muted">Faithfulness 阈值（小于）</Form.Label><Form.Control type="number" step="0.01" value={faithfulnessLt} onChange={(e) => setFaithfulnessLt(e.target.value === '' ? '' : Number(e.target.value))} /></Form.Group>
          <Form.Group><Form.Label className="small text-muted">Correctness 阈值（小于）</Form.Label><Form.Control type="number" step="0.01" value={correctnessLt} onChange={(e) => setCorrectnessLt(e.target.value === '' ? '' : Number(e.target.value))} /></Form.Group>
          <Form.Group>
            <Form.Label className="small text-muted">目标数据集</Form.Label>
            <Form.Select value={targetType} onChange={(e) => setTargetType(e.target.value as any)}>
              <option value="auto">auto</option>
              <option value="challenge">challenge</option>
              <option value="regression">regression</option>
            </Form.Select>
          </Form.Group>
        </div>

        <div className="ui-actions-row mt-3 rag-report-actions">
          <Button variant="outline-primary" disabled={busy} onClick={() => void handleGenerate()}>{busy ? '生成中...' : '从运行生成候选'}</Button>
        </div>
      </div>

      <div className="ui-section-card">
        <div className="ui-section-title">候选筛选</div>
        <div className="grid grid-cols-4 gap-3 rag-report-grid rag-report-grid-wide">
          <Form.Group>
            <Form.Label className="small text-muted">候选状态</Form.Label>
            <Form.Select value={status} onChange={(e) => { setPage(1); setStatus(e.target.value); }}>
              <option value="">all</option>
              <option value="pending">pending</option>
              <option value="approved">approved</option>
              <option value="rejected">rejected</option>
            </Form.Select>
          </Form.Group>
          <Form.Group><Form.Label className="small text-muted">失败原因</Form.Label><Form.Control value={filterFailureReason} onChange={(e) => { setPage(1); setFilterFailureReason(e.target.value); }} /></Form.Group>
          <Form.Group>
            <Form.Label className="small text-muted">建议数据集类型</Form.Label>
            <Form.Select value={suggestedDatasetType} onChange={(e) => { setPage(1); setSuggestedDatasetType(e.target.value); }}>
              <option value="">all</option>
              <option value="challenge">challenge</option>
              <option value="regression">regression</option>
            </Form.Select>
          </Form.Group>
        </div>
      </div>

      <div className="ui-section-card">
        <div className="ui-section-title">候选列表</div>
        <div className="table-responsive rag-report-table">
          <Table striped bordered hover size="sm" className="mb-0">
            <thead>
              <tr>
                <th>id</th><th>status</th><th>query</th><th>failure_reason</th><th>suggested_dataset</th><th>source</th><th>操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={7} className="text-center text-muted">暂无候选</td></tr>
              ) : rows.map((r) => (
                <tr key={r.id}>
                  <td>{r.id}</td>
                  <td><span className="ui-badge-soft">{r.status}</span></td>
                  <td className="rag-report-query-cell rag-report-query-cell--wide">{String(r.query || '').slice(0, 120)}</td>
                  <td>{r.failure_reason || '-'}</td>
                  <td><Badge bg={r.suggested_dataset_type === 'regression' ? 'info' : 'warning'}>{r.suggested_dataset_type}</Badge></td>
                  <td>{r.source_type}#{r.source_id}</td>
                  <td className="d-flex gap-2 flex-wrap rag-report-action-cell">
                    <Button size="sm" variant="outline-secondary" onClick={() => void openDraftModal(r)}>草稿</Button>
                    <Button size="sm" variant="outline-success" onClick={() => void approve(r)}>批准</Button>
                    <Button size="sm" variant="outline-danger" onClick={() => void reject(r)}>拒绝</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>

        <div className="d-flex gap-2 rag-report-pagination mt-2">
          <Button size="sm" variant="outline-secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>上一页</Button>
          <Button size="sm" variant="outline-secondary" disabled={page * pageSize >= total} onClick={() => setPage((p) => p + 1)}>下一页</Button>
          <span className="small text-muted align-self-center">共 {total} 条</span>
        </div>
      </div>

      <Modal show={detailOpen} onHide={() => setDetailOpen(false)} size="lg">
        <Modal.Header closeButton>
          <Modal.Title>候选草稿编辑 #{active?.id || '-'}</Modal.Title>
        </Modal.Header>
        <Modal.Body className="d-flex flex-column gap-2">
          <Form.Group><Form.Label>query</Form.Label><Form.Control as="textarea" rows={2} value={draft?.query || ''} readOnly /></Form.Group>
          <Form.Group><Form.Label>gold_docs (JSON array)</Form.Label><Form.Control as="textarea" rows={3} value={typeof draft?.gold_docs === 'string' ? draft?.gold_docs : JSON.stringify(draft?.gold_docs || [], null, 2)} onChange={(e) => setDraft((v: any) => ({ ...v, gold_docs: e.target.value }))} /></Form.Group>
          <Form.Group><Form.Label>gold_chunks (JSON array)</Form.Label><Form.Control as="textarea" rows={3} value={typeof draft?.gold_chunks === 'string' ? draft?.gold_chunks : JSON.stringify(draft?.gold_chunks || [], null, 2)} onChange={(e) => setDraft((v: any) => ({ ...v, gold_chunks: e.target.value }))} /></Form.Group>
          <Form.Group><Form.Label>answer_points (JSON array)</Form.Label><Form.Control as="textarea" rows={3} value={typeof draft?.answer_points === 'string' ? draft?.answer_points : JSON.stringify(draft?.answer_points || [], null, 2)} onChange={(e) => setDraft((v: any) => ({ ...v, answer_points: e.target.value }))} /></Form.Group>
          <Form.Group><Form.Label>gold_answer</Form.Label><Form.Control as="textarea" rows={3} value={draft?.gold_answer || ''} onChange={(e) => setDraft((v: any) => ({ ...v, gold_answer: e.target.value }))} /></Form.Group>
          <Form.Group><Form.Label>tags (JSON array)</Form.Label><Form.Control value={typeof draft?.tags === 'string' ? draft?.tags : JSON.stringify(draft?.tags || [])} onChange={(e) => setDraft((v: any) => ({ ...v, tags: e.target.value }))} /></Form.Group>
          <Form.Group><Form.Label>difficulty</Form.Label><Form.Select value={draft?.difficulty || 'medium'} onChange={(e) => setDraft((v: any) => ({ ...v, difficulty: e.target.value }))}><option value="easy">easy</option><option value="medium">medium</option><option value="hard">hard</option></Form.Select></Form.Group>
        </Modal.Body>
        <Modal.Footer>
          <Button variant="outline-secondary" onClick={() => void saveDraft()}>保存草稿</Button>
          <Button variant="outline-warning" onClick={() => active && void approve(active, 'challenge')}>批准到 challenge</Button>
          <Button variant="outline-info" onClick={() => active && void approve(active, 'regression')}>批准到 regression</Button>
        </Modal.Footer>
      </Modal>
    </div>
  );
}
