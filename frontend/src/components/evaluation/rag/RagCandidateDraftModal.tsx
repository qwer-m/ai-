import { Button, Form, Modal } from 'react-bootstrap';

type RagCandidateDraftModalProps = {
  active: { id: number } | null;
  draft: Record<string, any> | null;
  open: boolean;
  onClose: () => void;
  onChange: (field: string, value: string) => void;
  onSave: () => void;
  onApprove: (targetType: 'challenge' | 'regression') => void;
};

function displayJsonValue(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value || [], null, 2);
}

export function RagCandidateDraftModal({
  active,
  draft,
  open,
  onClose,
  onChange,
  onSave,
  onApprove,
}: RagCandidateDraftModalProps) {
  return (
    <Modal show={open} onHide={onClose} size="lg" className="rag-candidate-modal">
      <Modal.Header closeButton>
        <Modal.Title>候选草稿编辑 #{active?.id || '-'}</Modal.Title>
      </Modal.Header>
      <Modal.Body className="d-flex flex-column gap-2">
        <Form.Group>
          <Form.Label>query</Form.Label>
          <Form.Control as="textarea" rows={2} value={draft?.query || ''} readOnly />
        </Form.Group>
        <Form.Group>
          <Form.Label>gold_docs (JSON array)</Form.Label>
          <Form.Control
            as="textarea"
            rows={3}
            value={displayJsonValue(draft?.gold_docs)}
            onChange={(e) => onChange('gold_docs', e.target.value)}
          />
        </Form.Group>
        <Form.Group>
          <Form.Label>gold_chunks (JSON array)</Form.Label>
          <Form.Control
            as="textarea"
            rows={3}
            value={displayJsonValue(draft?.gold_chunks)}
            onChange={(e) => onChange('gold_chunks', e.target.value)}
          />
        </Form.Group>
        <Form.Group>
          <Form.Label>answer_points (JSON array)</Form.Label>
          <Form.Control
            as="textarea"
            rows={3}
            value={displayJsonValue(draft?.answer_points)}
            onChange={(e) => onChange('answer_points', e.target.value)}
          />
        </Form.Group>
        <Form.Group>
          <Form.Label>gold_answer</Form.Label>
          <Form.Control
            as="textarea"
            rows={3}
            value={draft?.gold_answer || ''}
            onChange={(e) => onChange('gold_answer', e.target.value)}
          />
        </Form.Group>
        <Form.Group>
          <Form.Label>tags (JSON array)</Form.Label>
          <Form.Control
            value={displayJsonValue(draft?.tags)}
            onChange={(e) => onChange('tags', e.target.value)}
          />
        </Form.Group>
        <Form.Group>
          <Form.Label>difficulty</Form.Label>
          <Form.Select
            value={draft?.difficulty || 'medium'}
            onChange={(e) => onChange('difficulty', e.target.value)}
          >
            <option value="easy">easy</option>
            <option value="medium">medium</option>
            <option value="hard">hard</option>
          </Form.Select>
        </Form.Group>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="outline-secondary" onClick={onSave}>保存草稿</Button>
        <Button variant="outline-warning" onClick={() => onApprove('challenge')}>批准到 challenge</Button>
        <Button variant="outline-info" onClick={() => onApprove('regression')}>批准到 regression</Button>
      </Modal.Footer>
    </Modal>
  );
}
