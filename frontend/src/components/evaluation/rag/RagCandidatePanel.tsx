import { useEffect, useState } from 'react';
import { Alert, Badge, Button, Form, Modal, Table } from 'react-bootstrap';
import {
  approveRagEvalCandidate,
  draftRagEvalCandidate,
  generateRagEvalCandidates,
  listRagEvalCandidates,
  rejectRagEvalCandidate,
  translateError,
} from '../evaluationService';

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
    <div className="d-flex flex-column gap-3">
      {error ? <Alert variant="danger" className="mb-0">{error}</Alert> : null}

      <div className="fw-bold">候选回流</div>

      <div className="grid grid-cols-5 gap-3">
        <Form.Group><Form.Label className="small text-muted">run_id</Form.Label><Form.Control type="number" min={1} value={runId} onChange={(e) => setRunId(e.target.value ? Number(e.target.value) : '')} /></Form.Group>
        <Form.Group><Form.Label className="small text-muted">failure_reasons</Form.Label><Form.Control value={failureReasons} onChange={(e) => setFailureReasons(e.target.value)} /></Form.Group>
        <Form.Group><Form.Label className="small text-muted">faithfulness_lt</Form.Label><Form.Control type="number" step="0.01" value={faithfulnessLt} onChange={(e) => setFaithfulnessLt(e.target.value === '' ? '' : Number(e.target.value))} /></Form.Group>
        <Form.Group><Form.Label className="small text-muted">answer_correctness_lt</Form.Label><Form.Control type="number" step="0.01" value={correctnessLt} onChange={(e) => setCorrectnessLt(e.target.value === '' ? '' : Number(e.target.value))} /></Form.Group>
        <Form.Group>
          <Form.Label className="small text-muted">target_dataset</Form.Label>
          <Form.Select value={targetType} onChange={(e) => setTargetType(e.target.value as any)}>
            <option value="auto">auto</option>
            <option value="challenge">challenge</option>
            <option value="regression">regression</option>
          </Form.Select>
        </Form.Group>
      </div>

      <div className="d-flex gap-2 align-items-center">
        <Button variant="outline-primary" disabled={busy} onClick={() => void handleGenerate()}>{busy ? '生成中...' : '从运行生成候选'}</Button>
      </div>

      <div className="grid grid-cols-4 gap-3">
        <Form.Group>
          <Form.Label className="small text-muted">status</Form.Label>
          <Form.Select value={status} onChange={(e) => { setPage(1); setStatus(e.target.value); }}>
            <option value="">all</option>
            <option value="pending">pending</option>
            <option value="approved">approved</option>
            <option value="rejected">rejected</option>
          </Form.Select>
        </Form.Group>
        <Form.Group><Form.Label className="small text-muted">failure_reason</Form.Label><Form.Control value={filterFailureReason} onChange={(e) => { setPage(1); setFilterFailureReason(e.target.value); }} /></Form.Group>
        <Form.Group>
          <Form.Label className="small text-muted">suggested_dataset_type</Form.Label>
          <Form.Select value={suggestedDatasetType} onChange={(e) => { setPage(1); setSuggestedDatasetType(e.target.value); }}>
            <option value="">all</option>
            <option value="challenge">challenge</option>
            <option value="regression">regression</option>
          </Form.Select>
        </Form.Group>
      </div>

      <div className="table-responsive">
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
                <td>{r.status}</td>
                <td style={{ maxWidth: 360 }}>{String(r.query || '').slice(0, 120)}</td>
                <td>{r.failure_reason || '-'}</td>
                <td><Badge bg={r.suggested_dataset_type === 'regression' ? 'info' : 'warning'}>{r.suggested_dataset_type}</Badge></td>
                <td>{r.source_type}#{r.source_id}</td>
                <td className="d-flex gap-2">
                  <Button size="sm" variant="outline-secondary" onClick={() => void openDraftModal(r)}>草稿</Button>
                  <Button size="sm" variant="outline-success" onClick={() => void approve(r)}>批准</Button>
                  <Button size="sm" variant="outline-danger" onClick={() => void reject(r)}>拒绝</Button>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>

      <div className="d-flex gap-2">
        <Button size="sm" variant="outline-secondary" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>上一页</Button>
        <Button size="sm" variant="outline-secondary" disabled={page * pageSize >= total} onClick={() => setPage((p) => p + 1)}>下一页</Button>
        <span className="small text-muted align-self-center">共 {total} 条</span>
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
