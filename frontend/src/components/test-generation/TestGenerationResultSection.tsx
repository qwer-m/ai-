import classNames from 'classnames';
import { Badge, Button } from 'react-bootstrap';
import { FaCheckCircle, FaCopy, FaFileCode } from 'react-icons/fa';
import type { TestGenerationMode } from './types';

type TestGenerationResultSectionProps = {
  mode: TestGenerationMode;
  result: any;
  streamingContent: string;
  loading: boolean;
  statsCount: number;
  onCopy: () => void;
};

export function TestGenerationResultSection({
  mode,
  result,
  streamingContent,
  loading,
  statsCount,
  onCopy,
}: TestGenerationResultSectionProps) {
  return (
    <div className="bento-card col-span-12 p-0 overflow-hidden d-flex flex-column" style={{ height: '600px', maxHeight: '600px' }}>
      <div className="bg-light border-bottom d-flex justify-content-between align-items-center px-4 py-3">
        <h6 className="mb-0 fw-bold d-flex align-items-center gap-2">
          <FaCheckCircle className={result ? 'text-success' : 'text-muted'} /> 生成结果
        </h6>
        <div className="d-flex align-items-center gap-2">
          {result && (
            <Badge bg="success" className="d-flex align-items-center gap-1">
              总计 {statsCount} 条
            </Badge>
          )}
          {streamingContent && (
            <Badge bg="primary" className="d-flex align-items-center gap-1">
              {loading ? '生成中...' : '最新批次'}
            </Badge>
          )}
          {streamingContent && (
            <Button
              variant="link"
              size="sm"
              className="p-0 text-decoration-none d-flex align-items-center gap-1 text-primary"
              onClick={onCopy}
              title="复制内容"
            >
              <FaCopy /> 复制
            </Button>
          )}
        </div>
      </div>

      <div className="flex-grow-1 d-flex flex-column" style={{ minHeight: 0 }}>
        <div className={classNames('d-flex flex-column flex-grow-1 transition-all')} style={{ minWidth: 0, minHeight: 0 }}>
          <div className="px-4 py-2 border-bottom small fw-bold text-secondary flex-shrink-0" style={{ backgroundColor: '#f8f9fa' }}>
            {streamingContent ? '合并后结果 / 历史结果' : '生成结果'}
          </div>
          <div className="flex-grow-1 overflow-auto p-4 font-monospace" style={{ whiteSpace: 'pre-wrap', overflowY: 'auto' }}>
            {mode === 'text' ? (
              result ? (
                JSON.stringify(result, null, 2)
              ) : (
                <div className="text-center text-muted mt-5 py-5">
                  <div className="mb-3 opacity-25"><FaFileCode size={48} /></div>
                  暂无历史结果
                </div>
              )
            ) : result ? (
              JSON.stringify(result, null, 2)
            ) : (
              <div className="text-center text-muted mt-5 py-5">
                <div className="mb-3 opacity-25"><FaFileCode size={48} /></div>
                暂无历史结果
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
