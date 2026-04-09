import { useEffect, useMemo, useState } from 'react';
import { Badge, Button, Form } from 'react-bootstrap';
import {
  SAMPLE_POOL_STORAGE_KEY,
  SAMPLE_TAG_ORDER,
  REASON_CATEGORY_OPTIONS,
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
  sampleUsageLabel,
  normalizePriority,
  normalizeReasonCategory,
  classifySampleTags,
  resolveSampleUsage,
} from './PriorityDebugTable.helpers';
import type { Props, PriorityRow, PrioritySample, ViewFilter } from './PriorityDebugTable.helpers';
export function PriorityDebugTable({ result, resultSource }: Props) {
  const [viewFilter, setViewFilter] = useState<ViewFilter>('all');
  const [rawFilter, setRawFilter] = useState<string>('all');
  const [finalFilter, setFinalFilter] = useState<string>('all');
  const [displayFilter, setDisplayFilter] = useState<string>('all');
  const [actionMessage, setActionMessage] = useState<string>('');
  const [recommendationText, setRecommendationText] = useState<string>('');
  const [samplePool, setSamplePool] = useState<PrioritySample[]>(() => {
    if (typeof window === 'undefined') return [];
    return parseSamplePool(window.localStorage.getItem(SAMPLE_POOL_STORAGE_KEY));
  });

  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(SAMPLE_POOL_STORAGE_KEY, JSON.stringify(samplePool));
  }, [samplePool]);

  const rows = useMemo(() => buildRows(result, resultSource), [result, resultSource]);
  const sortedRows = useMemo(() => [...rows].sort(compareRows), [rows]);
  const filteredRows = useMemo(
    () => sortedRows.filter((row) => {
      if (!matchPriority(row.rawPriority, rawFilter)) return false;
      if (!matchPriority(row.finalPriority, finalFilter)) return false;
      if (!matchPriority(row.displayPriority, displayFilter)) return false;
      if (viewFilter === 'corrected') return row.corrected;
      if (viewFilter === 'unchanged') return !row.corrected;
      if (viewFilter === 'raw_mismatch') return row.rawFinalMismatch;
      if (viewFilter === 'display_mismatch') return row.displayFinalMismatch;
      return true;
    }),
    [sortedRows, rawFilter, finalFilter, displayFilter, viewFilter]
  );
  const correctedCount = useMemo(() => rows.filter((row) => row.corrected).length, [rows]);
  const unchangedCount = useMemo(() => rows.filter((row) => !row.corrected).length, [rows]);
  const displayMismatchCount = useMemo(() => rows.filter((row) => row.displayFinalMismatch).length, [rows]);
  const transitions = useMemo(() => buildTransitions(rows), [rows]);
  const displayMismatchRows = useMemo(() => sortedRows.filter((row) => row.displayFinalMismatch), [sortedRows]);
  const anomalyRows = useMemo(() => sortedRows.filter((row) => row.displayFinalMismatch || row.rawFinalMismatch), [sortedRows]);
  const summaryLine = useMemo(() => buildSummaryLine(displayMismatchCount, correctedCount, unchangedCount), [displayMismatchCount, correctedCount, unchangedCount]);
  const sampleTagCounts = useMemo(() => getSampleTagCounts(samplePool), [samplePool]);
  const sampleDirectionTop = useMemo(() => getSampleDirectionTop(samplePool, 5), [samplePool]);

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
      const next = mergeSamples(prev, anomalyRows.map(toSample));
      setActionMessage(`已加入异常样本池（样本池共 ${next.length} 条）`);
      return next;
    });
  };
  const handleAddCurrentRowToPool = (row: PriorityRow) => {
    setSamplePool((prev) => {
      const next = mergeSamples(prev, [toSample(row)]);
      setActionMessage(`已加入样本池：${row.caseId}`);
      return next;
    });
  };
  const handleUpdateSample = (sampleId: string, patch: Partial<Pick<PrioritySample, 'userComment' | 'expectedPriority' | 'reasonCategory'>>) => {
    setSamplePool((prev) => prev.map((sample) => (sample.sampleId === sampleId ? { ...sample, ...patch } : sample)));
  };
  const handleExportSamplePool = () => {
    if (!samplePool.length) { setActionMessage('异常样本池为空，暂无可导出数据'); return; }
    downloadCsv(buildCsvFromRows(toSamplePoolExportRows(samplePool)), `priority-anomaly-sample-pool-${new Date().toISOString().replace(/[:.]/g, '-')}.csv`);
    setActionMessage(`已导出异常样本池（${samplePool.length} 条）`);
  };
  const handleExportEvalDatasetJson = () => {
    if (!samplePool.length) { setActionMessage('异常样本池为空，暂无可导出评估数据'); return; }
    downloadJson(`${JSON.stringify(toEvalDataset(samplePool), null, 2)}\n`, `priority-eval-dataset-${new Date().toISOString().replace(/[:.]/g, '-')}.json`);
    setActionMessage(`已导出评估数据集 JSON（${samplePool.length} 条）`);
  };
  const handleGenerateRuleSuggestions = () => {
    if (!samplePool.length) { setActionMessage('异常样本池为空，无法生成规则建议'); return; }
    setRecommendationText(buildRecommendationText(buildRecommendationDraft(samplePool, sampleTagCounts, sampleDirectionTop)));
    setActionMessage('已基于当前样本池生成规则建议');
  };
  const handleCopyRuleSuggestions = async () => {
    if (!recommendationText.trim()) { setActionMessage('请先生成规则建议'); return; }
    try { await copyTextToClipboard(recommendationText); setActionMessage('已复制规则建议文本'); } catch { setActionMessage('复制失败，请重试'); }
  };
  const handleExportOptimizationInputPackage = () => {
    if (!samplePool.length) { setActionMessage('异常样本池为空，暂无可导出输入包'); return; }
    const payload = buildOptimizationInputPackage(samplePool, sampleTagCounts, sampleDirectionTop);
    downloadJson(`${JSON.stringify(payload, null, 2)}\n`, `priority-optimization-input-package-${new Date().toISOString().replace(/[:.]/g, '-')}.json`);
    setActionMessage(`已导出优化建议输入包（${samplePool.length} 条样本）`);
  };
  const handleClearSamplePool = () => {
    setSamplePool([]);
    setRecommendationText('');
    setActionMessage('异常样本池已清空');
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
        <Button size="sm" variant={viewFilter === 'display_mismatch' ? 'primary' : 'outline-primary'} onClick={() => setViewFilter('display_mismatch')}>一键只看异常</Button>
        <Button size="sm" variant={viewFilter === 'raw_mismatch' ? 'primary' : 'outline-primary'} onClick={() => setViewFilter('raw_mismatch')}>只看被修正</Button>
        <Button size="sm" variant={viewFilter === 'all' ? 'primary' : 'outline-primary'} onClick={() => setViewFilter('all')}>查看全部</Button>
        <Button size="sm" variant="outline-secondary" onClick={handleExportCurrent} disabled={!filteredRows.length}>导出当前结果</Button>
        <Button size="sm" variant="outline-secondary" onClick={() => void handleCopyCurrent()} disabled={!filteredRows.length}>复制当前结果</Button>
        <Button size="sm" variant="outline-secondary" onClick={() => void handleCopyDisplayMismatch()} disabled={!displayMismatchRows.length}>复制展示异常</Button>
        <Button size="sm" variant="outline-secondary" onClick={handleAddAnomalySamples} disabled={!anomalyRows.length}>加入异常样本池</Button>
        <Button size="sm" variant="outline-secondary" onClick={handleExportSamplePool} disabled={!samplePool.length}>导出样本池 CSV</Button>
        <Button size="sm" variant="outline-secondary" onClick={handleExportEvalDatasetJson} disabled={!samplePool.length}>导出为评估数据集（JSON）</Button>
        <Button size="sm" variant="outline-secondary" onClick={handleGenerateRuleSuggestions} disabled={!samplePool.length}>生成规则建议</Button>
        <Button size="sm" variant="outline-secondary" onClick={() => void handleCopyRuleSuggestions()} disabled={!recommendationText.trim()}>复制规则建议</Button>
        <Button size="sm" variant="outline-secondary" onClick={handleExportOptimizationInputPackage} disabled={!samplePool.length}>导出优化建议输入包</Button>
        <Button size="sm" variant="outline-danger" onClick={handleClearSamplePool} disabled={!samplePool.length}>清空样本池</Button>
        {transitions.map((item) => <Badge key={item.transition} bg="dark">{item.transition} {item.count}</Badge>)}
      </div>
      {actionMessage ? <div className="small text-muted rag-debug-muted mb-3">{actionMessage}</div> : null}

      <div className="mb-3 p-2 border rounded-2">
        <div className="d-flex flex-wrap align-items-center gap-2 mb-2">
          <span className="fw-semibold">异常样本池：</span>
          <Badge bg={samplePool.length > 0 ? 'dark' : 'secondary'}>{samplePool.length}</Badge>
        </div>
        <div className="small mb-2">
          <div className="fw-semibold mb-1">标签分布</div>
          {SAMPLE_TAG_ORDER.map((tag) => {
            const count = sampleTagCounts[tag];
            const ratio = samplePool.length > 0 ? Math.round((count / samplePool.length) * 100) : 0;
            return (
              <div key={tag} className="d-flex align-items-center gap-2 mb-1">
                <span style={{ minWidth: 92 }}>{sampleTagLabel(tag)}</span>
                <div className="flex-grow-1" style={{ height: 8, background: '#e9ecef', borderRadius: 4 }}>
                  <div style={{ height: 8, width: `${ratio}%`, background: '#6c757d', borderRadius: 4 }} />
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
        <div className="small text-muted rag-debug-muted">可在下方样本条目填写 user_comment / expected_priority / reason_category，用于后续优化建议。</div>
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
            <table className="table table-sm align-middle mb-0">
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Direction</th>
                  <th>Tags</th>
                  <th>Expected</th>
                  <th>Reason</th>
                  <th>User Comment</th>
                </tr>
              </thead>
              <tbody>
                {samplePool.map((sample) => (
                  <tr key={sample.sampleId}>
                    <td><div className="fw-semibold">{sample.caseId}</div><div className="small text-muted rag-debug-muted">{sample.title || '-'}</div></td>
                    <td>{sample.direction}</td>
                    <td>
                      <div className="d-flex flex-wrap gap-1">
                        {sample.tags.map((tag) => <Badge key={`${sample.sampleId}-${tag}`} bg="light" text="dark">{sampleTagLabel(tag)}</Badge>)}
                        <Badge bg="info" text="dark">{sampleUsageLabel(sample.usage)}</Badge>
                      </div>
                    </td>
                    <td>
                      <Form.Select size="sm" value={sample.expectedPriority} onChange={(e) => handleUpdateSample(sample.sampleId, { expectedPriority: normalizePriority(e.target.value) })} style={{ width: 90 }}>
                        <option value="">-</option><option value="P0">P0</option><option value="P1">P1</option><option value="P2">P2</option><option value="P3">P3</option>
                      </Form.Select>
                    </td>
                    <td>
                      <Form.Select size="sm" value={sample.reasonCategory} onChange={(e) => handleUpdateSample(sample.sampleId, { reasonCategory: normalizeReasonCategory(e.target.value) })} style={{ minWidth: 160 }}>
                        {REASON_CATEGORY_OPTIONS.map((opt) => <option key={`reason-${opt.value || 'none'}`} value={opt.value}>{opt.label}</option>)}
                      </Form.Select>
                    </td>
                    <td style={{ minWidth: 280 }}>
                      <Form.Control size="sm" as="textarea" rows={2} placeholder="填写该样本为何不合理、你期望的优先级依据" value={sample.userComment} onChange={(e) => handleUpdateSample(sample.sampleId, { userComment: e.target.value })} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      <div className="d-flex flex-wrap align-items-end gap-2 mb-3">
        <div><div className="small text-muted rag-debug-muted mb-1">View</div><Form.Select size="sm" value={viewFilter} onChange={(e) => setViewFilter(e.target.value as ViewFilter)} style={{ width: 180 }}><option value="all">all</option><option value="corrected">corrected</option><option value="unchanged">unchanged</option><option value="raw_mismatch">raw != final</option><option value="display_mismatch">display != final</option></Form.Select></div>
        <div><div className="small text-muted rag-debug-muted mb-1">raw</div><Form.Select size="sm" value={rawFilter} onChange={(e) => setRawFilter(e.target.value)} style={{ width: 120 }}><option value="all">all</option><option value="P0">P0</option><option value="P1">P1</option><option value="P2">P2</option><option value="P3">P3</option></Form.Select></div>
        <div><div className="small text-muted rag-debug-muted mb-1">final</div><Form.Select size="sm" value={finalFilter} onChange={(e) => setFinalFilter(e.target.value)} style={{ width: 120 }}><option value="all">all</option><option value="P0">P0</option><option value="P1">P1</option><option value="P2">P2</option><option value="P3">P3</option></Form.Select></div>
        <div><div className="small text-muted rag-debug-muted mb-1">display</div><Form.Select size="sm" value={displayFilter} onChange={(e) => setDisplayFilter(e.target.value)} style={{ width: 120 }}><option value="all">all</option><option value="P0">P0</option><option value="P1">P1</option><option value="P2">P2</option><option value="P3">P3</option></Form.Select></div>
      </div>

      <div className="table-responsive">
        <table className="table table-sm align-middle mb-0">
          <thead>
            <tr>
              <th style={{ width: 130 }}>#</th><th>Case</th><th>Raw</th><th>Final</th><th>Display</th><th>Corrected</th><th>Result Source</th><th>Tags</th><th>Usage</th><th>priority_debug</th><th>Action</th>
            </tr>
          </thead>
          <tbody>
            {!filteredRows.length ? (<tr><td colSpan={11} className="text-center text-muted py-4">暂无符合筛选条件的数据</td></tr>) : null}
            {filteredRows.map((row) => {
              const tags = classifySampleTags(row);
              const usage = resolveSampleUsage(tags);
              return (
                <tr key={`${row.caseId}-${row.index}`} className={row.displayFinalMismatch ? 'tg-priority-display-mismatch-row' : undefined}>
                  <td><div>{row.index}</div>{row.displayFinalMismatch ? <Badge bg="danger" className="mt-1">展示异常</Badge> : null}</td>
                  <td><div className="fw-semibold">{row.caseId}</div><div className="small text-muted rag-debug-muted">{row.title || '-'}</div></td>
                  <td>{row.rawPriority || '-'}</td>
                  <td className={row.displayFinalMismatch ? 'tg-priority-display-mismatch-cell' : undefined}>{row.finalPriority || '-'}</td>
                  <td className={row.displayFinalMismatch ? 'tg-priority-display-mismatch-cell' : undefined}>{row.displayPriority || '-'}</td>
                  <td>{row.corrected ? <Badge bg="warning" text="dark">yes</Badge> : <Badge bg="secondary">no</Badge>}</td>
                  <td>{row.resultSource}</td>
                  <td><div className="d-flex flex-wrap gap-1">{tags.map((tag) => <Badge key={`${row.caseId}-${tag}`} bg="light" text="dark">{sampleTagLabel(tag)}</Badge>)}</div></td>
                  <td><Badge bg="info" text="dark">{sampleUsageLabel(usage)}</Badge></td>
                  <td>{row.priorityDebug ? (<details><summary>view</summary><pre className="rag-priority-debug-pre">{JSON.stringify(row.priorityDebug, null, 2)}</pre></details>) : (<span className="text-muted">-</span>)}</td>
                  <td><Button size="sm" variant="outline-secondary" onClick={() => handleAddCurrentRowToPool(row)}>加入样本池</Button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

