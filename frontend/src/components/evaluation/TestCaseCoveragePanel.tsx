import { Button, Col, Form, Row } from 'react-bootstrap';
import type {
  EvaluationHistoryPoint,
  EvaluationRunRecord,
  LoadingType,
  QualityReport,
} from './state/types';
import { TestCaseEvaluationReport } from './TestCaseEvaluationReport';

type Props = {
  evalGenerated: string;
  evalModified: string;
  setEvalModified: (v: string) => void;
  evalResult: QualityReport | null;
  loading: LoadingType;
  runHistory: EvaluationRunRecord[];
  selectedRunId: number | null;
  onSelectRunId: (id: number | null) => void;
  onLoadRunById: (id: number) => void;
  onFileChange: (file: File | null) => void;
  uploadedReferenceFilename: string;
  loadedReferenceFilename: string;
  onEvaluate: () => void;
  onInvalidateEvaluation: () => void;
  history: EvaluationHistoryPoint[];
};

export function TestCaseCoveragePanel({
  evalGenerated,
  evalModified,
  setEvalModified,
  evalResult,
  loading,
  runHistory,
  selectedRunId,
  onSelectRunId,
  onLoadRunById,
  onFileChange,
  uploadedReferenceFilename,
  loadedReferenceFilename,
  onEvaluate,
  onInvalidateEvaluation,
  history,
}: Props) {
  const evaluateDisabled = loading === 'eval';
  const evaluateLabel = loading === 'eval'
    ? '评测中...'
    : '开始评估质量（含召回率/精准率/缺陷分析）';

  return (
    <div className="col-span-12 evaluation-testcase-stack">
      <div className="bento-card p-4 ui-section-card evaluation-testcase-card panel-card">
        <div className="mb-3">
          <Row className="mb-3">
            <Col md={6}>
              <Form.Group className="ui-section-card p-3 h-100">
                <Form.Label className="small text-muted">生成的测试用例</Form.Label>
                <Form.Control
                  as="textarea"
                  rows={10}
                  className="input-pro bg-light"
                  value={evalGenerated}
                  readOnly
                />
              </Form.Group>
            </Col>
            <Col md={6}>
              <Form.Group className="ui-section-card p-3 h-100">
                <Form.Label className="small text-muted">用户修改后的测试用例</Form.Label>
                <Form.Control
                  as="textarea"
                  rows={10}
                  className="input-pro bg-light"
                  value={evalModified}
                  onChange={(e) => {
                    setEvalModified(e.target.value);
                    onInvalidateEvaluation();
                  }}
                  placeholder="可以直接输入文本..."
                />
              </Form.Group>
            </Col>
          </Row>

          <Row className="align-items-end">
            <Col md={6}>
              <Form.Group className="ui-section-card p-3 h-100">
                <Form.Label className="small text-muted">从历史加载</Form.Label>
                <Form.Select
                  size="sm"
                  className="input-pro bg-white"
                  value={selectedRunId ? String(selectedRunId) : ''}
                  onChange={(e) => {
                    const id = Number(e.target.value);
                    onSelectRunId(id || null);
                    if (id) onLoadRunById(id);
                  }}
                >
                  <option value="">-- 选择历史记录 --</option>
                  {runHistory.map((h) => {
                    const rawTitle = ((h.requirement_text || '').split(/[\n|]/)[0]).trim();
                    const displayTitle = rawTitle.length > 20 ? `${rawTitle.substring(0, 20)}...` : rawTitle;
                    const hasEvaluation = h.has_evaluation;
                    return (
                      <option key={h.run_id} value={h.run_id}>
                        {displayTitle} ({new Date(h.created_at).toLocaleString()}){hasEvaluation ? '' : ' - 暂无质量评估'}
                      </option>
                    );
                  })}
                </Form.Select>
              </Form.Group>
            </Col>
            <Col md={6}>
              <Form.Group className="ui-section-card p-3 h-100">
                <Form.Label className="small text-muted">或上传文件 (Excel, CSV, PNG)</Form.Label>
                <Form.Control
                  type="file"
                  size="sm"
                  accept=".xlsx,.xls,.csv,.png"
                  className="input-pro"
                  onChange={(e) => {
                    const target = e.target as HTMLInputElement;
                    onFileChange(target.files && target.files.length > 0 ? target.files[0] : null);
                    onInvalidateEvaluation();
                  }}
                />
                {uploadedReferenceFilename ? (
                  <div className="small text-muted mt-1">
                    当前上传文件：{uploadedReferenceFilename}
                  </div>
                ) : null}
                {loadedReferenceFilename ? (
                  <div className="small text-muted mt-1">
                    已从历史加载人工参考内容：{loadedReferenceFilename}
                  </div>
                ) : null}
              </Form.Group>
            </Col>
          </Row>
        </div>

      </div>

      <div className="evaluation-testcase-action-row d-flex flex-column flex-md-row flex-wrap gap-2">
        <Button className="btn-pro-primary flex-fill panel-card-primary-action" disabled={evaluateDisabled} onClick={onEvaluate}>
          {evaluateLabel}
        </Button>
      </div>

      {evalResult ? (
        <div className="evaluation-testcase-report-wrap">
          <TestCaseEvaluationReport
            evalResult={evalResult}
            history={history}
          />
        </div>
      ) : null}
    </div>
  );
}
