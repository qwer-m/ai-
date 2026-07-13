import { useEffect, useMemo, useRef, useState } from 'react';
import { Badge, Button, Dropdown, Form } from 'react-bootstrap';
import { FaExclamationCircle } from 'react-icons/fa';
import {
  SAMPLE_POOL_STORAGE_KEY,
  SAMPLE_SOURCES,
  SAMPLE_TAG_ORDER,
  REASON_CATEGORY_OPTIONS,
  PATTERN_CATEGORY_OPTIONS,
  parseSamplePool,
  buildRows,
  compareRows,
  buildTransitions,
  sourceTypeLabel,
  categoryDisplayLabel,
  sourceTypeBadgeVariant,
  formatWeight,
  isInPattern,
  getSampleTagCounts,
  getSampleDirectionTop,
  buildCsvFromRows,
  downloadCsv,
  toSamplePoolExportRows,
  toEvalDataset,
  downloadJson,
  sampleTagLabel,
  resultSourceLabel,
  sampleKindLabel as sampleKindDisplayLabel,
  directionLabel,
  normalizePriority,
  normalizeReasonCategory,
  normalizePatternCategory,
  buildWeakLinkCaseKey,
  normalizeWeakLinkGenerationId,
} from './PriorityDebugTable.helpers';
import type { Props, PriorityRow, PrioritySample } from './PriorityDebugTable.helpers';
import {
  deletePrioritySamplePoolItem,
  fetchPrioritySamplePool,
} from './debugService';

type SamplePoolFilter = 'all' | 'anomaly' | 'positive';

export function PriorityDebugTable({
  result,
  resultSource,
  projectId,
  generationId,
  enableSamplePoolFeedback,
  onToggleSamplePoolFeedback,
}: Props) {
  const [samplePoolFilter, setSamplePoolFilter] = useState<SamplePoolFilter>('all');
  const [samplePoolStatusFilter, setSamplePoolStatusFilter] = useState<'active' | 'deleted' | 'all'>('active');
  const [actionMessage, setActionMessage] = useState<string>('');
  const [lastSavedAt, setLastSavedAt] = useState<number | null>(null);
  const [lastCloudSavedAt, setLastCloudSavedAt] = useState<number | null>(null);
  const [isCloudSyncing, setIsCloudSyncing] = useState<boolean>(false);
  const [cloudSyncError, setCloudSyncError] = useState<string>('');
  const hasHydratedRemoteRef = useRef<boolean>(false);
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
        setSamplePool(localSamples);
        const payload = await fetchPrioritySamplePool(projectId);
        if (cancelled) return;
        const remoteSamples = parseSamplePool(JSON.stringify(payload?.samples || []));
        // 只要云端读取成功，就以当前项目云端数据为准（包括空数组），确保项目隔离不被本地旧缓存污染。
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

  const correctedCount = useMemo(() => evaluatedRows.filter((item) => item.rowForDisplay.corrected).length, [evaluatedRows]);
  const unchangedCount = useMemo(() => evaluatedRows.filter((item) => !item.rowForDisplay.corrected).length, [evaluatedRows]);
  const displayMismatchCount = useMemo(() => evaluatedRows.filter((item) => item.rowForDisplay.displayFinalMismatch).length, [evaluatedRows]);
  const transitions = useMemo(() => buildTransitions(evaluatedRows.map((item) => item.rowForDisplay)), [evaluatedRows]);
  const filteredSamplePool = useMemo(
    () => samplePool.filter((sample) => {
      if (samplePoolFilter !== 'all') {
        if (samplePoolFilter === 'anomaly' && sample.sampleKind !== 'anomaly') return false;
        if (samplePoolFilter === 'positive' && sample.sampleKind !== 'positive') return false;
      }
      if (samplePoolStatusFilter === 'active' && sample.status === 'deleted') return false;
      if (samplePoolStatusFilter === 'deleted' && sample.status !== 'deleted') return false;
      return true;
    }),
    [samplePool, samplePoolFilter, samplePoolStatusFilter]
  );

  const handleUpdateSample = (sampleId: string, patch: Partial<Pick<PrioritySample, 'userComment' | 'expectedPriority' | 'reasonCategory' | 'patternCategory'>>) => {
    setSamplePool((prev) => prev.map((sample) => (sample.sampleId === sampleId ? { ...sample, ...patch } : sample)));
  };
  const handleRollbackSample = async (sampleId: string, caseId: string) => {
    const targetSample = samplePool.find((sample) => sample.sampleId === sampleId);
    const persistedSampleId = String(targetSample?.persistedSampleId || sampleId);
    const nextSamples = samplePool.filter((sample) => sample.sampleId !== sampleId);
    setSamplePool(nextSamples);
    setActionMessage(`已从样本池删除：${caseId}`);
    if (!projectId || !hasHydratedRemoteRef.current) return;
    setIsCloudSyncing(true);
    try {
      const payload = await deletePrioritySamplePoolItem(projectId, {
        generation_id: generationId ?? null,
        sample_id: persistedSampleId,
      });
      const remoteSamples = parseSamplePool(JSON.stringify(payload?.samples || []));
      setSamplePool(remoteSamples);
      const cloudTs = Date.parse(String(payload?.updated_at || ''));
      if (Number.isFinite(cloudTs)) setLastCloudSavedAt(cloudTs);
      setCloudSyncError('');
      setActionMessage(`已从样本池删除并写入云端：${caseId}`);
    } catch {
      setSamplePool(samplePool);
      setCloudSyncError('云端删除失败，已恢复本地显示');
      setActionMessage(`删除失败，已恢复：${caseId}`);
    } finally {
      setIsCloudSyncing(false);
    }
  };
  const isManualDebugSample = (sample: PrioritySample): boolean => (
    sample.source === SAMPLE_SOURCES.PRIORITY_DEBUG_MANUAL_ADD
    || Boolean(sample.weakLinkCaseKey)
  );
  const handleExportSamplePool = () => {
    if (!samplePool.length) { setActionMessage('样本池为空，暂无可导出数据'); return; }
    downloadCsv(buildCsvFromRows(toSamplePoolExportRows(samplePool)), `priority-anomaly-sample-pool-${new Date().toISOString().replace(/[:.]/g, '-')}.csv`);
    setActionMessage(`已导出样本池（${samplePool.length} 条）`);
  };
  const handleExportEvalDatasetJson = () => {
    if (!samplePool.length) { setActionMessage('样本池为空，暂无可导出评估数据'); return; }
    downloadJson(`${JSON.stringify(toEvalDataset(samplePool), null, 2)}\n`, `priority-eval-dataset-${new Date().toISOString().replace(/[:.]/g, '-')}.json`);
    setActionMessage(`已导出评估数据集（${samplePool.length} 条）`);
  };
  const handleClearSamplePool = () => {
    setSamplePool([]);
    setActionMessage('样本池已清空');
  };
  const handleToggleSamplePoolFeedback = () => {
    const next = !enableSamplePoolFeedback;
    onToggleSamplePoolFeedback(next);
    setActionMessage(next ? '样本池回流已开启' : '样本池回流已关闭');
  };

  return (
    <div className="rag-debug-card rounded-2xl shadow-md p-4 border bg-white dark:bg-slate-900">
      <div className="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
        <h6 className="mb-0 fw-bold">优先级调试</h6>
        <div className="small text-muted rag-debug-muted">结果来源：{resultSourceLabel(resultSource)}</div>
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
        <Button size="sm" variant="outline-secondary" onClick={handleExportSamplePool} disabled={!samplePool.length}>导出样本池表格</Button>
        <Button size="sm" variant="outline-secondary" onClick={handleExportEvalDatasetJson} disabled={!samplePool.length}>导出评估数据集</Button>
        <Button size="sm" variant="outline-danger" onClick={handleClearSamplePool} disabled={!samplePool.length}>清空样本池</Button>
        <Button
          size="sm"
          variant={enableSamplePoolFeedback ? 'outline-success' : 'outline-secondary'}
          onClick={handleToggleSamplePoolFeedback}
        >
          样本池回流：{enableSamplePoolFeedback ? '开启' : '关闭'}
        </Button>
        {transitions.map((item) => <Badge key={item.transition} bg="dark">{directionLabel(item.transition)} {item.count}</Badge>)}
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
          <div className="fw-semibold mb-1">主要修正方向</div>
          {sampleDirectionTop.length ? (
            <div className="d-flex flex-wrap gap-2">
              {sampleDirectionTop.map((item) => <Badge key={`sample-direction-${item.direction}`} bg="secondary">{directionLabel(item.direction)} {item.count}</Badge>)}
            </div>
          ) : (
            <div className="text-muted rag-debug-muted">暂无方向统计</div>
          )}
        </div>
        <div className="small text-muted rag-debug-muted">主链路优先消费分类、模式、权重和置信度；补充说明仅作为低置信或纠偏场景的辅助证据。</div>
        <div className="small text-muted rag-debug-muted">异常样本使用“原因分类”；正向样本使用“模式分类”。备注不再承担模式描述职责。</div>
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

      {samplePool.length > 0 ? (
        <div className="mb-3 p-2 border rounded-2">
          <div className="d-flex flex-wrap align-items-center gap-2 mb-2">
            <span className="fw-semibold">样本池模式与人工纠偏</span>
            <span className="small text-muted rag-debug-muted">状态：</span>
            <Button
              size="sm"
              variant={samplePoolStatusFilter === 'active' ? 'success' : 'outline-success'}
              onClick={() => setSamplePoolStatusFilter('active')}
            >活跃</Button>
            <Button
              size="sm"
              variant={samplePoolStatusFilter === 'deleted' ? 'danger' : 'outline-danger'}
              onClick={() => setSamplePoolStatusFilter('deleted')}
            >已删除</Button>
            <Button
              size="sm"
              variant={samplePoolStatusFilter === 'all' ? 'secondary' : 'outline-secondary'}
              onClick={() => setSamplePoolStatusFilter('all')}
            >全部</Button>
          </div>
          <div className="table-responsive tg-priority-sample-scroll">
            <table className="table table-sm align-middle mb-0 tg-priority-sample-table">
              <thead>
                <tr>
                  <th className="tg-priority-sample-case-col">用例</th>
                  <th className="tg-priority-sample-source-col">来源</th>
                  <th className="tg-priority-sample-tag-col">标签</th>
                  <th className="tg-priority-sample-priority-col">期望优先级</th>
                  <th className="tg-priority-sample-reason-col">分类</th>
                  <th className="tg-priority-sample-comment-col">人工补充</th>
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
                  const categoryLabel = sample.categoryLabel || categoryDisplayLabel(categoryValue, sample.sampleKind) || '未分类';
                  const hasUserComment = Boolean(sample.userComment.trim());
                  const isLowConfidence = sample.confidence != null && sample.confidence < 0.7;
                  const isManualReview = sample.tags.includes('manual_review');
                  const supplementStatus = hasUserComment
                    ? '已有说明'
                    : (isLowConfidence && !isManualReview ? '低置信' : (!categoryValue && !isManualReview ? '建议补充' : ''));
                  const showSupplementStatus = Boolean(supplementStatus);
                  const patternSummary = isInPattern(sample) ? String(sample.patternClusterKey || '') : '未入模式';
                  return (
                  <tr key={sample.sampleId} className={kindSelected ? 'tg-priority-sample-row-kind-selected' : undefined}>
                    <td className="tg-priority-sample-case-col">
                      <div className="fw-semibold">{sample.caseId}</div>
                      <div className="small text-muted rag-debug-muted tg-priority-sample-case-title">{sample.title || '-'}</div>
                    </td>
                    <td className="tg-priority-sample-source-col">
                      <Badge bg={sourceTypeBadgeVariant(sample.sourceType || sample.source)}>{sourceTypeLabel(sample.sourceType || sample.source)}</Badge>
                      {sample.status === 'deleted' ? <Badge bg="danger" className="mt-1">已删除</Badge> : null}
                    </td>
                    <td className="tg-priority-sample-tag-col">
                      <div className="d-flex gap-1 tg-priority-tags-wrap">
                        <Badge bg={sample.sampleKind === 'positive' ? 'primary' : 'danger'} className={`tg-priority-kind-badge ${kindSelected ? 'is-selected' : ''}`}>{sampleKindDisplayLabel(sample.sampleKind)}</Badge>
                        {sample.tags.map((tag) => {
                          if (tag === 'rule_adjusted' || tag === 'display_mismatch') return null;
                          if (tag !== 'manual_review') {
                            return (
                              <Badge key={`${sample.sampleId}-${tag}`} bg="light" text="dark">
                                {sampleTagLabel(tag)}
                              </Badge>
                            );
                          }
                          return null;
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
                      <div className="tg-priority-comment-cell">
                        <Form.Control
                          size="sm"
                          as="textarea"
                          rows={2}
                          placeholder={sample.sampleKind === 'positive'
                            ? '可选：补充该模式的业务前提或人工判断依据'
                            : '可选：补充该样本为何不合理或如何纠偏'}
                          value={sample.userComment}
                          onChange={(e) => handleUpdateSample(sample.sampleId, { userComment: e.target.value })}
                        />
                        <span className="tg-priority-pattern-info" tabIndex={0} aria-label={`模式：${patternSummary}，权重：${formatWeight(sample.patternWeight)}，置信度：${formatWeight(sample.confidence)}`}>
                          <FaExclamationCircle aria-hidden="true" />
                          <span className="tg-priority-pattern-tooltip" role="tooltip">
                            <span className="tg-priority-tooltip-title-row">
                              <span className="tg-priority-tooltip-pattern-label">模式：</span>
                              {showSupplementStatus ? (
                                <Badge bg={hasUserComment ? 'info' : (isLowConfidence ? 'warning' : 'secondary')} text={hasUserComment ? undefined : 'dark'} className="tg-priority-tooltip-status">
                                  {supplementStatus}
                                </Badge>
                              ) : null}
                            </span>
                            <span className="tg-priority-tooltip-pattern-title">{patternSummary}</span>
                            <span className="tg-priority-tooltip-metric-row">
                              <span className="tg-priority-pattern-metric-label">权重</span>
                              <span>{formatWeight(sample.patternWeight)}</span>
                              <span className="tg-priority-pattern-metric-label">置信度</span>
                              <span>{formatWeight(sample.confidence)}</span>
                            </span>
                          </span>
                        </span>
                      </div>
                    </td>
                    <td className="tg-priority-sample-action-col">
                      <Button
                        size="sm"
                        variant={isManualDebugSample(sample) ? 'outline-secondary' : 'outline-danger'}
                        className="tg-priority-row-action-btn"
                        onClick={() => void handleRollbackSample(sample.sampleId, sample.caseId)}
                      >
                        {isManualDebugSample(sample) ? '用例回退' : '删除样本'}
                      </Button>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}


