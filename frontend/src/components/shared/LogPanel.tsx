import { useState, useEffect, useRef, useMemo, useCallback, type MouseEvent as ReactMouseEvent } from 'react';
import { Button, Nav } from 'react-bootstrap';
import { FaChevronUp, FaChevronDown, FaDownload, FaTrash, FaExclamationCircle, FaCheckCircle } from 'react-icons/fa';
import classNames from 'classnames';

type LogEntry = {
  id: number;
  project_id: number;
  log_type: 'user' | 'system';
  message: string;
  created_at: string;
};

type Props = {
  userLogs: LogEntry[];
  systemLogs: LogEntry[];
  loading?: boolean;
  error?: string | null;
  onClear?: () => void;
};

function toNumber(value: unknown): number | null {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function numberText(value: unknown): string {
  const n = toNumber(value);
  return n === null ? '-' : String(n);
}

function percentText(value: unknown): string {
  const n = toNumber(value);
  if (n === null) return '-';
  return `${Math.round(n * 1000) / 10}%`;
}

function boolText(value: unknown): string {
  if (value === true) return '是';
  if (value === false) return '否';
  return '-';
}

function statusText(value: unknown): string {
  const raw = String(value || '').trim();
  if (!raw) return '-';
  const map: Record<string, string> = {
    high: '高',
    medium: '中',
    low: '低',
    completed_with_optimal_set: '完成：最优集合',
  };
  return map[raw] || raw;
}

function parsePrefixedJson(message: string, prefix: string): Record<string, unknown> | null {
  const idx = message.indexOf(prefix);
  if (idx < 0) return null;
  const raw = message.slice(idx + prefix.length).trim();
  if (!raw.startsWith('{')) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function formatStopReasons(value: unknown): string {
  const reasons = Array.isArray(value) ? value.map((item) => String(item || '').trim()).filter(Boolean) : [];
  if (!reasons.length) return '-';
  const map: Record<string, string> = {
    coverage_satisfied: '覆盖满足',
    stopped_due_to_diminishing_returns: '收益递减',
    optimal_case_set_reached: '最优集合',
    dedup_reduced_count_stop: '去重后收敛',
    final_description_dedup_reduced_count: '最终描述去重',
    low_quality_filtered: '低质已过滤',
  };
  return reasons.map((item) => map[item] || item).join(' / ');
}

function formatGenDiagMessage(message: string): string | null {
  const payload = parsePrefixedJson(message, 'GEN_DIAG:');
  if (!payload) return null;

  const kind = String(payload.kind || '').trim();
  if (kind === 'generation_quality_ledger') {
    const coverage = (payload.coverage && typeof payload.coverage === 'object') ? payload.coverage as Record<string, unknown> : {};
    const review = (payload.review && typeof payload.review === 'object') ? payload.review as Record<string, unknown> : {};
    const funnel = (payload.funnel && typeof payload.funnel === 'object') ? payload.funnel as Record<string, unknown> : {};
    const judge = (payload.judge && typeof payload.judge === 'object') ? payload.judge as Record<string, unknown> : {};
    const context = (payload.context && typeof payload.context === 'object') ? payload.context as Record<string, unknown> : {};
    return [
      `诊断摘要：最终 ${numberText(payload.final_count)} 条，质量 ${statusText(payload.quality_assessment)}，覆盖 ${percentText(coverage.coverage_rate)}，缺失规则 ${numberText(coverage.missing_rules_count)}`,
      `漏斗：候选 ${numberText(funnel.primary_count)}，Review ${numberText(funnel.review_count)}，LLM丢弃 ${numberText(review.drop_by_review_llm_count)}，后置去重 ${numberText(review.drop_by_post_review_dedup_count)}，Judge拒绝/待定 ${numberText(Number(judge.rejected_out_count || 0) + Number(judge.pending_out_count || 0))}`,
      `上下文：snapshot=${boolText(context.snapshot_used)}，RAG=${boolText(context.realtime_rag_used)}，当前文档=${boolText(context.current_document_used)}，压缩率=${numberText(context.compression_ratio)}`,
    ].join('\n');
  }

  if (kind === 'review_decision_summary') {
    const runtime = (payload.review_llm_runtime_debug && typeof payload.review_llm_runtime_debug === 'object')
      ? payload.review_llm_runtime_debug as Record<string, unknown>
      : {};
    const primaryMeta = (runtime.primary_response_metadata && typeof runtime.primary_response_metadata === 'object')
      ? runtime.primary_response_metadata as Record<string, unknown>
      : {};
    const compactMeta = (runtime.primary_compact_retry_response_metadata && typeof runtime.primary_compact_retry_response_metadata === 'object')
      ? runtime.primary_compact_retry_response_metadata as Record<string, unknown>
      : {};
    const compactRetry = runtime.primary_compact_retry_invoked
      ? `，同模型紧凑重试 ${String(runtime.primary_compact_retry_model || '-')}(${String(runtime.primary_compact_retry_invalid_reason || 'ok')})`
      : '';
    const overlapCount = Number(runtime.final_selected_and_dropped_overlap_count || 0);
    const consistencyText = Object.prototype.hasOwnProperty.call(runtime, 'final_payload_consistent')
      ? `，信号一致=${boolText(runtime.final_payload_consistent)}${overlapCount > 0 ? `，kept/dropped重叠 ${overlapCount}` : ''}`
      : '';
    const reasonHealthText = Object.prototype.hasOwnProperty.call(payload, 'final_reason_incomplete')
      ? `，理由完整=${boolText(!payload.final_reason_incomplete)}，理由覆盖=${percentText(payload.final_reason_coverage_ratio)}`
      : '';
    const reasonRepairText = runtime.reason_repair_invoked
      ? `，理由修复 ${String(runtime.reason_repair_model || '-')}(${numberText(runtime.reason_repair_mapped_count)}条/${String(runtime.reason_repair_invalid_reason || 'ok')})`
      : '';
    const responseMetaText = primaryMeta && Object.keys(primaryMeta).length > 0
      ? `主响应：status=${String(primaryMeta.http_status || '-')} finish=${String(primaryMeta.finish_reason || '-')} content=${numberText(primaryMeta.content_len)} reasoning=${numberText(primaryMeta.reasoning_len)}${compactMeta && Object.keys(compactMeta).length > 0 ? `；紧凑重试 content=${numberText(compactMeta.content_len)} reasoning=${numberText(compactMeta.reasoning_len)}` : ''}`
      : '';
    return [
      `Review摘要：候选 ${numberText(payload.candidate_total)} → 保留 ${numberText(payload.retained_total)}，丢弃 ${numberText(payload.dropped_total)}`,
      `丢弃来源：LLM ${numberText(payload.drop_by_review_llm_count)}，Gate ${numberText(payload.drop_by_review_gate_count)}，语义去重 ${numberText(payload.drop_by_post_review_dedup_count)}，最终描述重复 ${numberText(payload.drop_final_description_duplicate_count)}`,
      `审查调用：主模型 ${String(runtime.primary_model || '-')}，fallback ${String(runtime.retry_model || '-')}${compactRetry}${reasonRepairText}，来源 ${String(runtime.final_source || '-')}${consistencyText}${reasonHealthText}`,
      responseMetaText,
    ].filter(Boolean).join('\n');
  }

  if (kind === 'generation_summary') {
    return `生成摘要：最终 ${numberText(payload.final_count)} 条，状态 ${statusText(payload.status)}，质量 ${statusText(payload.quality_assessment)}，停止原因 ${formatStopReasons(payload.stop_reason)}`;
  }

  if (kind === 'generation_convergence') {
    return `收敛摘要：primary ${numberText(payload.primary_count)} / gap ${numberText(payload.gap_count)} / review ${numberText(payload.review_count)} / final ${numberText(payload.final_count)}，重复率 ${percentText(payload.duplication_rate_estimate)}，最终描述重复 ${numberText(payload.final_description_dedup_drop_count)}`;
  }

  return null;
}

function formatLogMessage(message: string): string {
  return formatGenDiagMessage(message) || message;
}

export function LogPanel({ userLogs, systemLogs, loading, error, onClear }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState<'user' | 'system'>('user');
  const [filter, setFilter] = useState<'all' | 'error' | 'success'>('all');
  const [panelHeight, setPanelHeight] = useState<number>(() => {
    if (typeof window === 'undefined') return 420;
    return Math.round(window.innerHeight * 0.5);
  });

  const userLogRef = useRef<HTMLDivElement>(null);
  const systemLogRef = useRef<HTMLDivElement>(null);
  const followBottomRef = useRef<{ user: boolean; system: boolean }>({ user: true, system: true });
  const resizeStateRef = useRef<{ active: boolean; startY: number; startHeight: number }>({
    active: false,
    startY: 0,
    startHeight: 0,
  });
  const SCROLL_BOTTOM_THRESHOLD = 24;
  const MIN_PANEL_HEIGHT = 220;
  const MAX_PANEL_HEIGHT_RATIO = 0.85;

  const isNearBottom = (el: HTMLDivElement) => el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_BOTTOM_THRESHOLD;
  const clampPanelHeight = useCallback((height: number) => {
    const viewportHeight = typeof window !== 'undefined' ? window.innerHeight : 900;
    const maxHeight = Math.max(MIN_PANEL_HEIGHT, Math.round(viewportHeight * MAX_PANEL_HEIGHT_RATIO));
    return Math.max(MIN_PANEL_HEIGHT, Math.min(maxHeight, Math.round(height)));
  }, []);

  const isErrorMessage = (msg: string) => /error|失败|异常/i.test(msg);
  const isSuccessMessage = (msg: string) => /成功|完成|success/i.test(msg);
  const isWarningMessage = (msg: string) => /警告|warning/i.test(msg);

  useEffect(() => {
    if (!expanded) return;
    if (activeTab === 'user' && userLogRef.current && followBottomRef.current.user) {
      userLogRef.current.scrollTop = userLogRef.current.scrollHeight;
    } else if (activeTab === 'system' && systemLogRef.current && followBottomRef.current.system) {
      systemLogRef.current.scrollTop = systemLogRef.current.scrollHeight;
    }
  }, [userLogs, systemLogs, activeTab, expanded]);

  useEffect(() => {
    if (!expanded) return;
    if (activeTab === 'user' && userLogRef.current) {
      followBottomRef.current.user = isNearBottom(userLogRef.current);
    } else if (activeTab === 'system' && systemLogRef.current) {
      followBottomRef.current.system = isNearBottom(systemLogRef.current);
    }
  }, [activeTab, expanded]);

  useEffect(() => {
    const onMouseMove = (event: MouseEvent) => {
      const state = resizeStateRef.current;
      if (!state.active) return;
      const nextHeight = clampPanelHeight(state.startHeight + (state.startY - event.clientY));
      setPanelHeight(nextHeight);
    };

    const onMouseUp = () => {
      if (!resizeStateRef.current.active) return;
      resizeStateRef.current.active = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [clampPanelHeight]);

  const handleResizeMouseDown = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (!expanded) return;
    event.preventDefault();
    event.stopPropagation();
    resizeStateRef.current = {
      active: true,
      startY: event.clientY,
      startHeight: panelHeight,
    };
    document.body.style.cursor = 'ns-resize';
    document.body.style.userSelect = 'none';
  };

  const handleLogScroll = () => {
    const target = activeTab === 'user' ? userLogRef.current : systemLogRef.current;
    if (!target) return;
    followBottomRef.current[activeTab] = isNearBottom(target);
  };

  useEffect(() => {
    if (systemLogs.length > 0) {
      const lastLog = systemLogs[systemLogs.length - 1];
      if (isErrorMessage(lastLog.message || '') && !expanded) {
        setExpanded(true);
      }
    }
  }, [systemLogs.length]);

  const formatTime = (iso: string) => {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleTimeString('zh-CN', { hour12: false });
  };

  const getFilteredLogs = (logs: LogEntry[]) => {
    if (filter === 'all') return logs;
    return logs.filter((l) => {
      const msg = l.message || '';
      if (filter === 'error') return isErrorMessage(msg);
      if (filter === 'success') return !isErrorMessage(msg);
      return true;
    });
  };

  const currentLogs = activeTab === 'user' ? userLogs : systemLogs;
  const filteredLogs = getFilteredLogs(currentLogs);

  const errorCount = useMemo(() => systemLogs.filter((l) => isErrorMessage(l.message || '')).length, [systemLogs]);

  const handleExport = () => {
    const content = [...userLogs, ...systemLogs]
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
      .map((l) => `[${l.created_at}] [${l.log_type.toUpperCase()}] ${l.message}`)
      .join('\n');

    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `logs_${new Date().toISOString().split('T')[0]}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div
      className={classNames('dashboard-log-shell w-100 border-top shadow-lg d-flex flex-column transition-all', {
        'is-expanded': expanded,
        'is-collapsed': !expanded,
      })}
      style={expanded ? { height: `${panelHeight}px` } : undefined}
    >
      {expanded ? (
        <div
          className="dashboard-log-resizer"
          onMouseDown={handleResizeMouseDown}
          onClick={(e) => e.stopPropagation()}
          role="separator"
          aria-label="调整日志面板高度"
        />
      ) : null}

      <div
        className={classNames('dashboard-log-header d-flex align-items-center justify-content-between px-3 py-1', {
          'is-expanded': expanded,
        })}
        onClick={() => setExpanded(!expanded)}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && setExpanded(!expanded)}
        tabIndex={0}
        role="button"
        aria-expanded={expanded}
        aria-label="切换日志面板"
      >
        <div className="d-flex align-items-center gap-3 overflow-hidden w-100">
          <div className="d-flex align-items-center gap-2 fw-bold flex-shrink-0">
            {expanded ? (
              <span className="text-primary d-flex align-items-center gap-2">
                <FaChevronDown /> 实时日志
              </span>
            ) : (
              <div className="d-flex align-items-center gap-3">
                {errorCount > 0 ? (
                  <span className="text-danger d-flex align-items-center gap-2">
                    <FaExclamationCircle />
                    <span>{errorCount} 个错误</span>
                  </span>
                ) : (
                  <span className="text-success d-flex align-items-center gap-2">
                    <FaCheckCircle />
                    <span>运行正常</span>
                  </span>
                )}
                {loading ? (
                  <span className="text-muted small">
                    <span className="spinner-border spinner-border-sm me-1" />连接中...
                  </span>
                ) : null}
                {error ? <span className="text-danger small">服务异常: {error}</span> : null}
              </div>
            )}
          </div>
        </div>

        <div className="d-flex gap-2 ms-3" onClick={(e) => e.stopPropagation()}>
          {expanded ? (
            <>
              <Button variant="outline-secondary" size="sm" onClick={handleExport} title="导出日志" className="py-0 px-2 dashboard-log-mini-btn">
                <FaDownload />
              </Button>
              {onClear ? (
                <Button variant="outline-danger" size="sm" onClick={onClear} title="清空日志" className="py-0 px-2 dashboard-log-mini-btn">
                  <FaTrash />
                </Button>
              ) : null}
            </>
          ) : (
            <FaChevronUp size={12} className="opacity-50" />
          )}
        </div>
      </div>

      {expanded ? (
        <div className="d-flex flex-column flex-grow-1 overflow-hidden dashboard-log-content">
          <div className="d-flex align-items-center justify-content-between px-3 py-1 border-bottom bg-light dashboard-log-toolbar">
            <Nav variant="tabs" className="border-bottom-0 dashboard-log-tabs" activeKey={activeTab} onSelect={(k) => setActiveTab(k as 'user' | 'system')}>
              <Nav.Item>
                <Nav.Link eventKey="user" className="py-1 px-3 small dashboard-log-tab-link">
                  用户操作 ({userLogs.length})
                </Nav.Link>
              </Nav.Item>
              <Nav.Item>
                <Nav.Link eventKey="system" className="py-1 px-3 small dashboard-log-tab-link">
                  系统日志 ({systemLogs.length})
                </Nav.Link>
              </Nav.Item>
            </Nav>

            <div className="d-flex gap-1 dashboard-log-filter-group">
              <Button size="sm" variant={filter === 'all' ? 'primary' : 'outline-secondary'} className="py-0 px-2 dashboard-log-mini-btn" onClick={() => setFilter('all')}>
                全部
              </Button>
              <Button size="sm" variant={filter === 'error' ? 'danger' : 'outline-secondary'} className="py-0 px-2 dashboard-log-mini-btn" onClick={() => setFilter('error')}>
                错误
              </Button>
              <Button size="sm" variant={filter === 'success' ? 'success' : 'outline-secondary'} className="py-0 px-2 dashboard-log-mini-btn" onClick={() => setFilter('success')}>
                正常
              </Button>
            </div>
          </div>

          <div className="flex-grow-1 overflow-auto dashboard-log-stream dashboard-log-stream-body p-3 font-monospace" ref={activeTab === 'user' ? userLogRef : systemLogRef} onScroll={handleLogScroll}>
            {loading ? <div className="text-muted">正在连接日志服务...</div> : null}
            {error ? <div className="text-danger">日志服务异常: {error}</div> : null}
            {filteredLogs.length === 0 ? <div className="text-muted opacity-50">暂无日志</div> : null}
            {filteredLogs.map((log) => {
              const msg = log.message || '';
              const displayMsg = formatLogMessage(msg);
              return (
                <div
                  key={log.id}
                  className={classNames('d-flex gap-2 mb-1', {
                    'text-danger': isErrorMessage(msg),
                    'text-success': isSuccessMessage(msg),
                    'text-warning': isWarningMessage(msg),
                  })}
                >
                  <span className="opacity-50 flex-shrink-0 dashboard-log-time-col">
                    {formatTime(log.created_at)}
                  </span>
                  <span className="text-break" style={{ whiteSpace: 'pre-wrap' }}>{displayMsg}</span>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
