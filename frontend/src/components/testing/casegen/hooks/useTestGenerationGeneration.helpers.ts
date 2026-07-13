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

const presentationOrderOf = (item: any, fallback: number): number => {
  const raw = Number(
    item?.presentation_order
      ?? item?.presentationOrder
      ?? item?.display_order
      ?? item?.displayOrder
      ?? 0,
  );
  return Number.isFinite(raw) && raw > 0 ? raw : fallback;
};

export const sortCasesByPresentationOrder = (cases: any[]): any[] => {
  if (!Array.isArray(cases) || cases.length <= 1) return cases;
  const hasPresentationOrder = cases.some((item) => presentationOrderOf(item, 0) > 0);
  if (!hasPresentationOrder) return cases;
  return [...cases].sort((a, b) => (
    presentationOrderOf(a, Number.MAX_SAFE_INTEGER)
    - presentationOrderOf(b, Number.MAX_SAFE_INTEGER)
  ));
};

export const normalizeHistoryCases = (data: any): any[] => {
  const rawCases = parseHistoryCases(data);
  if (!rawCases.length) return [];
  const sanitized = sanitizeStandardCases(rawCases);
  if (sanitized.valid.length > 0) {
    return sortCasesByPresentationOrder(deduplicateStandardCases(sanitized.valid));
  }
  return sortCasesByPresentationOrder(deduplicateStandardCases(normalizeStandardCases(rawCases)));
};

