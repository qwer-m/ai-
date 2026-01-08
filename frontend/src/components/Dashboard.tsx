import { useEffect, useMemo, useState } from 'react';
import { Nav, Button, Form, Container, Badge, Spinner } from 'react-bootstrap';
import { FaFileCode, FaMousePointer, FaNetworkWired, FaClipboardCheck, FaDatabase, FaFolder, FaCog, FaPlus, FaCheckCircle, FaExclamationTriangle, FaServer, FaSignOutAlt } from 'react-icons/fa';
import 'bootstrap/dist/css/bootstrap.min.css';
import '../theme.css'; 
import '../App.css';
import { ConfigModal } from './ConfigModal';
import { ProjectManagement, type Project } from './ProjectManagement';
import { TestGeneration } from './TestGeneration';
import { KnowledgeBase } from './KnowledgeBase';
import { UIAutomation } from './UIAutomation';
import { APITesting } from './APITesting';
import { Evaluation } from './Evaluation';
import { LogPanel } from './LogPanel';
import { api } from '../utils/api';
import { useAuth } from '../contexts/AuthContext';
import { useNavigate } from 'react-router-dom';

type LogEntry = {
  id: number;
  project_id: number;
  log_type: 'user' | 'system';
  message: string;
  created_at: string;
};

type HealthResponse = {
  mysql?: { ok: boolean; details?: string };
  redis?: { ok: boolean; details?: string; host?: string; port?: number };
};

const safeGetItem = (key: string) => {
    try { return window.localStorage.getItem(key); } catch { return null; }
};

const safeSetItem = (key: string, value: string) => {
    try { window.localStorage.setItem(key, value); } catch {}
};

export const Dashboard = () => {
  console.log("Dashboard component rendering...");
  const { logout, user } = useAuth();
  const navigate = useNavigate();

  const navItems = [
    { key: 'gen', label: '测试用例生成', icon: <FaFileCode /> },
    { key: 'ui', label: 'UI自动化执行', icon: <FaMousePointer /> },
    { key: 'api', label: '接口测试执行', icon: <FaNetworkWired /> },
    { key: 'eval', label: '质量评估与召回', icon: <FaClipboardCheck /> },
    { key: 'kb', label: '知识库管理', icon: <FaDatabase /> },
    { key: 'proj', label: '项目管理', icon: <FaFolder /> },
  ];

  const [activeTab, setActiveTab] = useState(() => {
      const saved = safeGetItem('currentActiveTab');
      const validKeys = navItems.map(i => i.key);
      return (saved && validKeys.includes(saved)) ? saved : 'gen';
  });
  
  useEffect(() => {
      safeSetItem('currentActiveTab', activeTab);
  }, [activeTab]);

  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  const [projectId, setProjectId] = useState<number | null>(null);
  
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsError, setLogsError] = useState<string | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [clearedAt] = useState<number>(0);
  
  const [healthLoading, setHealthLoading] = useState(false);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  
  // Shared state for Evaluation Module import
  const [evalGenerated, setEvalGenerated] = useState('');
  const [evalModified, setEvalModified] = useState('');
  const [evalResult, setEvalResult] = useState<string | null>(null);
  const [recallRetrieved, setRecallRetrieved] = useState('');
  const [recallRelevant, setRecallRelevant] = useState('');
  const [recallResult, setRecallResult] = useState<string | null>(null);
  const [uiEvalScript, setUiEvalScript] = useState('');
  const [uiEvalExec, setUiEvalExec] = useState('');
  const [uiEvalOutput, setUiEvalOutput] = useState<string | null>(null);
  const [apiEvalScript, setApiEvalScript] = useState('');
  const [apiEvalExec, setApiEvalExec] = useState('');
  const [apiEvalOutput, setApiEvalOutput] = useState<string | null>(null);

  // Modals
  const [showConfig, setShowConfig] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);

  const fetchProjects = async () => {
    setProjectsLoading(true);
    setProjectsError(null);
    try {
      const data = await api.get<Project[]>('/api/projects');
      setProjects(Array.isArray(data) ? data : []);
      if (!projectId) {
          const saved = safeGetItem('currentProjectId');
          const savedId = saved ? Number(saved) : NaN;
          const projectExists = Array.isArray(data) && data.some(p => p.id === savedId);
          const nextId = (projectExists && Number.isFinite(savedId))
            ? savedId
            : (Array.isArray(data) && data.length ? data[0].id : null);
          setProjectId(nextId);
      }
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : String(e);
      setProjectsError(errMsg);
      if (errMsg.includes('401') || errMsg.includes('Invalid API-key') || errMsg.includes('QUOTA') || errMsg.includes('API Key not set')) {
          setConfigError(errMsg);
          setShowConfig(true);
      }
    } finally {
      setProjectsLoading(false);
    }
  };

  useEffect(() => {
    fetchProjects();
  }, []);

  useEffect(() => {
    let cancelled = false;
    const fetchHealth = async () => {
      setHealthLoading(true);
      setHealthError(null);
      try {
        const data = await api.get<HealthResponse>('/api/health');
        if (cancelled) return;
        setHealth(data && typeof data === 'object' ? data : null);
      } catch (e) {
        if (cancelled) return;
        setHealthError(e instanceof Error ? e.message : String(e));
        setHealth(null);
      } finally {
        if (!cancelled) setHealthLoading(false);
      }
    };
    fetchHealth();
    const timer = window.setInterval(fetchHealth, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!projectId) return;
    safeSetItem('currentProjectId', String(projectId));
  }, [projectId]);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    
    const load = async (isPolling = false) => {
      if (!isPolling) {
          setLogsLoading(true);
          setLogsError(null);
      }
      try {
        const url = `/api/logs/${projectId}`;
        const data = await api.get<LogEntry[]>(url);
        if (cancelled) return;
        
        const validLogs = Array.isArray(data) ? data : [];
            
        setLogs(validLogs);
      } catch (e) {
        if (cancelled) return;
        if (!isPolling) {
            setLogsError(e instanceof Error ? e.message : String(e));
            setLogs([]);
        } else {
            console.error("Polling logs failed", e);
        }
      } finally {
        if (!cancelled && !isPolling) setLogsLoading(false);
      }
    };
    
    load(false);
    const timer = window.setInterval(() => load(true), 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [projectId, clearedAt]);

  const { userLogs, systemLogs } = useMemo(() => {
    const u: LogEntry[] = [];
    const s: LogEntry[] = [];
    const allLogs = [...logs].sort((a, b) => {
        const timeA = new Date(a.created_at).getTime() || 0;
        const timeB = new Date(b.created_at).getTime() || 0;
        return timeA - timeB; 
    });

    const seenIds = new Set<number>();
    allLogs.forEach((l) => {
      if (seenIds.has(l.id)) return;
      seenIds.add(l.id);
      if (l.log_type === 'user') u.push(l);
      else s.push(l);
    });
    return { userLogs: u, systemLogs: s };
  }, [logs]);

  const handleLog = async (msg: string, type: 'user' | 'system' = 'user') => {
      if (!projectId) return;
      const tempLog: LogEntry = {
          id: Date.now(),
          project_id: projectId,
          log_type: type,
          message: msg,
          created_at: new Date().toISOString()
      };
      setLogs(prev => [...prev, tempLog]); 

      try {
          const data = await api.post<any>('/api/logs', {
              project_id: projectId,
              log_type: type,
              message: msg
          });
          if (data.status === 'success' && data.id) {
             setLogs(prev => prev.map(l => l.id === tempLog.id ? { ...l, id: data.id } : l));
          }
      } catch (e) {
          console.error(e);
          setLogs(prev => prev.filter(l => l.id !== tempLog.id));
      }
  };

  const handleTestGenerated = (data: any) => {
      try {
          setEvalGenerated(JSON.stringify(data, null, 2));
      } catch {}
  };

  const handleLogout = () => {
      logout();
      navigate('/login');
  };

  return (
    <div className="d-flex flex-column h-100 w-100 overflow-hidden bg-app">
        {/* Middle Area: Sidebar + Main Content */}
        <div className="flex-grow-1 d-flex overflow-hidden p-3 pb-0" style={{ gap: '16px', marginBottom: '6px' }}>
            {/* Left Sidebar Frame */}
            <div className="d-flex flex-column glass-panel rounded-xl flex-shrink-0 overflow-hidden border-0" style={{ width: '260px', minWidth: '260px' }}>
                <div className="p-4 border-bottom border-light bg-white bg-opacity-50">
                    <h1 className="h5 mb-0 text-gradient fw-bold d-flex align-items-center gap-2">
                        <FaServer className="text-primary-500" /> AI测试平台
                        <Badge bg="light" text="secondary" className="ms-auto fw-normal opacity-75" style={{fontSize: '0.6em'}}>PRO</Badge>
                    </h1>
                    {user && <div className="small text-secondary mt-2">Welcome, {user.username}</div>}
                </div>

                <div className="flex-grow-1 p-3 overflow-auto">
                    <Nav variant="pills" className="flex-column gap-2" activeKey={activeTab} onSelect={(k) => setActiveTab(k || 'gen')}>
                        {navItems.map(item => (
                            <Nav.Link 
                                key={item.key} 
                                eventKey={item.key} 
                                className={`sidebar-link d-flex align-items-center gap-3 px-3 py-2 rounded-lg transition-all ${activeTab === item.key ? 'active-pro shadow-sm bg-primary text-white fw-bold' : 'text-secondary hover-bg-light'}`}
                                style={{ transition: 'all 0.2s ease' }}
                            >
                                <span className={activeTab === item.key ? 'text-white' : 'text-tertiary'}>{item.icon}</span>
                                <span>{item.label}</span>
                                {activeTab === item.key && <div className="ms-auto rounded-circle bg-white" style={{width: '6px', height: '6px'}}></div>}
                            </Nav.Link>
                        ))}
                    </Nav>
                </div>

                <div className="p-3 border-top border-light bg-white bg-opacity-25">
                    {healthLoading ? (
                        <div className="text-center text-muted small"><Spinner size="sm" animation="border" /> 检查服务状态...</div>
                    ) : (
                    <div className="d-flex flex-column gap-2">
                        <div className="d-flex justify-content-between align-items-center small p-2 rounded bg-white bg-opacity-50 border border-light">
                            <span className="text-secondary fw-medium">MySQL</span>
                            {healthError ? <Badge bg="danger">错误</Badge> : (health?.mysql?.ok ? 
                                <span className="text-success d-flex align-items-center gap-1 fw-bold" style={{fontSize: '0.8em'}}><FaCheckCircle /> 正常</span> : 
                                <span className="text-danger d-flex align-items-center gap-1 fw-bold" style={{fontSize: '0.8em'}}><FaExclamationTriangle /> 异常</span>
                            )}
                        </div>
                        <div className="d-flex justify-content-between align-items-center small p-2 rounded bg-white bg-opacity-50 border border-light">
                            <span className="text-secondary fw-medium">Redis</span>
                            {healthError ? <Badge bg="danger">错误</Badge> : (health?.redis?.ok ? 
                                <span className="text-success d-flex align-items-center gap-1 fw-bold" style={{fontSize: '0.8em'}}><FaCheckCircle /> 正常</span> : 
                                <span className="text-warning d-flex align-items-center gap-1 fw-bold" style={{fontSize: '0.8em'}}><FaExclamationTriangle /> 未连接</span>
                            )}
                        </div>
                    </div>
                    )}
                </div>
            </div>

            {/* Main Content Frame */}
            <div className="flex-grow-1 d-flex flex-column glass-panel rounded-xl overflow-hidden position-relative border-0">
                {/* Top Header */}
                <div className="bg-white bg-opacity-50 border-bottom border-light px-4 py-3 d-flex justify-content-between align-items-center backdrop-blur" style={{ height: '64px' }}>
                    <div className="d-flex align-items-center gap-2">
                        <div className="bg-white text-secondary px-3 py-1 rounded fw-bold shadow-sm d-flex align-items-center justify-content-center" style={{ height: '36px', fontSize: '0.875rem', whiteSpace: 'nowrap', minWidth: 'fit-content' }}>
                            项目
                        </div>
                        <Form.Select 
                            value={projectId ?? ''} 
                            onChange={(e) => setProjectId(e.target.value ? Number(e.target.value) : null)}
                            disabled={projectsLoading || !projects.length}
                            style={{ minWidth: '120px', maxWidth: '200px', height: '36px' }}
                            size="sm"
                            className="input-pro border-0 shadow-sm bg-light text-secondary position-relative"
                        >
                            {projects.length > 0 ? (
                                projects.map((p) => (
                                    <option key={p.id} value={p.id}>{p.name}</option>
                                ))
                            ) : (
                                <option value="">请创建项目</option>
                            )}
                        </Form.Select>
                    </div>
                    <div className="d-flex gap-2">
                        <Button variant="link" className="text-decoration-none text-secondary p-1" title="通知">
                             <div className="position-relative">
                                <FaServer />
                                <span className="position-absolute top-0 start-100 translate-middle p-1 bg-danger border border-light rounded-circle"></span>
                             </div>
                        </Button>
                        <div className="vr mx-2 opacity-25"></div>
                        <Button variant="primary" size="sm" onClick={() => setActiveTab('proj')} className="btn-pro-primary d-flex align-items-center gap-2">
                            <FaPlus /> <span className="d-none d-md-inline">新建项目</span>
                        </Button>
                        <Button variant="light" size="sm" onClick={() => setShowConfig(true)} className="btn-light-pro d-flex align-items-center gap-2 bg-white shadow-sm border-0 text-secondary">
                            <FaCog />
                        </Button>
                        <Button variant="light" size="sm" onClick={handleLogout} className="btn-light-pro d-flex align-items-center gap-2 bg-white shadow-sm border-0 text-secondary" title="Logout">
                            <FaSignOutAlt />
                        </Button>
                    </div>
                </div>

                {/* Scrollable Content */}
                <div className={`flex-grow-1 p-4 pb-0 position-relative ${activeTab === 'kb' ? 'overflow-hidden' : 'overflow-auto custom-scrollbar'}`}>
                    <Container fluid className={`p-0 d-flex flex-column ${activeTab === 'kb' ? 'h-100' : ''}`} style={{ minHeight: '100%' }}>
                        <div 
                            className={`d-flex flex-column ${
                                activeTab === 'kb' ? 'flex-grow-1 overflow-hidden' : ''
                            }`} 
                            style={activeTab === 'kb' ? { minHeight: '0' } : {}}
                        >
                            {activeTab === 'gen' && (
                                <TestGeneration 
                                    key={projectId}
                                    projectId={projectId} 
                                    onLog={msg => handleLog(msg, 'user')} 
                                    onGenerated={handleTestGenerated}
                                    onError={(msg) => {
                                        setConfigError(msg);
                                        setShowConfig(true);
                                    }}
                                />
                            )}
                            {activeTab === 'ui' && <UIAutomation projectId={projectId} onLog={msg => handleLog(msg, 'user')} />}
                            {activeTab === 'api' && <APITesting projectId={projectId} onLog={msg => handleLog(msg, 'user')} />}
                            {activeTab === 'kb' && <KnowledgeBase projectId={projectId} onLog={msg => handleLog(msg, 'system')} />}
                            {activeTab === 'proj' && (
                                <ProjectManagement 
                                    projects={projects} 
                                    loading={projectsLoading} 
                                    error={projectsError} 
                                    onRefresh={fetchProjects} 
                                    onSelectProject={setProjectId}
                                    onLog={handleLog}
                                />
                            )}
                            {activeTab === 'eval' && (
                                <Evaluation 
                                    projectId={projectId} 
                                    logs={logs}
                                    onLog={msg => handleLog(msg, 'user')}
                                    evalGenerated={evalGenerated} setEvalGenerated={setEvalGenerated}
                                    evalModified={evalModified} setEvalModified={setEvalModified}
                                    evalResult={evalResult} setEvalResult={setEvalResult}
                                    recallRetrieved={recallRetrieved} setRecallRetrieved={setRecallRetrieved}
                                    recallRelevant={recallRelevant} setRecallRelevant={setRecallRelevant}
                                    recallResult={recallResult} setRecallResult={setRecallResult}
                                    uiEvalScript={uiEvalScript} setUiEvalScript={setUiEvalScript}
                                    uiEvalExec={uiEvalExec} setUiEvalExec={setUiEvalExec}
                                    uiEvalOutput={uiEvalOutput} setUiEvalOutput={setUiEvalOutput}
                                    apiEvalScript={apiEvalScript} setApiEvalScript={setApiEvalScript}
                                    apiEvalExec={apiEvalExec} setApiEvalExec={setApiEvalExec}
                                    apiEvalOutput={apiEvalOutput} setApiEvalOutput={setApiEvalOutput}
                                />
                            )}
                        </div>
                    </Container>
                </div>
            </div>
        </div>

        {/* Log Panel Area */}
        <div className="flex-shrink-0 w-100 position-relative">
            <LogPanel 
                userLogs={userLogs} 
                systemLogs={systemLogs} 
                loading={logsLoading} 
                error={logsError} 
                onClear={async () => {
                    if (projectId) {
                        try {
                            await api.delete(`/api/logs/${projectId}`);
                            setLogs([]);
                        } catch (e) {
                            console.error("Failed to clear logs", e);
                        }
                    }
                }}
            />
        </div>

        <ConfigModal show={showConfig} onHide={() => setShowConfig(false)} initialError={configError} />
    </div>
  );
};
