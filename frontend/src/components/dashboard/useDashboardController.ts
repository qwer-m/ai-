import { useEffect, useMemo, useState } from 'react';
import type { Project } from '../pages/ProjectManagement';
import { api } from '../../utils/api';
import { dashboardNavItems, findParentKeyByChild, normalizeDashboardActiveTab } from './model/dashboardNavigation';
import type { HealthResponse, LogEntry } from './model/types';

const safeGetItem = (key: string) => {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
};

const safeSetItem = (key: string, value: string) => {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // 无痕模式/受限环境下忽略本地存储失败，不影响主流程。
  }
};

const shouldOpenConfigByError = (message: string) => {
  return [
    '401',
    'Invalid API-key',
    'QUOTA',
    'API Key not set',
  ].some((flag) => message.includes(flag));
};

export function useDashboardController() {
  const [themeMode, setThemeMode] = useState<'light' | 'dark'>(
    () => (safeGetItem('themeMode') === 'dark' ? 'dark' : 'light'),
  );
  const [shouldAutoEval, setShouldAutoEval] = useState(false);

  const [activeTab, setActiveTab] = useState(() =>
    normalizeDashboardActiveTab(safeGetItem('currentActiveTab'), dashboardNavItems),
  );
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);

  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  const [projectId, setProjectId] = useState<number | null>(null);

  const [logsLoading, setLogsLoading] = useState(false);
  const [logsError, setLogsError] = useState<string | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);

  const [healthLoading, setHealthLoading] = useState(false);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);

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

  const [showConfig, setShowConfig] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);

  const toggleExpand = (key: string) => {
    setExpandedKeys((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  };

  const handleOpenConfig = () => {
    setConfigError(null);
    setShowConfig(true);
  };

  const handleCloseConfig = () => {
    setShowConfig(false);
    setConfigError(null);
  };

  const openConfigWithError = (message: string) => {
    setConfigError(message);
    setShowConfig(true);
  };

  const fetchProjects = async () => {
    setProjectsLoading(true);
    setProjectsError(null);

    try {
      const data = await api.get<Project[]>('/api/projects');
      const projectList = Array.isArray(data) ? data : [];
      setProjects(projectList);

      if (!projectId) {
        const saved = safeGetItem('currentProjectId');
        const savedId = saved ? Number(saved) : NaN;
        const savedExists = projectList.some((project) => project.id === savedId);

        if (savedExists && Number.isFinite(savedId)) {
          setProjectId(savedId);
        } else {
          setProjectId(projectList.length > 0 ? projectList[0].id : null);
        }
      }
    } catch (error) {
      const errorMsg = error instanceof Error ? error.message : String(error);
      setProjectsError(errorMsg);
      if (shouldOpenConfigByError(errorMsg)) {
        openConfigWithError(errorMsg);
      }
    } finally {
      setProjectsLoading(false);
    }
  };

  useEffect(() => {
    void fetchProjects();
    // 仅首屏加载项目，后续由业务动作手动刷新。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    safeSetItem('currentActiveTab', activeTab);

    const parentKey = findParentKeyByChild(dashboardNavItems, activeTab);
    if (parentKey && !expandedKeys.includes(parentKey)) {
      setExpandedKeys((prev) => [...prev, parentKey]);
    }
  }, [activeTab, expandedKeys]);

  /**
   * 主题切换同步 body class 与本地缓存，确保页面刷新后仍保留用户偏好。
   */
  useEffect(() => {
    document.body.classList.toggle('theme-dark', themeMode === 'dark');
    document.body.classList.toggle('theme-light', themeMode === 'light');
    safeSetItem('themeMode', themeMode);
  }, [themeMode]);

  useEffect(() => {
    let cancelled = false;

    const fetchHealth = async () => {
      setHealthError(null);
      try {
        const data = await api.get<HealthResponse>('/api/health');
        if (cancelled) return;
        setHealth(data && typeof data === 'object' ? data : null);
      } catch (error) {
        if (cancelled) return;
        setHealthError(error instanceof Error ? error.message : String(error));
        setHealth(null);
      } finally {
        if (!cancelled) setHealthLoading(false);
      }
    };

    void fetchHealth();
    const timer = window.setInterval(() => {
      void fetchHealth();
    }, 15000);

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

    const loadLogs = async (isPolling = false) => {
      if (!isPolling) {
        setLogsLoading(true);
        setLogsError(null);
      }

      try {
        const data = await api.get<LogEntry[]>(`/api/logs/${projectId}`);
        if (cancelled) return;
        setLogs(Array.isArray(data) ? data : []);
      } catch (error) {
        if (cancelled) return;
        if (!isPolling) {
          setLogsError(error instanceof Error ? error.message : String(error));
          setLogs([]);
        } else {
          console.error('Polling logs failed', error);
        }
      } finally {
        if (!cancelled && !isPolling) setLogsLoading(false);
      }
    };

    void loadLogs(false);
    const timer = window.setInterval(() => {
      void loadLogs(true);
    }, 3000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [projectId]);

  const { userLogs, systemLogs } = useMemo(() => {
    const userLogList: LogEntry[] = [];
    const systemLogList: LogEntry[] = [];

    const sortedLogs = [...logs].sort((a, b) => {
      const timeA = new Date(a.created_at).getTime() || 0;
      const timeB = new Date(b.created_at).getTime() || 0;
      return timeA - timeB;
    });

    const seenIds = new Set<number>();
    sortedLogs.forEach((log) => {
      if (seenIds.has(log.id)) return;
      seenIds.add(log.id);
      if (log.log_type === 'user') {
        userLogList.push(log);
      } else {
        systemLogList.push(log);
      }
    });

    return { userLogs: userLogList, systemLogs: systemLogList };
  }, [logs]);

  /**
   * 日志采用“先本地追加，后端回写 ID”的乐观更新策略，保证操作即时反馈。
   */
  const handleLog = async (msg: string, type: 'user' | 'system' = 'user') => {
    if (!projectId) return;

    const tempLog: LogEntry = {
      id: Date.now(),
      project_id: projectId,
      log_type: type,
      message: msg,
      created_at: new Date().toISOString(),
    };

    setLogs((prev) => [...prev, tempLog]);

    try {
      const data = await api.post<{ status?: string; id?: number }>('/api/logs', {
        project_id: projectId,
        log_type: type,
        message: msg,
      });
      if (data.status === 'success' && data.id) {
        setLogs((prev) => prev.map((log) => (log.id === tempLog.id ? { ...log, id: data.id as number } : log)));
      }
    } catch (error) {
      console.error(error);
      setLogs((prev) => prev.filter((log) => log.id !== tempLog.id));
    }
  };

  const clearLogs = async () => {
    if (!projectId) return;

    try {
      await api.delete(`/api/logs/${projectId}`);
      setLogs([]);
    } catch (error) {
      console.error('Failed to clear logs', error);
    }
  };

  const handleTestGenerated = (data: unknown) => {
    try {
      setEvalGenerated(JSON.stringify(data, null, 2));
    } catch {
      // 仅兜底，正常数据均应可序列化。
    }
  };

  const handleGenerationComplete = () => {
    // ????????????????????????????? Tab?
    setShouldAutoEval(false);
  };

  const handleToggleTheme = () => {
    setThemeMode((prev) => (prev === 'dark' ? 'light' : 'dark'));
  };

  return {
    themeMode,
    handleToggleTheme,
    shouldAutoEval,
    setShouldAutoEval,
    activeTab,
    setActiveTab,
    expandedKeys,
    toggleExpand,

    projects,
    projectsLoading,
    projectsError,
    projectId,
    setProjectId,
    fetchProjects,

    logs,
    logsLoading,
    logsError,
    userLogs,
    systemLogs,
    handleLog,
    clearLogs,

    health,
    healthLoading,
    healthError,

    evalGenerated,
    setEvalGenerated,
    evalModified,
    setEvalModified,
    evalResult,
    setEvalResult,
    recallRetrieved,
    setRecallRetrieved,
    recallRelevant,
    setRecallRelevant,
    recallResult,
    setRecallResult,
    uiEvalScript,
    setUiEvalScript,
    uiEvalExec,
    setUiEvalExec,
    uiEvalOutput,
    setUiEvalOutput,
    apiEvalScript,
    setApiEvalScript,
    apiEvalExec,
    setApiEvalExec,
    apiEvalOutput,
    setApiEvalOutput,

    showConfig,
    configError,
    handleOpenConfig,
    handleCloseConfig,
    openConfigWithError,

    handleTestGenerated,
    handleGenerationComplete,
  };
}

export type DashboardController = ReturnType<typeof useDashboardController>;
