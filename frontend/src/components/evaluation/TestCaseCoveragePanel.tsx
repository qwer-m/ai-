import type { ChangeEvent, ClipboardEvent } from 'react';
import { Button, Col, Form, Row } from 'react-bootstrap';
import { FaClipboardCheck } from 'react-icons/fa';
import type { DefectAnalysis, LoadingType } from './state/types';
import { TestCaseEvaluationReport } from './TestCaseEvaluationReport';

type Props = {
  evalGenerated: string;
  setEvalGenerated: (v: string) => void;
  evalModified: string;
  setEvalModified: (v: string) => void;
  evalResult: string | null;
  loading: LoadingType;
  genHistory: any[];
  selectedGenerationId: number | null;
  onSelectGenerationId: (id: number | null) => void;
  onLoadGenerationById: (id: number) => void;
  onFileChange: (file: File | null) => void;
  uploadedCompareFilename: string;
  loadedCompareFilename: string;
  onCompare: () => void;
  history: any[];
  showSupplement: boolean;
  setShowSupplement: (next: boolean) => void;
  supplementText: string;
  setSupplementText: (v: string) => void;
  supplementImages: File[];
  setSupplementImages: (files: File[]) => void;
  savedDocId: number | null;
  lastSavedContent: string;
  handleSupplementPaste: (e: ClipboardEvent<HTMLTextAreaElement>) => void;
  handleSupplementFilesChange: (e: ChangeEvent<HTMLInputElement>) => void;
  onSaveKnowledge: (defectAnalysis: DefectAnalysis) => Promise<void>;
  savingKnowledge: boolean;
};

export function TestCaseCoveragePanel({
  evalGenerated,
  setEvalGenerated,
  evalModified,
  setEvalModified,
  evalResult,
  loading,
  genHistory,
  selectedGenerationId,
  onSelectGenerationId,
  onLoadGenerationById,
  onFileChange,
  uploadedCompareFilename,
  loadedCompareFilename,
  onCompare,
  history,
  showSupplement,
  setShowSupplement,
  supplementText,
  setSupplementText,
  supplementImages,
  setSupplementImages,
  savedDocId,
  lastSavedContent,
  handleSupplementPaste,
  handleSupplementFilesChange,
  onSaveKnowledge,
  savingKnowledge,
}: Props) {
  return (
    <div className="bento-card col-span-12 p-4 d-flex flex-column ui-section-card evaluation-testcase-card">
      <div className="d-flex align-items-center gap-2 mb-3 text-secondary">
        <FaClipboardCheck />
        <span className="fw-bold">测试用例质量评估</span>
      </div>

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
                onChange={(e) => setEvalGenerated(e.target.value)}
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
                onChange={(e) => setEvalModified(e.target.value)}
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
                value={selectedGenerationId ? String(selectedGenerationId) : ''}
                onChange={(e) => {
                  const id = Number(e.target.value);
                  onSelectGenerationId(id || null);
                  if (id) onLoadGenerationById(id);
                }}
              >
                <option value="">-- 选择历史记录 --</option>
                {genHistory.map((h: any) => {
                  const rawTitle = (h.history_title || (h.requirement_text || '').split(/[\n|]/)[0]).trim();
                  const displayTitle = rawTitle.length > 20 ? `${rawTitle.substring(0, 20)}...` : rawTitle;
                  const hasComparison = h?.has_comparison !== false;
                  return (
                    <option key={h.id} value={h.id}>
                      {displayTitle} ({new Date(h.created_at).toLocaleString()}){hasComparison ? '' : ' - 暂无质量评估'}
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
                }}
              />
              {uploadedCompareFilename ? (
                <div className="small text-muted mt-1">
                  当前上传文件：{uploadedCompareFilename}
                </div>
              ) : null}
              {loadedCompareFilename ? (
                <div className="small text-muted mt-1">
                  已从历史加载对比文件内容：{loadedCompareFilename}
                </div>
              ) : null}
            </Form.Group>
          </Col>
        </Row>
      </div>

      <Button className="btn-pro-primary w-100 mt-auto" disabled={loading === 'eval'} onClick={onCompare}>
        {loading === 'eval' ? '评估中...' : '开始评估质量（含召回率/精准率/缺陷分析）'}
      </Button>

      {evalResult ? (
        <div className="mt-3 alert alert-light border small automation-eval-output">
          <TestCaseEvaluationReport
            evalResult={evalResult}
            history={history}
            showSupplement={showSupplement}
            setShowSupplement={setShowSupplement}
            supplementText={supplementText}
            setSupplementText={setSupplementText}
            supplementImages={supplementImages}
            setSupplementImages={setSupplementImages}
            savedDocId={savedDocId}
            lastSavedContent={lastSavedContent}
            handleSupplementPaste={handleSupplementPaste}
            handleSupplementFilesChange={handleSupplementFilesChange}
            handleSaveKnowledge={onSaveKnowledge}
            savingKnowledge={savingKnowledge}
          />
        </div>
      ) : null}
    </div>
  );
}
