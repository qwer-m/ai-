import { useEffect, useMemo, useRef, useState } from 'react';
import type { Project } from '../pages/ProjectManagement';
import { api } from '../../utils/api';
import { dashboardNavItems, findParentKeyByChild, normalizeDashboardActiveTab } from './model/dashboardNavigation';
import type { HealthResponse, LogEntry } from './model/types';
import type { AutomationEvaluationReport, QualityReport } from '../evaluation/state/types';

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
    // 受限环境下忽略本地存储失败，不影响主流程。
  }
};

const shouldOpenConfigByError = (message: string) => {
  return ['401', 'Invalid API-key', 'QUOTA', 'API Key not set'].some((flag) => message.includes(flag));
};

const isTransientFetchError = (error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  return [
    'Failed to fetch',
    'NetworkError',
    'Load failed',
    'ERR_NETWORK_CHANGED',
    'ERR_INTERNET_DISCONNECTED',
  ].some((flag) => message.includes(flag));
};

const LOG_POLL_VISIBLE_MS = 5000;
const LOG_POLL_HIDDEN_MS = 15000;
const LOG_POLL_BACKOFF_STEP_MS = 4000;
const LOG_POLL_BACKOFF_MAX_MS = 30000;

export function useDashboardController() {
  const logPollBackoffRef = useRef(0);
  const [themeMode, setThemeMode] = useState<'light' | 'dark'>(
    () => (safeGetItem('themeMode') === 'dark' ? 'dark' : 'light'),
  );
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
  const [evalResult, setEvalResult] = useState<QualityReport | null>(null);
  const [uiEvalScript, setUiEvalScript] = useState('');
  const [uiEvalExec, setUiEvalExec] = useState('');
  const [uiEvalOutput, setUiEvalOutput] = useState<AutomationEvaluationReport | null>(null);
  const [apiEvalScript, setApiEvalScript] = useState('');
  const [apiEvalExec, setApiEvalExec] = useState('');
  const [apiEvalOutput, setApiEvalOutput] = useState<AutomationEvaluationReport | null>(null);
  const [showConfig, setShowConfig] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);

  const toggleExpand = (key: string) => {
    setExpandedKeys((previous) => (
      previous.includes(key) ? previous.filter((item) => item !== key) : [...previous, key]
    ));
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
        const savedId = saved ? Number(saved) : Number.NaN;
        const savedExists = projectList.some((project) => project.id === savedId);
        setProjectId(
          savedExists && Number.isFinite(savedId)
            ? savedId
            : (projectList.length > 0 ? projectList[0].id : null),
        );
      }
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      setProjectsError(errorMessage);
      if (shouldOpenConfigByError(errorMessage)) openConfigWithError(errorMessage);
    } finally {
      setProjectsLoading(false);
    }
  };

  useEffect(() => {
    void fetchProjects();
    // 项目只在首屏自动加载，后续由业务动作主动刷新。
  }, []);

  useEffect(() => {
    safeSetItem('currentActiveTab', activeTab);
    const parentKey = findParentKeyByChild(dashboardNavItems, activeTab);
    if (!parentKey) return;
    setExpandedKeys((previous) => (
      previous.includes(parentKey) ? previous : [...previous, parentKey]
    ));
  }, [activeTab]);

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
    const timer = window.setInterval(() => void fetchHealth(), 15000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (projectId) safeSetItem('currentProjectId', String(projectId));
  }, [projectId]);

  useEffect(() => {
    // 健康检查异常时暂停日志轮询，避免网络抖动期间重复报错。
    if (!projectId || healthError) return;
    let cancelled = false;
    let timer: number | null = null;

    const pollDelay = () => (
      (document.hidden ? LOG_POLL_HIDDEN_MS : LOG_POLL_VISIBLE_MS) + logPollBackoffRef.current
    );
    const scheduleNext = () => {
      if (cancelled) return;
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(() => void loadLogs(true), pollDelay());
    };
    const loadLogs = async (isPolling = false) => {
      let pollSuccess = false;
      if (!isPolling) {
        setLogsLoading(true);
        setLogsError(null);
      }
      try {
        const data = await api.get<LogEntry[]>(`/api/logs/${projectId}`);
        if (cancelled) return;
        setLogs(Array.isArray(data) ? data : []);
        pollSuccess = true;
      } catch (error) {
        if (cancelled) return;
        if (!isPolling) {
          setLogsError(error instanceof Error ? error.message : String(error));
          setLogs([]);
        } else {
          if (!isTransientFetchError(error)) console.error('Polling logs failed', error);
          logPollBackoffRef.current = Math.min(
            logPollBackoffRef.current + LOG_POLL_BACKOFF_STEP_MS,
            LOG_POLL_BACKOFF_MAX_MS,
          );
        }
      } finally {
        if (isPolling && !cancelled) {
          if (pollSuccess) logPollBackoffRef.current = 0;
          scheduleNext();
        }
        if (!cancelled && !isPolling) setLogsLoading(false);
      }
    };

    void loadLogs(false);
    scheduleNext();
    const handleVisibilityChange = () => scheduleNext();
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [projectId, healthError]);

  const { userLogs, systemLogs } = useMemo(() => {
    const userLogList: LogEntry[] = [];
    const systemLogList: LogEntry[] = [];
    const sortedLogs = [...logs].sort((first, second) => {
      const firstTime = new Date(first.created_at).getTime() || 0;
      const secondTime = new Date(second.created_at).getTime() || 0;
      return firstTime - secondTime;
    });
    const seenIds = new Set<number>();
    sortedLogs.forEach((log) => {
      if (seenIds.has(log.id)) return;
      seenIds.add(log.id);
      if (log.log_type === 'user') userLogList.push(log);
      else systemLogList.push(log);
    });
    return { userLogs: userLogList, systemLogs: systemLogList };
  }, [logs]);

  const handleLog = async (message: string, type: 'user' | 'system' = 'user') => {
    if (!projectId) return;
    const temporaryLog: LogEntry = {
      id: Date.now(),
      project_id: projectId,
      log_type: type,
      message,
      created_at: new Date().toISOString(),
    };
    setLogs((previous) => [...previous, temporaryLog]);
    try {
      const data = await api.post<{ status?: string; id?: number }>('/api/logs', {
        project_id: projectId,
        log_type: type,
        message,
      });
      if (data.status === 'success' && data.id) {
        setLogs((previous) => previous.map((log) => (
          log.id === temporaryLog.id ? { ...log, id: data.id as number } : log
        )));
      }
    } catch (error) {
      console.error(error);
      setLogs((previous) => previous.filter((log) => log.id !== temporaryLog.id));
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

  return {
    themeMode,
    handleToggleTheme: () => setThemeMode((previous) => (previous === 'dark' ? 'light' : 'dark')),
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
  };
}
