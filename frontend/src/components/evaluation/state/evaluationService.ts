import { api } from '../../../utils/api';
import { parseAgentRunResponse } from '../../agent-platform/agentApi';
import type { AgentRun } from '../../agent-platform/types';
import { assertionLabels } from './types';
import type {
  DefectAnalysis,
  EvaluationSource,
  GeneratedTestCase,
  QualityMetrics,
  QualityReportPayload,
  RequirementBaseline,
  TestEvaluationArtifact,
} from './types';

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actualKeys = Object.keys(value);
  return actualKeys.length === keys.length
    && keys.every((key) => Object.prototype.hasOwnProperty.call(value, key));
}

function isRatio(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

const REPORT_SUMMARY_MAX_LENGTH = 1000;

function isBoundedText(value: unknown, maxLength: number): value is string {
  // 与后端 JSON Schema 一致，按 Unicode 字符计数，避免扩展汉字或 emoji 被重复计数。
  return typeof value === 'string' && value.length > 0 && Array.from(value).length <= maxLength;
}

function isTextList(value: unknown): value is string[] {
  return Array.isArray(value)
    && value.every((item) => typeof item === 'string' && item.trim().length > 0);
}

function isNullableRatio(value: unknown): value is number | null {
  return value === null || isRatio(value);
}

function isQualityMetrics(value: unknown): value is QualityMetrics {
  if (!isRecord(value) || !hasExactKeys(value, ['precision', 'recall', 'f1_score', 'coverage_jaccard'])) {
    return false;
  }
  return isNullableRatio(value.precision)
    && isNullableRatio(value.recall)
    && isNullableRatio(value.f1_score)
    && isNullableRatio(value.coverage_jaccard);
}

function isDefectAnalysis(value: unknown): value is DefectAnalysis {
  if (!isRecord(value) || !hasExactKeys(value, ['missing_points', 'hallucinations', 'modifications'])) {
    return false;
  }
  return isTextList(value.missing_points)
    && isTextList(value.hallucinations)
    && isTextList(value.modifications);
}

function isAssertionEvidence(value: unknown): boolean {
  return isRecord(value) && typeof value.verdict === 'string' && Object.prototype.hasOwnProperty.call(assertionLabels, value.verdict)
    && typeof value.case_id === 'string' && Array.isArray(value.steps)
    && value.steps.every(n => isNonNegativeInteger(n) && n > 0)
    && typeof value.fault === 'string' && value.fault.trim().length > 0;
}

function isRequirementBaseline(value: unknown): value is RequirementBaseline {
  const keys = [
    'groups', 'requirement_points',
    'ai_requirement_gaps',
    'human_requirement_gaps',
    'ai_unanchored_points',
    'human_added_value',
    'both_missing_points',
    'covered_by_both',
    'generated_coverage_count',
    'modified_coverage_count',
    'generated_coverage_rate',
    'modified_coverage_rate',
    'summary',
  ];
  if (!isRecord(value) || !hasExactKeys(value, keys)) return false;
  return Array.isArray(value.groups) && value.groups.every((g) =>
    isRecord(g) && typeof g.point === 'string' && typeof g.reason === 'string'
    && typeof g.excluded === 'boolean' && typeof g.difference === 'string'
    && ['full', 'partial', 'missing'].includes(String(g.agent))
    && ['full', 'partial', 'missing'].includes(String(g.manual))
    && (g.assertion === undefined || isAssertionEvidence(g.assertion))
    && isTextList(g.case_ids) && isTextList(g.manual_refs))
    && isTextList(value.requirement_points)
    && isTextList(value.ai_requirement_gaps)
    && isTextList(value.human_requirement_gaps)
    && isTextList(value.ai_unanchored_points)
    && isTextList(value.human_added_value)
    && isTextList(value.both_missing_points)
    && isTextList(value.covered_by_both)
    && isNonNegativeInteger(value.generated_coverage_count)
    && isNonNegativeInteger(value.modified_coverage_count)
    && isNullableRatio(value.generated_coverage_rate)
    && isNullableRatio(value.modified_coverage_rate)
    && isBoundedText(value.summary, REPORT_SUMMARY_MAX_LENGTH);
}

function isQualityReportPayload(value: unknown): value is QualityReportPayload {
  if (!isRecord(value) || !hasExactKeys(value, ['metrics', 'defect_analysis', 'requirement_baseline', 'summary'])) {
    return false;
  }
  return isQualityMetrics(value.metrics)
    && isDefectAnalysis(value.defect_analysis)
    && isRequirementBaseline(value.requirement_baseline)
    && isBoundedText(value.summary, REPORT_SUMMARY_MAX_LENGTH);
}

function isGeneratedTestCase(value: unknown): value is GeneratedTestCase {
  if (!isRecord(value)) return false;
  return typeof value.case_id === 'string'
    && typeof value.title === 'string'
    && typeof value.module === 'string'
    && (value.priority === 'P0' || value.priority === 'P1' || value.priority === 'P2')
    && Array.isArray(value.preconditions)
    && value.preconditions.every((item) => typeof item === 'string')
    && Array.isArray(value.steps)
    && value.steps.every((step) => isRecord(step)
      && typeof step.action === 'string' && typeof step.expected === 'string')
    && typeof value.test_input === 'string';
}

export function readTestEvaluationArtifact(
  run: AgentRun, sourceRunId: number, evaluationRunId?: number,
): TestEvaluationArtifact | null {
  const artifacts = run.run_context.artifacts;
  return parseEvaluation(isRecord(artifacts) ? artifacts.test_evaluation : null, run.project_id, sourceRunId, evaluationRunId);
}

function parseEvaluation(evaluation: unknown, projectId: number, sourceRunId: number, evaluationRunId?: number) {
  if (evaluation == null) return null;
  if (!isRecord(evaluation)
    || evaluation.project_id !== projectId
    || evaluation.source_run_id !== sourceRunId
    || !isNonNegativeInteger(evaluation.evaluation_run_id) || evaluation.evaluation_run_id < 1
    || (evaluationRunId !== undefined && evaluation.evaluation_run_id !== evaluationRunId)
    || typeof evaluation.reference_content !== 'string'
    || !isQualityReportPayload(evaluation.evaluation)
    || !isRecord(evaluation.upload)
    || typeof evaluation.upload.filename !== 'string'
  ) throw new Error(`Run ${sourceRunId} 的质量评估产物格式或运行身份无效`);
  return {
    source_run_id: sourceRunId,
    evaluation_run_id: evaluation.evaluation_run_id,
    reference_content: evaluation.reference_content,
    evaluation: evaluation.evaluation,
    upload: { filename: evaluation.upload.filename },
  };
}

export async function fetchEvaluationSource(projectId: number, documentId: number, runId?: number): Promise<EvaluationSource | null> {
  const params = new URLSearchParams({ project_id: String(projectId), requirement_doc_id: String(documentId) });
  if (runId !== undefined) params.set('run_id', String(runId));
  const { source } = await api.get<{ source: unknown }>(`/api/evaluation/source?${params}`);
  if (source === null && runId === undefined) return null;
  if (!isRecord(source) || source.project_id !== projectId || source.document_id !== documentId
    || !isNonNegativeInteger(source.run_id) || (runId !== undefined && source.run_id !== runId)
    || typeof source.requirement !== 'string' || !Array.isArray(source.cases)
    || !source.cases.length || !source.cases.every(isGeneratedTestCase)) {
    throw new Error('生成用例或需求来源与本次选择不一致');
  }
  return { project_id: projectId, document_id: documentId, run_id: source.run_id, requirement: source.requirement,
    cases: source.cases, evaluation: parseEvaluation(source.evaluation, projectId, source.run_id) };
}

export async function evaluateTestCasesRequest(formData: FormData): Promise<{ run: AgentRun }> {
  return parseAgentRunResponse(
    await api.upload<unknown>('/api/evaluate-test-cases', formData),
    '创建测试用例评估运行响应',
  );
}
