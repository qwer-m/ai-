import { useLayoutEffect, useRef, useState } from 'react';
import { Button, Form, Modal, ProgressBar, Spinner } from 'react-bootstrap';
import { FiArrowLeft, FiArrowRight, FiCheck, FiMessageSquare } from 'react-icons/fi';
import type { AgentApproval } from '../agent-platform/types';

type ConflictItem = {
  id: string;
  reason: string;
  coverage_fragment: string;
  facts: { fact_id: string; assertion: string }[];
  conflict?: {
    subject: string;
    condition: string;
    sides: { fact_id: string; quote: string }[];
    incompatibility: string;
  };
};

export function AgentConflictModal({ approval, show, onHide, onComplete, submitError }: {
  approval: AgentApproval; show: boolean; onHide: () => void;
  onComplete: (answers: Record<string, string>) => Promise<boolean>;
  submitError: string | null;
}) {
  const items = approval.request_payload.items as ConflictItem[];
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [activeIndex, setActiveIndex] = useState(0);
  const [saving, setSaving] = useState(false);
  const [submitFailed, setSubmitFailed] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const currentStepRef = useRef<HTMLButtonElement>(null);
  const item = items[activeIndex];
  const answered = items.map(({ id }) => Boolean(answers[id]?.trim()));
  const answeredCount = answered.filter(Boolean).length;
  const allAnswered = answeredCount === items.length;

  const focusQuestion = () => {
    currentStepRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    if (bodyRef.current) bodyRef.current.scrollTop = 0;
    headingRef.current?.focus({ preventScroll: true });
  };
  useLayoutEffect(focusQuestion, [activeIndex]);

  if (!item) return null;

  return (
    <Modal show={show} onHide={onHide} onEntered={focusQuestion}
      centered scrollable dialogClassName="agent-clarification-dialog" aria-labelledby="agent-conflict-title"
      aria-describedby="agent-conflict-description" backdrop={saving ? 'static' : true} keyboard={!saving}>
      <Modal.Header closeButton={!saving} closeLabel="关闭">
        <span className="agent-clarification-icon" aria-hidden="true"><FiMessageSquare /></span>
        <div>
          <Modal.Title as="h1" id="agent-conflict-title">确认需求规则</Modal.Title>
          <p id="agent-conflict-description">生成已暂停。答完 {items.length} 项并提交后继续。</p>
        </div>
      </Modal.Header>
      <Form className="agent-clarification-form" aria-busy={saving} onSubmit={async (event) => {
        event.preventDefault();
        if (!answered[activeIndex] || saving) return;
        if (!allAnswered) {
          const next = answered.findIndex((done, index) => index > activeIndex && !done);
          setActiveIndex(next < 0 ? answered.indexOf(false) : next);
          return;
        }
        setSaving(true);
        setSubmitFailed(false);
        try {
          const trimmedAnswers = Object.fromEntries(items.map(({ id }) => [id, answers[id].trim()]));
          if (await onComplete(trimmedAnswers)) onHide();
          else setSubmitFailed(true);
        } catch {
          setSubmitFailed(true);
        } finally {
          setSaving(false);
        }
      }}>
        <Modal.Body className="agent-clarification-body" ref={bodyRef}>
          <nav className="agent-clarification-nav" aria-label="待确认事项">
            <div className="agent-clarification-progress">
              <span>确认进度</span>
              <strong aria-live="polite">已答 {answeredCount} / {items.length}</strong>
            </div>
            <ProgressBar now={answeredCount} max={items.length} aria-label="确认进度" />
            <ol>
              {items.map((entry, index) => (
                <li key={entry.id}>
                  <button type="button" className="agent-clarification-step" disabled={saving}
                    ref={index === activeIndex ? currentStepRef : undefined}
                    aria-current={index === activeIndex ? 'step' : undefined}
                    aria-label={`第 ${index + 1} 项：${entry.coverage_fragment}，${answered[index] ? '已回答' : '待回答'}`}
                    onClick={() => setActiveIndex(index)}>
                    <span className="agent-clarification-step-number" aria-hidden="true">
                      {answered[index] ? <FiCheck /> : index + 1}
                    </span>
                    <span className="agent-clarification-step-title">{entry.coverage_fragment}</span>
                  </button>
                </li>
              ))}
            </ol>
          </nav>
          <section className="agent-clarification-question" aria-labelledby="agent-conflict-question">
            <p className="agent-clarification-index">第 {activeIndex + 1} 项 / 共 {items.length} 项</p>
            <h2 id="agent-conflict-question" ref={headingRef} tabIndex={-1}>{item.coverage_fragment}</h2>
            <p className="agent-clarification-reason">{item.reason}</p>
            <Form.Group controlId="agent-conflict-answer">
              <Form.Label>你的答复</Form.Label>
              <Form.Control as="textarea" rows={3} required disabled={saving} aria-describedby="agent-conflict-help"
                maxLength={4000} value={answers[item.id] || ''}
                onChange={(event) => setAnswers((current) => ({ ...current, [item.id]: event.target.value }))}
                placeholder="写下本次应采用的规则" />
              <Form.Text id="agent-conflict-help">填写后可继续；本次不测的事项可写明排除范围。</Form.Text>
            </Form.Group>
            {item.facts.length > 0 && <details key={item.id} className="agent-clarification-evidence">
              <summary>查看来源（{item.facts.length} 条）</summary>
              {item.conflict && <>
                <p><strong>比较对象：</strong>{item.conflict.subject}</p>
                <p><strong>适用条件：</strong>{item.conflict.condition}</p>
                <p><strong>不能同时满足的原因：</strong>{item.conflict.incompatibility}</p>
                <ol>{item.conflict.sides.map((side, index) => <li key={index}>
                  <q>{side.quote}</q> <small>{side.fact_id}</small>
                </li>)}</ol>
              </>}
              <ul>{item.facts.map((fact) => <li key={fact.fact_id}>
                {fact.assertion} <small>{fact.fact_id}</small>
              </li>)}</ul>
            </details>}
          </section>
        </Modal.Body>
        <Modal.Footer className="agent-clarification-footer">
          {submitFailed && <p className="agent-clarification-error" role="alert">
            {submitError || '未能提交，请重试。'} 已填写的答复仍在。
          </p>}
          <Button type="button" variant="link" className="agent-clarification-later" onClick={onHide} disabled={saving}>稍后填写</Button>
          <div className="agent-clarification-actions">
            {activeIndex > 0 && <Button type="button" variant="outline-secondary" disabled={saving}
              onClick={() => setActiveIndex(activeIndex - 1)}><FiArrowLeft aria-hidden="true" />上一项</Button>}
            <Button type="submit" disabled={!answered[activeIndex] || saving}>
              {saving ? <><Spinner size="sm" aria-hidden="true" />提交中</> : allAnswered ? '提交并继续生成' : <>
                下一项<FiArrowRight aria-hidden="true" />
              </>}
            </Button>
          </div>
        </Modal.Footer>
      </Form>
    </Modal>
  );
}
