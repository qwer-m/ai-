import { Badge } from 'react-bootstrap';
import { FaCheckCircle, FaExclamationCircle } from 'react-icons/fa';
import type { ResponsePanelProps } from './ResponsePanel.types';

type Props = Pick<ResponsePanelProps, 'scriptTests'>;

export function ResponsePanelTestResultsTab({ scriptTests }: Props) {
  return (
    <div className="flex-grow-1 overflow-auto p-3 custom-scrollbar standard-api-panel-scroll">
      {scriptTests.length > 0 ? (
        <div className="d-flex flex-column gap-2">
          <div className="d-flex justify-content-between align-items-center mb-2 pb-2 border-bottom">
            <h6 className="text-secondary mb-0">
              测试结果：{scriptTests.filter((t) => t.passed).length}/{scriptTests.length} 通过
            </h6>
            <Badge bg={scriptTests.every((t) => t.passed) ? 'success' : 'danger'}>
              {scriptTests.every((t) => t.passed) ? 'PASS' : 'FAIL'}
            </Badge>
          </div>
          {scriptTests.map((test, index) => (
            <div
              key={`${test.name}-${index}`}
              className={`p-2 border rounded d-flex align-items-start gap-2 ${
                test.passed
                  ? 'bg-success bg-opacity-10 border-success border-opacity-25'
                  : 'bg-danger bg-opacity-10 border-danger border-opacity-25'
              }`}
            >
              <div className={`mt-1 ${test.passed ? 'text-success' : 'text-danger'}`}>
                {test.passed ? <FaCheckCircle size={14} /> : <FaExclamationCircle size={14} />}
              </div>
              <div className="flex-grow-1">
                <div className={`fw-bold small ${test.passed ? 'text-success' : 'text-danger'}`}>
                  {test.name}
                </div>
                {!test.passed && test.error && (
                  <div className="text-danger small font-monospace mt-1">{test.error}</div>
                )}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="d-flex flex-column align-items-center justify-content-center h-100 text-muted opacity-50">
          <FaCheckCircle size={48} className="mb-3" />
          <div>暂无测试结果</div>
          <div className="small mt-2">可在 Scripts 标签页编写测试脚本</div>
        </div>
      )}
    </div>
  );
}
