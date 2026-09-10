import { Button, Spinner } from 'react-bootstrap';
import { FaRobot } from 'react-icons/fa';
import type { ResponsePanelProps } from './ResponsePanel.types';

type Props = Pick<
  ResponsePanelProps,
  'aiAnalysis' | 'handleAnalyzeResponse' | 'isAnalyzing'
>;

export function ResponsePanelReportTab({
  aiAnalysis,
  handleAnalyzeResponse,
  isAnalyzing,
}: Props) {
  return (
    <div className="flex-grow-1 overflow-auto bg-light p-3 standard-api-report-tab-wrap">
      {aiAnalysis ? (
        <div className="p-3 border rounded standard-api-report-card">
          <h6 className="border-bottom pb-2 mb-3">AI 分析报告</h6>
          <pre className="mb-0 font-monospace small text-dark standard-api-report-pre">
            {aiAnalysis}
          </pre>
        </div>
      ) : (
        <div className="d-flex flex-column align-items-center justify-content-center h-100">
          <FaRobot size={48} className="mb-3 text-primary opacity-50" />
          <h5 className="mb-3">AI 智能分析</h5>
          <p className="text-muted mb-4 text-center standard-api-report-tip">
            使用 AI 分析当前响应数据，识别潜在问题与风险，并给出优化建议。
          </p>
          <Button variant="primary" onClick={handleAnalyzeResponse} disabled={isAnalyzing}>
            {isAnalyzing ? (
              <>
                <Spinner size="sm" animation="border" className="me-2" />
                分析中...
              </>
            ) : (
              '开始智能分析'
            )}
          </Button>
        </div>
      )}
    </div>
  );
}
