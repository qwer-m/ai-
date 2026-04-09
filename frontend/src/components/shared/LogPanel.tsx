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
                  <span className="text-break">{msg}</span>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
