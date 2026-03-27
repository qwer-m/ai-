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
  hideErrorAlert?: boolean;
  hideLoadingProgress?: boolean;
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
  hideErrorAlert = false,
  hideLoadingProgress = false,
}: TestGenerationFeedbackProps) {
  return (
    <>
      <div className="position-fixed top-50 start-50 translate-middle p-3 test-generation-toast-wrap">
        <Toast show={!!toastMsg} onClose={onToastClose} delay={3000} autohide bg={toastType === 'success' ? 'success' : 'danger'}>
          <Toast.Body className="text-white text-center fw-bold">{toastType === 'success' ? '复制成功' : toastMsg?.includes('复制') ? '复制失败' : toastMsg}</Toast.Body>
        </Toast>
      </div>

      {loading && !hideLoadingProgress ? (
        <div className="col-span-12 animate-pulse">
          <ProgressBar
            animated
            now={100}
            label={<div className="test-generation-progress-label">{pollStatus}</div>}
            variant="info"
            className="rounded-1 test-generation-progress"
          />
          <div className="text-center mt-2 text-muted small">AI 正在分析需求文档，请稍候...</div>
        </div>
      ) : null}

      {error && !hideErrorAlert ? (
        <div className="col-span-12">
          <Alert variant="danger" dismissible onClose={onErrorClose} className="shadow-sm border-0 mb-0">
            <FaExclamationCircle className="me-2" /> {error}
          </Alert>
        </div>
      ) : null}

      <Modal show={showDuplicateModal} onHide={onDuplicateCancel} centered backdrop="static">
        <Modal.Header closeButton className="border-0 pb-0">
          <Modal.Title className="fw-bold text-warning">
            <FaExclamationTriangle className="me-2" />
            文档内容重复
          </Modal.Title>
        </Modal.Header>
        <Modal.Body className="pt-2">
          <p className="mb-3">检测到文档内容未变化，系统已找到历史生成结果。</p>
          <div className="bg-light p-3 rounded small text-secondary">
            <ul className="mb-0 ps-3">
              <li className="mb-1">
                <strong>加载历史：</strong>直接使用上次生成结果（推荐）。
              </li>
              <li>
                <strong>强制生成：</strong>忽略重复，重新请求 AI 生成（会增加耗时和 Token 消耗）。
              </li>
            </ul>
          </div>
        </Modal.Body>
        <Modal.Footer className="border-0 pt-0">
          <Button variant="secondary" onClick={onDuplicateCancel}>
            加载历史结果
          </Button>
          <Button variant="primary" onClick={onDuplicateConfirm}>
            强制重新生成
          </Button>
        </Modal.Footer>
      </Modal>
    </>
  );
}
