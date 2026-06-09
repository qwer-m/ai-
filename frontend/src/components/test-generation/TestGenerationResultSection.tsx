import classNames from 'classnames';
import { useMemo } from 'react';
import { Badge, Button } from 'react-bootstrap';
import { FaCheckCircle, FaCopy, FaFileCode } from 'react-icons/fa';
import type { TestGenerationMode } from './types';

type ResultSource = 'none' | 'streaming_preview' | 'final_persisted';

type TestGenerationResultSectionProps = {
  mode: TestGenerationMode;
  result: any;
  resultSource: ResultSource;
  generationId: number | null;
  isFinalResultLoaded: boolean;
  streamingContent: string;
  loading: boolean;
  statsCount: number;
  previewCaseCount: number;
  finalCaseCount: number;
  displayCaseCount: number;
  funnelMetrics: {
    rawPreviewCount: number;
    reviewCandidateCount: number | null;
    reviewSelectedCount: number | null;
    judgeInputCount: number | null;
    judgeRejectedOrPendingCount: number | null;
    finalCount: number;
  };
  onCopy: () => void;
  highlightRuleId?: string | null;
  highlightRuleText?: string;
  onClearHighlight?: () => void;
};

type PriorityRow = {
  id: string;
  rawPriority: 'P0' | 'P1' | 'P2';
  finalPriority: 'P0' | 'P1' | 'P2';
  displayPriority: 'P0' | 'P1' | 'P2';
  changed: boolean;
  hasPriorityDebug: boolean;
};

function normalizeRuleToken(ruleId: string | null | undefined): string {
  return String(ruleId || '').trim();
}

function getRenderedText(mode: TestGenerationMode, result: any, streamingContent: string): string {
  if (!result && typeof streamingContent === 'string' && streamingContent.trim()) {
    return streamingContent;
  }
  if (mode === 'text') {
    return result ? JSON.stringify(result, null, 2) : '';
  }
  return result ? JSON.stringify(result, null, 2) : '';
}

function normalizePriorityValue(v: unknown): 'P0' | 'P1' | 'P2' {
  const s = String(v ?? '').trim().toUpperCase();
  if (s === 'P0' || s === 'P1' || s === 'P2') return s;
  if (s === 'HIGH' || s === '高') return 'P0';
  if (s === 'MEDIUM' || s === '中') return 'P1';
  if (s === 'LOW' || s === '低') return 'P2';
  return 'P1';
}

function buildPriorityRows(result: any): PriorityRow[] {
  if (!Array.isArray(result)) return [];
  return result.map((item, idx) => {
    const priorityDebug = item?.priorityDebug ?? item?.priority_debug ?? item?.meta?.priority_debug;
    const hasPriorityDebug = Boolean(priorityDebug && typeof priorityDebug === 'object');
    const displayPriority = normalizePriorityValue(item?.displayPriority ?? item?.priority);
    const rawPriority = normalizePriorityValue(item?.rawPriority ?? item?.raw_priority ?? priorityDebug?.original_priority ?? item?.priority);
    const finalPriority = normalizePriorityValue(item?.finalPriority ?? item?.final_priority ?? item?.priority_final ?? priorityDebug?.final_priority ?? displayPriority);
    const caseId = String(item?.id || item?.case_id || `CASE-${idx + 1}`);
    return {
      id: caseId,
      rawPriority,
      finalPriority,
      displayPriority,
      changed: rawPriority !== finalPriority,
      hasPriorityDebug,
    };
  });
}

function buildPriorityTransitionStats(rows: PriorityRow[]): Array<{ transition: string; count: number }> {
  const counts = new Map<string, number>();
  for (const row of rows) {
    if (!row.changed) continue;
    const transition = `${row.rawPriority}→${row.finalPriority}`;
    counts.set(transition, (counts.get(transition) || 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([transition, count]) => ({ transition, count }))
    .sort((a, b) => b.count - a.count || a.transition.localeCompare(b.transition));
}

export function TestGenerationResultSection({
  mode,
  result,
  resultSource,
  generationId,
  isFinalResultLoaded,
  streamingContent,
  loading,
  statsCount,
  previewCaseCount,
  finalCaseCount,
  displayCaseCount,
  funnelMetrics,
  onCopy,
  highlightRuleId,
  highlightRuleText,
  onClearHighlight,
}: TestGenerationResultSectionProps) {
  const normalizedRuleId = normalizeRuleToken(highlightRuleId);
  const renderedText = useMemo(() => getRenderedText(mode, result, streamingContent), [mode, result, streamingContent]);

  const isPreview = resultSource === 'streaming_preview';
  const isFinal = resultSource === 'final_persisted' && isFinalResultLoaded;
  const priorityRows = useMemo(() => buildPriorityRows(result), [result]);
  const correctedRows = useMemo(() => priorityRows.filter((row) => row.changed), [priorityRows]);
  const hasPriorityDebugRows = useMemo(() => priorityRows.some((row) => row.hasPriorityDebug), [priorityRows]);
  const transitions = useMemo(() => buildPriorityTransitionStats(priorityRows), [priorityRows]);

  return (
    <div className="test-generation-result test-generation-result-panel bento-card col-span-12 p-0 overflow-hidden d-flex flex-column panel-card panel-card-result">
      <div className="test-generation-result-head bg-light border-bottom d-flex justify-content-between align-items-center px-4 py-3 panel-card-head">
        <h6 className="mb-0 fw-bold d-flex align-items-center gap-2 panel-card-title">
          <FaCheckCircle className={result ? 'text-success' : 'text-muted'} /> 生成结果
        </h6>
        <div className="d-flex align-items-center gap-2 panel-card-actions-inline">
          {result ? (
            <Badge bg="success" className="d-flex align-items-center gap-1">
              当前展示 {displayCaseCount} 条
            </Badge>
          ) : null}
          {(previewCaseCount > 0 || finalCaseCount > 0) ? (
            <Badge bg="secondary" className="d-flex align-items-center gap-1">
              预览 {previewCaseCount} / 最终 {finalCaseCount || statsCount}
            </Badge>
          ) : null}
          {streamingContent ? (
            <Badge bg="primary" className="d-flex align-items-center gap-1">
              {loading ? '生成中...' : '最新批次'}
            </Badge>
          ) : null}
          {isPreview ? (
            <Badge bg="warning" text="dark" className="d-flex align-items-center gap-1">
              预览态
            </Badge>
          ) : null}
          {isFinal ? (
            <Badge bg="success" className="d-flex align-items-center gap-1">
              最终结果
            </Badge>
          ) : null}
          {isFinal && generationId ? (
            <Badge bg="secondary" className="d-flex align-items-center gap-1">
              ID {generationId}
            </Badge>
          ) : null}
          {streamingContent ? (
            <Button
              variant="link"
              size="sm"
              className="p-0 text-decoration-none d-flex align-items-center gap-1 text-primary"
              onClick={onCopy}
              title="复制内容"
            >
              <FaCopy /> 复制
            </Button>
          ) : null}
        </div>
      </div>

      {isPreview ? (
        <div className="px-4 py-2 border-bottom bg-warning-subtle small text-muted">
          当前为模型流式预览，生成完成后会自动切换为最终结果。
        </div>
      ) : null}

      {isFinal ? (
        <div className="px-4 py-2 border-bottom bg-success-subtle small text-success-emphasis">
          当前展示为后处理后的最终结果。
        </div>
      ) : null}

      <div className="px-4 py-2 border-bottom bg-light small d-flex flex-wrap gap-2 align-items-center">
        <span className="fw-semibold">漏斗:</span>
        <Badge bg="secondary">raw预览 {funnelMetrics.rawPreviewCount}</Badge>
        <Badge bg="secondary">review候选 {funnelMetrics.reviewCandidateCount ?? '-'}</Badge>
        <Badge bg="secondary">review入选 {funnelMetrics.reviewSelectedCount ?? '-'}</Badge>
        <Badge bg="secondary">judge输入 {funnelMetrics.judgeInputCount ?? '-'}</Badge>
        <Badge bg="secondary">judge拒绝/待定 {funnelMetrics.judgeRejectedOrPendingCount ?? '-'}</Badge>
        <Badge bg="dark">final {funnelMetrics.finalCount}</Badge>
      </div>

      {priorityRows.length > 0 ? (
        <div className="px-4 py-2 border-bottom bg-light small">
          <div className="d-flex flex-wrap align-items-center gap-2 mb-2">
            <span className="fw-semibold">Priority 修正概览:</span>
            <Badge bg={correctedRows.length > 0 ? 'warning' : 'secondary'}>
              修正 {correctedRows.length}/{priorityRows.length}
            </Badge>
            <Badge bg={hasPriorityDebugRows ? 'info' : 'secondary'}>
              priority_debug {hasPriorityDebugRows ? 'available' : 'not_available'}
            </Badge>
            {transitions.map((item) => (
              <Badge key={item.transition} bg="dark">
                {item.transition} {item.count}
              </Badge>
            ))}
          </div>
          <details>
            <summary className="cursor-pointer">查看 Raw / Final / Display Priority 对比</summary>
            <div className="table-responsive mt-2">
              <table className="table table-sm table-striped align-middle mb-0">
                <thead>
                  <tr>
                    <th style={{ minWidth: 120 }}>Case ID</th>
                    <th>Raw Priority</th>
                    <th>Final Priority</th>
                    <th>Display Priority</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {priorityRows.map((row) => (
                    <tr key={row.id}>
                      <td className="fw-semibold">{row.id}</td>
                      <td>{row.rawPriority}</td>
                      <td>{row.finalPriority}</td>
                      <td>{row.displayPriority}</td>
                      <td>
                        {row.changed ? (
                          <Badge bg="warning" text="dark">corrected</Badge>
                        ) : (
                          <Badge bg="secondary">unchanged</Badge>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </div>
      ) : null}

      {normalizedRuleId ? (
        <div className="px-4 py-2 border-bottom bg-warning-subtle small d-flex justify-content-between align-items-center">
          <div className="d-flex align-items-center gap-2 flex-wrap">
            <span className="fw-semibold">需求点聚焦:</span>
            <Badge bg="warning" text="dark">
              {normalizedRuleId}
            </Badge>
            <span className="text-muted">{highlightRuleText || '缺少需求点文本'}</span>
          </div>
          <Button variant="outline-secondary" size="sm" onClick={onClearHighlight}>
            清除聚焦
          </Button>
        </div>
      ) : null}

      <div className="flex-grow-1 d-flex flex-column test-generation-min-h-0">
        <div className={classNames('d-flex flex-column flex-grow-1 transition-all test-generation-min-0')}>
          <div className="px-4 py-2 border-bottom small fw-bold text-secondary flex-shrink-0 test-generation-result-subhead">
            {streamingContent ? '合并结果 / 历史结果' : '生成结果'}
          </div>
          <div className="flex-grow-1 overflow-auto p-4 font-monospace test-generation-result-content test-generation-prewrap">
            {mode === 'text' ? (
              result || renderedText ? (
                renderedText
              ) : (
                <div className="text-center text-muted mt-5 py-5">
                  <div className="mb-3 opacity-25">
                    <FaFileCode size={48} />
                  </div>
                  暂无历史结果
                </div>
              )
            ) : result || renderedText ? (
              renderedText
            ) : (
              <div className="text-center text-muted mt-5 py-5">
                <div className="mb-3 opacity-25">
                  <FaFileCode size={48} />
                </div>
                暂无历史结果
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
