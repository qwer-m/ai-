import { useState, type ChangeEvent, type ClipboardEvent } from 'react';
import { Button, Form, Modal, ProgressBar, Spinner } from 'react-bootstrap';
import {
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LineElement,
  LinearScale,
  PointElement,
  Title,
  Tooltip,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import { FaPlus, FaRobot } from 'react-icons/fa';
import {
  applyLearningCandidatesRequest,
  buildLearningCandidatesFromEvaluationRequest,
  parseQualityReport,
} from './state/evaluationService';
import type { DefectAnalysis } from './state/types';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend);

type Props = {
  projectId: number | null;
  evalResult: string;
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
  handleSaveKnowledge: (defectAnalysis: DefectAnalysis) => Promise<void>;
  savingKnowledge: boolean;
};

export function TestCaseEvaluationReport({
  projectId,
  evalResult,
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
  handleSaveKnowledge,
  savingKnowledge,
}: Props) {
  const [learningCandidates, setLearningCandidates] = useState<any[]>([]);
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<Set<string>>(new Set());
  const [appliedCandidateIds, setAppliedCandidateIds] = useState<Set<string>>(new Set());
  const [candidateLoading, setCandidateLoading] = useState(false);
  const [candidateMessage, setCandidateMessage] = useState('');
  const report = parseQualityReport(evalResult);
  if (!report) {
    return <div className="evaluation-prewrap">{evalResult}</div>;
  }

  const {
    metrics,
    defectAnalysis,
    requirementBaseline,
    summary,
    analysisStatus,
    isFinalEvaluation,
    comparisonId,
    progress,
    partialChunkResults = [],
  } = report;
  const isRunning = analysisStatus === 'running';
  const isModelFailed = analysisStatus === 'model_failed';
  const isPartialCompleted = analysisStatus === 'partial_completed';
  const isIncomplete = isFinalEvaluation === false && !isRunning;
  const canUseDefectLearning = isFinalEvaluation !== false && !isRunning && !isModelFailed;
  const totalChunks = Number(progress?.total_chunks || 0);
  const completedChunks = Number(progress?.completed_chunks || 0);
  const failedChunkCount = Number(progress?.failed_chunks || 0);
  const retryingChunks = Array.isArray(progress?.retrying_chunks) ? progress.retrying_chunks : [];
  const progressPercent = totalChunks > 0
    ? Math.min(100, Math.max(0, Math.round((completedChunks / totalChunks) * 100)))
    : 0;
  const partialPreview = partialChunkResults.slice(-5);
  const phaseLabelMap: Record<string, string> = {
    chunking: '分片评估中',
    single_pass_evaluating: '全量平衡评估中',
    retrying: '分片重试中',
    splitting: '拆分重试中',
    aggregating: '汇总评估中',
    aggregate_retrying: '汇总重试中',
    aggregate_failed: '汇总失败',
    failed: '分片失败',
    partial_completed: '部分完成',
    chunk_failed_continuing: '分片失败，继续后续分片',
    stopped_after_repeated_model_failures: '连续失败，已停止重试',
  };
  const phaseLabel = progress?.phase ? (phaseLabelMap[progress.phase] || progress.phase) : '';
  const statusTitle = isRunning
    ? '模型质量评估后台执行中'
    : isPartialCompleted
      ? '模型质量评估部分完成'
      : isModelFailed || isIncomplete
      ? '模型质量评估未完成'
      : '模型质量评估已完成';
  const statusClass = isRunning
    ? 'bg-light'
    : isModelFailed || isIncomplete
      ? 'bg-warning-subtle'
      : 'bg-white';
  const baselineHeuristic = requirementBaseline?.heuristic;
  const generatedCoverageRate = typeof requirementBaseline?.generated_coverage_rate === 'number'
    ? requirementBaseline.generated_coverage_rate
    : baselineHeuristic?.generated_coverage_rate;
  const modifiedCoverageRate = typeof requirementBaseline?.modified_coverage_rate === 'number'
    ? requirementBaseline.modified_coverage_rate
    : baselineHeuristic?.modified_coverage_rate;
  const requirementPoints = requirementBaseline?.requirement_points || baselineHeuristic?.requirement_points || [];
  const aiRequirementGaps = requirementBaseline?.ai_requirement_gaps || requirementBaseline?.missing_in_generated || baselineHeuristic?.missing_in_generated || [];
  const humanRequirementGaps = requirementBaseline?.human_requirement_gaps || requirementBaseline?.missing_in_modified || baselineHeuristic?.missing_in_modified || [];
  const bothMissingPoints = requirementBaseline?.both_missing_points || baselineHeuristic?.both_missing_points || [];
  const aiUnanchoredPoints = requirementBaseline?.ai_unanchored_points || [];
  const humanAddedValue = requirementBaseline?.human_added_value || [];
  const disableConfirm =
    (!supplementText.trim() && supplementImages.length === 0)
    || (savedDocId !== null && supplementText === lastSavedContent && supplementImages.length === 0);
  const selectedCandidates = learningCandidates.filter((item) => selectedCandidateIds.has(String(item.id)));

  const buildLearningCandidates = async () => {
    if (!projectId) {
      window.alert('请先选择项目。');
      return;
    }
    setCandidateLoading(true);
    setCandidateMessage('正在从缺陷归因生成学习候选...');
    try {
      const payload = await buildLearningCandidatesFromEvaluationRequest({
        project_id: projectId,
        evaluation_result: evalResult,
      });
      const candidates = Array.isArray(payload?.candidates) ? payload.candidates : [];
      setLearningCandidates(candidates);
      setAppliedCandidateIds(new Set());
      setSelectedCandidateIds(
        new Set(
          candidates
            .filter((item: any) => item?.selected_by_default)
            .map((item: any) => String(item.id)),
        ),
      );
      const defaultCount = Number(payload?.diagnostics?.selected_by_default_count || 0);
      setCandidateMessage(`已生成 ${candidates.length} 个候选，默认选中 ${defaultCount} 个。`);
    } catch (err) {
      setCandidateMessage(`生成候选失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setCandidateLoading(false);
    }
  };

  const applySelectedCandidates = async () => {
    if (!projectId) {
      window.alert('请先选择项目。');
      return;
    }
    if (selectedCandidates.length === 0) {
      window.alert('请至少选择一个候选。');
      return;
    }
    setCandidateLoading(true);
    setCandidateMessage('正在预览候选写入...');
    try {
      const preview = await applyLearningCandidatesRequest({
        project_id: projectId,
        candidates: selectedCandidates,
        dry_run: true,
      });
      const diagnostics = preview?.derived?.diagnostics || {};
      const positiveCount = Number(diagnostics.positive_sample_count || 0);
      const negativeCount = Number(diagnostics.negative_sample_count || 0);
      const confirmed = window.confirm(
        `将写入现有样本池：正向 ${positiveCount} 条，异常 ${negativeCount} 条。\n\n这些候选来自本次质量评估缺陷归因，只有本次选中的项会写入。是否确认？`,
      );
      if (!confirmed) {
        setCandidateMessage('已取消写入。');
        return;
      }
      setCandidateMessage('正在写入现有样本池...');
      const applied = await applyLearningCandidatesRequest({
        project_id: projectId,
        candidates: selectedCandidates,
        dry_run: false,
      });
      const appliedDiagnostics = applied?.derived?.diagnostics || diagnostics;
      const appliedIds = new Set(selectedCandidates.map((item: any) => String(item.id)));
      setAppliedCandidateIds((prev) => {
        const next = new Set(prev);
        appliedIds.forEach((id) => next.add(id));
        return next;
      });
      setSelectedCandidateIds((prev) => {
        const next = new Set(prev);
        appliedIds.forEach((id) => next.delete(id));
        return next;
      });
      setCandidateMessage(
        `已写入样本池：正向 ${Number(appliedDiagnostics.positive_sample_count || 0)} 条，异常 ${Number(appliedDiagnostics.negative_sample_count || 0)} 条；当前样本池 ${Number(applied?.sample_pool_count || 0)} 条。`,
      );
    } catch (err) {
      setCandidateMessage(`写入候选失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setCandidateLoading(false);
    }
  };

  return (
    <div className="evaluation-report-panel panel-card">
      <h6 className="border-bottom pb-2 mb-3 evaluation-report-title">质量评估报告</h6>

      <div className="mb-4 evaluation-chart-wrap evaluation-report-chart-wrap">
        <Line
          data={{
            labels: history.length > 0 ? history.map((h) => h.created_at) : ['Current'],
            datasets: [
              {
                label: 'Precision',
                data: history.length > 0 ? history.map((h) => h.precision) : [metrics.precision],
                borderColor: '#0d6efd',
                tension: 0.1,
                pointRadius: 3,
                borderWidth: 2,
                hoverBorderWidth: 4,
              },
              {
                label: 'Recall',
                data: history.length > 0 ? history.map((h) => h.recall) : [metrics.recall],
                borderColor: '#198754',
                tension: 0.1,
                pointRadius: 3,
                borderWidth: 2,
                hoverBorderWidth: 4,
              },
              {
                label: 'F1 分数',
                data: history.length > 0 ? history.map((h) => h.f1_score) : [metrics.f1_score],
                borderColor: '#6f42c1',
                tension: 0.1,
                pointRadius: 3,
                borderWidth: 2,
                hoverBorderWidth: 4,
              },
              {
                label: '相似度',
                data: history.length > 0 ? history.map((h) => h.semantic_similarity) : [metrics.semantic_similarity],
                borderColor: '#fd7e14',
                tension: 0.1,
                pointRadius: 3,
                borderWidth: 2,
                hoverBorderWidth: 4,
              },
            ],
          }}
          options={{
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
              mode: 'nearest',
              intersect: true,
              axis: 'x',
            },
            plugins: {
              legend: {
                display: true,
                position: 'top',
                labels: { boxWidth: 10, usePointStyle: true, padding: 10, font: { size: 10 } },
              },
              title: { display: true, text: '质量评估历史趋势' },
              tooltip: {
                mode: 'index',
                intersect: false,
              },
            },
            scales: {
              y: {
                type: 'linear',
                display: true,
                position: 'left',
                title: { display: true, text: '评分 (0-1)' },
                min: 0,
                max: 1,
                beginAtZero: true,
                grid: { drawOnChartArea: true },
              },
            },
          }}
        />
      </div>

      <div className="d-flex gap-2 mb-3 text-center evaluation-report-kpi-grid">
        <div className="p-2 bg-white border rounded flex-fill evaluation-report-kpi">
          <div className="fw-bold text-primary">{typeof metrics.precision === 'number' ? metrics.precision.toFixed(2) : '-'}</div>
          <div className="x-small text-muted">精准率</div>
        </div>
        <div className="p-2 bg-white border rounded flex-fill evaluation-report-kpi">
          <div className="fw-bold text-primary">{typeof metrics.recall === 'number' ? metrics.recall.toFixed(2) : '-'}</div>
          <div className="x-small text-muted">召回率</div>
        </div>
        <div className="p-2 bg-white border rounded flex-fill evaluation-report-kpi">
          <div className="fw-bold text-primary">{typeof metrics.f1_score === 'number' ? metrics.f1_score.toFixed(2) : '-'}</div>
          <div className="x-small text-muted">F1 分数</div>
        </div>
        <div className="p-2 bg-white border rounded flex-fill evaluation-report-kpi">
          <div className="fw-bold text-primary">
            {typeof metrics.semantic_similarity === 'number' ? metrics.semantic_similarity.toFixed(2) : '-'}
          </div>
          <div className="x-small text-muted">语义相似度</div>
        </div>
      </div>

      <div className="d-flex align-items-center justify-content-between mb-2 evaluation-report-defect-head">
        <strong>缺陷归因分析:</strong>
        <Button variant="outline-secondary" size="sm" className="py-0 px-2 evaluation-report-supplement-btn" onClick={() => setShowSupplement(true)}>
          <FaPlus className="me-1" /> 用户补充描述
        </Button>
      </div>

      <Modal
        show={showSupplement}
        onHide={() => {
          if (!savingKnowledge) setShowSupplement(false);
        }}
        centered
        size="lg"
        dialogClassName="evaluation-report-modal"
      >
        <Modal.Header closeButton={!savingKnowledge}>
          <Modal.Title>用户补充描述</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          <Form.Group className="mb-2">
            <Form.Control
              as="textarea"
              rows={4}
              placeholder="请输入补充描述..."
              value={supplementText}
              onChange={(e) => setSupplementText(e.target.value)}
              onPaste={handleSupplementPaste}
            />
          </Form.Group>
          <Form.Group className="mb-2">
            <Form.Label className="small text-muted">导入图片（最多 10 张）</Form.Label>
            <Form.Control
              type="file"
              size="sm"
              accept="image/*"
              multiple
              onChange={handleSupplementFilesChange}
            />
          </Form.Group>
          {supplementImages.length > 0 ? (
            <div className="mt-2 d-flex flex-wrap gap-2">
              {supplementImages.map((f, idx) => (
                <div key={idx} className="border rounded p-1 small bg-white">
                  <span className="text-muted">{f.name}</span>
                </div>
              ))}
            </div>
          ) : null}
        </Modal.Body>
        <Modal.Footer>
          <Button
            variant="secondary"
            size="sm"
            disabled={savingKnowledge}
            onClick={() => {
              setShowSupplement(false);
              setSupplementText('');
              setSupplementImages([]);
            }}
          >
            取消
          </Button>
          <Button
            variant="primary"
            size="sm"
            disabled={disableConfirm || savingKnowledge}
            onClick={() => {
              void handleSaveKnowledge(defectAnalysis);
            }}
          >
            {savingKnowledge ? (
              <>
                <Spinner animation="border" size="sm" className="me-2" />
                录入中...
              </>
            ) : '确定'}
          </Button>
        </Modal.Footer>
      </Modal>

      {analysisStatus ? (
        <div className={`mb-3 p-2 border rounded ${statusClass} evaluation-report-status`}>
          <div className="d-flex align-items-center gap-2">
            {isRunning ? <Spinner animation="border" size="sm" /> : null}
            <strong className="small">
              {statusTitle}
            </strong>
            {comparisonId ? <span className="x-small text-muted">comparison_id={comparisonId}</span> : null}
          </div>
          <div className="small text-muted mt-1">
            {isRunning ? '页面会自动刷新结果；当前不要重复点击开始评估。' : summary}
          </div>
          {progress ? (
            <div className="mt-2">
              {totalChunks > 0 ? (
                <>
                  <div className="d-flex align-items-center justify-content-between gap-2 x-small text-muted">
                    <span>已加载完成分片 {completedChunks}/{totalChunks}</span>
                    {phaseLabel ? <span>{phaseLabel}</span> : null}
                  </div>
                  <ProgressBar now={progressPercent} className="mt-1" style={{ height: 6 }} />
                </>
              ) : null}
              {retryingChunks.length > 0 ? (
                <div className="x-small text-warning mt-1">
                  正在重试：{retryingChunks.map((item) => `${item.chunk_index || '-'}(${item.attempt || 0}/${item.max_attempts || 0})`).join('、')}
                </div>
              ) : null}
              {failedChunkCount > 0 ? (
                <div className="x-small text-danger mt-1">失败分片：{failedChunkCount} 个，已完成分片仍保留展示。</div>
              ) : null}
            </div>
          ) : null}
          {partialPreview.length > 0 ? (
            <div className="mt-2 pt-2 border-top">
              <div className="x-small text-muted mb-1">已完成分片预览（最近 {partialPreview.length} 个）</div>
              <div className="d-flex flex-column gap-1">
                {partialPreview.map((chunk, index) => {
                  const defect = chunk.defect_analysis || {};
                  const defectCount =
                    Number(defect.missing_points?.length || 0)
                    + Number(defect.hallucinations?.length || 0)
                    + Number(defect.modifications?.length || 0);
                  return (
                    <div key={`${chunk.chunk_index ?? index}`} className="x-small text-secondary">
                      <span className="fw-semibold">分片 {chunk.chunk_index ?? '-'}</span>
                      <span className="ms-2">{chunk.summary || `已完成 ${chunk.chunk_unit_count || 0} 个对比单元`}</span>
                      {defectCount > 0 ? <span className="ms-2 text-muted">缺陷项 {defectCount}</span> : null}
                      {Number(chunk.retry_attempts || 0) > 0 ? <span className="ms-2 text-warning">已重试 {chunk.retry_attempts} 次</span> : null}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="mb-3 p-2 border rounded bg-white evaluation-report-learning-candidates">
        <div className="d-flex align-items-center justify-content-between gap-2 mb-2">
          <div>
            <strong className="small">缺陷归因学习候选</strong>
            <div className="x-small text-muted">从本次质量评估的遗漏、幻觉、逻辑修正中提取，确认后写入现有样本池。</div>
          </div>
          <div className="d-flex gap-2">
            <Button variant="outline-primary" size="sm" disabled={candidateLoading || !projectId || !canUseDefectLearning} onClick={buildLearningCandidates}>
              {candidateLoading && learningCandidates.length === 0 ? <Spinner animation="border" size="sm" className="me-1" /> : null}
              生成候选
            </Button>
            <Button variant="primary" size="sm" disabled={candidateLoading || selectedCandidates.length === 0 || !canUseDefectLearning} onClick={applySelectedCandidates}>
              {candidateLoading && learningCandidates.length > 0 ? <Spinner animation="border" size="sm" className="me-1" /> : null}
              写入选中
            </Button>
          </div>
        </div>
        {learningCandidates.length > 0 ? (
          <div className="d-flex flex-column gap-1">
            {learningCandidates.map((item: any) => {
              const id = String(item.id);
              const applied = appliedCandidateIds.has(id);
              const checked = selectedCandidateIds.has(id);
              return (
                <Form.Check
                  key={id}
                  type="checkbox"
                  id={`learning-candidate-${id}`}
                  checked={checked}
                  disabled={applied || candidateLoading}
                  onChange={(e) => {
                    const nextChecked = e.currentTarget.checked;
                    setSelectedCandidateIds((prev) => {
                      const next = new Set(prev);
                      if (nextChecked) next.add(id);
                      else next.delete(id);
                      return next;
                    });
                  }}
                  label={(
                    <span className="small">
                      <span className={item.candidate_type === 'negative_pattern' ? 'text-danger' : 'text-primary'}>
                        {item.candidate_type}
                      </span>
                      <span className="text-muted ms-2">{item.source_field}</span>
                      <span className="ms-2">{item.text}</span>
                      <span className="text-muted ms-2">confidence {Number(item.confidence || 0).toFixed(2)}</span>
                      {applied ? <span className="badge bg-success ms-2">已写入</span> : null}
                    </span>
                  )}
                />
              );
            })}
          </div>
        ) : null}
        {!canUseDefectLearning ? (
          <div className="small text-muted mt-2">正式质量评估完成后才能生成和写入学习候选。</div>
        ) : candidateMessage ? <div className="small text-muted mt-2">{candidateMessage}</div> : null}
      </div>

      {requirementBaseline ? (
        <div className="mb-3 p-2 border rounded bg-white evaluation-report-requirement-baseline">
          <div className="d-flex align-items-center justify-content-between gap-2 mb-2">
            <div>
              <strong className="small">需求基准评估</strong>
              <div className="x-small text-muted">以需求文档为锚点，同时检查 AI 生成用例和人工最终用例。</div>
            </div>
            <div className="d-flex gap-2 text-center">
              <div className="px-2 py-1 border rounded bg-light">
                <div className="fw-bold text-primary">{typeof generatedCoverageRate === 'number' ? generatedCoverageRate.toFixed(2) : '-'}</div>
                <div className="x-small text-muted">AI覆盖</div>
              </div>
              <div className="px-2 py-1 border rounded bg-light">
                <div className="fw-bold text-primary">{typeof modifiedCoverageRate === 'number' ? modifiedCoverageRate.toFixed(2) : '-'}</div>
                <div className="x-small text-muted">人工覆盖</div>
              </div>
            </div>
          </div>
          {requirementBaseline.summary ? <div className="small text-secondary mb-2">{requirementBaseline.summary}</div> : null}
          {requirementPoints.length ? (
            <div className="small text-muted mb-2">识别需求点：{requirementPoints.length} 个</div>
          ) : null}
          {humanAddedValue.length ? (
            <div className="mb-2 evaluation-report-defect-group">
              <span className="badge bg-success text-white me-2">人工增益</span>
              <ul className="mb-1 ps-3 mt-1 text-muted">
                {humanAddedValue.map((item, index) => <li key={index}>{item}</li>)}
              </ul>
            </div>
          ) : null}
          {aiRequirementGaps.length ? (
            <div className="mb-2 evaluation-report-defect-group">
              <span className="badge bg-warning text-dark me-2">AI需求遗漏</span>
              <ul className="mb-1 ps-3 mt-1 text-muted">
                {aiRequirementGaps.map((item, index) => <li key={index}>{item}</li>)}
              </ul>
            </div>
          ) : null}
          {humanRequirementGaps.length ? (
            <div className="mb-2 evaluation-report-defect-group">
              <span className="badge bg-secondary text-white me-2">人工需复核</span>
              <ul className="mb-1 ps-3 mt-1 text-muted">
                {humanRequirementGaps.map((item, index) => <li key={index}>{item}</li>)}
              </ul>
            </div>
          ) : null}
          {bothMissingPoints.length ? (
            <div className="mb-2 evaluation-report-defect-group">
              <span className="badge bg-dark text-white me-2">双方遗漏</span>
              <ul className="mb-1 ps-3 mt-1 text-muted">
                {bothMissingPoints.map((item, index) => <li key={index}>{item}</li>)}
              </ul>
            </div>
          ) : null}
          {aiUnanchoredPoints.length ? (
            <div className="mb-2 evaluation-report-defect-group">
              <span className="badge bg-danger text-white me-2">AI无需求依据</span>
              <ul className="mb-1 ps-3 mt-1 text-muted">
                {aiUnanchoredPoints.map((item, index) => <li key={index}>{item}</li>)}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}

      {defectAnalysis.missing_points?.length ? (
        <div className="mb-2 evaluation-report-defect-group">
          <span className="badge bg-warning text-dark me-2">遗漏点（召回损失）</span>
          <ul className="mb-1 ps-3 mt-1 text-muted">
            {defectAnalysis.missing_points.map((item, index) => <li key={index}>{item}</li>)}
          </ul>
        </div>
      ) : null}

      {defectAnalysis.hallucinations?.length ? (
        <div className="mb-2 evaluation-report-defect-group">
          <span className="badge bg-danger text-white me-2">幻觉/多余（精度损失）</span>
          <ul className="mb-1 ps-3 mt-1 text-muted">
            {defectAnalysis.hallucinations.map((item, index) => <li key={index}>{item}</li>)}
          </ul>
        </div>
      ) : null}

      {defectAnalysis.modifications?.length ? (
        <div className="mb-2 evaluation-report-defect-group">
          <span className="badge bg-info text-white me-2">逻辑修正</span>
          <ul className="mb-1 ps-3 mt-1 text-muted">
            {defectAnalysis.modifications.map((item, index) => <li key={index}>{item}</li>)}
          </ul>
        </div>
      ) : null}

      <div className="mt-3 pt-2 border-top text-secondary evaluation-report-summary">
        <strong>总结:</strong> {summary}
      </div>
      <div className="mt-2 text-end text-muted x-small">
        <FaRobot className="me-1" /> 缺陷归因分析由 AI 模型生成
      </div>
    </div>
  );
}
