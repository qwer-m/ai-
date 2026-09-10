import { api } from '../../../utils/api';
import type {
  AgentRunStatus,
  AutomationEvaluationReport,
  DefectAnalysis,
  EvaluationHistoryPoint,
  EvaluationRunRecord,
  QualityMetrics,
  QualityReport,
  QualityReportPayload,
  RequirementBaseline,
  TestEvaluationArtifact,
  TestGenerationArtifact,
} from './types';

export function getErrorText(error: unknown): string {
  if (!error) return '';
  if (typeof error === 'string') return error;

  const e = error as {
    data?: { error?: unknown; detail?: unknown; message?: unknown };
    message?: unknown;
  };

  if (e?.data?.error) return String(e.data.error);
  if (e?.data?.detail) return String(e.data.detail);
  if (e?.data?.message) return String(e.data.message);
  if (e?.message) return String(e.message);

  try {
    return JSON.stringify(error);
  } catch {
    return String(error);
  }
}

export async function translateError(error: unknown): Promise<string> {
  const raw = getErrorText(error);
  try {
    const res = await api.post<{ message?: string }>('/api/error/translate', { error: raw });
    return res?.message ? String(res.message) : raw;
  } catch {
    return raw;
  }
}

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

function isTextList(value: unknown): value is string[] {
  return Array.isArray(value)
    && value.every((item) => typeof item === 'string' && item.length > 0 && item.length <= 300);
}

function isQualityMetrics(value: unknown): value is QualityMetrics {
  if (!isRecord(value) || !hasExactKeys(value, ['precision', 'recall', 'f1_score', 'semantic_similarity'])) {
    return false;
  }
  return isRatio(value.precision)
    && isRatio(value.recall)
    && isRatio(value.f1_score)
    && isRatio(value.semantic_similarity);
}

function isDefectAnalysis(value: unknown): value is DefectAnalysis {
  if (!isRecord(value) || !hasExactKeys(value, ['missing_points', 'hallucinations', 'modifications'])) {
    return false;
  }
  return isTextList(value.missing_points)
    && isTextList(value.hallucinations)
    && isTextList(value.modifications);
}

function isRequirementBaseline(value: unknown): value is RequirementBaseline {
  const keys = [
    'requirement_points',
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
  return isTextList(value.requirement_points)
    && isTextList(value.ai_requirement_gaps)
    && isTextList(value.human_requirement_gaps)
    && isTextList(value.ai_unanchored_points)
    && isTextList(value.human_added_value)
    && isTextList(value.both_missing_points)
    && isTextList(value.covered_by_both)
    && isNonNegativeInteger(value.generated_coverage_count)
    && isNonNegativeInteger(value.modified_coverage_count)
    && isRatio(value.generated_coverage_rate)
    && isRatio(value.modified_coverage_rate)
    && typeof value.summary === 'string'
    && value.summary.length > 0
    && value.summary.length <= 500;
}

function isQualityReportPayload(value: unknown): value is QualityReportPayload {
  if (!isRecord(value) || !hasExactKeys(value, ['metrics', 'defect_analysis', 'requirement_baseline', 'summary'])) {
    return false;
  }
  return isQualityMetrics(value.metrics)
    && isDefectAnalysis(value.defect_analysis)
    && isRequirementBaseline(value.requirement_baseline)
    && typeof value.summary === 'string'
    && value.summary.length > 0
    && value.summary.length <= 1000;
}

/** 校验 Agent Artifact 中的结构化质量评测结果。 */
export function normalizeQualityReport(payload: unknown): QualityReport | null {
  if (!isQualityReportPayload(payload)) return null;
  return {
    metrics: payload.metrics,
    defectAnalysis: payload.defect_analysis,
    requirementBaseline: payload.requirement_baseline,
    summary: payload.summary,
  };
}

function isScore(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 10;
}

/** 严格校验自动化评测 Agent 的结构化报告，不兼容旧字段或 JSON 字符串。 */
export function normalizeAutomationEvaluationReport(payload: unknown): AutomationEvaluationReport | null {
  const reportKeys = [
    'summary',
    'overall_score',
    'execution_status',
    'criteria',
    'coverage',
    'risks',
    'recommendations',
  ];
  if (!isRecord(payload) || !hasExactKeys(payload, reportKeys)) return null;
  if (typeof payload.summary !== 'string' || payload.summary.length === 0) return null;
  if (!isScore(payload.overall_score)) return null;
  if (
    typeof payload.execution_status !== 'string'
    || !['success', 'failed', 'unknown'].includes(payload.execution_status)
  ) return null;
  if (!Array.isArray(payload.criteria) || payload.criteria.length !== 5) return null;

  const criteria = payload.criteria.map((item) => {
    if (!isRecord(item) || !hasExactKeys(item, ['key', 'name', 'score', 'analysis'])) return null;
    if (
      typeof item.key !== 'string' || item.key.length === 0
      || typeof item.name !== 'string' || item.name.length === 0
      || !isScore(item.score)
      || typeof item.analysis !== 'string' || item.analysis.length === 0
    ) return null;
    return {
      key: item.key,
      name: item.name,
      score: item.score,
      analysis: item.analysis,
    };
  });
  if (criteria.some((item) => item === null)) return null;

  const coverage = payload.coverage;
  if (!isRecord(coverage) || !hasExactKeys(coverage, ['rate', 'covered_items', 'missing_items', 'explanation'])) {
    return null;
  }
  if (
    coverage.rate !== null
    && (typeof coverage.rate !== 'number' || !Number.isFinite(coverage.rate) || coverage.rate < 0 || coverage.rate > 1)
  ) return null;
  if (!Array.isArray(coverage.covered_items) || !coverage.covered_items.every((item) => typeof item === 'string')) return null;
  if (!Array.isArray(coverage.missing_items) || !coverage.missing_items.every((item) => typeof item === 'string')) return null;
  if (typeof coverage.explanation !== 'string') return null;
  if (!Array.isArray(payload.risks) || !payload.risks.every((item) => typeof item === 'string' && item.length > 0)) return null;
  if (!Array.isArray(payload.recommendations) || !payload.recommendations.every((item) => typeof item === 'string' && item.length > 0)) return null;

  return {
    summary: payload.summary,
    overall_score: payload.overall_score,
    execution_status: payload.execution_status as AutomationEvaluationReport['execution_status'],
    criteria: criteria as AutomationEvaluationReport['criteria'],
    coverage: {
      rate: coverage.rate as number | null,
      covered_items: [...coverage.covered_items] as string[],
      missing_items: [...coverage.missing_items] as string[],
      explanation: coverage.explanation,
    },
    risks: [...payload.risks] as string[],
    recommendations: [...payload.recommendations] as string[],
  };
}

type AgentNodeRunResponse = {
  id: number;
  node_key: string;
  node_type: 'agent' | 'tool';
  status: AgentRunStatus;
  attempt: number;
  input_payload: Record<string, unknown>;
  output_payload: Record<string, unknown>;
  sdk_state: Record<string, unknown>;
  error_message: string;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

type AgentApprovalResponse = {
  id: number;
  run_id: number;
  node_run_id: number;
  status: 'pending' | 'approved' | 'rejected';
  request_payload: Record<string, unknown>;
  decision_payload: Record<string, unknown>;
  requested_at: string;
  decided_at: string | null;
};

type AgentRunResponse = {
  id: number;
  project_id: number;
  workflow_definition_id: number;
  status: AgentRunStatus;
  current_node_key: string | null;
  input_payload: Record<string, unknown>;
  run_context: {
    node_outputs: Record<string, unknown>;
    artifacts: Record<string, unknown> & {
      test_generation?: TestGenerationArtifact;
      test_evaluation?: TestEvaluationArtifact;
    };
  };
  output_payload: Record<string, unknown>;
  error_message: string;
  parent_run_id: number | null;
  task_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  nodes: AgentNodeRunResponse[];
  approvals: AgentApprovalResponse[];
};

type AgentRunListResponse = { items: AgentRunResponse[] };
type AgentRunDetailResponse = { run: AgentRunResponse };
type EvaluationHistoryResponse = { history: EvaluationHistoryPoint[] };

export async function fetchEvaluationHistory(projectId: number): Promise<EvaluationHistoryResponse> {
  return api.get<EvaluationHistoryResponse>(`/api/evaluation/history/${projectId}`);
}

function toEvaluationRunRecord(run: AgentRunResponse): EvaluationRunRecord | null {
  const testGenerationArtifact = run.run_context.artifacts.test_generation;
  if (!testGenerationArtifact) return null;
  const evaluationArtifact = run.run_context.artifacts.test_evaluation ?? null;
  return {
    run_id: run.id,
    project_id: run.project_id,
    status: run.status,
    current_node_key: run.current_node_key,
    parent_run_id: run.parent_run_id,
    requirement_text: testGenerationArtifact.requirement,
    case_count: testGenerationArtifact.case_count,
    test_cases: testGenerationArtifact.test_cases,
    created_at: run.created_at,
    started_at: run.started_at,
    finished_at: run.finished_at,
    has_evaluation: Boolean(evaluationArtifact),
    evaluation_artifact: evaluationArtifact,
  };
}

export async function fetchAgentRunHistory(projectId: number): Promise<EvaluationRunRecord[]> {
  const response = await api.get<AgentRunListResponse>(`/api/agents/runs?project_id=${projectId}&limit=200`);
  return response.items
    .map(toEvaluationRunRecord)
    .filter((record): record is EvaluationRunRecord => record !== null);
}

export async function fetchAgentRunBundle(runId: number) {
  const response = await api.get<AgentRunDetailResponse>(`/api/agents/runs/${runId}`);
  const run = toEvaluationRunRecord(response.run);
  const evaluationArtifact = run?.evaluation_artifact ?? null;
  return {
    run,
    evaluation_artifact: evaluationArtifact,
    evaluation_status: evaluationArtifact ? 'found' as const : 'missing' as const,
  };
}

export async function fetchAgentRunStatus(runId: number): Promise<AgentRunResponse> {
  const response = await api.get<AgentRunDetailResponse>(`/api/agents/runs/${runId}`);
  return response.run;
}

export async function evaluateTestCasesRequest(formData: FormData): Promise<AgentRunDetailResponse> {
  return api.upload<AgentRunDetailResponse>('/api/evaluate-test-cases', formData);
}

type AutomationEvaluationResponse = {
  result: unknown;
  run_id: number;
  status: string;
};

export async function evaluateUiRequest(payload: Record<string, unknown>) {
  return api.post<AutomationEvaluationResponse>('/api/evaluate-ui-automation', payload);
}

export async function evaluateApiRequest(payload: Record<string, unknown>) {
  return api.post<AutomationEvaluationResponse>('/api/evaluate-api-test', payload);
}

export async function ragSingleDebugRequest(payload: {
  project_id: number;
  query: string;
  top_k?: number;
  max_tokens?: number;
  llm_model?: string;
  retrieval_mode?: 'vector' | 'keyword' | 'hybrid' | 'bm25';
  recall_top_k?: number;
  rerank_top_n?: number;
  max_chunks_per_doc?: number;
  min_docs?: number;
  enable_query_rewrite?: boolean;
  enable_rerank?: boolean;
  title_weight?: number;
  keyword_weight?: number;
  vector_weight?: number;
  redundancy_threshold?: number;
}) {
  return api.post<any>('/api/rag/eval/debug/single', payload);
}

export async function listRagDatasets() {
  return api.get<any[]>('/api/rag/datasets');
}

export async function createRagDataset(payload: { name: string; type: string; description?: string }) {
  return api.post<any>('/api/rag/datasets', payload);
}

export async function deleteRagDataset(datasetId: number) {
  return api.delete<any>(`/api/rag/datasets/${datasetId}`);
}

export async function listRagDatasetSamples(
  datasetId: number,
  params?: { tags?: string[]; difficulty?: string; enabled_only?: boolean; page?: number; page_size?: number },
) {
  const q = new URLSearchParams();
  if (params?.tags?.length) q.set('tags', params.tags.join(','));
  if (params?.difficulty) q.set('difficulty', params.difficulty);
  if (typeof params?.enabled_only === 'boolean') q.set('enabled_only', String(params.enabled_only));
  if (params?.page) q.set('page', String(params.page));
  if (params?.page_size) q.set('page_size', String(params.page_size));
  const suffix = q.toString() ? `?${q.toString()}` : '';
  return api.get<any[]>(`/api/rag/datasets/${datasetId}/samples${suffix}`);
}

export async function startRagEvalRun(projectId: number, payload: { dataset_id: number; config: any; run_name?: string }) {
  return api.post<any>(`/api/rag/eval/run?project_id=${projectId}`, payload);
}

export async function stopRagEvalRun(runId: number) {
  return api.post<any>(`/api/rag/eval/run/${runId}/stop`, {});
}

export async function resumeRagEvalRun(runId: number) {
  return api.post<any>(`/api/rag/eval/run/${runId}/resume`, {});
}

export async function getRagEvalRun(runId: number) {
  return api.get<any>(`/api/rag/eval/run/${runId}`);
}

export async function getRagEvalRunCompare(runA: number, runB: number) {
  return api.get<any>(`/api/rag/eval/run/compare?run_a=${runA}&run_b=${runB}`);
}

export async function getRagEvalRunSamples(
  runId: number,
  params?: { page?: number; page_size?: number; tag?: string; failure_reason?: string; answer_correct?: boolean; sample_ids?: number[] },
) {
  const q = new URLSearchParams();
  if (params?.page) q.set('page', String(params.page));
  if (params?.page_size) q.set('page_size', String(params.page_size));
  if (params?.tag) q.set('tag', params.tag);
  if (params?.failure_reason) q.set('failure_reason', params.failure_reason);
  if (typeof params?.answer_correct === 'boolean') q.set('answer_correct', String(params.answer_correct));
  if (params?.sample_ids?.length) q.set('sample_ids', params.sample_ids.join(','));
  const suffix = q.toString() ? `?${q.toString()}` : '';
  return api.get<any>(`/api/rag/eval/run/${runId}/samples${suffix}`);
}

export async function promoteRagSample(sampleId: number, targetDatasetType: 'challenge' | 'regression') {
  return api.post<any>(`/api/rag/eval/sample/${sampleId}/promote`, {
    target_dataset_type: targetDatasetType,
  });
}

export async function generateRagEvalCandidates(payload: {
  run_id: number;
  filters?: {
    failure_reasons?: string[];
    answer_correct_false?: boolean;
    faithfulness_lt?: number | null;
    answer_correctness_lt?: number | null;
  };
  target_dataset_type?: 'challenge' | 'regression' | null;
}) {
  return api.post<any>('/api/rag/eval/candidates/generate', payload);
}

export async function listRagEvalCandidates(params?: {
  status?: string;
  source_type?: string;
  failure_reason?: string;
  suggested_dataset_type?: string;
  page?: number;
  page_size?: number;
}) {
  const q = new URLSearchParams();
  if (params?.status) q.set('status', params.status);
  if (params?.source_type) q.set('source_type', params.source_type);
  if (params?.failure_reason) q.set('failure_reason', params.failure_reason);
  if (params?.suggested_dataset_type) q.set('suggested_dataset_type', params.suggested_dataset_type);
  if (params?.page) q.set('page', String(params.page));
  if (params?.page_size) q.set('page_size', String(params.page_size));
  const suffix = q.toString() ? `?${q.toString()}` : '';
  return api.get<any>(`/api/rag/eval/candidates${suffix}`);
}

export async function draftRagEvalCandidate(candidateId: number, payload?: Record<string, unknown>) {
  return api.post<any>(`/api/rag/eval/candidates/${candidateId}/draft`, payload || {});
}

export async function approveRagEvalCandidate(
  candidateId: number,
  payload?: {
    target_dataset_type?: 'challenge' | 'regression';
    draft?: Record<string, unknown>;
  },
) {
  return api.post<any>(`/api/rag/eval/candidates/${candidateId}/approve`, payload || {});
}

export async function rejectRagEvalCandidate(candidateId: number, notes?: string) {
  return api.post<any>(`/api/rag/eval/candidates/${candidateId}/reject`, { notes: notes || '' });
}

export async function importRagDataset(formData: FormData) {
  return api.upload<any>('/api/rag/datasets/import', formData);
}

export async function exportRagDataset(datasetId: number) {
  return api.getBlob(`/api/rag/datasets/export/${datasetId}`);
}


