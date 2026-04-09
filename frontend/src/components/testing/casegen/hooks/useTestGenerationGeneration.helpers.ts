import {
  deduplicateStandardCases,
  normalizeStandardCases,
  sanitizeStandardCases,
} from './testGenerationCaseUtils';
export const MAX_FINAL_RESULT_FETCH_RETRIES = 3;

export const sleep = (ms: number) => new Promise<void>((resolve) => {
  globalThis.setTimeout(resolve, ms);
});

export const parsePersistedGenerationIdFromLine = (line: string): number | null => {
  const text = String(line || '').trim();
  if (!text) return null;
  const idx = text.indexOf('GEN_DIAG:');
  if (idx < 0) return null;
  const rawPayload = text.slice(idx + 'GEN_DIAG:'.length).trim();
  if (!rawPayload) return null;
  try {
    const payload = JSON.parse(rawPayload);
    if (String(payload?.kind || '').trim() !== 'generation_persisted') return null;
    const generationId = Number(payload?.generation_id);
    return Number.isFinite(generationId) && generationId > 0 ? generationId : null;
  } catch {
    return null;
  }
};

export const parseHistoryCases = (data: any): any[] => {
  if (Array.isArray(data)) return data;

  const fromGeneratedResult = data?.generated_result;
  if (Array.isArray(fromGeneratedResult)) return fromGeneratedResult;
  if (typeof fromGeneratedResult === 'string' && fromGeneratedResult.trim()) {
    try {
      const parsed = JSON.parse(fromGeneratedResult);
      if (Array.isArray(parsed)) return parsed;
    } catch {
      // ignore parse error
    }
  }

  const nestedGeneratedResult = data?.generation?.generated_result;
  if (Array.isArray(nestedGeneratedResult)) return nestedGeneratedResult;
  if (typeof nestedGeneratedResult === 'string' && nestedGeneratedResult.trim()) {
    try {
      const parsed = JSON.parse(nestedGeneratedResult);
      if (Array.isArray(parsed)) return parsed;
    } catch {
      // ignore parse error
    }
  }

  return [];
};

export const normalizeHistoryCases = (data: any): any[] => {
  const rawCases = parseHistoryCases(data);
  if (!rawCases.length) return [];
  const sanitized = sanitizeStandardCases(rawCases);
  if (sanitized.valid.length > 0) return deduplicateStandardCases(sanitized.valid);
  return deduplicateStandardCases(normalizeStandardCases(rawCases));
};

