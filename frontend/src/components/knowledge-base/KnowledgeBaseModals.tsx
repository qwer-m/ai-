import { Button, Modal, Spinner } from 'react-bootstrap';
import { FaExclamationTriangle, FaPlus, FaTimes } from 'react-icons/fa';
import type { Doc } from './types';

type DeleteConfirmModalProps = {
  show: boolean;
  filename?: string;
  onCancel: () => void;
  onConfirm: () => void;
};

export function DeleteConfirmModal({ show, filename, onCancel, onConfirm }: DeleteConfirmModalProps) {
  return (
    <Modal show={show} onHide={onCancel} centered>
      <Modal.Header closeButton className="bg-danger text-white">
        <Modal.Title>
          <FaExclamationTriangle /> 确认删除
        </Modal.Title>
      </Modal.Header>
      <Modal.Body>
        <p>确定要永久删除下列文档吗？该操作无法撤销。</p>
        <div className="alert alert-secondary p-2">
          <strong>{filename}</strong>
        </div>
        <p className="small text-muted mb-0">这也会解除其与所有测试用例的关联。</p>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onCancel}>
          取消
        </Button>
        <Button variant="danger" onClick={onConfirm}>
          确认删除
        </Button>
      </Modal.Footer>
    </Modal>
  );
}

type ManageRelationModalProps = {
  show: boolean;
  manageTarget: Doc | null;
  manageLoading: boolean;
  candidates: Doc[];
  onClose: () => void;
  onToggleRelation: (candidate: Doc, isLinked: boolean) => void;
};

export function ManageRelationModal({
  show,
  manageTarget,
  manageLoading,
  candidates,
  onClose,
  onToggleRelation,
}: ManageRelationModalProps) {
  return (
    <Modal show={show} onHide={onClose} centered size="lg">
      <Modal.Header closeButton>
        <Modal.Title className="fw-bold">管理关联用例</Modal.Title>
      </Modal.Header>
      <Modal.Body className="p-0">
        <div className="p-3 bg-light border-bottom">
          <h6 className="mb-1 text-primary">{manageTarget?.filename}</h6>
          <p className="small text-secondary mb-0">点击下列候选项以添加或移除关联测试用例。</p>
        </div>
        <div className="overflow-auto knowledge-manage-list">
          {manageLoading ? (
            <div className="text-center py-5">
              <Spinner animation="border" variant="primary" />
              <div className="mt-2 text-muted small">正在加载候选列表...</div>
            </div>
          ) : candidates.length === 0 ? (
            <div className="text-center py-5 text-muted">
              <FaExclamationTriangle className="mb-2" />
              <p className="mb-0">暂无可操作的测试用例</p>
            </div>
          ) : (
            <div className="list-group list-group-flush">
              {candidates.map((c) => (
                <div
                  key={c.global_id}
                  className={`list-group-item d-flex align-items-center justify-content-between action-hover-bg ${c._isLinked ? 'bg-light' : ''}`}
                >
                  <div className="d-flex align-items-center gap-3 flex-grow-1">
                    <Button
                      variant={c._isLinked ? 'outline-danger' : 'outline-primary'}
                      size="sm"
                      className="rounded-circle p-0 d-flex align-items-center justify-content-center knowledge-manage-toggle"
                      onClick={() => onToggleRelation(c, !!c._isLinked)}
                      title={c._isLinked ? '移除关联' : '添加关联'}
                    >
                      {c._isLinked ? <FaTimes size={10} /> : <FaPlus size={10} />}
                    </Button>
                    <div>
                      <div className="fw-medium">{c.filename}</div>
                      <div className="small text-muted">
                        {new Date(c.created_at).toLocaleDateString()} · {(c.file_size || 0) / 1024 < 1 ? '<1KB' : `${((c.file_size || 0) / 1024).toFixed(1)}KB`}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onClose}>
          关闭
        </Button>
      </Modal.Footer>
    </Modal>
  );
}
