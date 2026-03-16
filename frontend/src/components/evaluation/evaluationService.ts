import { api } from '../../utils/api';
import type { ParsedApiReport, ParsedQualityReport } from './types';

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

export async function fetchLatestSupplement(projectId: number) {
  return api.get<any>(`/api/evaluation/latest-supplement/${projectId}`);
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

export async function compareTestCasesRequest(formData: FormData) {
  return api.upload<any>('/api/compare-test-cases', formData);
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
