import type { ChangeEvent, ClipboardEvent } from 'react';
import { Button, Form, Popover } from 'react-bootstrap';

type Props = {
  supplementText: string;
  setSupplementText: (v: string) => void;
  supplementImages: File[];
  onPaste: (e: ClipboardEvent<HTMLTextAreaElement>) => void;
  onFilesChange: (e: ChangeEvent<HTMLInputElement>) => void;
  onCancel: () => void;
  onConfirm: () => void;
  disableConfirm: boolean;
};

export function SupplementKnowledgePopover({
  supplementText,
  setSupplementText,
  supplementImages,
  onPaste,
  onFilesChange,
  onCancel,
  onConfirm,
  disableConfirm,
}: Props) {
  return (
    <Popover id="popover-supplement" style={{ maxWidth: '400px', width: '350px' }}>
      <Popover.Header as="h3">用户补充描述</Popover.Header>
      <Popover.Body>
        <Form.Group className="mb-2">
          <Form.Control
            as="textarea"
            rows={3}
            placeholder="请输入补充描述..."
            value={supplementText}
            onChange={(e) => setSupplementText(e.target.value)}
            onPaste={onPaste}
          />
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label className="small text-muted">导入图片（最多10张）</Form.Label>
          <Form.Control
            type="file"
            size="sm"
            accept="image/*"
            multiple
            onChange={onFilesChange}
          />
          {supplementImages.length > 0 && (
            <div className="mt-2 d-flex flex-wrap gap-2">
              {supplementImages.map((f, idx) => (
                <div key={idx} className="border rounded p-1 small bg-white">
                  <span className="text-muted">{f.name}</span>
                </div>
              ))}
            </div>
          )}
        </Form.Group>
        <div className="d-flex justify-content-end gap-2">
          <Button variant="secondary" size="sm" onClick={onCancel}>取消</Button>
          <Button variant="primary" size="sm" onClick={onConfirm} disabled={disableConfirm}>确定</Button>
        </div>
      </Popover.Body>
    </Popover>
  );
}
