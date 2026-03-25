import { api } from '../../../../utils/api';
import { cleanStreamingContent, getCopyContent, parseMultipleJsonArrays } from '../../../test-generation/streamContent';

type AnyRecord = Record<string, any>;

export const MAX_FILE_SIZE = 50 * 1024 * 1024;

export const getErrorText = (error: any) => {
  if (!error) return '';
  if (typeof error === 'string') return error;
  if (error?.data?.error) return String(error.data.error);
  if (error?.data?.detail) return String(error.data.detail);
  if (error?.data?.message) return String(error.data.message);
  if (error?.message) return String(error.message);
  try { return JSON.stringify(error); } catch { return String(error); }
};

export const normalizeTestCaseId = (n: number) => `TC-${String(n).padStart(3, '0')}`;

export const normalizeStringList = (v: unknown, fallback?: string) => Array.isArray(v)
  ? v.map((x) => String(x).trim()).filter(Boolean)
  : typeof v === 'string'
    ? v.trim() ? v.split('\n').map((x) => x.trim()).filter(Boolean) : []
    : (fallback ? [fallback] : []);

export const normalizePriority = (v: unknown) => {
  const s = String(v ?? '').trim().toUpperCase();
  if (s === 'P0' || s === 'P1' || s === 'P2') return s;
  if (s === '高' || s === 'HIGH') return 'P0';
  if (s === '中' || s === 'MEDIUM') return 'P1';
  if (s === '低' || s === 'LOW') return 'P2';
  return 'P1';
};

export const normalizeId = (v: unknown, fallbackIndex: number) => {
  const raw = String(v ?? '').trim();
  if (/^TC-\d{3,}$/.test(raw)) return raw;
  if (/^\d+$/.test(raw)) return normalizeTestCaseId(Number(raw));
  return normalizeTestCaseId(fallbackIndex + 1);
};

export const pickField = (item: AnyRecord, keys: string[]) => {
  for (const k of keys) {
    const v = item?.[k];
    if (v === undefined || v === null) continue;
    if (typeof v === 'string') { const s = v.trim(); if (s) return s; continue; }
    if (Array.isArray(v) && v.length > 0) return v;
    if (typeof v === 'number' || typeof v === 'boolean') return v;
    if (typeof v === 'object') return v;
  }
  return undefined;
};

export const extractFirstJsonArray = (content: string): any[] | null => {
  if (!content) return null;
  const foundItems: any[] = [];
  let cursor = 0;
  while (cursor < content.length) {
    const start = content.indexOf('[', cursor);
    if (start === -1) break;
    let balance = 0; let end = -1; let inString = false; let escape = false;
    for (let i = start; i < content.length; i++) {
      const char = content[i];
      if (escape) { escape = false; continue; }
      if (char === '\\') { escape = true; continue; }
      if (char === '"') { inString = !inString; continue; }
      if (!inString) {
        if (char === '[') balance++;
        else if (char === ']') { balance--; if (balance === 0) { end = i; break; } }
      }
    }
    if (end !== -1) {
      try { const parsed = JSON.parse(content.substring(start, end + 1)); if (Array.isArray(parsed)) foundItems.push(...parsed); } catch {}
      cursor = end + 1;
    } else {
      cursor = start + 1;
    }
    if (foundItems.length > 0) return foundItems;
  }
  return null;
};

export const normalizeStandardCases = (items: any[]) => items
  .filter((item) => item && typeof item === 'object' && !Array.isArray(item))
  .map((item, index) => ({
    id: normalizeId(pickField(item, ['id', 'ID', 'test_case_id', 'case_id', '用例编号', '用例ID', '编号', 'ID号']), index),
    description: String(pickField(item, ['description', 'desc', 'name', '描述', '用例描述', '场景']) ?? '').trim(),
    test_module: String(pickField(item, ['test_module', 'module', '模块', '测试模块']) ?? '').trim(),
    preconditions: normalizeStringList(pickField(item, ['preconditions', 'precondition', '前置条件']) ?? item.preconditions),
    steps: normalizeStringList(pickField(item, ['steps', 'step', '步骤', '测试步骤']) ?? item.steps),
    test_input: String(pickField(item, ['test_input', 'input', '输入', '测试输入']) ?? '').trim(),
    expected_result: String(pickField(item, ['expected_result', 'expected', 'expect', '预期', '预期结果']) ?? '').trim(),
    priority: normalizePriority(pickField(item, ['priority', 'p', '优先级']) ?? item.priority),
  }));

export const deduplicateStandardCases = (items: any[]) => {
  const seen = new Set<string>();
  const norm = (v: unknown) => String(v ?? '').trim().toLowerCase().replace(/\r/g, '').replace(/\n/g, ' ');
  return items.filter((item) => {
    const steps = Array.isArray(item.steps) ? item.steps.map((s: unknown) => norm(s)).join(' | ') : norm(item.steps);
    const key = `${norm(item.test_module)}||${norm(item.description)}||${norm(item.test_input)}||${norm(item.expected_result)}||${steps}`;
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
};

export const getUniqueCaseCount = (items: any) => Array.isArray(items) ? deduplicateStandardCases(normalizeStandardCases(items)).length : 0;

export const validateStandardCases = (items: any[]) => {
  if (!Array.isArray(items)) return { ok: false as const, error: '结果不是 JSON 数组' };
  if (items.length === 0) return { ok: false as const, error: '结果为空数组，请重试生成' };
  const descSet = new Set<string>();
  const overlapSet = new Set<string>();
  const normalizeText = (v: unknown) => String(v ?? '').trim().replace(/\s+/g, ' ').toLowerCase();
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    if (!it || typeof it !== 'object' || Array.isArray(it)) return { ok: false as const, error: `第 ${i + 1} 条不是对象` };
    const required = ['id', 'description', 'test_module', 'preconditions', 'steps', 'test_input', 'expected_result', 'priority'];
    for (const k of required) if (!(k in it)) return { ok: false as const, error: `第 ${i + 1} 条缺少字段 ${k}` };
    const description = String(it.description ?? '').trim();
    const testModule = String(it.test_module ?? '').trim();
    const expectedResult = String(it.expected_result ?? '').trim();
    const testInput = String(it.test_input ?? '').trim();
    const steps = Array.isArray(it.steps) ? it.steps : [];
    const preconditions = it.preconditions;
    const priority = String(it.priority ?? '').trim().toUpperCase();
    if (!description) return { ok: false as const, error: `第 ${i + 1} 条用例描述为空` };
    if (!testModule) return { ok: false as const, error: `第 ${i + 1} 条测试模块为空` };
    if (!expectedResult) return { ok: false as const, error: `第 ${i + 1} 条预期结果为空` };
    if (!Array.isArray(steps) || steps.length === 0 || steps.some((s) => !String(s).trim())) return { ok: false as const, error: `第 ${i + 1} 条步骤为空或包含空步骤` };
    if (!Array.isArray(preconditions)) return { ok: false as const, error: `第 ${i + 1} 条前置条件不是数组` };
    if (!['P0', 'P1', 'P2'].includes(priority)) return { ok: false as const, error: `第 ${i + 1} 条优先级不合法: ${it.priority}` };
    const normalizedDesc = normalizeText(description);
    if (descSet.has(normalizedDesc)) return { ok: false as const, error: `第 ${i + 1} 条用例描述重复(违反 MECE 原则): "${description}"` };
    descSet.add(normalizedDesc);
    const overlapKey = `${normalizeText(testModule)}||${normalizeText(testInput)}||${normalizeText(expectedResult)}`;
    if (overlapSet.has(overlapKey)) return { ok: false as const, error: `第 ${i + 1} 条用例与已有用例存在重复验证点(模块+输入+预期重复)` };
    overlapSet.add(overlapKey);
  }
  return { ok: true as const };
};

export const translateError = async (error: any) => {
  const raw = getErrorText(error);
  try {
    const res = await api.post<any>('/api/error/translate', { error: raw });
    return res?.message ? String(res.message) : raw;
  } catch {
    return raw;
  }
};

export const extractStreamingCases = (streamingContent: string) => {
  const parsed = extractFirstJsonArray(cleanStreamingContent(streamingContent));
  return deduplicateStandardCases(normalizeStandardCases(parsed ?? []));
};

export const getCopyPayload = (result: any, streamingContent: string) => getCopyContent(result, streamingContent);

export const parseStreamingArrayContent = (text: string) => parseMultipleJsonArrays(text);
