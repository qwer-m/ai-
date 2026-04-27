import { useEffect, useMemo, useRef, useState } from 'react';
import { Badge, Button, Dropdown, Form } from 'react-bootstrap';
import {
  SAMPLE_POOL_STORAGE_KEY,
  SAMPLE_TAG_ORDER,
  REASON_CATEGORY_OPTIONS,
  PATTERN_CATEGORY_OPTIONS,
  parseSamplePool,
  buildRows,
  compareRows,
  matchPriority,
  buildTransitions,
  buildSummaryLine,
  getSampleTagCounts,
  getSampleDirectionTop,
  buildCsvFromRows,
  toExportRows,
  downloadCsv,
  buildCopyText,
  copyTextToClipboard,
  mergeSamples,
  toSample,
  toSamplePoolExportRows,
  toEvalDataset,
  downloadJson,
  buildRecommendationText,
  buildRecommendationDraft,
  buildOptimizationInputPackage,
  sampleTagLabel,
  normalizePriority,
  normalizeReasonCategory,
  normalizePatternCategory,
  classifySampleTags,
  resolveSampleUsage,
  buildWeakLinkCaseKey,
  normalizeWeakLinkGenerationId,
} from './PriorityDebugTable.helpers';
import type { Props, PriorityRow, PrioritySample, SampleKind, SampleTag, ViewFilter } from './PriorityDebugTable.helpers';
import { fetchPrioritySamplePool, savePrioritySamplePool } from './debugService';

type SamplePoolFilter = 'all' | 'anomaly' | 'positive';

export function PriorityDebugTable({
  result,
  resultSource,
  projectId,
  generationId,
  enableSamplePoolFeedback,
  onToggleSamplePoolFeedback,
}: Props) {
  const sampleKindLabel = (kind: SampleKind): string => (kind === 'positive' ? '正向' : '异常');
  const [samplePoolFilter, setSamplePoolFilter] = useState<SamplePoolFilter>('all');
  const [viewFilter, setViewFilter] = useState<ViewFilter>('all');
  const [rawFilter, setRawFilter] = useState<string>('all');
  const [debugFilter, setDebugFilter] = useState<string>('all');
  const [actionMessage, setActionMessage] = useState<string>('');
  const [recommendationText, setRecommendationText] = useState<string>('');
  const [lastSavedAt, setLastSavedAt] = useState<number | null>(null);
  const [lastCloudSavedAt, setLastCloudSavedAt] = useState<number | null>(null);
  const [isCloudSyncing, setIsCloudSyncing] = useState<boolean>(false);
  const [cloudSyncError, setCloudSyncError] = useState<string>('');
  const [confirmingManualTagSampleId, setConfirmingManualTagSampleId] = useState<string | null>(null);
  const skipNextRemoteSaveRef = useRef<boolean>(false);
  const hasHydratedRemoteRef = useRef<boolean>(false);
  const remoteSaveTimerRef = useRef<number | null>(null);
  const samplePoolStorageKey = projectId ? `${SAMPLE_POOL_STORAGE_KEY}_${projectId}` : SAMPLE_POOL_STORAGE_KEY;
  const [samplePool, setSamplePool] = useState<PrioritySample[]>(() => {
    if (typeof window === 'undefined') return [];
    const projectRaw = window.localStorage.getItem(samplePoolStorageKey);
    return parseSamplePool(projectRaw);
  });

  useEffect(() => {
    if (typeof window === 'undefined') return;
    // 切换项目后，先完成当前项目数据水合，再允许写入，避免把上一项目数据写入新项目 key。
    if (projectId && !hasHydratedRemoteRef.current) return;
    window.localStorage.setItem(samplePoolStorageKey, JSON.stringify(samplePool));
    setLastSavedAt(Date.now());
  }, [samplePool, samplePoolStorageKey, projectId]);

  useEffect(() => {
    if (!projectId) {
      hasHydratedRemoteRef.current = false;
      setLastCloudSavedAt(null);
      setCloudSyncError('');
      if (typeof window !== 'undefined') {
        const localRaw = window.localStorage.getItem(SAMPLE_POOL_STORAGE_KEY);
        skipNextRemoteSaveRef.current = true;
        setSamplePool(parseSamplePool(localRaw));
      }
      return;
    }
    let cancelled = false;
    hasHydratedRemoteRef.current = false;
    setIsCloudSyncing(true);
    setCloudSyncError('');
    (async () => {
      try {
        const localRaw = typeof window !== 'undefined' ? window.localStorage.getItem(samplePoolStorageKey) : null;
        const localSamples = parseSamplePool(localRaw);
        skipNextRemoteSaveRef.current = true;
        setSamplePool(localSamples);
        const payload = await fetchPrioritySamplePool(projectId);
        if (cancelled) return;
        const remoteSamples = parseSamplePool(JSON.stringify(payload?.samples || []));
        // 只要云端读取成功，就以当前项目云端数据为准（包括空数组），确保项目隔离不被本地旧缓存污染。
        skipNextRemoteSaveRef.current = true;
        setSamplePool(remoteSamples);
        const cloudTs = Date.parse(String(payload?.updated_at || ''));
        setLastCloudSavedAt(Number.isFinite(cloudTs) ? cloudTs : null);
      } catch {
        if (cancelled) return;
        setCloudSyncError('云端加载失败，已使用本地缓存');
      } finally {
        if (!cancelled) {
          hasHydratedRemoteRef.current = true;
          setIsCloudSyncing(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, samplePoolStorageKey]);

  useEffect(() => {
    if (!projectId || !hasHydratedRemoteRef.current) return;
    if (skipNextRemoteSaveRef.current) {
      skipNextRemoteSaveRef.current = false;
      return;
    }
    if (typeof window === 'undefined') return;
    if (remoteSaveTimerRef.current !== null) {
      window.clearTimeout(remoteSaveTimerRef.current);
      remoteSaveTimerRef.current = null;
    }
    remoteSaveTimerRef.current = window.setTimeout(async () => {
      setIsCloudSyncing(true);
      try {
        const payload = await savePrioritySamplePool(projectId, {
          generation_id: generationId ?? null,
          samples: samplePool as unknown as any[],
        });
        setCloudSyncError('');
        const cloudTs = Date.parse(String(payload?.updated_at || ''));
        if (Number.isFinite(cloudTs)) setLastCloudSavedAt(cloudTs);
      } catch {
        setCloudSyncError('云端自动保存失败，数据仍保留在本地浏览器');
      } finally {
        setIsCloudSyncing(false);
      }
    }, 800);
    return () => {
      if (remoteSaveTimerRef.current !== null) {
        window.clearTimeout(remoteSaveTimerRef.current);
        remoteSaveTimerRef.current = null;
      }
    };
  }, [projectId, generationId, samplePool]);

  const rows = useMemo(() => buildRows(result, resultSource), [result, resultSource]);
  const sortedRows = useMemo(() => [...rows].sort(compareRows), [rows]);
  const sampleTagCounts = useMemo(() => getSampleTagCounts(samplePool), [samplePool]);
  const sampleDirectionTop = useMemo(() => getSampleDirectionTop(samplePool, 5), [samplePool]);
  const samplePoolByWeakLink = useMemo(() => {
    const withGenerationMap = new Map<string, PrioritySample>();
    const withoutGenerationMap = new Map<string, PrioritySample>();
    samplePool.forEach((sample) => {
      const caseKey = String(sample.weakLinkCaseKey || '').trim();
      if (!caseKey) return;
      const generationId = normalizeWeakLinkGenerationId(sample.weakLinkGenerationId ?? null);
      if (generationId !== null) {
        const mapKey = `${generationId}::${caseKey}`;
        const prev = withGenerationMap.get(mapKey);
        if (!prev || (sample.addedAt || 0) >= (prev.addedAt || 0)) withGenerationMap.set(mapKey, sample);
        return;
      }
      const prev = withoutGenerationMap.get(caseKey);
      if (!prev || (sample.addedAt || 0) >= (prev.addedAt || 0)) withoutGenerationMap.set(caseKey, sample);
    });
    return { withGenerationMap, withoutGenerationMap };
  }, [samplePool]);

  const resolveLinkedSample = (row: PriorityRow): PrioritySample | undefined => {
    const rowWeakLinkCaseKey = buildWeakLinkCaseKey(row);
    const rowGenerationId = normalizeWeakLinkGenerationId(generationId ?? null);
    return rowGenerationId !== null
      ? samplePoolByWeakLink.withGenerationMap.get(`${rowGenerationId}::${rowWeakLinkCaseKey}`)
      : samplePoolByWeakLink.withoutGenerationMap.get(rowWeakLinkCaseKey);
  };

  const resolvePriorityDebugValue = (row: PriorityRow, linkedSample?: PrioritySample) => {
    const sampleExpectedPriority = normalizePriority(linkedSample?.expectedPriority ?? '');
    if (sampleExpectedPriority) return sampleExpectedPriority;
    if (row.finalPriority) return row.finalPriority;
    if (row.displayPriority) return row.displayPriority;
    return row.rawPriority;
  };

  type EvaluatedRow = {
    row: PriorityRow;
    rowForDisplay: PriorityRow;
    linkedSample?: PrioritySample;
    priorityDebugPriority: ReturnType<typeof normalizePriority>;
  };

  const evaluatedRows = useMemo<EvaluatedRow[]>(
    () => sortedRows.map((row) => {
      const linkedSample = resolveLinkedSample(row);
      const priorityDebugPriority = resolvePriorityDebugValue(row, linkedSample);
      const rawMismatch = Boolean(row.rawPriority && priorityDebugPriority && row.rawPriority !== priorityDebugPriority);
      const displayMismatch = Boolean(row.displayPriority && priorityDebugPriority && row.displayPriority !== priorityDebugPriority);
      const rowForDisplay: PriorityRow = {
        ...row,
        finalPriority: priorityDebugPriority,
        corrected: rawMismatch,
        rawFinalMismatch: rawMismatch,
        displayFinalMismatch: displayMismatch,
      };
      return { row, rowForDisplay, linkedSample, priorityDebugPriority };
    }),
    [sortedRows, samplePoolByWeakLink, generationId]
  );

  const filteredRowViews = useMemo(
    () => evaluatedRows.filter(({ rowForDisplay }) => {
      if (!matchPriority(rowForDisplay.rawPriority, rawFilter)) return false;
      if (!matchPriority(rowForDisplay.finalPriority, debugFilter)) return false;
      if (viewFilter === 'corrected') return rowForDisplay.corrected;
      if (viewFilter === 'unchanged') return !rowForDisplay.corrected;
      if (viewFilter === 'raw_mismatch') return rowForDisplay.rawFinalMismatch;
      if (viewFilter === 'display_mismatch') return rowForDisplay.displayFinalMismatch;
      return true;
    }),
    [evaluatedRows, rawFilter, debugFilter, viewFilter]
  );

  const filteredRows = useMemo(() => filteredRowViews.map((item) => item.rowForDisplay), [filteredRowViews]);
  const correctedCount = useMemo(() => evaluatedRows.filter((item) => item.rowForDisplay.corrected).length, [evaluatedRows]);
  const unchangedCount = useMemo(() => evaluatedRows.filter((item) => !item.rowForDisplay.corrected).length, [evaluatedRows]);
  const displayMismatchCount = useMemo(() => evaluatedRows.filter((item) => item.rowForDisplay.displayFinalMismatch).length, [evaluatedRows]);
  const transitions = useMemo(() => buildTransitions(evaluatedRows.map((item) => item.rowForDisplay)), [evaluatedRows]);
  const displayMismatchRows = useMemo(() => evaluatedRows.filter((item) => item.rowForDisplay.displayFinalMismatch).map((item) => item.rowForDisplay), [evaluatedRows]);
  const anomalyRows = useMemo(() => evaluatedRows.filter((item) => item.rowForDisplay.displayFinalMismatch || item.rowForDisplay.rawFinalMismatch).map((item) => item.rowForDisplay), [evaluatedRows]);
  const summaryLine = useMemo(() => buildSummaryLine(displayMismatchCount, correctedCount, unchangedCount), [displayMismatchCount, correctedCount, unchangedCount]);
  const filteredSamplePool = useMemo(
    () => samplePool.filter((sample) => {
      if (samplePoolFilter === 'all') return true;
      if (samplePoolFilter === 'anomaly') return sample.sampleKind === 'anomaly';
      return sample.sampleKind === 'positive';
    }),
    [samplePool, samplePoolFilter]
  );

  const buildPriorityDebugDisplay = (row: PriorityRow, linkedSample: PrioritySample | undefined, priorityDebugPriority: ReturnType<typeof normalizePriority>): Record<string, unknown> | null => {
    if (!linkedSample && !row.priorityDebug) return null;
    const base = row.priorityDebug && typeof row.priorityDebug === 'object' ? row.priorityDebug : {};
    const sampleExpectedPriority = normalizePriority(linkedSample?.expectedPriority ?? '');
    const priorityDebugSource = sampleExpectedPriority
      ? 'sample_pool_expected_priority'
      : (row.priorityDebug ? 'priority_debug' : 'original_list');
    return {
      ...base,
      priority_debug_priority: priorityDebugPriority || '',
      priority_debug_source: priorityDebugSource,
      final_priority: priorityDebugPriority || '',
      final_priority_source: priorityDebugSource,
      ...(linkedSample
        ? {
          manual_feedback: {
            case_id: linkedSample.caseId,
            sample_kind: linkedSample.sampleKind,
            tags: linkedSample.tags,
            usage: linkedSample.usage,
            expected_priority: linkedSample.expectedPriority || '',
            reason_category: linkedSample.reasonCategory || '',
            pattern_category: linkedSample.patternCategory || '',
            user_comment: linkedSample.userComment || '',
            weak_link_case_key: linkedSample.weakLinkCaseKey || '',
            weak_link_generation_id: normalizeWeakLinkGenerationId(linkedSample.weakLinkGenerationId ?? null),
            manual_confirmed: Boolean(linkedSample.manualConfirmed),
            manual_confirmed_at: linkedSample.manualConfirmedAt ? new Date(linkedSample.manualConfirmedAt).toISOString() : null,
          },
        }
        : {}),
    };
  };

  const resolveCategoryDisplayBadge = (
    rowForDisplay: PriorityRow,
    linkedSample: PrioritySample | undefined
  ): { text: string; bg: 'primary' | 'danger' } | null => {
    if (linkedSample) {
      if (linkedSample.sampleKind === 'positive') {
        const patternLabel = PATTERN_CATEGORY_OPTIONS.find((opt) => opt.value === linkedSample.patternCategory)?.label || '';
        if (patternLabel && linkedSample.patternCategory) return { text: patternLabel, bg: 'primary' };
        const reasonLabel = REASON_CATEGORY_OPTIONS.find((opt) => opt.value === linkedSample.reasonCategory)?.label || '';
        if (reasonLabel && linkedSample.reasonCategory) return { text: reasonLabel, bg: 'primary' };
        return { text: '正向', bg: 'primary' };
      }
      const reasonLabel = REASON_CATEGORY_OPTIONS.find((opt) => opt.value === linkedSample.reasonCategory)?.label || '';
      if (reasonLabel && linkedSample.reasonCategory) return { text: reasonLabel, bg: 'danger' };
      const patternLabel = PATTERN_CATEGORY_OPTIONS.find((opt) => opt.value === linkedSample.patternCategory)?.label || '';
      if (patternLabel && linkedSample.patternCategory) return { text: patternLabel, bg: 'danger' };
      return { text: '异常', bg: 'danger' };
    }
    if (rowForDisplay.displayFinalMismatch) return { text: '展示异常', bg: 'danger' };
    return null;
  };

  const handleExportCurrent = () => {
    if (!filteredRows.length) { setActionMessage('当前无可导出数据'); return; }
    const csvText = buildCsvFromRows(toExportRows(filteredRows));
    downloadCsv(csvText, `priority-debug-${new Date().toISOString().replace(/[:.]/g, '-')}.csv`);
    setActionMessage(`已导出当前筛选结果（${filteredRows.length} 条）`);
  };
  const handleCopyCurrent = async () => {
    if (!filteredRows.length) { setActionMessage('当前无可复制数据'); return; }
    try { await copyTextToClipboard(buildCopyText(filteredRows, summaryLine, '当前筛选结果')); setActionMessage(`已复制当前筛选结果（${filteredRows.length} 条）`); } catch { setActionMessage('复制失败，请重试'); }
  };
  const handleCopyDisplayMismatch = async () => {
    if (!displayMismatchRows.length) { setActionMessage('当前无展示异常数据'); return; }
    try { await copyTextToClipboard(buildCopyText(displayMismatchRows, summaryLine, '展示异常列表')); setActionMessage(`已复制展示异常列表（${displayMismatchRows.length} 条）`); } catch { setActionMessage('复制失败，请重试'); }
  };
  const handleAddAnomalySamples = () => {
    if (!anomalyRows.length) { setActionMessage('当前无可加入异常样本池的数据'); return; }
    setSamplePool((prev) => {
      const next = mergeSamples(prev, anomalyRows.map((row) => toSample(row, { generationId, sampleKind: 'anomaly' })));
      setActionMessage(`已加入异常样本池（样本池共 ${next.length} 条）`);
      return next;
    });
  };
  const handleAddCurrentRowToPool = (row: PriorityRow, sampleKind: SampleKind = 'anomaly') => {
    setSamplePool((prev) => {
      const next = mergeSamples(prev, [toSample(row, { generationId, sampleKind })]);
      setActionMessage(`已加入${sampleKindLabel(sampleKind)}样本池：${row.caseId}`);
      return next;
    });
  };
  const handleUpdateSample = (sampleId: string, patch: Partial<Pick<PrioritySample, 'userComment' | 'expectedPriority' | 'reasonCategory' | 'patternCategory'>>) => {
    setSamplePool((prev) => prev.map((sample) => (sample.sampleId === sampleId ? { ...sample, ...patch } : sample)));
  };
  const handleRollbackSample = (sampleId: string, caseId: string) => {
    setSamplePool((prev) => {
      const next = prev.filter((sample) => sample.sampleId !== sampleId);
      setActionMessage(`已回退到下方样本池：${caseId}`);
      return next;
    });
  };
  const handleConfirmManualReview = async (sampleId: string, caseId: string) => {
    let nextSamples: PrioritySample[] = [];
    let didUpdate = false;
    if (projectId && hasHydratedRemoteRef.current) skipNextRemoteSaveRef.current = true;
    setSamplePool((prev) => {
      nextSamples = prev.map((sample) => {
        if (sample.sampleId !== sampleId) return sample;
        didUpdate = true;
        const filteredTags = sample.tags.filter((tag) => tag !== 'manual_review');
        const nextTags: SampleTag[] = filteredTags.length > 0
          ? filteredTags
          : [sample.isDisplayMismatch ? 'display_mismatch' : 'rule_adjusted'];
        return {
          ...sample,
          tags: nextTags,
          usage: resolveSampleUsage(nextTags),
          manualConfirmed: true,
          manualConfirmedAt: Date.now(),
        };
      });
      return nextSamples;
    });
    if (!didUpdate) return;
    setActionMessage(`已确认：${caseId}（前端已移除“待人工确认”）`);
    if (!projectId || !hasHydratedRemoteRef.current) return;
    setConfirmingManualTagSampleId(sampleId);
    setIsCloudSyncing(true);
    try {
      const payload = await savePrioritySamplePool(projectId, {
        generation_id: generationId ?? null,
        samples: nextSamples as unknown as any[],
      });
      const cloudTs = Date.parse(String(payload?.updated_at || ''));
      if (Number.isFinite(cloudTs)) setLastCloudSavedAt(cloudTs);
      setCloudSyncError('');
      setActionMessage(`已确认并写入云端：${caseId}`);
    } catch {
      setCloudSyncError('云端写入失败，数据仍保留在本地浏览器');
      setActionMessage(`已确认：${caseId}，但云端写入失败`);
    } finally {
      setIsCloudSyncing(false);
      setConfirmingManualTagSampleId((prev) => (prev === sampleId ? null : prev));
    }
  };
  const handleExportSamplePool = () => {
    if (!samplePool.length) { setActionMessage('样本池为空，暂无可导出数据'); return; }
    downloadCsv(buildCsvFromRows(toSamplePoolExportRows(samplePool)), `priority-anomaly-sample-pool-${new Date().toISOString().replace(/[:.]/g, '-')}.csv`);
    setActionMessage(`已导出样本池（${samplePool.length} 条）`);
  };
  const handleExportEvalDatasetJson = () => {
    if (!samplePool.length) { setActionMessage('样本池为空，暂无可导出评估数据'); return; }
    downloadJson(`${JSON.stringify(toEvalDataset(samplePool), null, 2)}\n`, `priority-eval-dataset-${new Date().toISOString().replace(/[:.]/g, '-')}.json`);
    setActionMessage(`已导出评估数据集 JSON（${samplePool.length} 条）`);
  };
  const handleGenerateRuleSuggestions = () => {
    if (!samplePool.length) { setActionMessage('样本池为空，无法生成规则建议'); return; }
    setRecommendationText(buildRecommendationText(buildRecommendationDraft(samplePool, sampleTagCounts, sampleDirectionTop)));
    setActionMessage('已基于当前样本池生成规则建议');
  };
  const handleCopyRuleSuggestions = async () => {
    if (!recommendationText.trim()) { setActionMessage('请先生成规则建议'); return; }
    try { await copyTextToClipboard(recommendationText); setActionMessage('已复制规则建议文本'); } catch { setActionMessage('复制失败，请重试'); }
  };
  const handleExportOptimizationInputPackage = () => {
    if (!samplePool.length) { setActionMessage('样本池为空，暂无可导出输入包'); return; }
    const payload = buildOptimizationInputPackage(samplePool, sampleTagCounts, sampleDirectionTop);
    downloadJson(`${JSON.stringify(payload, null, 2)}\n`, `priority-optimization-input-package-${new Date().toISOString().replace(/[:.]/g, '-')}.json`);
    setActionMessage(`已导出优化建议输入包（${samplePool.length} 条样本）`);
  };
  const handleClearSamplePool = () => {
    setSamplePool([]);
    setRecommendationText('');
    setActionMessage('样本池已清空');
  };
  const handleSaveSamplePool = async () => {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(samplePoolStorageKey, JSON.stringify(samplePool));
      setLastSavedAt(Date.now());
      if (projectId) {
        setIsCloudSyncing(true);
        const payload = await savePrioritySamplePool(projectId, {
          generation_id: generationId ?? null,
          samples: samplePool as unknown as any[],
        });
        const cloudTs = Date.parse(String(payload?.updated_at || ''));
        if (Number.isFinite(cloudTs)) setLastCloudSavedAt(cloudTs);
        setCloudSyncError('');
        setActionMessage('已保存到云端（并同步本地）');
      } else {
        setActionMessage('已保存到本地浏览器');
      }
    } catch {
      setCloudSyncError('云端保存失败，数据仍保留在本地浏览器');
      setActionMessage('保存失败，请检查网络或浏览器存储权限');
    } finally {
      setIsCloudSyncing(false);
    }
  };
  const handleToggleSamplePoolFeedback = () => {
    const next = !enableSamplePoolFeedback;
    onToggleSamplePoolFeedback(next);
    setActionMessage(next ? '样本池回流已开启' : '样本池回流已关闭');
  };

  return (
    <div className="rag-debug-card rounded-2xl shadow-md p-4 border bg-white dark:bg-slate-900">
      <div className="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
        <h6 className="mb-0 fw-bold">Priority Debug</h6>
        <div className="small text-muted rag-debug-muted">resultSource: {resultSource}</div>
      </div>

      <div className={displayMismatchCount > 0 ? 'alert alert-danger py-2 px-3 mb-3' : 'alert alert-success py-2 px-3 mb-3'}>
        <div className="d-flex flex-wrap align-items-center gap-2">
          <span className="fw-semibold">展示异常：</span>
          <Badge bg={displayMismatchCount > 0 ? 'danger' : 'success'}>{displayMismatchCount}</Badge>
          <span className="fw-semibold ms-2">已修正：</span>
          <Badge bg={correctedCount > 0 ? 'warning' : 'secondary'} text={correctedCount > 0 ? 'dark' : undefined}>{correctedCount}</Badge>
          <span className="fw-semibold ms-2">未变化：</span>
          <Badge bg="secondary">{unchangedCount}</Badge>
          {displayMismatchCount === 0 ? <span className="small">（当前展示已与最终结果一致）</span> : null}
        </div>
      </div>

      <div className="d-flex flex-wrap align-items-center gap-2 mb-3 tg-priority-actions">
        <Button size="sm" variant={samplePoolFilter === 'anomaly' ? 'primary' : 'outline-primary'} className={`tg-priority-sample-filter-btn ${samplePoolFilter === 'anomaly' ? 'is-active' : ''}`} onClick={() => setSamplePoolFilter('anomaly')}>异常</Button>
        <Button size="sm" variant={samplePoolFilter === 'positive' ? 'primary' : 'outline-primary'} className={`tg-priority-sample-filter-btn ${samplePoolFilter === 'positive' ? 'is-active' : ''}`} onClick={() => setSamplePoolFilter('positive')}>正常</Button>
        <Button size="sm" variant={samplePoolFilter === 'all' ? 'primary' : 'outline-primary'} className={`tg-priority-sample-filter-btn ${samplePoolFilter === 'all' ? 'is-active' : ''}`} onClick={() => setSamplePoolFilter('all')}>查看全部</Button>
        <Button size="sm" variant="outline-secondary" onClick={handleExportCurrent} disabled={!filteredRows.length}>导出当前结果</Button>
        <Button size="sm" variant="outline-secondary" onClick={() => void handleCopyCurrent()} disabled={!filteredRows.length}>复制当前结果</Button>
        <Button size="sm" variant="outline-secondary" onClick={() => void handleCopyDisplayMismatch()} disabled={!displayMismatchRows.length}>复制展示异常</Button>
        <Button size="sm" variant="outline-secondary" onClick={handleAddAnomalySamples} disabled={!anomalyRows.length}>加入异常样本池</Button>
        <Button size="sm" variant="outline-secondary" onClick={handleExportSamplePool} disabled={!samplePool.length}>导出样本池 CSV</Button>
        <Button size="sm" variant="outline-secondary" onClick={handleExportEvalDatasetJson} disabled={!samplePool.length}>导出为评估数据集（JSON）</Button>
        <Button size="sm" variant="outline-secondary" onClick={handleGenerateRuleSuggestions} disabled={!samplePool.length}>生成规则建议</Button>
        <Button size="sm" variant="outline-secondary" onClick={() => void handleCopyRuleSuggestions()} disabled={!recommendationText.trim()}>复制规则建议</Button>
        <Button size="sm" variant="outline-secondary" onClick={handleExportOptimizationInputPackage} disabled={!samplePool.length}>导出优化建议输入包</Button>
        <Button size="sm" variant="outline-secondary" onClick={() => void handleSaveSamplePool()} disabled={!samplePool.length || isCloudSyncing}>
          {projectId ? (isCloudSyncing ? '同步中...' : '保存到云端') : '保存到本地'}
        </Button>
        <Button size="sm" variant="outline-danger" onClick={handleClearSamplePool} disabled={!samplePool.length}>清空样本池</Button>
        <Button
          size="sm"
          variant={enableSamplePoolFeedback ? 'outline-success' : 'outline-secondary'}
          onClick={handleToggleSamplePoolFeedback}
        >
          样本池回流：{enableSamplePoolFeedback ? '开启' : '关闭'}
        </Button>
        {transitions.map((item) => <Badge key={item.transition} bg="dark">{item.transition} {item.count}</Badge>)}
      </div>
      {actionMessage ? <div className="small text-muted rag-debug-muted mb-3">{actionMessage}</div> : null}

      <div className="mb-3 p-2 border rounded-2">
        <div className="d-flex flex-wrap align-items-center gap-2 mb-2">
          <span className="fw-semibold">样本池：</span>
          <Badge bg={samplePool.length > 0 ? 'dark' : 'secondary'}>
            {samplePoolFilter === 'all' ? samplePool.length : `${filteredSamplePool.length}/${samplePool.length}`}
          </Badge>
          {samplePoolFilter !== 'all' ? <span className="small text-muted rag-debug-muted">当前筛选：{samplePoolFilter === 'positive' ? '正向' : '异常'}</span> : null}
        </div>
        <div className="small mb-2">
          <div className="fw-semibold mb-1">标签分布</div>
          {SAMPLE_TAG_ORDER.map((tag) => {
            if (tag === 'rule_adjusted') return null;
            const count = sampleTagCounts[tag];
            const ratio = samplePool.length > 0 ? Math.round((count / samplePool.length) * 100) : 0;
            return (
              <div key={tag} className="d-flex align-items-center gap-2 mb-1">
                <span style={{ minWidth: 92 }}>{sampleTagLabel(tag)}</span>
                <div className="flex-grow-1 tg-priority-ratio-track">
                  <div className="tg-priority-ratio-fill" style={{ width: `${ratio}%` }} />
                </div>
                <span>{count}</span>
              </div>
            );
          })}
        </div>
        <div className="small mb-2">
          <div className="fw-semibold mb-1">修正方向 Top5</div>
          {sampleDirectionTop.length ? (
            <div className="d-flex flex-wrap gap-2">
              {sampleDirectionTop.map((item) => <Badge key={`sample-direction-${item.direction}`} bg="secondary">{item.direction} {item.count}</Badge>)}
            </div>
          ) : (
            <div className="text-muted rag-debug-muted">暂无方向统计</div>
          )}
        </div>
        <div className="small text-muted rag-debug-muted">可在下方样本条目填写 user_comment / expected_priority / reason_category / pattern_category，用于后续优化建议。</div>
        <div className="small text-muted rag-debug-muted">仅填写了以上字段的样本会进入下一轮 control_state（保守闭环，避免噪音样本自动回流）。</div>
        <div className="small text-muted rag-debug-muted">异常样本使用“原因分类”；正向样本使用“模式分类”。</div>
        <div className="small text-muted rag-debug-muted mt-1">
          编辑内容会自动保存到当前浏览器。
          {lastSavedAt ? ` 最近保存：${new Date(lastSavedAt).toLocaleString('zh-CN', { hour12: false })}` : ''}
        </div>
        <div className="small text-muted rag-debug-muted mt-1">
          {projectId
            ? (
              isCloudSyncing
                ? '云端同步中...'
                : (
                  cloudSyncError
                    ? cloudSyncError
                    : (lastCloudSavedAt
                      ? `云端最近保存：${new Date(lastCloudSavedAt).toLocaleString('zh-CN', { hour12: false })}`
                      : '云端暂无保存记录')
                )
            )
            : '未选择项目，当前仅本地保存'}
        </div>
      </div>

      {recommendationText ? (
        <div className="mb-3 p-2 border rounded-2">
          <div className="fw-semibold mb-2">规则建议草稿</div>
          <Form.Control as="textarea" rows={10} value={recommendationText} readOnly className="mb-2" />
          <div className="small text-muted rag-debug-muted">该建议由本地样本统计与人工解释拼装生成，可直接复制用于后续模型总结或评审。</div>
        </div>
      ) : null}

      {samplePool.length > 0 ? (
        <div className="mb-3 p-2 border rounded-2">
          <div className="fw-semibold mb-2">样本池解释编辑</div>
          <div className="table-responsive">
            <table className="table table-sm align-middle mb-0 tg-priority-sample-table">
              <thead>
                <tr>
                  <th className="tg-priority-sample-case-col">用例</th>
                  <th className="tg-priority-sample-direction-col">修正方向</th>
                  <th className="tg-priority-sample-tag-col">标签</th>
                  <th className="tg-priority-sample-priority-col">期望优先级</th>
                  <th className="tg-priority-sample-reason-col">分类（异常:原因 / 正向:模式）</th>
                  <th className="tg-priority-sample-comment-col">用户备注</th>
                  <th className="tg-priority-sample-action-col">操作</th>
                </tr>
              </thead>
              <tbody>
                {!filteredSamplePool.length ? (
                  <tr>
                    <td colSpan={7} className="text-center text-muted py-3">
                      当前筛选下暂无样本
                    </td>
                  </tr>
                ) : null}
                {filteredSamplePool.map((sample) => {
                  const kindSelected = samplePoolFilter !== 'all' && sample.sampleKind === samplePoolFilter;
                  const categoryOptions = sample.sampleKind === 'positive' ? PATTERN_CATEGORY_OPTIONS : REASON_CATEGORY_OPTIONS;
                  const categoryValue = sample.sampleKind === 'positive' ? sample.patternCategory : sample.reasonCategory;
                  const categoryLabel = categoryOptions.find((opt) => opt.value === categoryValue)?.label || '未分类';
                  return (
                  <tr key={sample.sampleId} className={kindSelected ? 'tg-priority-sample-row-kind-selected' : undefined}>
                    <td className="tg-priority-sample-case-col">
                      <div className="fw-semibold">{sample.caseId}</div>
                      <div className="small text-muted rag-debug-muted tg-priority-sample-case-title">{sample.title || '-'}</div>
                    </td>
                    <td className="tg-priority-sample-direction-col">{sample.direction}</td>
                    <td className="tg-priority-sample-tag-col">
                      <div className="d-flex gap-1 tg-priority-tags-wrap">
                        <Badge bg={sample.sampleKind === 'positive' ? 'primary' : 'danger'} className={`tg-priority-kind-badge ${kindSelected ? 'is-selected' : ''}`}>{sampleKindLabel(sample.sampleKind)}</Badge>
                        {sample.manualConfirmed ? <Badge bg="success">已确认</Badge> : null}
                        {sample.tags.map((tag) => {
                          if (tag === 'rule_adjusted') return null;
                          if (tag !== 'manual_review') {
                            return (
                              <Badge key={`${sample.sampleId}-${tag}`} bg="light" text="dark">
                                {sampleTagLabel(tag)}
                              </Badge>
                            );
                          }
                          const isConfirming = confirmingManualTagSampleId === sample.sampleId;
                          return (
                            <span
                              key={`${sample.sampleId}-${tag}`}
                              className="tg-priority-manual-confirm-wrap"
                            >
                              <Badge bg="light" text="dark" className="tg-priority-manual-pending-pill">{sampleTagLabel(tag)}</Badge>
                              <Badge
                                bg="success"
                                pill
                                className={`tg-priority-manual-confirm-pill ${isConfirming ? 'is-disabled' : ''}`}
                                role="button"
                                tabIndex={isConfirming ? -1 : 0}
                                onClick={() => {
                                  if (isConfirming) return;
                                  void handleConfirmManualReview(sample.sampleId, sample.caseId);
                                }}
                                onKeyDown={(e) => {
                                  if (isConfirming) return;
                                  if (e.key === 'Enter' || e.key === ' ') {
                                    e.preventDefault();
                                    void handleConfirmManualReview(sample.sampleId, sample.caseId);
                                  }
                                }}
                              >
                                {isConfirming ? '确认中...' : '确认'}
                              </Badge>
                            </span>
                          );
                        })}
                      </div>
                    </td>
                    <td className="tg-priority-sample-priority-col">
                      <Form.Select size="sm" value={sample.expectedPriority} onChange={(e) => handleUpdateSample(sample.sampleId, { expectedPriority: normalizePriority(e.target.value) })} style={{ width: 90 }}>
                        <option value="">-</option><option value="P0">P0</option><option value="P1">P1</option><option value="P2">P2</option><option value="P3">P3</option>
                      </Form.Select>
                    </td>
                    <td className="tg-priority-sample-reason-col">
                      <Dropdown
                        className="tg-priority-select-dropdown"
                        focusFirstItemOnShow={false}
                        onSelect={(eventKey) => {
                          const nextValue = String(eventKey ?? '');
                          if (sample.sampleKind === 'positive') {
                            handleUpdateSample(sample.sampleId, { patternCategory: normalizePatternCategory(nextValue) });
                            return;
                          }
                          handleUpdateSample(sample.sampleId, { reasonCategory: normalizeReasonCategory(nextValue) });
                        }}
                      >
                        <Dropdown.Toggle
                          size="sm"
                          id={`sample-category-${sample.sampleId}`}
                          className="tg-priority-select-toggle"
                        >
                          {categoryLabel}
                        </Dropdown.Toggle>
                        <Dropdown.Menu className="tg-priority-select-menu">
                          {categoryOptions.map((opt) => (
                            <Dropdown.Item
                              key={`${sample.sampleKind}-category-${opt.value || 'none'}`}
                              eventKey={opt.value}
                              active={opt.value === categoryValue}
                              className="tg-priority-select-item"
                            >
                              {opt.label}
                            </Dropdown.Item>
                          ))}
                        </Dropdown.Menu>
                      </Dropdown>
                    </td>
                    <td className="tg-priority-sample-comment-col">
                      <Form.Control
                        size="sm"
                        as="textarea"
                        rows={2}
                        placeholder={sample.sampleKind === 'positive'
                          ? '填写该用例的优秀设计点，可复用的测试模式或覆盖思路'
                          : '填写该样本为何不合理、你期望的优先级依据'}
                        value={sample.userComment}
                        onChange={(e) => handleUpdateSample(sample.sampleId, { userComment: e.target.value })}
                      />
                    </td>
                    <td className="tg-priority-sample-action-col">
                      <Button size="sm" variant="outline-secondary" className="tg-priority-row-action-btn" onClick={() => handleRollbackSample(sample.sampleId, sample.caseId)}>case回退</Button>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      <div className="d-flex flex-wrap align-items-end gap-2 mb-3">
        <div><div className="small text-muted rag-debug-muted mb-1">视图</div><Form.Select size="sm" value={viewFilter} onChange={(e) => setViewFilter(e.target.value as ViewFilter)} style={{ width: 180 }}><option value="all">全部</option><option value="corrected">已修正</option><option value="unchanged">未变化</option><option value="raw_mismatch">原始 != 调试</option><option value="display_mismatch">展示 != 调试</option></Form.Select></div>
        <div><div className="small text-muted rag-debug-muted mb-1">原始优先级</div><Form.Select size="sm" value={rawFilter} onChange={(e) => setRawFilter(e.target.value)} style={{ width: 120 }}><option value="all">全部</option><option value="P0">P0</option><option value="P1">P1</option><option value="P2">P2</option><option value="P3">P3</option></Form.Select></div>
        <div><div className="small text-muted rag-debug-muted mb-1">优先级调试</div><Form.Select size="sm" value={debugFilter} onChange={(e) => setDebugFilter(e.target.value)} style={{ width: 120 }}><option value="all">全部</option><option value="P0">P0</option><option value="P1">P1</option><option value="P2">P2</option><option value="P3">P3</option></Form.Select></div>
      </div>

      <div className="table-responsive tg-priority-result-scroll">
        <table className="table table-sm align-middle mb-0 tg-priority-result-table">
          <thead>
            <tr>
              <th className="tg-priority-index-col">用例标号</th>
              <th className="tg-priority-case-col">用例</th>
              <th className="tg-priority-raw-col">原始优先级</th>
              <th className="tg-priority-source-col">结果来源</th>
              <th className="tg-priority-tag-col">标签</th>
              <th className="tg-priority-debug-col">优先级调试</th>
              <th className="tg-priority-action-col">操作</th>
            </tr>
          </thead>
          <tbody>
            {!filteredRows.length ? (<tr><td colSpan={7} className="text-center text-muted py-4">暂无符合筛选条件的数据</td></tr>) : null}
            {filteredRowViews.map(({ rowForDisplay, linkedSample, priorityDebugPriority }) => {
              const tags = linkedSample?.tags?.length ? linkedSample.tags : classifySampleTags(rowForDisplay);
              const priorityDebugDisplay = buildPriorityDebugDisplay(rowForDisplay, linkedSample, priorityDebugPriority);
              const categoryBadge = resolveCategoryDisplayBadge(rowForDisplay, linkedSample);
              return (
                <tr key={`${rowForDisplay.caseId}-${rowForDisplay.index}`} className={rowForDisplay.displayFinalMismatch ? 'tg-priority-display-mismatch-row' : undefined}>
                  <td className="tg-priority-index-col"><div className="fw-semibold">{rowForDisplay.caseId || '-'}</div>{categoryBadge ? <Badge bg={categoryBadge.bg} className="mt-1">{categoryBadge.text}</Badge> : null}</td>
                  <td className="tg-priority-case-col">
                    <div className="small text-muted rag-debug-muted tg-priority-case-title">{rowForDisplay.title || '-'}</div>
                  </td>
                  <td className="tg-priority-raw-col">{rowForDisplay.rawPriority || '-'}</td>
                  <td className="tg-priority-source-col">{rowForDisplay.resultSource}</td>
                  <td className="tg-priority-tag-col">
                    <div className="d-flex flex-wrap gap-1 tg-priority-tags-wrap">
                      {linkedSample?.manualConfirmed ? <Badge bg="success">已确认</Badge> : null}
                      {tags.filter((tag) => tag !== 'rule_adjusted').map((tag) => <Badge key={`${rowForDisplay.caseId}-${tag}`} bg="light" text="dark">{sampleTagLabel(tag)}</Badge>)}
                    </div>
                  </td>
                  <td className="tg-priority-debug-col">
                    {priorityDebugDisplay
                      ? (<details><summary>查看</summary><pre className="rag-priority-debug-pre">{JSON.stringify(priorityDebugDisplay, null, 2)}</pre></details>)
                      : (<span className="text-muted">-</span>)}
                  </td>
                  <td className="tg-priority-action-col">
                    <span className="tg-priority-row-action-switch">
                      <Button
                        size="sm"
                        variant="outline-secondary"
                        className="tg-priority-row-action-btn tg-priority-row-action-default"
                        onClick={() => handleAddCurrentRowToPool(rowForDisplay, 'anomaly')}
                      >
                        加入样本池
                      </Button>
                      <span className="tg-priority-row-action-options">
                        <Button
                          size="sm"
                          variant="outline-primary"
                          className="tg-priority-row-action-btn tg-priority-row-action-positive"
                          onClick={() => handleAddCurrentRowToPool(rowForDisplay, 'positive')}
                        >
                          正向
                        </Button>
                        <Button
                          size="sm"
                          variant="outline-danger"
                          className="tg-priority-row-action-btn tg-priority-row-action-anomaly"
                          onClick={() => handleAddCurrentRowToPool(rowForDisplay, 'anomaly')}
                        >
                          异常
                        </Button>
                      </span>
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}


