export type ResultSource = 'none' | 'streaming_preview' | 'final_persisted';
export type PriorityValue = 'P0' | 'P1' | 'P2' | 'P3' | '';
export type ViewFilter = 'all' | 'corrected' | 'unchanged' | 'raw_mismatch' | 'display_mismatch';
export type SampleTag = 'over_raised' | 'over_lowered' | 'display_mismatch' | 'rule_adjusted' | 'manual_review';
export type SampleUsage = 'prompt_opt' | 'rule_opt' | 'retrieval_opt' | 'manual_review';
export type SampleKind = 'anomaly' | 'positive';
export type ReasonCategory = '' | 'core_flow' | 'exception_path' | 'boundary_condition' | 'state_transition' | 'redundant_case' | 'display_issue' | 'other';
export type PatternCategory = '' | 'core_flow_closure' | 'cross_page_flow' | 'multi_step_interaction' | 'state_transition_pattern' | 'critical_path_coverage' | 'complex_business_combination' | 'high_value_assertion' | 'boundary_effective_coverage';

export const SAMPLE_SOURCES = {
  PRIORITY_DEBUG_MANUAL_ADD: 'priority_debug_manual_add',
  QUALITY_EVALUATION_DEFECT: 'quality_evaluation_defect',
  LINKED_FINAL_CASE_PATTERN: 'linked_final_case_pattern',
  LINKED_FINAL_CASE_BUSINESS_EXTENSION: 'linked_final_case_business_extension',
  MANUAL_POOL_INPUT: 'manual_pool_input',
} as const;
export type SampleSource = (typeof SAMPLE_SOURCES)[keyof typeof SAMPLE_SOURCES];

export function sourceTypeLabel(source: string | null | undefined): string {
  const s = (source ?? '').trim();
  if (s === SAMPLE_SOURCES.PRIORITY_DEBUG_MANUAL_ADD) return '手动标记';
  if (s === SAMPLE_SOURCES.QUALITY_EVALUATION_DEFECT) return '质量评估';
  if (s === SAMPLE_SOURCES.LINKED_FINAL_CASE_PATTERN) return '关联用例';
  if (s === SAMPLE_SOURCES.LINKED_FINAL_CASE_BUSINESS_EXTENSION) return '业务扩展';
  if (s === SAMPLE_SOURCES.MANUAL_POOL_INPUT) return '手动入池';
  return s || '未知';
}

export function sourceTypeBadgeVariant(source: string | null | undefined): string {
  const s = (source ?? '').trim();
  if (s === SAMPLE_SOURCES.PRIORITY_DEBUG_MANUAL_ADD) return 'secondary';
  if (s === SAMPLE_SOURCES.QUALITY_EVALUATION_DEFECT) return 'warning';
  if (s === SAMPLE_SOURCES.LINKED_FINAL_CASE_PATTERN) return 'primary';
  if (s === SAMPLE_SOURCES.LINKED_FINAL_CASE_BUSINESS_EXTENSION) return 'info';
  if (s === SAMPLE_SOURCES.MANUAL_POOL_INPUT) return 'dark';
  return 'light';
}

export function statusLabel(status: string | null | undefined): string {
  const s = (status ?? '').trim().toLowerCase();
  if (s === 'deleted') return '已删除';
  if (s === 'disabled') return '已禁用';
  return '活跃';
}

export function formatWeight(weight: number | null | undefined): string {
  if (weight == null) return '-';
  return weight.toFixed(2);
}

export function isInPattern(sample: PrioritySample): boolean {
  return Boolean(sample.patternClusterKey && sample.patternClusterKey !== 'misc');
}

export type Props = {
  result: any;
  resultSource: ResultSource;
  projectId?: number | null;
  generationId?: number | null;
  enableSamplePoolFeedback: boolean;
  onToggleSamplePoolFeedback: (next: boolean) => void;
};
export type PriorityRow = {
  index: number; caseId: string; title: string; rawPriority: PriorityValue; finalPriority: PriorityValue; displayPriority: PriorityValue;
  corrected: boolean; rawFinalMismatch: boolean; displayFinalMismatch: boolean; resultSource: ResultSource; priorityDebug: Record<string, unknown> | null;
};
export type PrioritySample = {
  sampleId: string; caseId: string; title: string; rawPriority: string; finalPriority: string; displayPriority: string; resultSource: ResultSource; direction: string;
  sampleKind: SampleKind;
  corrected: boolean; isDisplayMismatch: boolean; isRawMismatch: boolean; priorityDebug: string; tags: SampleTag[]; usage: SampleUsage;
  userComment: string; expectedPriority: PriorityValue; reasonCategory: ReasonCategory; patternCategory: PatternCategory; addedAt: number;
  weakLinkCaseKey?: string; weakLinkGenerationId?: number | null;
  manualConfirmed?: boolean; manualConfirmedAt?: number;
  source?: string;
  status?: string | null;
  sourceType?: string | null;
  sourceId?: number | null;
  sourceCaseId?: string | null;
  confidence?: number | null;
  patternClusterKey?: string | null;
  patternWeight?: number | null;
  persistedSampleId?: string;
  learningStatus?: string | null;
  learningConfirmedAt?: string | null;
  learningConfirmedBy?: number | null;
};
export type ExportRow = {
  index: number; caseId: string; title: string; rawPriority: string; finalPriority: string; displayPriority: string; corrected: string; isDisplayMismatch: string;
  isRawMismatch: string; resultSource: string; sampleKind: string; direction: string; usage: string; tags: string; expectedPriority: string; reasonCategory: string; patternCategory: string; userComment: string; addedAt: string; priorityDebug: string;
};
export type EvalDatasetItem = {
  case_id: string; title: string; raw_priority: string; final_priority: string; display_priority: string; result_source: string; direction: string; tags: string[];
  usage: SampleUsage; sample_kind: SampleKind; priority_debug: Record<string, unknown> | null; user_comment: string; expected_priority: string; reason_category: string; pattern_category: string; added_at: number;
  manual_confirmed?: boolean; manual_confirmed_at?: number;
};
export type RecommendationDraft = { patterns: string[]; rule_suggestions: string[]; prompt_suggestions: string[]; routing_suggestions: string[] };
export type OptimizationInputPackage = {
  summary: { total_samples: number; display_mismatch_count: number; corrected_count: number; top_directions: string[]; top_tags: string[] };
  samples: EvalDatasetItem[]; draft_recommendations: RecommendationDraft;
};

export const SAMPLE_POOL_STORAGE_KEY = 'tg_priority_anomaly_pool_v1';
export const SAMPLE_TAG_ORDER: SampleTag[] = ['over_raised', 'over_lowered', 'display_mismatch', 'rule_adjusted', 'manual_review'];
export const REASON_CATEGORY_OPTIONS: Array<{ value: ReasonCategory; label: string }> = [
  { value: '', label: '未分类' }, { value: 'core_flow', label: '核心流程' }, { value: 'exception_path', label: '异常路径' }, { value: 'boundary_condition', label: '边界条件' },
  { value: 'state_transition', label: '状态迁移' }, { value: 'redundant_case', label: '冗余用例' }, { value: 'display_issue', label: '展示问题' }, { value: 'other', label: '其他' },
];
export const PATTERN_CATEGORY_OPTIONS: Array<{ value: PatternCategory; label: string }> = [
  { value: '', label: '未分类' },
  { value: 'core_flow_closure', label: '核心流程闭环' },
  { value: 'cross_page_flow', label: '跨页面流程' },
  { value: 'multi_step_interaction', label: '多步骤交互' },
  { value: 'state_transition_pattern', label: '状态流转' },
  { value: 'critical_path_coverage', label: '关键路径覆盖' },
  { value: 'complex_business_combination', label: '复杂业务组合' },
  { value: 'high_value_assertion', label: '高价值断言' },
  { value: 'boundary_effective_coverage', label: '边界有效覆盖' },
];

type CategoryInferenceInput = {
  sampleKind?: SampleKind | string | null;
  title?: unknown;
  userComment?: unknown;
  reasonCategory?: unknown;
  patternCategory?: unknown;
  patternClusterKey?: unknown;
  patternSummary?: unknown;
  tags?: unknown;
};

function buildCategoryInferenceText(input: CategoryInferenceInput): string {
  const tags = Array.isArray(input.tags) ? input.tags.join(' ') : '';
  return [
    input.patternCategory,
    input.reasonCategory,
    input.patternClusterKey,
    input.patternSummary,
    input.title,
    input.userComment,
    tags,
  ].map((part) => String(part ?? '').toLowerCase()).join(' ');
}

function hasAnyCategorySignal(text: string, tokens: string[]): boolean {
  return tokens.some((token) => text.includes(token));
}

export function inferPatternCategoryFromMode(input: CategoryInferenceInput): PatternCategory {
  const explicit = normalizePatternCategory(input.patternCategory);
  if (explicit) return explicit;
  const text = buildCategoryInferenceText(input);
  if (!text.trim()) return '';
  if (hasAnyCategorySignal(text, ['boundary', '边界'])) return 'boundary_effective_coverage';
  if (hasAnyCategorySignal(text, ['assertion', 'assert', '断言', '校验点'])) return 'high_value_assertion';
  if (hasAnyCategorySignal(text, ['complex', 'combination', '组合', '复杂业务'])) return 'complex_business_combination';
  if (hasAnyCategorySignal(text, ['critical', '关键路径', '阻断', '高风险'])) return 'critical_path_coverage';
  if (hasAnyCategorySignal(text, ['state', 'transition', 'consistency', '状态', '流转', '迁移', '一致性'])) return 'state_transition_pattern';
  if (hasAnyCategorySignal(text, ['cross_page', 'cross-page', 'cross page', 'cross_system', 'cross-system', '跨页面', '跨系统'])) return 'cross_page_flow';
  if (hasAnyCategorySignal(text, ['multi_step', 'multi-step', 'multi step', 'interaction', '多步骤', '交互', '连续操作'])) return 'multi_step_interaction';
  if (hasAnyCategorySignal(text, ['core_flow', 'closure', 'transaction', 'permission', 'scope', 'manual_final_business_coverage', 'business_flow', '核心', '主流程', '闭环', '权限', '范围', '业务流程'])) return 'core_flow_closure';
  return 'core_flow_closure';
}

export function inferReasonCategoryFromMode(input: CategoryInferenceInput): ReasonCategory {
  const explicit = normalizeReasonCategory(input.reasonCategory);
  if (explicit) return explicit;
  const text = buildCategoryInferenceText(input);
  if (!text.trim()) return '';
  if (hasAnyCategorySignal(text, ['display_issue', 'ui_display', 'ui-only', 'static ui', 'static display', 'low_value_ui', 'priority_overpromotion_for_low_value_ui_case', '展示', '页面', '样式', '布局', '文案'])) return 'display_issue';
  if (hasAnyCategorySignal(text, ['redundant', 'hallucination', 'duplicate', '重复', '冗余', '幻觉'])) return 'redundant_case';
  if (hasAnyCategorySignal(text, ['boundary', '边界'])) return 'boundary_condition';
  if (hasAnyCategorySignal(text, ['state', 'transition', 'consistency', '状态', '流转', '迁移', '一致性'])) return 'state_transition';
  if (hasAnyCategorySignal(text, ['exception', 'error', 'failure', 'failed', 'rollback', '异常', '错误', '失败', '回滚'])) return 'exception_path';
  if (hasAnyCategorySignal(text, ['core_flow', 'critical', 'transaction', 'permission', 'scope', 'business_flow', '核心', '主流程', '关键路径', '权限', '业务流程'])) return 'core_flow';
  return 'other';
}

export function applyAutoCategoryFromMode(sample: PrioritySample): PrioritySample {
  if (sample.sampleKind === 'positive') {
    if (sample.patternCategory) return sample;
    const patternCategory = inferPatternCategoryFromMode(sample);
    return patternCategory ? { ...sample, patternCategory } : sample;
  }
  if (sample.reasonCategory) return sample;
  const reasonCategory = inferReasonCategoryFromMode(sample);
  return reasonCategory ? { ...sample, reasonCategory } : sample;
}

export function normalizePriority(value: unknown): PriorityValue {
  const s = String(value ?? '').trim().toUpperCase();
  if (s === 'P0' || s === 'P1' || s === 'P2' || s === 'P3') return s;
  if (s === 'HIGH') return 'P0';
  if (s === 'MEDIUM') return 'P1';
  if (s === 'LOW') return 'P2';
  return '';
}
export function toPriorityDebug(input: unknown): Record<string, unknown> | null {
  if (!input || typeof input !== 'object' || Array.isArray(input)) return null;
  return input as Record<string, unknown>;
}
function pickPriorityFromDebug(debug: Record<string, unknown> | null, keys: string[]): PriorityValue {
  if (!debug) return '';
  for (const key of keys) {
    const normalized = normalizePriority(debug[key]);
    if (normalized) return normalized;
  }
  return '';
}
export function normalizeWeakLinkGenerationId(raw: unknown): number | null {
  const num = Number(raw);
  if (!Number.isFinite(num)) return null;
  const intVal = Math.trunc(num);
  if (intVal <= 0) return null;
  return intVal;
}
export function normalizeSampleKind(raw: unknown): SampleKind {
  const text = String(raw ?? '').trim().toLowerCase();
  return text === 'positive' ? 'positive' : 'anomaly';
}
function normalizeWeakLinkText(raw: unknown, maxLen: number): string {
  return String(raw ?? '').trim().replace(/\s+/g, ' ').slice(0, maxLen);
}
export function buildWeakLinkCaseKey(input: Pick<PriorityRow, 'caseId' | 'title' | 'rawPriority' | 'finalPriority' | 'displayPriority' | 'resultSource'>): string {
  const caseId = normalizeWeakLinkText(input.caseId, 64).toLowerCase();
  const title = normalizeWeakLinkText(input.title, 320).toLowerCase();
  const raw = normalizeWeakLinkText(input.rawPriority, 8).toUpperCase();
  const final = normalizeWeakLinkText(input.finalPriority, 8).toUpperCase();
  const display = normalizeWeakLinkText(input.displayPriority, 8).toUpperCase();
  const source = normalizeWeakLinkText(input.resultSource, 32).toLowerCase();
  return [caseId, title, raw, final, display, source].join('|');
}
function sanitizeSampleIdPart(raw: unknown, maxLen: number): string {
  const text = normalizeWeakLinkText(raw, maxLen).toLowerCase();
  return text.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, maxLen) || 'na';
}
export function parsePriorityDebugString(input: string): Record<string, unknown> | null {
  if (!input.trim()) return null;
  try { return toPriorityDebug(JSON.parse(input)); } catch { return null; }
}
export function normalizeReasonCategory(value: unknown): ReasonCategory {
  const s = String(value ?? '').trim();
  if (s === 'core_flow' || s === 'exception_path' || s === 'boundary_condition' || s === 'state_transition' || s === 'redundant_case' || s === 'display_issue' || s === 'other') return s;
  return '';
}
export function normalizePatternCategory(value: unknown): PatternCategory {
  const s = String(value ?? '').trim();
  if (
    s === 'core_flow_closure'
    || s === 'cross_page_flow'
    || s === 'multi_step_interaction'
    || s === 'state_transition_pattern'
    || s === 'critical_path_coverage'
    || s === 'complex_business_combination'
    || s === 'high_value_assertion'
    || s === 'boundary_effective_coverage'
  ) return s;
  return '';
}
export function extractCaseArray(result: any): any[] {
  if (Array.isArray(result)) return result;
  if (Array.isArray(result?.cases)) return result.cases;
  if (Array.isArray(result?.generated_result)) return result.generated_result;
  return [];
}
export function getDirection(row: PriorityRow): string {
  return `${row.rawPriority || '-'}->${row.finalPriority || '-'}`;
}
export function getPriorityWeight(v: PriorityValue): number {
  if (v === 'P0') return 4; if (v === 'P1') return 3; if (v === 'P2') return 2; if (v === 'P3') return 1; return 0;
}
export function classifySampleTags(row: PriorityRow): SampleTag[] {
  const tags: SampleTag[] = [];
  if (row.displayFinalMismatch) tags.push('display_mismatch');
  if (row.rawFinalMismatch) tags.push('rule_adjusted');
  const rw = getPriorityWeight(row.rawPriority);
  const fw = getPriorityWeight(row.finalPriority);
  if (rw > 0 && fw > 0) {
    if (fw > rw) tags.push('over_raised');
    if (fw < rw) tags.push('over_lowered');
  }
  if (!tags.length) tags.push('manual_review');
  return Array.from(new Set(tags));
}
export function resolveSampleUsage(tags: SampleTag[]): SampleUsage {
  if (tags.includes('manual_review')) return 'manual_review';
  if (tags.includes('over_raised') || tags.includes('over_lowered')) return 'prompt_opt';
  if (tags.includes('display_mismatch')) return 'rule_opt';
  return 'rule_opt';
}
export function sampleTagLabel(tag: SampleTag): string {
  if (tag === 'over_raised') return '过度抬高';
  if (tag === 'over_lowered') return '过度压低';
  if (tag === 'display_mismatch') return '展示异常';
  if (tag === 'rule_adjusted') return '规则修正';
  return '待人工确认';
}
export function sampleUsageLabel(usage: SampleUsage): string {
  if (usage === 'prompt_opt') return '提示词优化';
  if (usage === 'rule_opt') return '规则优化';
  if (usage === 'retrieval_opt') return '检索优化';
  return '人工复核';
}
export function resultSourceLabel(source: ResultSource | string | null | undefined): string {
  if (source === 'streaming_preview') return '流式预览';
  if (source === 'final_persisted') return '最终结果';
  if (source === 'none') return '无';
  return String(source || '未知');
}
export function sampleKindLabel(kind: SampleKind | string | null | undefined): string {
  return kind === 'positive' ? '正向' : '异常';
}
export function directionLabel(direction: string | null | undefined): string {
  return String(direction || '-').replace('->', ' → ');
}
export function priorityDebugSourceLabel(source: string | null | undefined): string {
  const s = String(source || '').trim();
  if (s === 'sample_pool_expected_priority') return '样本池期望优先级';
  if (s === 'priority_debug') return '调试判定';
  if (s === 'original_list') return '原始列表';
  return s || '未知';
}

export function buildRows(result: any, resultSource: ResultSource): PriorityRow[] {
  return extractCaseArray(result).map((item, idx) => {
    const debug = toPriorityDebug(item?.priorityDebug) ?? toPriorityDebug(item?.meta?.priority_debug);
    const listDisplayPriority = normalizePriority(item?.priority ?? item?.displayPriority);
    const debugRawPriority = pickPriorityFromDebug(debug, ['original_priority', 'model_priority', 'raw_priority', 'source_priority']);
    const debugFinalPriority = pickPriorityFromDebug(debug, ['final_priority', 'resolved_priority', 'priority_after_rules', 'adjusted_priority', 'target_priority', 'priority']);
    const rawPriority = normalizePriority(item?.rawPriority) || debugRawPriority || listDisplayPriority;
    const displayPriority = listDisplayPriority;
    const finalPriority = normalizePriority(item?.finalPriority) || debugFinalPriority || listDisplayPriority;
    const caseId = String(item?.id || item?.case_id || `CASE-${idx + 1}`);
    const title = String(item?.description || item?.title || item?.name || '').trim();
    const rawFinalMismatch = Boolean(rawPriority && finalPriority && rawPriority !== finalPriority);
    const displayFinalMismatch = Boolean(displayPriority && finalPriority && displayPriority !== finalPriority);
    return {
      index: idx + 1, caseId, title, rawPriority, finalPriority, displayPriority, corrected: rawFinalMismatch,
      rawFinalMismatch, displayFinalMismatch, resultSource, priorityDebug: debug,
    };
  });
}
export function toSample(row: PriorityRow, options?: { generationId?: number | null; sampleKind?: SampleKind }): PrioritySample {
  const tags = classifySampleTags(row);
  const sampleKind = normalizeSampleKind(options?.sampleKind ?? 'anomaly');
  const weakLinkGenerationId = normalizeWeakLinkGenerationId(options?.generationId ?? null);
  const weakLinkCaseKey = buildWeakLinkCaseKey(row);
  const sampleId = [
    `kind-${sampleKind}`,
    `gid-${weakLinkGenerationId ?? 'none'}`,
    `case-${sanitizeSampleIdPart(row.caseId, 64)}`,
    `raw-${sanitizeSampleIdPart(row.rawPriority || '-', 8)}`,
    `final-${sanitizeSampleIdPart(row.finalPriority || '-', 8)}`,
    `display-${sanitizeSampleIdPart(row.displayPriority || '-', 8)}`,
    `src-${sanitizeSampleIdPart(row.resultSource, 32)}`,
    `title-${sanitizeSampleIdPart(row.title, 72)}`,
  ].join('__');
  return {
    sampleId, caseId: row.caseId, title: row.title || '', rawPriority: row.rawPriority || '-', finalPriority: row.finalPriority || '-', displayPriority: row.displayPriority || '-',
    resultSource: row.resultSource, sampleKind, direction: getDirection(row), corrected: row.corrected, isDisplayMismatch: row.displayFinalMismatch, isRawMismatch: row.rawFinalMismatch,
    priorityDebug: row.priorityDebug ? JSON.stringify(row.priorityDebug) : '', tags, usage: resolveSampleUsage(tags), userComment: '', expectedPriority: '', reasonCategory: '', patternCategory: '', addedAt: Date.now(),
    weakLinkCaseKey,
    weakLinkGenerationId,
    manualConfirmed: false, manualConfirmedAt: undefined,
    source: SAMPLE_SOURCES.PRIORITY_DEBUG_MANUAL_ADD,
    sourceType: SAMPLE_SOURCES.PRIORITY_DEBUG_MANUAL_ADD,
    sourceId: null,
    sourceCaseId: row.caseId || undefined,
    confidence: null,
    persistedSampleId: sampleId,
  };
}
export function buildTransitions(rows: PriorityRow[]): Array<{ transition: string; count: number }> {
  const counts = new Map<string, number>();
  for (const row of rows) {
    if (!row.rawFinalMismatch) continue;
    const key = `${row.rawPriority || '-'}->${row.finalPriority || '-'}`;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return Array.from(counts.entries()).map(([transition, count]) => ({ transition, count })).sort((a, b) => b.count - a.count || a.transition.localeCompare(b.transition));
}
export function compareRows(a: PriorityRow, b: PriorityRow): number {
  if (a.displayFinalMismatch !== b.displayFinalMismatch) return a.displayFinalMismatch ? -1 : 1;
  if (a.rawFinalMismatch !== b.rawFinalMismatch) return a.rawFinalMismatch ? -1 : 1;
  return a.index - b.index;
}
export function matchPriority(value: PriorityValue, filter: string): boolean {
  if (filter === 'all') return true;
  return value === filter;
}
export function toExportRows(rows: PriorityRow[]): ExportRow[] {
  return rows.map((row) => {
    const tags = classifySampleTags(row);
    return {
      index: row.index, caseId: row.caseId, title: row.title || '', rawPriority: row.rawPriority || '-', finalPriority: row.finalPriority || '-', displayPriority: row.displayPriority || '-',
      corrected: row.corrected ? 'true' : 'false', isDisplayMismatch: row.displayFinalMismatch ? 'true' : 'false', isRawMismatch: row.rawFinalMismatch ? 'true' : 'false',
      resultSource: resultSourceLabel(row.resultSource), sampleKind: '异常', direction: directionLabel(getDirection(row)), usage: sampleUsageLabel(resolveSampleUsage(tags)), tags: tags.map(sampleTagLabel).join('|'), expectedPriority: '-', reasonCategory: '-', patternCategory: '-', userComment: '-', addedAt: '-',
      priorityDebug: row.priorityDebug ? JSON.stringify(row.priorityDebug) : '',
    };
  });
}
export function toSamplePoolExportRows(samples: PrioritySample[]): ExportRow[] {
  return samples.map((s, i) => ({
    index: i + 1, caseId: s.caseId, title: s.title, rawPriority: s.rawPriority, finalPriority: s.finalPriority, displayPriority: s.displayPriority,
    corrected: s.corrected ? 'true' : 'false', isDisplayMismatch: s.isDisplayMismatch ? 'true' : 'false', isRawMismatch: s.isRawMismatch ? 'true' : 'false',
    resultSource: resultSourceLabel(s.resultSource), sampleKind: sampleKindLabel(s.sampleKind), direction: directionLabel(s.direction), usage: sampleUsageLabel(s.usage), tags: s.tags.map(sampleTagLabel).join('|'), expectedPriority: s.expectedPriority || '-', reasonCategory: s.reasonCategory || '-', patternCategory: s.patternCategory || '-',
    userComment: s.userComment || '-', addedAt: String(s.addedAt), priorityDebug: s.priorityDebug || '',
  }));
}
export function toEvalDataset(samples: PrioritySample[]): EvalDatasetItem[] {
  return samples.map((s) => ({
    case_id: s.caseId, title: s.title, raw_priority: s.rawPriority, final_priority: s.finalPriority, display_priority: s.displayPriority, result_source: s.resultSource,
    direction: s.direction, tags: s.tags, usage: s.usage, sample_kind: s.sampleKind, priority_debug: parsePriorityDebugString(s.priorityDebug), user_comment: s.userComment || '',
    expected_priority: s.expectedPriority || '', reason_category: s.reasonCategory || '', pattern_category: s.patternCategory || '', added_at: s.addedAt,
    manual_confirmed: Boolean(s.manualConfirmed), manual_confirmed_at: s.manualConfirmedAt,
  }));
}
export function escapeCsvValue(value: unknown): string {
  const text = String(value ?? '');
  return `"${text.replace(/"/g, '""')}"`;
}
export function buildCsvFromRows(rows: ExportRow[]): string {
  const header = ['index', 'caseId', 'title', 'rawPriority', 'finalPriority', 'displayPriority', 'corrected', 'isDisplayMismatch', 'isRawMismatch', 'resultSource', 'sampleKind', 'direction', 'usage', 'tags', 'expectedPriority', 'reasonCategory', 'patternCategory', 'userComment', 'addedAt', 'priorityDebug'];
  const lines = [header.join(',')];
  for (const row of rows) {
    lines.push([row.index, row.caseId, row.title, row.rawPriority, row.finalPriority, row.displayPriority, row.corrected, row.isDisplayMismatch, row.isRawMismatch, row.resultSource, row.sampleKind, row.direction, row.usage, row.tags, row.expectedPriority, row.reasonCategory, row.patternCategory, row.userComment, row.addedAt, row.priorityDebug].map(escapeCsvValue).join(','));
  }
  return lines.join('\n');
}
export function downloadText(content: string, filename: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
export function downloadCsv(csvText: string, filename: string): void { downloadText(csvText, filename, 'text/csv;charset=utf-8;'); }
export function downloadJson(jsonText: string, filename: string): void { downloadText(jsonText, filename, 'application/json;charset=utf-8;'); }
export async function copyTextToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return; }
  const textArea = document.createElement('textarea');
  textArea.value = text;
  textArea.style.position = 'fixed';
  textArea.style.opacity = '0';
  document.body.appendChild(textArea);
  textArea.focus();
  textArea.select();
  document.execCommand('copy');
  textArea.remove();
}

export function buildSummaryLine(displayMismatchCount: number, correctedCount: number, unchangedCount: number): string {
  if (displayMismatchCount === 0) return '展示异常：0（当前展示已与最终结果一致）';
  return `展示异常：${displayMismatchCount}，已修正：${correctedCount}，未变化：${unchangedCount}`;
}
export function buildCopyText(rows: PriorityRow[], summaryLine: string, title: string): string {
  const lines = [title, summaryLine, `共 ${rows.length} 条`];
  rows.forEach((row, idx) => lines.push(`${idx + 1}. [用例=${row.caseId}] ${row.title || '-'} | 原始=${row.rawPriority || '-'} | 最终=${row.finalPriority || '-'} | 展示=${row.displayPriority || '-'} | 来源=${resultSourceLabel(row.resultSource)} | 方向=${directionLabel(getDirection(row))}`));
  return lines.join('\n');
}
export function parseSamplePool(raw: string | null): PrioritySample[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const seenSampleIds = new Map<string, number>();
    return parsed.filter((item) => item && typeof item === 'object').map((item, index) => {
      const tags: SampleTag[] = Array.isArray(item.tags) ? (item.tags as unknown[]).filter((tag: unknown): tag is SampleTag => SAMPLE_TAG_ORDER.includes(tag as SampleTag)) : [];
      const sampleKind = normalizeSampleKind(item.sampleKind ?? item.sample_kind ?? (item.signal_type === 'positive' ? 'positive' : 'anomaly'));
      const inferredTags: SampleTag[] = sampleKind === 'positive' ? ['manual_review'] : ['display_mismatch', 'manual_review'];
      const safeTags: SampleTag[] = tags.length > 0 ? Array.from(new Set<SampleTag>(tags)) : inferredTags;
      const addedAtRaw = Number(item.addedAt ?? item.added_at ?? item.created_at);
      const manualConfirmed = Boolean(item.manualConfirmed ?? item.manual_confirmed);
      const manualConfirmedAtMixed = item.manualConfirmedAt ?? item.manual_confirmed_at;
      const manualConfirmedAtNumber = Number(manualConfirmedAtMixed);
      const manualConfirmedAtTs = Number.isFinite(manualConfirmedAtNumber) && manualConfirmedAtNumber > 0
        ? manualConfirmedAtNumber
        : Date.parse(String(manualConfirmedAtMixed || ''));
      const expectedPriority = normalizePriority(item.expectedPriority ?? item.expected_priority ?? '');
      const rawReasonCategory = item.reasonCategory ?? item.reason_category ?? '';
      const rawPatternCategory = item.patternCategory ?? item.pattern_category ?? '';
      const reasonCategory = normalizeReasonCategory(rawReasonCategory);
      const patternCategory = normalizePatternCategory(rawPatternCategory);
      const weakLinkCaseKey = normalizeWeakLinkText(item.weakLinkCaseKey ?? item.weak_link_case_key ?? '', 512);
      const weakLinkGenerationId = normalizeWeakLinkGenerationId(item.weakLinkGenerationId ?? item.weak_link_generation_id ?? null);
      const explicitSource = String(item.source ?? item.sampleSource ?? item.sample_source ?? '').trim();
      const hasPriorityDebugTableShape = Boolean(
        item.resultSource
        || item.caseId
        || item.rawPriority
        || item.finalPriority
        || item.displayPriority
        || item.weakLinkCaseKey
        || item.weak_link_case_key
      );
      const source = explicitSource || (hasPriorityDebugTableShape ? SAMPLE_SOURCES.PRIORITY_DEBUG_MANUAL_ADD : '');
      const caseId = String(item.caseId ?? item.case_id ?? item.id ?? item.sampleId ?? item.sample_id ?? `SAMPLE-${index + 1}`);
      const title = String(item.title ?? item.source_case_title ?? item.pattern_summary ?? item.user_comment ?? '');
      const userComment = String(item.userComment ?? item.user_comment ?? '');
      const rawPriority = String(item.rawPriority ?? item.raw_priority ?? '-');
      const finalPriority = String(item.finalPriority ?? item.final_priority ?? item.expected_priority ?? '-');
      const displayPriority = String(item.displayPriority ?? item.display_priority ?? '-');
      const resultSource = item.resultSource === 'streaming_preview' || item.resultSource === 'final_persisted' ? item.resultSource : 'none';
      const direction = String(item.direction || `${rawPriority || '-'}->${finalPriority || '-'}`);
      const patternClusterKey = String(item.patternClusterKey ?? item.pattern_cluster_key ?? '').trim() || null;
      const patternWeight = typeof (item.patternWeight ?? item.pattern_weight) === 'number'
        ? (item.patternWeight ?? item.pattern_weight) as number
        : null;
      const confidence = typeof (item.confidence ?? item.pattern_confidence ?? item.patternConfidence) === 'number'
        ? (item.confidence ?? item.pattern_confidence ?? item.patternConfidence) as number
        : null;
      const categoryInput = {
        sampleKind,
        title,
        userComment,
        reasonCategory: rawReasonCategory,
        patternCategory: rawPatternCategory,
        patternClusterKey,
        patternSummary: item.patternSummary ?? item.pattern_summary ?? item.patternCanonical ?? item.pattern_canonical ?? '',
        tags: safeTags,
      };
      const resolvedReasonCategory = reasonCategory || (sampleKind === 'anomaly' ? inferReasonCategoryFromMode(categoryInput) : '');
      const resolvedPatternCategory = patternCategory || (sampleKind === 'positive' ? inferPatternCategoryFromMode(categoryInput) : '');
      const baseSampleId = String(
        item.sampleId
        || item.sample_id
        || [
          `kind-${sampleKind}`,
          `gid-${weakLinkGenerationId ?? item.generation_id ?? 'none'}`,
          `case-${sanitizeSampleIdPart(caseId, 64)}`,
          `source-${sanitizeSampleIdPart(item.source || 'sample-pool', 48)}`,
          `summary-${sanitizeSampleIdPart(title, 72)}`,
        ].join('__')
      );
      const duplicateIndex = seenSampleIds.get(baseSampleId) || 0;
      seenSampleIds.set(baseSampleId, duplicateIndex + 1);
      const sampleId = duplicateIndex > 0 ? `${baseSampleId}__dup-${duplicateIndex}` : baseSampleId;
      return applyAutoCategoryFromMode({
        sampleId,
        caseId,
        title,
        rawPriority,
        finalPriority,
        displayPriority,
        resultSource,
        sampleKind,
        direction,
        corrected: Boolean(item.corrected),
        isDisplayMismatch: Boolean(item.isDisplayMismatch),
        isRawMismatch: Boolean(item.isRawMismatch),
        priorityDebug: typeof item.priorityDebug === 'string' ? item.priorityDebug : JSON.stringify(item.priorityDebug ?? item.quality_ledger ?? ''),
        tags: safeTags,
        usage: resolveSampleUsage(safeTags),
        userComment,
        expectedPriority,
        reasonCategory: resolvedReasonCategory,
        patternCategory: resolvedPatternCategory,
        addedAt: Number.isFinite(addedAtRaw) && addedAtRaw > 0 ? addedAtRaw : Date.now(),
        weakLinkCaseKey: weakLinkCaseKey || undefined,
        weakLinkGenerationId,
        manualConfirmed,
        manualConfirmedAt: Number.isFinite(manualConfirmedAtTs) && manualConfirmedAtTs > 0 ? manualConfirmedAtTs : undefined,
        source: source || undefined,
        status: String(item.status ?? item.sampleStatus ?? 'active').trim().toLowerCase() || 'active',
        persistedSampleId: baseSampleId,
        learningStatus: (item.learningStatus ?? item.learning_status ?? null) as string | null | undefined,
        learningConfirmedAt: (item.learningConfirmedAt ?? item.learning_confirmed_at ?? null) as string | null | undefined,
        learningConfirmedBy: typeof (item.learningConfirmedBy ?? item.learning_confirmed_by) === 'number'
          ? (item.learningConfirmedBy ?? item.learning_confirmed_by) as number
          : null,
        sourceType: String(item.sourceType ?? item.source_type ?? item.source ?? item.sampleSource ?? '').trim() || undefined,
        sourceId: typeof (item.sourceId ?? item.source_id) === 'number' ? (item.sourceId ?? item.source_id) as number : null,
        sourceCaseId: String(item.sourceCaseId ?? item.source_case_id ?? '').trim() || undefined,
        confidence,
        patternClusterKey,
        patternWeight,
      } satisfies PrioritySample);
    });
  } catch {
    return [];
  }
}
export function mergeSamples(existing: PrioritySample[], incoming: PrioritySample[]): PrioritySample[] {
  const map = new Map<string, PrioritySample>();
  for (const sample of existing) map.set(sample.sampleId, sample);
  for (const sample of incoming) {
    const old = map.get(sample.sampleId);
    if (!old) { map.set(sample.sampleId, applyAutoCategoryFromMode(sample)); continue; }
    const mergedTags = Array.from(new Set<SampleTag>([...old.tags, ...sample.tags]));
    map.set(sample.sampleId, applyAutoCategoryFromMode({
      ...old, ...sample, tags: mergedTags, usage: resolveSampleUsage(mergedTags), addedAt: old.addedAt || sample.addedAt, priorityDebug: sample.priorityDebug || old.priorityDebug,
      userComment: old.userComment || sample.userComment, expectedPriority: old.expectedPriority || sample.expectedPriority, reasonCategory: old.reasonCategory || sample.reasonCategory, patternCategory: old.patternCategory || sample.patternCategory,
      sampleKind: normalizeSampleKind(sample.sampleKind || old.sampleKind || 'anomaly'),
      weakLinkCaseKey: sample.weakLinkCaseKey || old.weakLinkCaseKey,
      weakLinkGenerationId: sample.weakLinkGenerationId ?? old.weakLinkGenerationId ?? null,
      manualConfirmed: Boolean(old.manualConfirmed || sample.manualConfirmed),
      manualConfirmedAt: old.manualConfirmedAt || sample.manualConfirmedAt,
    }));
  }
  return Array.from(map.values()).sort((a, b) => b.addedAt - a.addedAt);
}
export function getSampleTagCounts(samples: PrioritySample[]): Record<SampleTag, number> {
  const counts: Record<SampleTag, number> = { over_raised: 0, over_lowered: 0, display_mismatch: 0, rule_adjusted: 0, manual_review: 0 };
  for (const sample of samples) for (const tag of sample.tags) counts[tag] += 1;
  return counts;
}
export function getSampleDirectionTop(samples: PrioritySample[], limit = 5): Array<{ direction: string; count: number }> {
  const counts = new Map<string, number>();
  for (const sample of samples) counts.set(sample.direction || '-', (counts.get(sample.direction || '-') || 0) + 1);
  return Array.from(counts.entries()).map(([direction, count]) => ({ direction, count })).sort((a, b) => b.count - a.count || a.direction.localeCompare(b.direction)).slice(0, limit);
}
export function getTopTags(tagCounts: Record<SampleTag, number>, limit = 3): string[] {
  return SAMPLE_TAG_ORDER.map((tag) => ({ tag, count: tagCounts[tag] })).filter((x) => x.count > 0).sort((a, b) => b.count - a.count).slice(0, limit).map((x) => x.tag);
}
export function buildRecommendationDraft(samples: PrioritySample[], tagCounts: Record<SampleTag, number>, topDirections: Array<{ direction: string; count: number }>): RecommendationDraft {
  const patterns: string[] = [];
  const ruleSuggestions: string[] = [];
  const promptSuggestions: string[] = [];
  const routingSuggestions: string[] = [];
  const coreFlowCount = samples.filter((s) => s.reasonCategory === 'core_flow').length;
  const displayIssueCount = samples.filter((s) => s.reasonCategory === 'display_issue').length;
  const expectedP0Count = samples.filter((s) => s.expectedPriority === 'P0').length;
  if (tagCounts.over_lowered > 0) {
    patterns.push('存在“过度压低”样本，关键场景可能被降级。');
    ruleSuggestions.push('对核心流程与阻断型失败增加最低优先级下限，避免被压到 P2。');
    promptSuggestions.push('补充“关键链路失败场景优先级不得低于 P1，阻断场景优先 P0”。');
    routingSuggestions.push('优先回流到提示词优化与优先级语义规则校准。');
  }
  if (tagCounts.over_raised > 0) {
    patterns.push('存在“过度抬高”样本，补充性或展示态用例被抬高。');
    ruleSuggestions.push('对补充性展示态、弱边界校验设定上限，默认不高于 P2。');
    promptSuggestions.push('补充“展示态、映射类与长尾补充默认 P2，除非存在明确阻断证据”。');
    routingSuggestions.push('优先回流到提示词优化，次级回流规则优化。');
  }
  if (tagCounts.display_mismatch > 0 || displayIssueCount > 0) {
    patterns.push('仍有展示链路不一致，页面展示值与最终值存在偏差。');
    ruleSuggestions.push('增加 displayPriority 与 finalPriority 一致性校验点，减少展示层误导。');
    promptSuggestions.push('此类问题优先修正展示链路，不建议通过生成提示词规避。');
    routingSuggestions.push('优先回流到前端展示链路与规则优化。');
  }
  if (coreFlowCount > 0 || expectedP0Count > 0) {
    patterns.push('样本中有核心流程被人工标注为高优先级，当前判定与业务认知存在偏差。');
    ruleSuggestions.push('将核心流程相关标签（core_flow）纳入优先级放行条件，减少误降级。');
    promptSuggestions.push('强化“核心流程与发布阻断风险优先识别，证据不足时保持 P1”。');
    routingSuggestions.push('优先回流提示词优化与规则优化双通道联调。');
  }
  const hasManualReview = samples.some((s) => s.tags.includes('manual_review') || s.reasonCategory === 'other');
  if (hasManualReview) {
    patterns.push('部分样本需人工确认，当前规则无法稳定覆盖边界语义。');
    ruleSuggestions.push('为人工复核样本补充判定样例并形成白名单/黑名单规则。');
    routingSuggestions.push('先走人工复核，再决定提示词优化或规则优化。');
  }
  if (topDirections.length > 0) patterns.push(`主要修正方向集中在：${topDirections.slice(0, 3).map((x) => `${x.direction}(${x.count})`).join('，')}。`);
  if (!patterns.length) {
    patterns.push('当前样本池暂无明显误判模式，建议继续积累样本后再做规则调整。');
    ruleSuggestions.push('维持现有优先级规则，仅补充少量人工复核样本。');
    promptSuggestions.push('暂不调整提示词主体，仅保留保守优先级约束。');
    routingSuggestions.push('当前优先走人工复核。');
  }
  return { patterns: Array.from(new Set(patterns)), rule_suggestions: Array.from(new Set(ruleSuggestions)), prompt_suggestions: Array.from(new Set(promptSuggestions)), routing_suggestions: Array.from(new Set(routingSuggestions)) };
}
export function buildRecommendationText(draft: RecommendationDraft): string {
  const lines: string[] = ['常见误判模式']; draft.patterns.forEach((x, i) => lines.push(`${i + 1}. ${x}`)); lines.push('');
  lines.push('建议补充的 priority 规则'); draft.rule_suggestions.forEach((x, i) => lines.push(`${i + 1}. ${x}`)); lines.push('');
  lines.push('可能需要调整的 prompt 约束'); draft.prompt_suggestions.forEach((x, i) => lines.push(`${i + 1}. ${x}`)); lines.push('');
  lines.push('建议回流方向'); draft.routing_suggestions.forEach((x, i) => lines.push(`${i + 1}. ${x}`));
  return lines.join('\n');
}
export function buildOptimizationInputPackage(samples: PrioritySample[], tagCounts: Record<SampleTag, number>, topDirections: Array<{ direction: string; count: number }>): OptimizationInputPackage {
  return {
    summary: { total_samples: samples.length, display_mismatch_count: samples.filter((s) => s.isDisplayMismatch).length, corrected_count: samples.filter((s) => s.corrected).length, top_directions: topDirections.map((x) => x.direction), top_tags: getTopTags(tagCounts, 5) },
    samples: toEvalDataset(samples),
    draft_recommendations: buildRecommendationDraft(samples, tagCounts, topDirections),
  };
}

