import classNames from 'classnames';
import { useEffect, useMemo, useRef } from 'react';
import { Badge, Button } from 'react-bootstrap';
import { FaCheckCircle, FaCopy, FaFileCode } from 'react-icons/fa';
import type { TestGenerationMode } from './types';

type TestGenerationResultSectionProps = {
  mode: TestGenerationMode;
  result: any;
  streamingContent: string;
  loading: boolean;
  statsCount: number;
  onCopy: () => void;
  highlightRuleId?: string | null;
  onClearHighlight?: () => void;
};

type MatchedCase = {
  caseId: string;
  description: string;
};

function escapeRegExp(input: string): string {
  return input.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

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

function getMatchedCases(result: any, keyword: string): MatchedCase[] {
  if (!Array.isArray(result) || !keyword) return [];
  const lower = keyword.toLowerCase();
  return result
    .filter((item) => JSON.stringify(item ?? {}).toLowerCase().includes(lower))
    .map((item, idx) => ({
      caseId: String(item?.id || item?.case_id || `CASE-${idx + 1}`),
      description: String(item?.description || item?.title || '').slice(0, 80),
    }));
}

export function TestGenerationResultSection({
  mode,
  result,
  streamingContent,
  loading,
  statsCount,
  onCopy,
  highlightRuleId,
  onClearHighlight,
}: TestGenerationResultSectionProps) {
  const normalizedRuleId = normalizeRuleToken(highlightRuleId);
  const renderedText = useMemo(() => getRenderedText(mode, result, streamingContent), [mode, result, streamingContent]);
  const matchedCases = useMemo(() => getMatchedCases(result, normalizedRuleId), [result, normalizedRuleId]);
  const firstMarkRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!normalizedRuleId) return;
    if (firstMarkRef.current) {
      firstMarkRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [normalizedRuleId, renderedText]);

  const renderedContent = useMemo(() => {
    if (!normalizedRuleId || !renderedText) return renderedText;
    const regex = new RegExp(`(${escapeRegExp(normalizedRuleId)})`, 'ig');
    const segments = renderedText.split(regex);
    let marked = false;
    return segments.map((seg, idx) => {
      const isHit = idx % 2 === 1;
      if (!isHit) return <span key={`seg-${idx}`}>{seg}</span>;
      if (!marked) {
        marked = true;
        return (
          <mark
            key={`seg-${idx}`}
            ref={(node) => {
              if (node) firstMarkRef.current = node;
            }}
            className="test-generation-highlight-mark"
          >
            {seg}
          </mark>
        );
      }
      return (
        <mark key={`seg-${idx}`} className="test-generation-highlight-mark">
          {seg}
        </mark>
      );
    });
  }, [normalizedRuleId, renderedText]);

  return (
    <div className="test-generation-result test-generation-result-panel bento-card col-span-12 p-0 overflow-hidden d-flex flex-column panel-card panel-card-result">
      <div className="test-generation-result-head bg-light border-bottom d-flex justify-content-between align-items-center px-4 py-3 panel-card-head">
        <h6 className="mb-0 fw-bold d-flex align-items-center gap-2 panel-card-title">
          <FaCheckCircle className={result ? 'text-success' : 'text-muted'} /> 生成结果
        </h6>
        <div className="d-flex align-items-center gap-2 panel-card-actions-inline">
          {result ? (
            <Badge bg="success" className="d-flex align-items-center gap-1">
              总计 {statsCount} 条
            </Badge>
          ) : null}
          {streamingContent ? (
            <Badge bg="primary" className="d-flex align-items-center gap-1">
              {loading ? '生成中...' : '最新批次'}
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

      {normalizedRuleId ? (
        <div className="px-4 py-2 border-bottom bg-warning-subtle small d-flex justify-content-between align-items-center">
          <div className="d-flex align-items-center gap-2">
            <span className="fw-semibold">规则聚焦：</span>
            <Badge bg="warning" text="dark">
              {normalizedRuleId}
            </Badge>
            <span className="text-muted">命中用例 {matchedCases.length} 条</span>
          </div>
          <Button variant="outline-secondary" size="sm" onClick={onClearHighlight}>
            清除聚焦
          </Button>
        </div>
      ) : null}

      {normalizedRuleId && matchedCases.length ? (
        <div className="px-4 py-2 border-bottom small test-generation-related-cases">
          <span className="fw-semibold me-2">关联用例：</span>
          {matchedCases.slice(0, 8).map((item) => (
            <Badge key={`${item.caseId}-${item.description}`} bg="light" text="dark" className="me-2 mb-1">
              {item.caseId}
            </Badge>
          ))}
          {matchedCases.length > 8 ? <span className="text-muted">+{matchedCases.length - 8} 条</span> : null}
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
                renderedContent
              ) : (
                <div className="text-center text-muted mt-5 py-5">
                  <div className="mb-3 opacity-25">
                    <FaFileCode size={48} />
                  </div>
                  暂无历史结果
                </div>
              )
            ) : result || renderedText ? (
              renderedContent
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
