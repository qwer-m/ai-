import { Alert, Button, Modal, ProgressBar, Toast } from 'react-bootstrap';
import { FaExclamationCircle, FaExclamationTriangle } from 'react-icons/fa';

type TestGenerationFeedbackProps = {
  toastMsg: string | null;
  toastType: 'success' | 'error';
  onToastClose: () => void;
  loading: boolean;
  pollStatus: string;
  error: string | null;
  onErrorClose: () => void;
  showDuplicateModal: boolean;
  onDuplicateCancel: () => void | Promise<void>;
  onDuplicateConfirm: () => void;
};

export function TestGenerationFeedback({
  toastMsg,
  toastType,
  onToastClose,
  loading,
  pollStatus,
  error,
  onErrorClose,
  showDuplicateModal,
  onDuplicateCancel,
  onDuplicateConfirm,
}: TestGenerationFeedbackProps) {
  return (
    <>
      <div className="position-fixed top-50 start-50 translate-middle p-3" style={{ zIndex: 1100 }}>
        <Toast
          show={!!toastMsg}
          onClose={onToastClose}
          delay={3000}
          autohide
          bg={toastType === 'success' ? 'success' : 'danger'}
        >
          <Toast.Body className="text-white text-center fw-bold">
            {toastType === 'success'
              ? '复制成功'
              : (toastMsg?.includes('复制') ? '复制失败' : toastMsg)}
          </Toast.Body>
        </Toast>
      </div>

      {loading && (
        <div className="col-span-12 animate-pulse">
          <ProgressBar
            animated
            now={100}
            label={<div style={{ whiteSpace: 'normal', wordBreak: 'break-all', fontSize: '0.85rem', lineHeight: '1.2' }}>{pollStatus}</div>}
            variant="info"
            style={{ height: 'auto', minHeight: '30px' }}
            className="rounded-1"
          />
          <div className="text-center mt-2 text-muted small">AI 正在深度分析需求文档，请稍候...</div>
        </div>
      )}

      {error && (
        <div className="col-span-12">
          <Alert variant="danger" dismissible onClose={onErrorClose} className="shadow-sm border-0 mb-0">
            <FaExclamationCircle className="me-2" /> {error}
          </Alert>
        </div>
      )}

      <Modal show={showDuplicateModal} onHide={onDuplicateCancel} centered backdrop="static">
        <Modal.Header closeButton className="border-0 pb-0">
          <Modal.Title className="fw-bold text-warning">
            <FaExclamationTriangle className="me-2" />
            文档内容重复
          </Modal.Title>
        </Modal.Header>
        <Modal.Body className="pt-2">
          <p className="mb-3">检测到该文档内容未发生变化。系统已为您找到历史生成结果。</p>
          <div className="bg-light p-3 rounded small text-secondary">
            <ul className="mb-0 ps-3">
              <li className="mb-1">
                <strong>加载历史：</strong> 直接使用上次生成的结果（推荐，无需等待）。
              </li>
              <li>
                <strong>强制生成：</strong> 忽略重复，强制 AI 重新分析并生成（耗时且消耗 Token）。
              </li>
            </ul>
          </div>
        </Modal.Body>
        <Modal.Footer className="border-0 pt-0">
          <Button variant="secondary" onClick={onDuplicateCancel}>加载历史结果</Button>
          <Button variant="primary" onClick={onDuplicateConfirm}>强制重新生成</Button>
        </Modal.Footer>
      </Modal>
    </>
  );
}
