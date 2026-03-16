import { useEffect, useMemo, useState } from 'react';
import {
  buildInitialStages,
  defaultAgentConfig,
  runStatusLabel,
  stageOrder,
  toText,
  type PipelineAgentConfig,
  type PipelineRun,
  type PipelineRunStatus,
  type StageKey,
  type StageState,
  type WorkflowTraceItem,
} from './model';
import {
  createPipelineRun,
  fetchPipelineRun,
  fetchPipelineTraces,
  fetchProjectAgentDefaults,
  listPipelineRuns,
  resumePipelineRun,
  retryPipelineRun,
  updateProjectAgentDefaults,
} from './pipelineApi';

type UsePipelineOrchestrationControllerParams = {
  projectId: number | null;
  onLog: (msg: string) => void;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

export function usePipelineOrchestrationController({
  projectId,
  onLog,
}: UsePipelineOrchestrationControllerParams) {
  const [requirement, setRequirement] = useState('');
  const [expectedCount, setExpectedCount] = useState(20);
  const [compress, setCompress] = useState(false);

  const [uiTask, setUiTask] = useState('');
  const [uiTarget, setUiTarget] = useState('http://localhost:5173');
  const [uiAutomationType, setUiAutomationType] = useState<'web' | 'app'>('web');

  const [apiRequirement, setApiRequirement] = useState('');
  const [apiBaseUrl, setApiBaseUrl] = useState('http://127.0.0.1:8000');
  const [apiPath, setApiPath] = useState('/api/health');
  const [apiMode, setApiMode] = useState<'structured' | 'natural'>('structured');
  const [apiTestTypes, setApiTestTypes] = useState<string[]>(['Functional']);

  const [runTestcaseEval, setRunTestcaseEval] = useState(false);
  const [runUiEval, setRunUiEval] = useState(true);
  const [runApiEval, setRunApiEval] = useState(true);
  const [baselineTestCases, setBaselineTestCases] = useState('');

  const [agentEnabled, setAgentEnabled] = useState(defaultAgentConfig.enabled);
  const [agentPlannerLLM, setAgentPlannerLLM] = useState(defaultAgentConfig.planner_llm);
  const [agentReviewerLLM, setAgentReviewerLLM] = useState(defaultAgentConfig.reviewer_llm);
  const [agentExecutorParallel, setAgentExecutorParallel] = useState(defaultAgentConfig.executor_parallel);
  const [agentExecutorWorkers, setAgentExecutorWorkers] = useState(defaultAgentConfig.executor_workers);
  const [agentAutoRetryEnabled, setAgentAutoRetryEnabled] = useState(defaultAgentConfig.auto_retry_enabled);
  const [agentMaxAutoRetries, setAgentMaxAutoRetries] = useState(defaultAgentConfig.max_auto_retries);
  const [agentRetryPolicy, setAgentRetryPolicy] = useState<'conservative' | 'balanced' | 'aggressive'>(defaultAgentConfig.retry_policy);
  const [agentMaxContextChars, setAgentMaxContextChars] = useState(defaultAgentConfig.max_context_chars);
  const [agentDefaultsState, setAgentDefaultsState] = useState<'idle' | 'loading' | 'ready' | 'saving'>('idle');

  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [stages, setStages] = useState<Record<StageKey, StageState>>(buildInitialStages());
  const [runStatus, setRunStatus] = useState<PipelineRunStatus>('idle');
  const [activeRunId, setActiveRunId] = useState<number | null>(null);
  const [history, setHistory] = useState<PipelineRun[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [retryFromStage, setRetryFromStage] = useState<StageKey>('test_generation');
  const [traceLoading, setTraceLoading] = useState(false);
  const [traces, setTraces] = useState<WorkflowTraceItem[]>([]);
  const [selectedTraceId, setSelectedTraceId] = useState<number | null>(null);

  const [generatedCases, setGeneratedCases] = useState('');
  const [uiScript, setUiScript] = useState('');
  const [uiExecutionResult, setUiExecutionResult] = useState('');
  const [apiScript, setApiScript] = useState('');
  const [apiExecutionResult, setApiExecutionResult] = useState('');
  const [evaluationOutput, setEvaluationOutput] = useState('');
  const [agentInsights, setAgentInsights] = useState('');

  const isRunning = runStatus === 'running' || runStatus === 'pending';
  const canRun = Boolean(projectId) && !isRunning && requirement.trim().length > 0;

  const stageRows = useMemo(
    () => stageOrder.map((key) => ({ key, ...stages[key] })),
    [stages],
  );

  const selectedTrace = useMemo(
    () => traces.find((item) => item.id === selectedTraceId) || null,
    [traces, selectedTraceId],
  );

  const firstFailedOrPendingStage = useMemo<StageKey>(() => {
    for (const stage of stageOrder) {
      const status = stages[stage]?.status;
      if (status === 'failed' || status === 'pending' || status === 'idle') {
        return stage;
      }
    }
    return 'test_generation';
  }, [stages]);

  const buildAgentConfig = (): PipelineAgentConfig => ({
    enabled: agentEnabled,
    planner_llm: agentPlannerLLM,
    reviewer_llm: agentReviewerLLM,
    executor_parallel: agentExecutorParallel,
    executor_workers: Math.max(1, Math.min(8, Number(agentExecutorWorkers) || 1)),
    auto_retry_enabled: agentAutoRetryEnabled,
    max_auto_retries: Math.max(0, Math.min(3, Number(agentMaxAutoRetries) || 0)),
    retry_policy: agentRetryPolicy,
    max_context_chars: Math.max(800, Math.min(12000, Number(agentMaxContextChars) || 3500)),
  });

  const applyAgentConfig = (cfg: PipelineAgentConfig) => {
    setAgentEnabled(Boolean(cfg.enabled));
    setAgentPlannerLLM(Boolean(cfg.planner_llm));
    setAgentReviewerLLM(Boolean(cfg.reviewer_llm));
    setAgentExecutorParallel(Boolean(cfg.executor_parallel));
    setAgentExecutorWorkers(Math.max(1, Math.min(8, Number(cfg.executor_workers) || 1)));
    setAgentAutoRetryEnabled(Boolean(cfg.auto_retry_enabled));
    setAgentMaxAutoRetries(Math.max(0, Math.min(3, Number(cfg.max_auto_retries) || 0)));
    setAgentRetryPolicy(cfg.retry_policy);
    setAgentMaxContextChars(Math.max(800, Math.min(12000, Number(cfg.max_context_chars) || 3500)));
  };

  const loadProjectAgentDefaults = async (targetProjectId: number) => {
    setAgentDefaultsState('loading');
    try {
      const data = await fetchProjectAgentDefaults(targetProjectId);
      const agentCfg = data?.agent || defaultAgentConfig;
      applyAgentConfig(agentCfg);
      setAgentDefaultsState('ready');
      onLog(
        data?.source === 'saved'
          ? `已加载项目 #${targetProjectId} 的智能体默认配置。`
          : `项目 #${targetProjectId} 使用系统默认智能体配置。`,
      );
    } catch (e) {
      setAgentDefaultsState('idle');
      onLog(`警告：加载项目智能体默认配置失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const saveProjectAgentDefaults = async (targetProjectId: number, agentCfg: PipelineAgentConfig) => {
    setAgentDefaultsState('saving');
    try {
      await updateProjectAgentDefaults(targetProjectId, agentCfg);
      setAgentDefaultsState('ready');
    } catch (e) {
      setAgentDefaultsState('idle');
      onLog(`警告：保存项目智能体默认配置失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  useEffect(() => {
    setRetryFromStage(firstFailedOrPendingStage);
  }, [firstFailedOrPendingStage]);

  /**
   * 将后端各阶段产物同步到页面可读文本，保证“历史运行打开”和“轮询刷新”使用同一套映射逻辑。
   */
  const hydrateArtifacts = (artifacts: Record<string, unknown>) => {
    const tg = asRecord(artifacts.test_generation);
    const ui = asRecord(artifacts.ui_automation);
    const apiAuto = asRecord(artifacts.api_automation);
    const evalResult = asRecord(artifacts.evaluation);
    const agents = asRecord(artifacts.agents);

    setGeneratedCases(toText(tg.generated_cases));
    setUiScript(toText(ui.script));
    setUiExecutionResult(toText(ui.execution_result));
    setApiScript(toText(apiAuto.script));
    setApiExecutionResult(toText(apiAuto.execution_result));
    setEvaluationOutput(toText(evalResult.output));
    setAgentInsights(toText(agents));
  };

  const hydrateRun = (run: PipelineRun) => {
    setRunStatus(run.status);
    setActiveRunId(run.id);
    setStages(run.stage_states || buildInitialStages());
    hydrateArtifacts(run.artifacts || {});
    setErrorMsg(run.error_message || null);
  };

  const refreshHistory = async () => {
    if (!projectId) return;
    setHistoryLoading(true);
    try {
      const data = await listPipelineRuns(projectId);
      setHistory(Array.isArray(data.items) ? data.items : []);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setHistoryLoading(false);
    }
  };

  const refreshTraces = async (runId: number | null = activeRunId) => {
    if (!runId) {
      setTraces([]);
      return;
    }
    setTraceLoading(true);
    try {
      const data = await fetchPipelineTraces(runId);
      setTraces(Array.isArray(data.items) ? data.items : []);
      setSelectedTraceId(null);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setTraceLoading(false);
    }
  };

  useEffect(() => {
    if (!projectId) {
      setTraces([]);
      setAgentDefaultsState('idle');
      return;
    }
    void refreshHistory();
    void loadProjectAgentDefaults(projectId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  /**
   * 运行中采用短轮询刷新状态与追踪，保持页面展示与服务端持久化运行进度一致。
   */
  useEffect(() => {
    if (!activeRunId || !isRunning) return;
    const timer = window.setInterval(async () => {
      try {
        const data = await fetchPipelineRun(activeRunId);
        hydrateRun(data.run);
        void refreshTraces(data.run.id);
        if (data.run.status !== 'running' && data.run.status !== 'pending') {
          onLog(`流水线运行 #${data.run.id} 已结束，状态：${runStatusLabel[data.run.status] || data.run.status}。`);
          void refreshHistory();
        }
      } catch (e) {
        setErrorMsg(e instanceof Error ? e.message : String(e));
      }
    }, 2500);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRunId, isRunning]);

  const resetView = () => {
    setStages(buildInitialStages());
    setRunStatus('idle');
    setActiveRunId(null);
    setErrorMsg(null);
    setGeneratedCases('');
    setUiScript('');
    setUiExecutionResult('');
    setApiScript('');
    setApiExecutionResult('');
    setEvaluationOutput('');
    setAgentInsights('');
    setTraces([]);
    setSelectedTraceId(null);
  };

  const toggleApiType = (value: string) => {
    setApiTestTypes((prev) => {
      if (prev.includes(value)) return prev.filter((item) => item !== value);
      return [...prev, value];
    });
  };

  /**
   * 全局运行入口：先持久化智能体默认配置，再提交运行请求，避免“界面参数与服务端配置”不一致。
   */
  const runPipeline = async () => {
    if (!projectId) {
      setErrorMsg('请先选择项目。');
      return;
    }
    if (!requirement.trim()) {
      setErrorMsg('请输入需求说明。');
      return;
    }

    setErrorMsg(null);
    const agentPayload = buildAgentConfig();
    await saveProjectAgentDefaults(projectId, agentPayload);
    onLog('已启动全局编排流水线（持久化运行）。');

    try {
      const data = await createPipelineRun({
        project_id: projectId,
        requirement,
        expected_count: expectedCount,
        compress,
        ui: {
          task: uiTask,
          target: uiTarget,
          automation_type: uiAutomationType,
        },
        api: {
          requirement: apiRequirement,
          base_url: apiBaseUrl,
          api_path: apiPath,
          mode: apiMode,
          test_types: apiTestTypes,
        },
        evaluation: {
          run_testcase_eval: runTestcaseEval,
          run_ui_eval: runUiEval,
          run_api_eval: runApiEval,
          baseline_test_cases: baselineTestCases,
        },
        agent: agentPayload,
      });
      hydrateRun(data.run);
      void refreshHistory();
      void refreshTraces(data.run.id);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const resumeRun = async () => {
    if (!activeRunId) return;
    setErrorMsg(null);
    try {
      const data = await resumePipelineRun(activeRunId);
      hydrateRun(data.run);
      onLog(data.message || `已恢复运行 #${activeRunId}。`);
      void refreshHistory();
      void refreshTraces(data.run.id);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const retryRun = async () => {
    if (!activeRunId) return;
    setErrorMsg(null);
    try {
      const data = await retryPipelineRun(activeRunId, retryFromStage);
      hydrateRun(data.run);
      onLog(data.message || `已从运行 #${activeRunId} 创建重试任务。`);
      void refreshHistory();
      void refreshTraces(data.run.id);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const openHistoryRun = async (runId: number) => {
    try {
      const data = await fetchPipelineRun(runId);
      hydrateRun(data.run);
      onLog(`已加载流水线运行 #${runId}。`);
      void refreshTraces(data.run.id);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    }
  };

  return {
    requirement,
    setRequirement,
    expectedCount,
    setExpectedCount,
    compress,
    setCompress,
    uiTask,
    setUiTask,
    uiTarget,
    setUiTarget,
    uiAutomationType,
    setUiAutomationType,
    apiRequirement,
    setApiRequirement,
    apiBaseUrl,
    setApiBaseUrl,
    apiPath,
    setApiPath,
    apiMode,
    setApiMode,
    apiTestTypes,
    runTestcaseEval,
    setRunTestcaseEval,
    runUiEval,
    setRunUiEval,
    runApiEval,
    setRunApiEval,
    baselineTestCases,
    setBaselineTestCases,
    agentEnabled,
    setAgentEnabled,
    agentPlannerLLM,
    setAgentPlannerLLM,
    agentReviewerLLM,
    setAgentReviewerLLM,
    agentExecutorParallel,
    setAgentExecutorParallel,
    agentExecutorWorkers,
    setAgentExecutorWorkers,
    agentAutoRetryEnabled,
    setAgentAutoRetryEnabled,
    agentMaxAutoRetries,
    setAgentMaxAutoRetries,
    agentRetryPolicy,
    setAgentRetryPolicy,
    agentMaxContextChars,
    setAgentMaxContextChars,
    agentDefaultsState,
    errorMsg,
    stages,
    runStatus,
    activeRunId,
    history,
    historyLoading,
    retryFromStage,
    setRetryFromStage,
    traceLoading,
    traces,
    selectedTraceId,
    setSelectedTraceId,
    selectedTrace,
    generatedCases,
    uiScript,
    uiExecutionResult,
    apiScript,
    apiExecutionResult,
    evaluationOutput,
    agentInsights,
    isRunning,
    canRun,
    stageRows,
    toggleApiType,
    runPipeline,
    resetView,
    resumeRun,
    retryRun,
    refreshHistory,
    refreshTraces,
    openHistoryRun,
  };
}

export type PipelineOrchestrationController = ReturnType<typeof usePipelineOrchestrationController>;
