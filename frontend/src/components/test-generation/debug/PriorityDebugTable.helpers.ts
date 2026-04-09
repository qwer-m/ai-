export type ResultSource = 'none' | 'streaming_preview' | 'final_persisted';
export type PriorityValue = 'P0' | 'P1' | 'P2' | 'P3' | '';
export type ViewFilter = 'all' | 'corrected' | 'unchanged' | 'raw_mismatch' | 'display_mismatch';
export type SampleTag = 'over_raised' | 'over_lowered' | 'display_mismatch' | 'rule_adjusted' | 'manual_review';
export type SampleUsage = 'prompt_opt' | 'rule_opt' | 'retrieval_opt' | 'manual_review';
export type ReasonCategory = '' | 'core_flow' | 'exception_path' | 'boundary_condition' | 'state_transition' | 'redundant_case' | 'display_issue' | 'other';

export type Props = { result: any; resultSource: ResultSource };
export type PriorityRow = {
  index: number; caseId: string; title: string; rawPriority: PriorityValue; finalPriority: PriorityValue; displayPriority: PriorityValue;
  corrected: boolean; rawFinalMismatch: boolean; displayFinalMismatch: boolean; resultSource: ResultSource; priorityDebug: Record<string, unknown> | null;
};
export type PrioritySample = {
  sampleId: string; caseId: string; title: string; rawPriority: string; finalPriority: string; displayPriority: string; resultSource: ResultSource; direction: string;
  corrected: boolean; isDisplayMismatch: boolean; isRawMismatch: boolean; priorityDebug: string; tags: SampleTag[]; usage: SampleUsage;
  userComment: string; expectedPriority: PriorityValue; reasonCategory: ReasonCategory; addedAt: number;
};
export type ExportRow = {
  index: number; caseId: string; title: string; rawPriority: string; finalPriority: string; displayPriority: string; corrected: string; isDisplayMismatch: string;
  isRawMismatch: string; resultSource: string; direction: string; usage: string; tags: string; expectedPriority: string; reasonCategory: string; userComment: string; addedAt: string; priorityDebug: string;
};
export type EvalDatasetItem = {
  case_id: string; title: string; raw_priority: string; final_priority: string; display_priority: string; result_source: string; direction: string; tags: string[];
  usage: SampleUsage; priority_debug: Record<string, unknown> | null; user_comment: string; expected_priority: string; reason_category: string; added_at: number;
};
export type RecommendationDraft = { patterns: string[]; rule_suggestions: string[]; prompt_suggestions: string[]; routing_suggestions: string[] };
export type OptimizationInputPackage = {
  summary: { total_samples: number; display_mismatch_count: number; corrected_count: number; top_directions: string[]; top_tags: string[] };
  samples: EvalDatasetItem[]; draft_recommendations: RecommendationDraft;
};

export const SAMPLE_POOL_STORAGE_KEY = 'tg_priority_anomaly_pool_v1';
export const SAMPLE_TAG_ORDER: SampleTag[] = ['over_raised', 'over_lowered', 'display_mismatch', 'rule_adjusted', 'manual_review'];
export const REASON_CATEGORY_OPTIONS: Array<{ value: ReasonCategory; label: string }> = [
  { value: '', label: '未分类' }, { value: 'core_flow', label: 'core_flow' }, { value: 'exception_path', label: 'exception_path' }, { value: 'boundary_condition', label: 'boundary_condition' },
  { value: 'state_transition', label: 'state_transition' }, { value: 'redundant_case', label: 'redundant_case' }, { value: 'display_issue', label: 'display_issue' }, { value: 'other', label: 'other' },
];

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
export function parsePriorityDebugString(input: string): Record<string, unknown> | null {
  if (!input.trim()) return null;
  try { return toPriorityDebug(JSON.parse(input)); } catch { return null; }
}
export function normalizeReasonCategory(value: unknown): ReasonCategory {
  const s = String(value ?? '').trim();
  if (s === 'core_flow' || s === 'exception_path' || s === 'boundary_condition' || s === 'state_transition' || s === 'redundant_case' || s === 'display_issue' || s === 'other') return s;
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
  if (usage === 'prompt_opt') return 'prompt_opt';
  if (usage === 'rule_opt') return 'rule_opt';
  if (usage === 'retrieval_opt') return 'retrieval_opt';
  return 'manual_review';
}

export function buildRows(result: any, resultSource: ResultSource): PriorityRow[] {
  return extractCaseArray(result).map((item, idx) => {
    const debug = toPriorityDebug(item?.priorityDebug) ?? toPriorityDebug(item?.meta?.priority_debug);
    const rawPriority = normalizePriority(item?.rawPriority ?? debug?.original_priority ?? debug?.model_priority ?? '');
    const displayPriority = normalizePriority(item?.displayPriority ?? item?.priority);
    const finalPriority = normalizePriority(item?.finalPriority ?? debug?.final_priority ?? displayPriority);
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
export function toSample(row: PriorityRow): PrioritySample {
  const tags = classifySampleTags(row);
  const sampleId = `${row.caseId}__${row.rawPriority || '-'}__${row.finalPriority || '-'}__${row.displayPriority || '-'}__${row.resultSource}`;
  return {
    sampleId, caseId: row.caseId, title: row.title || '', rawPriority: row.rawPriority || '-', finalPriority: row.finalPriority || '-', displayPriority: row.displayPriority || '-',
    resultSource: row.resultSource, direction: getDirection(row), corrected: row.corrected, isDisplayMismatch: row.displayFinalMismatch, isRawMismatch: row.rawFinalMismatch,
    priorityDebug: row.priorityDebug ? JSON.stringify(row.priorityDebug) : '', tags, usage: resolveSampleUsage(tags), userComment: '', expectedPriority: '', reasonCategory: '', addedAt: Date.now(),
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
      resultSource: row.resultSource, direction: getDirection(row), usage: resolveSampleUsage(tags), tags: tags.join('|'), expectedPriority: '-', reasonCategory: '-', userComment: '-', addedAt: '-',
      priorityDebug: row.priorityDebug ? JSON.stringify(row.priorityDebug) : '',
    };
  });
}
export function toSamplePoolExportRows(samples: PrioritySample[]): ExportRow[] {
  return samples.map((s, i) => ({
    index: i + 1, caseId: s.caseId, title: s.title, rawPriority: s.rawPriority, finalPriority: s.finalPriority, displayPriority: s.displayPriority,
    corrected: s.corrected ? 'true' : 'false', isDisplayMismatch: s.isDisplayMismatch ? 'true' : 'false', isRawMismatch: s.isRawMismatch ? 'true' : 'false',
    resultSource: s.resultSource, direction: s.direction, usage: s.usage, tags: s.tags.join('|'), expectedPriority: s.expectedPriority || '-', reasonCategory: s.reasonCategory || '-',
    userComment: s.userComment || '-', addedAt: String(s.addedAt), priorityDebug: s.priorityDebug || '',
  }));
}
export function toEvalDataset(samples: PrioritySample[]): EvalDatasetItem[] {
  return samples.map((s) => ({
    case_id: s.caseId, title: s.title, raw_priority: s.rawPriority, final_priority: s.finalPriority, display_priority: s.displayPriority, result_source: s.resultSource,
    direction: s.direction, tags: s.tags, usage: s.usage, priority_debug: parsePriorityDebugString(s.priorityDebug), user_comment: s.userComment || '',
    expected_priority: s.expectedPriority || '', reason_category: s.reasonCategory || '', added_at: s.addedAt,
  }));
}
export function escapeCsvValue(value: unknown): string {
  const text = String(value ?? '');
  return `"${text.replace(/"/g, '""')}"`;
}
export function buildCsvFromRows(rows: ExportRow[]): string {
  const header = ['index', 'caseId', 'title', 'rawPriority', 'finalPriority', 'displayPriority', 'corrected', 'isDisplayMismatch', 'isRawMismatch', 'resultSource', 'direction', 'usage', 'tags', 'expectedPriority', 'reasonCategory', 'userComment', 'addedAt', 'priorityDebug'];
  const lines = [header.join(',')];
  for (const row of rows) {
    lines.push([row.index, row.caseId, row.title, row.rawPriority, row.finalPriority, row.displayPriority, row.corrected, row.isDisplayMismatch, row.isRawMismatch, row.resultSource, row.direction, row.usage, row.tags, row.expectedPriority, row.reasonCategory, row.userComment, row.addedAt, row.priorityDebug].map(escapeCsvValue).join(','));
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
  rows.forEach((row, idx) => lines.push(`${idx + 1}. [caseId=${row.caseId}] ${row.title || '-'} | raw=${row.rawPriority || '-'} | final=${row.finalPriority || '-'} | display=${row.displayPriority || '-'} | source=${row.resultSource} | direction=${getDirection(row)}`));
  return lines.join('\n');
}
export function parseSamplePool(raw: string | null): PrioritySample[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item) => item && typeof item === 'object').map((item) => {
      const tags: SampleTag[] = Array.isArray(item.tags) ? (item.tags as unknown[]).filter((tag: unknown): tag is SampleTag => SAMPLE_TAG_ORDER.includes(tag as SampleTag)) : [];
      const safeTags: SampleTag[] = tags.length > 0 ? Array.from(new Set<SampleTag>(tags)) : ['manual_review'];
      const addedAtRaw = Number(item.addedAt);
      const expectedPriority = normalizePriority(item.expectedPriority ?? item.expected_priority ?? '');
      const reasonCategory = normalizeReasonCategory(item.reasonCategory ?? item.reason_category ?? '');
      return {
        sampleId: String(item.sampleId || `${item.caseId || 'CASE'}__${addedAtRaw || Date.now()}`),
        caseId: String(item.caseId || ''),
        title: String(item.title || ''),
        rawPriority: String(item.rawPriority || '-'),
        finalPriority: String(item.finalPriority || '-'),
        displayPriority: String(item.displayPriority || '-'),
        resultSource: item.resultSource === 'streaming_preview' || item.resultSource === 'final_persisted' ? item.resultSource : 'none',
        direction: String(item.direction || '-'),
        corrected: Boolean(item.corrected),
        isDisplayMismatch: Boolean(item.isDisplayMismatch),
        isRawMismatch: Boolean(item.isRawMismatch),
        priorityDebug: typeof item.priorityDebug === 'string' ? item.priorityDebug : JSON.stringify(item.priorityDebug ?? ''),
        tags: safeTags,
        usage: resolveSampleUsage(safeTags),
        userComment: String(item.userComment ?? item.user_comment ?? ''),
        expectedPriority,
        reasonCategory,
        addedAt: Number.isFinite(addedAtRaw) && addedAtRaw > 0 ? addedAtRaw : Date.now(),
      } satisfies PrioritySample;
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
    if (!old) { map.set(sample.sampleId, sample); continue; }
    const mergedTags = Array.from(new Set<SampleTag>([...old.tags, ...sample.tags]));
    map.set(sample.sampleId, {
      ...old, ...sample, tags: mergedTags, usage: resolveSampleUsage(mergedTags), addedAt: old.addedAt || sample.addedAt, priorityDebug: sample.priorityDebug || old.priorityDebug,
      userComment: old.userComment || sample.userComment, expectedPriority: old.expectedPriority || sample.expectedPriority, reasonCategory: old.reasonCategory || sample.reasonCategory,
    });
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
    patterns.push('存在过度压低（over_lowered）样本，关键场景可能被降级。');
    ruleSuggestions.push('对核心流程与阻断型失败增加最低优先级下限，避免被压到 P2。');
    promptSuggestions.push('补充“关键链路失败场景优先级不得低于 P1，阻断场景优先 P0”。');
    routingSuggestions.push('优先回流到 prompt_opt 与 priority semantics 规则校准。');
  }
  if (tagCounts.over_raised > 0) {
    patterns.push('存在过度抬高（over_raised）样本，补充性/展示态 case 被抬高。');
    ruleSuggestions.push('对补充性展示态、弱边界校验设定上限，默认不高于 P2。');
    promptSuggestions.push('补充“展示态、映射类与长尾补充默认 P2，除非存在明确阻断证据”。');
    routingSuggestions.push('优先回流到 prompt_opt，次级回流 rule_opt。');
  }
  if (tagCounts.display_mismatch > 0 || displayIssueCount > 0) {
    patterns.push('仍有展示链路不一致（display_mismatch），页面展示值与最终值存在偏差。');
    ruleSuggestions.push('增加 displayPriority 与 finalPriority 一致性校验点，减少展示层误导。');
    promptSuggestions.push('此类问题优先修正展示链路，不建议通过生成 prompt 规避。');
    routingSuggestions.push('优先回流到前端展示链路与 rule_opt。');
  }
  if (coreFlowCount > 0 || expectedP0Count > 0) {
    patterns.push('样本中有核心流程被人工标注为高优先级，当前判定与业务认知存在偏差。');
    ruleSuggestions.push('将核心流程相关标签（core_flow）纳入优先级放行条件，减少误降级。');
    promptSuggestions.push('强化“核心流程与发布阻断风险优先识别，证据不足时保持 P1”。');
    routingSuggestions.push('优先回流 prompt_opt + rule_opt 双通道联调。');
  }
  const hasManualReview = samples.some((s) => s.tags.includes('manual_review') || s.reasonCategory === 'other');
  if (hasManualReview) {
    patterns.push('部分样本需人工确认，当前规则无法稳定覆盖边界语义。');
    ruleSuggestions.push('为 manual_review 样本补充判定样例并形成白名单/黑名单规则。');
    routingSuggestions.push('先走 manual_review，再决定 prompt_opt 或 rule_opt。');
  }
  if (topDirections.length > 0) patterns.push(`主要修正方向集中在：${topDirections.slice(0, 3).map((x) => `${x.direction}(${x.count})`).join('，')}。`);
  if (!patterns.length) {
    patterns.push('当前样本池暂无明显误判模式，建议继续积累样本后再做规则调整。');
    ruleSuggestions.push('维持现有优先级规则，仅补充少量人工复核样本。');
    promptSuggestions.push('暂不调整 prompt 主体，仅保留保守优先级约束。');
    routingSuggestions.push('当前优先走 manual_review。');
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

