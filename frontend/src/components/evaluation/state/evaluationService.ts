import { api } from '../../../utils/api';
import type { ParsedApiReport, ParsedQualityReport } from './types';
import type { RagRetrieveContextDebugRequest } from '../rag/shared/types';

export const maxSupplementImages = 10;

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

export function pickLatestByPrefix(logs: any[], prefix: string) {
  const matches = (Array.isArray(logs) ? logs : []).filter(
    (x: any) => typeof x?.message === 'string' && x.message.startsWith(prefix),
  );
  if (matches.length === 0) return null;

  let best = matches[0];
  let bestTime = new Date(best?.created_at || 0).getTime() || 0;
  for (let i = 1; i < matches.length; i += 1) {
    const t = new Date(matches[i]?.created_at || 0).getTime() || 0;
    if (t >= bestTime) {
      best = matches[i];
      bestTime = t;
    }
  }
  return best;
}

function parseJsonSafely<T>(raw: string): T | null {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function parseLatestPrefixedJson<T>(logs: any[], prefix: string): T | null {
  const latest = pickLatestByPrefix(logs, prefix);
  if (!latest || typeof latest.message !== 'string') return null;
  return parseJsonSafely<T>(latest.message.slice(prefix.length));
}

/**
 * 质量评估报告可能是纯 JSON，也可能被 ```json 代码块包裹。
 * 这里做兼容解析，保证历史接口格式波动时前端仍可稳定展示。
 */
export function parseQualityReport(rawText: string): ParsedQualityReport {
  let jsonStr = rawText.trim();
  const block = jsonStr.match(/```json\s*([\s\S]*?)\s*```/) || jsonStr.match(/```\s*([\s\S]*?)\s*```/);
  if (block?.[1]) jsonStr = block[1];

  const firstOpen = jsonStr.indexOf('{');
  const lastClose = jsonStr.lastIndexOf('}');
  if (firstOpen === -1 || lastClose === -1) return null;

  const payload = parseJsonSafely<any>(jsonStr.substring(firstOpen, lastClose + 1));
  if (!payload) return null;

  return {
    metrics: payload.metrics || {},
    defectAnalysis: payload.defect_analysis || {},
    summary: payload.summary || '',
  };
}

export function parseApiReport(rawText: string): ParsedApiReport {
  const jsonStr = rawText.match(/\{[\s\S]*\}/)?.[0];
  if (!jsonStr) return null;

  const payload = parseJsonSafely<any>(jsonStr);
  if (!payload) return null;

  return {
    similarity: payload.similarity,
    score: payload.score,
    coverage: payload.coverage,
    analysis: payload.analysis,
  };
}

export async function fetchLatestSupplement(projectId: number, sourceKey?: string) {
  const query = sourceKey ? `?source_key=${encodeURIComponent(sourceKey)}` : '';
  return api.get<any>(`/api/evaluation/latest-supplement/${projectId}${query}`);
}

export async function fetchEvaluationHistory(projectId: number) {
  return api.get<any>(`/api/evaluation/history/${projectId}`);
}

export async function fetchGenerationHistory(projectId: number) {
  return api.get<any>(`/api/test-generations?project_id=${projectId}`);
}

export async function fetchGenerationDetail(id: number) {
  return api.get<any>(`/api/test-generations/${id}`);
}

export async function fetchGenerationBundle(id: number) {
  return api.get<any>(`/api/test-generations/${id}/bundle`);
}

export async function compareTestCasesRequest(formData: FormData) {
  return api.upload<any>('/api/compare-test-cases', formData);
}

export async function learnFromEvaluationCasePairRequest(payload: {
  project_id: number;
  generated_cases: unknown;
  final_cases: unknown;
  generation_id?: number | null;
  include_negative_samples?: boolean;
  dry_run?: boolean;
}) {
  return api.post<any>('/api/test-generations/learn-from-evaluation', payload);
}

export async function learnFromEvaluationCasePairFileRequest(formData: FormData) {
  return api.upload<any>('/api/test-generations/learn-from-evaluation-file', formData);
}

export async function buildLearningCandidatesFromEvaluationRequest(payload: {
  project_id: number;
  evaluation_result: unknown;
}) {
  return api.post<any>('/api/test-generations/learning-candidates/from-evaluation', payload);
}

export async function applyLearningCandidatesRequest(payload: {
  project_id: number;
  candidates: any[];
  dry_run?: boolean;
}) {
  return api.post<any>('/api/test-generations/learning-candidates/apply', payload);
}

export async function evaluateUiRequest(payload: Record<string, unknown>) {
  return api.post<any>('/api/evaluate-ui-automation', payload);
}

export async function evaluateApiRequest(payload: Record<string, unknown>) {
  return api.post<any>('/api/evaluate-api-test', payload);
}

export async function fetchProjectLogs(projectId: number) {
  return api.get<any[]>(`/api/logs/${projectId}`);
}

export async function saveKnowledgeRequest(formData: FormData) {
  return api.upload<any>('/api/evaluation/save-knowledge', formData);
}

export async function retrieveRagContextDebugRequest(payload: RagRetrieveContextDebugRequest) {
  return api.post<any>('/api/knowledge/retrieve-context', {
    ...payload,
    // 中文注释：RAG 校验页固定开启 debug，便于观察召回/重排/压缩链路细节。
    debug: true,
  });
}

export async function ragSingleDebugRequest(payload: {
  project_id: number;
  query: string;
  limit?: number;
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

export async function updateRagDataset(datasetId: number, payload: { name?: string; type?: string; description?: string }) {
  return api.put<any>(`/api/rag/datasets/${datasetId}`, payload);
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


