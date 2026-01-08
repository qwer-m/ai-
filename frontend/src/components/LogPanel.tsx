import { useState, useEffect, useRef, useMemo } from 'react';
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
  
  const userLogRef = useRef<HTMLDivElement>(null);
  const systemLogRef = useRef<HTMLDivElement>(null);

  // Auto-scroll logic for expanded view
  useEffect(() => {
    if (activeTab === 'user' && userLogRef.current) {
      userLogRef.current.scrollTop = userLogRef.current.scrollHeight;
    } else if (activeTab === 'system' && systemLogRef.current) {
      systemLogRef.current.scrollTop = systemLogRef.current.scrollHeight;
    }
  }, [userLogs, systemLogs, activeTab, expanded]);

  // Smart Collapse Logic: Auto-expand on new error (only if not already expanded)
  useEffect(() => {
    if (systemLogs.length > 0) {
      const lastLog = systemLogs[systemLogs.length - 1];
      const msg = lastLog.message || '';
      const isError = msg.includes('Error') || msg.includes('失败') || msg.includes('异常');
      if (isError && !expanded) {
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
    return logs.filter(l => {
        const msg = l.message || '';
        const isError = msg.includes('Error') || msg.includes('失败') || msg.includes('异常');
        if (filter === 'error') return isError;
        if (filter === 'success') return !isError;
        return true;
    });
  };

  const currentLogs = activeTab === 'user' ? userLogs : systemLogs;
  const filteredLogs = getFilteredLogs(currentLogs);

  // Error Grading Logic
  const errorCount = useMemo(() => {
    return systemLogs.filter(l => {
      const msg = l.message || '';
      return msg.includes('Error') || msg.includes('失败') || msg.includes('异常');
    }).length;
  }, [systemLogs]);

  const handleExport = () => {
    const content = [...userLogs, ...systemLogs]
        .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
        .map(l => `[${l.created_at}] [${l.log_type.toUpperCase()}] ${l.message}`)
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
        className={classNames("w-100 border-top shadow-lg d-flex flex-column transition-all bg-white", {
            "h-auto": !expanded
        })} 
        style={{ 
            height: expanded ? '50vh' : 'auto',
            zIndex: 1040, 
            transition: 'height 0.3s cubic-bezier(0.4, 0, 0.2, 1)' 
        }}
    >
      
      {/* Status Bar (Collapsed) / Header (Expanded) */}
      <div 
        className="d-flex align-items-center justify-content-between px-3 py-1 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && setExpanded(!expanded)}
        tabIndex={0}
        role="button"
        aria-expanded={expanded}
        aria-label="Toggle Log Panel"
        style={{ 
            cursor: 'pointer', 
            height: expanded ? '40px' : '32px',
            backgroundColor: expanded ? '#f8f9fa' : '#2d3748', // Light when expanded, Dark Slate when collapsed
            color: expanded ? '#212529' : '#e2e8f0',
            borderBottom: expanded ? '1px solid #dee2e6' : 'none'
        }}
      >
        <div className="d-flex align-items-center gap-3 overflow-hidden w-100">
            {/* Status Indicator & Error Count */}
            <div className="d-flex align-items-center gap-2 fw-bold flex-shrink-0">
                {expanded ? (
                     <span className="text-primary"><FaChevronDown /> 实时日志</span>
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
                        {loading && <span className="text-muted small"><span className="spinner-border spinner-border-sm me-1" />连接中...</span>}
                        {error && <span className="text-danger small">服务异常: {error}</span>}
                    </div>
                )}
            </div>
        </div>

        {/* Actions */}
        <div className="d-flex gap-2 ms-3" onClick={e => e.stopPropagation()}>
            {expanded && (
                <>
                    <Button variant="outline-secondary" size="sm" onClick={handleExport} title="导出日志" className="py-0 px-2" style={{ fontSize: '12px' }}>
                        <FaDownload />
                    </Button>
                    {onClear && (
                        <Button variant="outline-danger" size="sm" onClick={onClear} title="清空" className="py-0 px-2" style={{ fontSize: '12px' }}>
                            <FaTrash />
                        </Button>
                    )}
                </>
            )}
            {!expanded && (
                <FaChevronUp size={12} className="opacity-50" />
            )}
        </div>
      </div>

      {/* Expanded Content */}
      {expanded && (
        <div className="d-flex flex-column flex-grow-1 overflow-hidden bg-white">
            <div className="d-flex align-items-center justify-content-between px-3 py-1 border-bottom bg-light">
                <Nav variant="tabs" className="border-bottom-0" activeKey={activeTab} onSelect={(k) => setActiveTab(k as 'user' | 'system')}>
                    <Nav.Item>
                        <Nav.Link eventKey="user" className="py-1 px-3 small">用户操作 ({userLogs.length})</Nav.Link>
                    </Nav.Item>
                    <Nav.Item>
                        <Nav.Link eventKey="system" className="py-1 px-3 small">系统日志 ({systemLogs.length})</Nav.Link>
                    </Nav.Item>
                </Nav>
                
                <div className="d-flex gap-1">
                    <Button size="sm" variant={filter === 'all' ? 'primary' : 'outline-secondary'} className="py-0 px-2" style={{ fontSize: '12px' }} onClick={() => setFilter('all')}>全部</Button>
                    <Button size="sm" variant={filter === 'error' ? 'danger' : 'outline-secondary'} className="py-0 px-2" style={{ fontSize: '12px' }} onClick={() => setFilter('error')}>错误</Button>
                    <Button size="sm" variant={filter === 'success' ? 'success' : 'outline-secondary'} className="py-0 px-2" style={{ fontSize: '12px' }} onClick={() => setFilter('success')}>正常</Button>
                </div>
            </div>

            <div className="flex-grow-1 overflow-auto bg-dark text-light p-3 font-monospace" style={{ fontSize: '13px' }} ref={activeTab === 'user' ? userLogRef : systemLogRef}>
                {loading && <div className="text-muted">正在连接日志服务...</div>}
                {error && <div className="text-danger">日志服务异常: {error}</div>}
                {filteredLogs.length === 0 && <div className="text-muted opacity-50">暂无日志</div>}
                {filteredLogs.map((log) => {
                    const msg = log.message || '';
                    const isError = msg.includes('Error') || msg.includes('失败') || msg.includes('异常');
                    const isSuccess = msg.includes('成功') || msg.includes('完成');
                    const isWarning = msg.includes('警告') || msg.includes('Warning');
                    return (
                        <div key={log.id} className={classNames("d-flex gap-2 mb-1", {
                            "text-danger": isError,
                            "text-success": isSuccess,
                            "text-warning": isWarning
                        })}>
                            <span className="opacity-50 flex-shrink-0" style={{ minWidth: '70px' }}>{formatTime(log.created_at)}</span>
                            <span className="text-break">{msg}</span>
                        </div>
                    );
                })}
            </div>
        </div>
      )}
      
      {/* Styles for marquee and pulse */}
      <style>{`
        @keyframes marquee {
            0% { transform: translateX(100%); }
            100% { transform: translateX(-100%); }
        }
        .animate-pulse {
            animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: .5; }
        }
      `}</style>
    </div>
  );
}
