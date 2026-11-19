import { useEffect, useId, useRef, useState } from 'react';
import { Alert, Badge, Button, Form, Spinner } from 'react-bootstrap';
import {
  confirmEvaluationFeedback,
  dismissEvaluationFeedback,
  listEvaluationFeedback,
} from './state/evaluationFeedbackService';
import type { EvaluationFeedbackRecord, FeedbackConfirmation } from './state/evaluationFeedbackService';

type Props = { projectId: number; sourceRunId: number; evaluationRunId: number };

const categoryLabels: Record<string, string> = {
  missing_points: '生成遗漏',
  hallucinations: '无依据或多余内容',
  modifications: '人工修改',
};

function FeedbackRecord({ record, evaluationRunId, busy, onConfirm, onDismiss }: {
  record: EvaluationFeedbackRecord;
  evaluationRunId: number;
  busy: boolean;
  onConfirm: (record: EvaluationFeedbackRecord, value: FeedbackConfirmation) => Promise<void>;
  onDismiss: (record: EvaluationFeedbackRecord, reason: string) => Promise<void>;
}) {
  const formId = useId();
  const [text, setText] = useState(record.content.text);
  const [applicability, setApplicability] = useState(record.content.applicability || '');
  const [invalidation, setInvalidation] = useState(record.content.invalidation_condition || '');
  const [reason, setReason] = useState('');
  const [validation, setValidation] = useState<'confirm' | 'dismiss' | null>(null);
  const pending = record.status === 'pending';
  const active = record.status === 'active';
  const invalidConfirmation = validation === 'confirm';
  const invalidDismissal = validation === 'dismiss' && !reason.trim();
  const confirm = async () => {
    setValidation('confirm');
    if (!text.trim() || !applicability.trim() || !invalidation.trim()) return;
    await onConfirm(record, {
      revision: record.revision,
      text: text.trim(),
      applicability: applicability.trim(),
      invalidation_condition: invalidation.trim(),
    });
  };
  const dismiss = async () => {
    setValidation('dismiss');
    if (!reason.trim()) return;
    await onDismiss(record, reason.trim());
  };

  return (
    <li className="border rounded p-3 mb-3">
      <div className="d-flex align-items-start justify-content-between flex-wrap gap-2 mb-2">
        <strong className="small">{categoryLabels[record.content.category] || '评测差异'}</strong>
        <Badge bg={pending ? 'warning' : active ? 'success' : 'secondary'} text={pending ? 'dark' : undefined}>
          {pending ? '待人工核对' : active ? '已在本项目生效' : '已拒绝或撤销'}
        </Badge>
      </div>
      <p className="small mb-1">原始差异：{record.content.difference_text}</p>
      <p className="small text-muted mb-2">
        生成 Run #{record.content.source_run_id} · 评测 Run #{record.content.evaluation_run_id}
        {record.content.evaluation_run_id === evaluationRunId ? '（当前报告）' : '（历史评测）'}
      </p>
      <details className="small mb-3">
        <summary>查看依据与输入版本</summary>
        <dl className="mt-2 mb-2">
          {Object.entries(record.content.input_hashes).map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd className="text-break">{value}</dd>
            </div>
          ))}
        </dl>
        <pre className="text-wrap text-break mb-0">{JSON.stringify(record.evidence_refs, null, 2)}</pre>
      </details>

      {pending ? (
        <Form onSubmit={(event) => { event.preventDefault(); void confirm(); }} noValidate>
          <p className="small text-muted" id={`${formId}-help`}>
            模型差异是待核对评价。请结合需求原文、生成版和人工定稿版，整理为可复用结论；确认后仅在本项目的后续任务中使用。
          </p>
          <Form.Group controlId={`${formId}-text`} className="mb-2">
            <Form.Label className="small">确认后的业务结论</Form.Label>
            <Form.Control as="textarea" rows={3} value={text} required disabled={busy}
              onChange={(event) => setText(event.target.value)}
              isInvalid={invalidConfirmation && !text.trim()}
              aria-invalid={invalidConfirmation && !text.trim()}
              aria-describedby={`${formId}-help ${formId}-text-error`} />
            <Form.Control.Feedback type="invalid" id={`${formId}-text-error`}>请填写核对后的业务结论。</Form.Control.Feedback>
          </Form.Group>
          <Form.Group controlId={`${formId}-applicability`} className="mb-2">
            <Form.Label className="small">适用范围与条件</Form.Label>
            <Form.Control as="textarea" rows={2} value={applicability} required disabled={busy}
              onChange={(event) => setApplicability(event.target.value)}
              isInvalid={invalidConfirmation && !applicability.trim()}
              aria-invalid={invalidConfirmation && !applicability.trim()}
              aria-describedby={`${formId}-applicability-error`} />
            <Form.Control.Feedback type="invalid" id={`${formId}-applicability-error`}>请说明结论适用的业务范围与条件。</Form.Control.Feedback>
          </Form.Group>
          <Form.Group controlId={`${formId}-invalidation`} className="mb-3">
            <Form.Label className="small">失效或重新核对条件</Form.Label>
            <Form.Control as="textarea" rows={2} value={invalidation} required disabled={busy}
              onChange={(event) => setInvalidation(event.target.value)}
              isInvalid={invalidConfirmation && !invalidation.trim()}
              aria-invalid={invalidConfirmation && !invalidation.trim()}
              aria-describedby={`${formId}-invalidation-error`} />
            <Form.Control.Feedback type="invalid" id={`${formId}-invalidation-error`}>请说明何时应停止采用或重新核对。</Form.Control.Feedback>
          </Form.Group>
          <Button type="submit" size="sm" disabled={busy}>确认并在本项目复用</Button>
        </Form>
      ) : (
        <div className="small">
          <p className="mb-1">结论：{record.content.text}</p>
          {record.content.applicability && <p className="mb-1">适用范围：{record.content.applicability}</p>}
          {record.content.invalidation_condition && <p className="mb-1">失效条件：{record.content.invalidation_condition}</p>}
          {record.revocation_reason && <p className="mb-1">拒绝或撤销原因：{record.revocation_reason}</p>}
        </div>
      )}

      {(pending || active) && (
        <Form className="mt-3" noValidate onSubmit={(event) => { event.preventDefault(); void dismiss(); }}>
          <Form.Group controlId={`${formId}-reason`} className="mb-2">
            <Form.Label className="small">{active ? '撤销原因' : '不采用的原因'}</Form.Label>
            <Form.Control as="textarea" rows={2} value={reason} required disabled={busy}
              onChange={(event) => setReason(event.target.value)}
              isInvalid={invalidDismissal} aria-invalid={invalidDismissal} aria-describedby={`${formId}-reason-error`} />
            <Form.Control.Feedback type="invalid" id={`${formId}-reason-error`}>请填写具体原因。</Form.Control.Feedback>
          </Form.Group>
          <Button type="submit" size="sm" variant="outline-secondary" disabled={busy}>
            {active ? '撤销后续复用' : '不采用此反馈'}
          </Button>
        </Form>
      )}
      {validation === 'confirm' && (!text.trim() || !applicability.trim() || !invalidation.trim()) && (
        <p className="small text-danger mt-2 mb-0" role="alert">请补充结论、适用范围和失效条件后再确认。</p>
      )}
      {invalidDismissal && <p className="small text-danger mt-2 mb-0" role="alert">填写原因后才能提交。</p>}
    </li>
  );
}

export function TestCaseFeedbackPanel({ projectId, sourceRunId, evaluationRunId }: Props) {
  const [records, setRecords] = useState<EvaluationFeedbackRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [savingId, setSavingId] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const alive = useRef(true);
  const requestRevision = useRef(0);
  const mutationPending = useRef(false);

  // 外层按项目、生成 Run、评测 Run 设置 key；卸载后旧响应不能回写新项目。
  const reload = async () => {
    const revision = ++requestRevision.current;
    setLoading(true);
    setError('');
    try {
      const next = await listEvaluationFeedback(projectId, sourceRunId);
      if (alive.current && requestRevision.current === revision) setRecords(next);
    } catch (reason) {
      if (alive.current && requestRevision.current === revision) {
        setRecords([]);
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (alive.current && requestRevision.current === revision) setLoading(false);
    }
  };

  useEffect(() => {
    alive.current = true;
    void reload();
    return () => {
      alive.current = false;
      requestRevision.current += 1;
    };
  }, [projectId, sourceRunId, evaluationRunId]);

  const mutate = async (record: EvaluationFeedbackRecord, operation: () => Promise<EvaluationFeedbackRecord>) => {
    if (mutationPending.current || loading || !alive.current) return;
    if (record.project_id !== projectId || record.origin_run_id !== sourceRunId) return;
    mutationPending.current = true;
    setSavingId(record.id);
    setError('');
    setMessage('');
    try {
      const saved = await operation();
      if (!alive.current) return;
      setRecords((current) => current.map((item) => item.id === saved.id ? saved : item));
      setMessage(saved.status === 'active' ? '反馈已确认，仅在当前项目中复用。' : '反馈已停止采用。');
    } catch (reason) {
      if (alive.current) setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      mutationPending.current = false;
      if (alive.current) setSavingId(null);
    }
  };

  return (
    <section className="evaluation-report-panel panel-card mt-3" aria-labelledby="project-feedback-heading" aria-busy={loading || savingId !== null}>
      <div className="d-flex align-items-center justify-content-between gap-2 mb-2">
        <h6 id="project-feedback-heading" className="mb-0">项目反馈与长期记忆</h6>
        <Button size="sm" variant="outline-secondary" disabled={loading || savingId !== null} onClick={() => void reload()}>刷新反馈</Button>
      </div>
      <p className="small text-muted">以下记录绑定生成 Run #{sourceRunId}，包含其历史评测。待确认记录不会进入检索，已确认反馈仅供项目 #{projectId} 复用。</p>
      {loading && <p className="small" role="status"><Spinner size="sm" animation="border" aria-hidden="true" /> 正在加载项目反馈…</p>}
      {savingId !== null && <p className="small" role="status">正在保存反馈…</p>}
      {error && <Alert variant="danger" role="alert">{error}</Alert>}
      {message && <p className="small text-success" role="status">{message}</p>}
      {!loading && !error && records.length === 0 && <p className="small mb-0" role="status">本次生成暂无已记录的评测差异候选。完成用例对比后，真实差异会显示在这里。</p>}
      {!loading && records.length > 0 && (
        <ul className="list-unstyled mb-0">
          {records.map((record) => <FeedbackRecord key={`${record.id}-${record.status}-${record.revision}`} record={record}
            evaluationRunId={evaluationRunId} busy={savingId !== null}
            onConfirm={(item, value) => mutate(item, () => confirmEvaluationFeedback(item, value))}
            onDismiss={(item, reason) => mutate(item, () => dismissEvaluationFeedback(item, reason))} />)}
        </ul>
      )}
    </section>
  );
}
