import { Button, Col, Form, Row } from 'react-bootstrap';
import type {
  EvaluationHistoryPoint,
  EvaluationRunRecord,
  LoadingType,
  QualityReport,
  TestEvaluationArtifact,
} from './state/types';
import { TestCaseEvaluationReport } from './TestCaseEvaluationReport';
import { TestCaseFeedbackPanel } from './TestCaseFeedbackPanel';

type Props = {
  evalGenerated: string;
  evalModified: string;
  setEvalModified: (v: string) => void;
  evalResult: QualityReport | null;
  loading: LoadingType;
  runHistory: EvaluationRunRecord[];
  baselineText: string;
  onBaselineChange: (text: string) => void;
  selectedRunId: number | null;
  onSelectRunId: (id: number | null) => void;
  onLoadRunById: (id: number) => void;
  onFileChange: (file: File | null) => void;
  uploadedReferenceFilename: string;
  loadedReferenceFilename: string;
  onEvaluate: () => void;
  history: EvaluationHistoryPoint[];
  projectId: number | null;
  verifiedEvaluation: TestEvaluationArtifact | null;
  sourceLoaded: boolean;
};

export function TestCaseCoveragePanel({
  evalGenerated,
  evalModified,
  setEvalModified,
  evalResult,
  loading,
  runHistory,
  baselineText, onBaselineChange,
  selectedRunId,
  onSelectRunId,
  onLoadRunById,
  onFileChange,
  uploadedReferenceFilename,
  loadedReferenceFilename,
  onEvaluate,
  history,
  projectId,
  verifiedEvaluation,
  sourceLoaded,
}: Props) {
  const evaluateDisabled = loading === 'eval' || !sourceLoaded;
  const evaluateLabel = loading === 'eval'
    ? '评测中...'
    : '开始评估质量（含召回率/精准率/缺陷分析）';

  return (
    <div className="col-span-12 evaluation-testcase-stack">
      <div className="bento-card p-4 ui-section-card evaluation-testcase-card panel-card">
        <div className="mb-3">
          <Row className="mb-3">
            <Col md={6}>
              <Form.Group controlId="evaluation-generated-cases" className="ui-section-card p-3 h-100">
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
              <Form.Group controlId="evaluation-reference-text" className="ui-section-card p-3 h-100">
                <Form.Label className="small text-muted">用户修改后的测试用例</Form.Label>
                <Form.Control
                  as="textarea"
                  rows={10}
                  className="input-pro bg-light"
                  value={evalModified}
                  disabled={loading === 'eval'}
                  onChange={(e) => setEvalModified(e.target.value)}
                  placeholder="可以直接输入文本..."
                />
              </Form.Group>
            </Col>
          </Row>

          <Row className="align-items-end">
            <Col md={6}>
              <Form.Group controlId="evaluation-source-run" className="ui-section-card p-3 h-100">
                <Form.Label className="small text-muted">从历史加载</Form.Label>
                <Form.Select
                  size="sm"
                  className="input-pro bg-white"
                  value={selectedRunId ? String(selectedRunId) : ''}
                  onChange={(e) => {
                    const id = Number(e.target.value);
                    if (id) onLoadRunById(id);
                    else onSelectRunId(null);
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
              <Form.Group controlId="evaluation-reference-file" className="ui-section-card p-3 h-100">
                <Form.Label className="small text-muted">或上传人工用例文件</Form.Label>
                <Form.Control
                  type="file"
                  size="sm"
                  className="input-pro"
                  disabled={loading === 'eval'}
                  key={uploadedReferenceFilename || 'no-reference-file'}
                  onChange={(e) => {
                    const target = e.target as HTMLInputElement;
                    onFileChange(target.files && target.files.length > 0 ? target.files[0] : null);
                  }}
                />
                <div className="small text-muted mt-1">
                  支持后端已配置的文本、表格、PDF 和图片解析；文件与上方文本互斥。
                </div>
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
          <Form.Group controlId="evaluation-baseline-groups">
            <Form.Label className="small">正文核对清单（可选，每行一组）</Form.Label>
            <Form.Control as="textarea" rows={2} value={baselineText} disabled={loading === 'eval'}
              placeholder="留空时由评审核对完整正文并分组"
              onChange={e => onBaselineChange(e.target.value)} />
          </Form.Group>
        </div>

      </div>

      <div className="evaluation-testcase-action-row d-flex flex-column flex-md-row flex-wrap gap-2">
        <Button className="btn-pro-primary flex-fill panel-card-primary-action" disabled={evaluateDisabled}
          aria-describedby="evaluation-submit-help" onClick={onEvaluate}>
          {evaluateLabel}
        </Button>
      </div>
      <p className="small text-muted mb-0" id="evaluation-submit-help" role="status">
        {loading === 'eval' ? '评测正在执行，请等待结果。'
          : !sourceLoaded ? '请先从历史记录加载当前项目的生成用例。'
            : '填写人工定稿用例或上传文件后进行对比；输入文本与上传文件互斥。'}
      </p>

      {evalResult ? (
        <div className="evaluation-testcase-report-wrap">
          <TestCaseEvaluationReport
            evalResult={evalResult}
            history={history}
          />
          {verifiedEvaluation && projectId ? (
            <TestCaseFeedbackPanel
              key={`${projectId}-${verifiedEvaluation.source_run_id}-${verifiedEvaluation.evaluation_run_id}`}
              projectId={projectId}
              sourceRunId={verifiedEvaluation.source_run_id}
              evaluationRunId={verifiedEvaluation.evaluation_run_id}
            />
          ) : (
            <p className="small text-muted mt-3" role="status">
              当前报告仅为本地展示草稿。请从服务器加载对应生成记录后再确认项目反馈。
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}
