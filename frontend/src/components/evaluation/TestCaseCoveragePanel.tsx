import { useState, type ChangeEvent, type ClipboardEvent } from 'react';
import { Button, Col, Form, Row, Spinner } from 'react-bootstrap';
import {
  learnFromEvaluationCasePairFileRequest,
  learnFromEvaluationCasePairRequest,
} from './state/evaluationService';
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
  compareFile: File | null;
  loadedCompareFilename: string;
  onCompare: () => void;
  onInvalidateEvaluation: () => void;
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
  projectId: number | null;
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
  compareFile,
  loadedCompareFilename,
  onCompare,
  onInvalidateEvaluation,
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
  projectId,
}: Props) {
  const [learning, setLearning] = useState(false);
  const [learningMessage, setLearningMessage] = useState('');
  const hasFinalCasesInput = Boolean(evalModified.trim() || compareFile);
  const hasEvaluationResult = Boolean(evalResult && String(evalResult).trim());

  const requestLearning = (dryRun: boolean) => {
    if (compareFile && !evalModified.trim()) {
      const formData = new FormData();
      formData.append('project_id', String(projectId));
      formData.append('generated_cases', evalGenerated);
      formData.append('final_cases', '');
      if (selectedGenerationId) formData.append('generation_id', String(selectedGenerationId));
      formData.append('include_negative_samples', 'true');
      formData.append('dry_run', String(dryRun));
      formData.append('file', compareFile);
      return learnFromEvaluationCasePairFileRequest(formData);
    }
    return learnFromEvaluationCasePairRequest({
      project_id: Number(projectId),
      generated_cases: evalGenerated,
      final_cases: evalModified,
      generation_id: selectedGenerationId,
      include_negative_samples: true,
      dry_run: dryRun,
    });
  };

  const handleLearnFromEvaluation = async () => {
    if (!projectId) {
      window.alert('请先选择项目。');
      return;
    }
    if (!evalGenerated.trim()) {
      window.alert('请先提供生成的测试用例。');
      return;
    }
    if (!hasFinalCasesInput) {
      window.alert('\u8bf7\u5148\u63d0\u4f9b\u7528\u6237\u4fee\u6539\u540e\u7684\u6d4b\u8bd5\u7528\u4f8b\u7ec8\u7a3f\uff0c\u6216\u4e0a\u4f20\u7ec8\u7a3f\u6587\u4ef6\u3002');
      return;
    }

    setLearning(true);
    setLearningMessage('正在预览样本池学习结果...');
    try {
      const preview = await requestLearning(true);
      const diagnostics = preview?.derived?.diagnostics || {};
      const positiveCount = Number(diagnostics.positive_sample_count || 0);
      const positiveCandidateCount = Number(diagnostics.positive_candidate_count || positiveCount);
      const negativeCount = Number(diagnostics.negative_sample_count || 0);
      const extensionCount = Number(diagnostics.manual_business_extension_count || 0);
      const totalCount = positiveCount + negativeCount;
      if (totalCount <= 0) {
        setLearningMessage('未抽取到可写入样本池的学习样本。');
        return;
      }

      const confirmed = window.confirm(
        `本次将写入样本池：正向模式 ${positiveCount} 条，异常模式 ${negativeCount} 条，人工业务扩展 ${extensionCount} 条。\n` +
          `终稿候选 ${positiveCandidateCount} 条已先按模块/风险/场景聚合，不会逐条全量入池。\n\n` +
          '异常样本仅来自明确质量失败的 AI-only 用例；人工终稿中的需求外补充会作为正向业务扩展。\n\n是否确认写入？',
      );
      if (!confirmed) {
        setLearningMessage('已取消写入样本池。');
        return;
      }

      setLearningMessage('正在写入样本池...');
      const applied = await requestLearning(false);
      const appliedDiagnostics = applied?.derived?.diagnostics || diagnostics;
      const appliedPositiveCount = Number(appliedDiagnostics.positive_sample_count || positiveCount);
      const appliedNegativeCount = Number(appliedDiagnostics.negative_sample_count || negativeCount);
      const poolCount = Number(applied?.sample_pool_count || 0);
      setLearningMessage(
        `已写入样本池：正向模式 ${appliedPositiveCount} 条，异常模式 ${appliedNegativeCount} 条；当前样本池 ${poolCount} 条。`,
      );
    } catch (err) {
      setLearningMessage(`写入样本池失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLearning(false);
    }
  };

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
                  onChange={(e) => {
                    setEvalGenerated(e.target.value);
                    onInvalidateEvaluation();
                  }}
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
                    onInvalidateEvaluation();
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

      </div>

      <div className="evaluation-testcase-action-row d-flex flex-column flex-md-row flex-wrap gap-2">
        <Button className="btn-pro-primary flex-fill panel-card-primary-action" disabled={loading === 'eval'} onClick={onCompare}>
          {loading === 'eval' ? '评估中...' : '开始评估质量（含召回率/精准率/缺陷分析）'}
        </Button>
        <Button
          variant={hasEvaluationResult ? 'outline-primary' : 'outline-secondary'}
          className="flex-fill"
          disabled={learning || loading === 'eval' || !hasEvaluationResult || !evalGenerated.trim() || !hasFinalCasesInput}
          onClick={handleLearnFromEvaluation}
        >
          {learning ? (
            <>
              <Spinner animation="border" size="sm" className="me-2" />
              {'样本池学习中...'}
            </>
          ) : (
            '从本次评估写入样本池（先预览）'
          )}
        </Button>
        {learningMessage ? <div className="small text-muted text-center w-100">{learningMessage}</div> : null}
      </div>

      {evalResult ? (
        <div className="evaluation-testcase-report-wrap">
          <TestCaseEvaluationReport
            projectId={projectId}
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
