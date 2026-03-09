import { Button, Form, Modal } from "react-bootstrap";
import type { Dispatch, ReactNode, SetStateAction } from "react";

type SaveForm = {
  name: string;
  description: string;
  parentId: number | null;
};

type SaveRequestModalProps = {
  show: boolean;
  onHide: () => void;
  saveForm: SaveForm;
  setSaveForm: Dispatch<SetStateAction<SaveForm>>;
  renderFolderOptions: (parentId: number | null) => ReactNode;
  onConfirmSave: () => void;
};

export function SaveRequestModal({
  show,
  onHide,
  saveForm,
  setSaveForm,
  renderFolderOptions,
  onConfirmSave,
}: SaveRequestModalProps) {
  // 只负责采集“请求名称 + 描述 + 保存目录”，提交逻辑由父组件处理。
  return (
    <Modal show={show} onHide={onHide} centered>
      <Modal.Header closeButton>
        <Modal.Title>保存请求</Modal.Title>
      </Modal.Header>
      <Modal.Body>
        <Form>
          <Form.Group className="mb-3">
            <Form.Label>请求名称</Form.Label>
            <Form.Control
              type="text"
              value={saveForm.name}
              onChange={(e) => setSaveForm({ ...saveForm, name: e.target.value })}
              autoFocus
            />
          </Form.Group>
          <Form.Group className="mb-3">
            <Form.Label>描述</Form.Label>
            <Form.Control
              as="textarea"
              rows={3}
              value={saveForm.description}
              onChange={(e) => setSaveForm({ ...saveForm, description: e.target.value })}
            />
          </Form.Group>
          <Form.Group className="mb-3">
            <Form.Label>保存到目录</Form.Label>
            <Form.Select
              value={saveForm.parentId || ""}
              onChange={(e) =>
                setSaveForm({
                  ...saveForm,
                  parentId: e.target.value ? Number(e.target.value) : null,
                })
              }
            >
              <option value="">（根目录）</option>
              {renderFolderOptions(null)}
            </Form.Select>
          </Form.Group>
        </Form>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onHide}>
          取消
        </Button>
        <Button variant="primary" onClick={onConfirmSave}>
          保存
        </Button>
      </Modal.Footer>
    </Modal>
  );
}
