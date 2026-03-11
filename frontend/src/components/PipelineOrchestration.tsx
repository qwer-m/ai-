import { useEffect, useMemo, useState } from 'react';
import { Alert, Badge, Button, Card, Form, Spinner, Table } from 'react-bootstrap';
import { FaPlay, FaRedo } from 'react-icons/fa';
import { api } from '../utils/api';

type StageKey = 'test_generation' | 'ui_automation' | 'api_automation' | 'evaluation';
type StageStatus = 'idle' | 'pending' | 'running' | 'success' | 'failed' | 'skipped';

type StageState = {
  status: StageStatus;
  message: string;
  started_at?: string | null;
  ended_at?: string | null;
};

type PipelineRun = {
  id: number;
  project_id: number;
  status: 'pending' | 'running' | 'success' | 'failed';
  current_stage?: string | null;
  stage_states: Record<StageKey, StageState>;
  artifacts: Record<string, any>;
  error_message?: string;
  retry_of_run_id?: number | null;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
};

type PipelineAgentConfig = {
  enabled: boolean;
  planner_llm: boolean;
  reviewer_llm: boolean;
  executor_parallel: boolean;
  executor_workers: number;
  auto_retry_enabled: boolean;
  max_auto_retries: number;
  retry_policy: 'conservative' | 'balanced' | 'aggressive';
  max_context_chars: number;
};

type ProjectAgentDefaultsResponse = {
  project_id: number;
  agent: PipelineAgentConfig;
  source: 'default' | 'saved';
  updated_at?: string;
};

type WorkflowTraceItem = {
  id: number;
  created_at?: string;
  kind: string;
  stage: string;
  action: string;
  details: Record<string, any>;
};

type Props = {
  projectId: number | null;
  onLog: (msg: string) => void;
};

const stageOrder: StageKey[] = ['test_generation', 'ui_automation', 'api_automation', 'evaluation'];
const stageLabel: Record<StageKey, string> = {
  test_generation: '测试用例生成',
  ui_automation: 'UI 自动化',
  api_automation: '接口自动化',
  evaluation: '质量评估',
};
const testTypeOptions = ['Functional', 'Performance', 'Security', 'Boundary'];
const testTypeLabel: Record<string, string> = {
  Functional: '功能',
  Performance: '性能',
  Security: '安全',
  Boundary: '边界',
};
const stageStatusLabel: Record<StageStatus, string> = {
  idle: '未开始',
  pending: '排队中',
  running: '运行中',
  success: '成功',
  failed: '失败',
  skipped: '已跳过',
};
const runStatusLabel: Record<'pending' | 'running' | 'success' | 'failed' | 'idle', string> = {
  pending: '排队中',
  running: '运行中',
  success: '成功',
  failed: '失败',
  idle: '未开始',
};
const traceKindLabel: Record<string, string> = {
  planner: '规划',
  reviewer: '评审',
  executor: '执行',
  orchestrator: '编排',
  system: '系统',
};
const defaultAgentConfig: PipelineAgentConfig = {
  enabled: true,
  planner_llm: true,
  reviewer_llm: true,
  executor_parallel: true,
  executor_workers: 3,
  auto_retry_enabled: true,
  max_auto_retries: 1,
  retry_policy: 'balanced',
  max_context_chars: 3500,
};

function buildInitialStages(): Record<StageKey, StageState> {
  return {
    test_generation: { status: 'idle', message: '' },
    ui_automation: { status: 'idle', message: '' },
    api_automation: { status: 'idle', message: '' },
    evaluation: { status: 'idle', message: '' },
  };
}

function statusVariant(status: StageStatus): string {
  if (status === 'success') return 'success';
  if (status === 'failed') return 'danger';
  if (status === 'running') return 'primary';
  if (status === 'pending') return 'warning';
  if (status === 'skipped') return 'secondary';
  return 'light';
}

function toText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === null || value === undefined) return '';
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function PipelineOrchestration({ projectId, onLog }: Props) {
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
  const [runStatus, setRunStatus] = useState<'pending' | 'running' | 'success' | 'failed' | 'idle'>('idle');
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
  const canRun = !!projectId && !isRunning && requirement.trim().length > 0;

  const stageRows = useMemo(
    () =>
      stageOrder.map((key) => ({
        key,
        ...stages[key],
      })),
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
      const data = await api.get<ProjectAgentDefaultsResponse>(`/api/projects/${targetProjectId}/pipeline-agent-defaults`);
      const agentCfg = data?.agent || defaultAgentConfig;
      applyAgentConfig(agentCfg);
      setAgentDefaultsState('ready');
      onLog(data?.source === 'saved'
        ? `已加载项目 #${targetProjectId} 的智能体默认配置。`
        : `项目 #${targetProjectId} 使用系统默认智能体配置。`);
    } catch (e) {
      setAgentDefaultsState('idle');
      onLog(`警告：加载项目智能体默认配置失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const saveProjectAgentDefaults = async (targetProjectId: number, agentCfg: PipelineAgentConfig) => {
    setAgentDefaultsState('saving');
    try {
      await api.put<ProjectAgentDefaultsResponse>(`/api/projects/${targetProjectId}/pipeline-agent-defaults`, { agent: agentCfg });
      setAgentDefaultsState('ready');
    } catch (e) {
      setAgentDefaultsState('idle');
      onLog(`警告：保存项目智能体默认配置失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  useEffect(() => {
    setRetryFromStage(firstFailedOrPendingStage);
  }, [firstFailedOrPendingStage]);

  const hydrateArtifacts = (artifacts: Record<string, any>) => {
    const tg = artifacts?.test_generation || {};
    const ui = artifacts?.ui_automation || {};
    const apiAuto = artifacts?.api_automation || {};
    const evalResult = artifacts?.evaluation || {};
    const agents = artifacts?.agents || {};

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
      const data = await api.get<{ items: PipelineRun[] }>(`/api/pipeline/runs?project_id=${projectId}&limit=30`);
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
      const data = await api.get<{ items: WorkflowTraceItem[] }>(`/api/pipeline/runs/${runId}/traces?limit=300`);
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
    refreshHistory();
    loadProjectAgentDefaults(projectId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  useEffect(() => {
    if (!activeRunId || !isRunning) return;
    const timer = window.setInterval(async () => {
      try {
        const data = await api.get<{ run: PipelineRun }>(`/api/pipeline/runs/${activeRunId}`);
        hydrateRun(data.run);
        refreshTraces(data.run.id);
        if (data.run.status !== 'running' && data.run.status !== 'pending') {
          onLog(`流水线运行 #${data.run.id} 已结束，状态：${runStatusLabel[data.run.status] || data.run.status}。`);
          refreshHistory();
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
      const data = await api.post<{ run: PipelineRun }>('/api/pipeline/runs', {
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
      refreshHistory();
      refreshTraces(data.run.id);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const resumeRun = async () => {
    if (!activeRunId) return;
    setErrorMsg(null);
    try {
      const data = await api.post<{ run: PipelineRun; message: string }>(`/api/pipeline/runs/${activeRunId}/resume`, {});
      hydrateRun(data.run);
      onLog(data.message || `已恢复运行 #${activeRunId}。`);
      refreshHistory();
      refreshTraces(data.run.id);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const retryRun = async () => {
    if (!activeRunId) return;
    setErrorMsg(null);
    try {
      const data = await api.post<{ run: PipelineRun; message: string }>(`/api/pipeline/runs/${activeRunId}/retry`, {
        from_stage: retryFromStage,
      });
      hydrateRun(data.run);
      onLog(data.message || `已从运行 #${activeRunId} 创建重试任务。`);
      refreshHistory();
      refreshTraces(data.run.id);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    }
  };

  const openHistoryRun = async (runId: number) => {
    try {
      const data = await api.get<{ run: PipelineRun }>(`/api/pipeline/runs/${runId}`);
      hydrateRun(data.run);
      onLog(`已加载流水线运行 #${runId}。`);
      refreshTraces(data.run.id);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="d-flex flex-column gap-3 p-3 h-100 overflow-auto">
      {errorMsg && <Alert variant="danger" className="mb-0">{errorMsg}</Alert>}
      {!projectId && <Alert variant="warning" className="mb-0">请先选择项目。</Alert>}

      <Card className="border-0 shadow-sm">
        <Card.Body className="d-flex justify-content-between align-items-center">
          <div>
            <h5 className="mb-1">全局编排</h5>
            <div className="text-muted small">
              支持运行持久化、历史记录、恢复执行和分阶段重试。
            </div>
          </div>
          <div className="d-flex gap-2">
            <Button variant="outline-secondary" onClick={resetView} disabled={isRunning}>
              <FaRedo className="me-1" />
              重置视图
            </Button>
            <Button variant="primary" onClick={runPipeline} disabled={!canRun}>
              {isRunning ? <Spinner size="sm" className="me-2" /> : <FaPlay className="me-2" />}
              运行流水线
            </Button>
          </div>
        </Card.Body>
      </Card>

      <div className="row g-3">
        <div className="col-lg-7">
          <Card className="border-0 shadow-sm h-100">
            <Card.Body className="d-flex flex-column gap-3">
              <h6 className="mb-0">流水线输入</h6>

              <div>
                <Form.Label>需求描述</Form.Label>
                <Form.Control
                  as="textarea"
                  rows={4}
                  value={requirement}
                  onChange={(e) => setRequirement(e.target.value)}
                  placeholder="请描述本次运行的端到端需求。"
                />
              </div>

              <div className="row g-3">
                <div className="col-md-4">
                  <Form.Label>期望用例数</Form.Label>
                  <Form.Control
                    type="number"
                    min={1}
                    value={expectedCount}
                    onChange={(e) => setExpectedCount(Math.max(1, Number(e.target.value) || 1))}
                  />
                </div>
                <div className="col-md-8 d-flex align-items-end">
                  <Form.Check
                    type="switch"
                    id="pipeline-compress"
                    label="在用例生成阶段启用上下文压缩"
                    checked={compress}
                    onChange={(e) => setCompress(e.target.checked)}
                  />
                </div>
              </div>

              <hr className="my-1" />
              <h6 className="mb-0">UI 自动化</h6>
              <div className="row g-3">
                <div className="col-md-6">
                  <Form.Label>目标地址</Form.Label>
                  <Form.Control value={uiTarget} onChange={(e) => setUiTarget(e.target.value)} />
                </div>
                <div className="col-md-3">
                  <Form.Label>类型</Form.Label>
                  <Form.Select
                    value={uiAutomationType}
                    onChange={(e) => setUiAutomationType(e.target.value as 'web' | 'app')}
                  >
                    <option value="web">网页（Web）</option>
                    <option value="app">应用（App）</option>
                  </Form.Select>
                </div>
                <div className="col-md-3">
                  <Form.Label>任务（可选）</Form.Label>
                  <Form.Control value={uiTask} onChange={(e) => setUiTask(e.target.value)} placeholder="默认使用全局需求" />
                </div>
              </div>

              <hr className="my-1" />
              <h6 className="mb-0">接口自动化</h6>
              <div className="row g-3">
                <div className="col-md-4">
                  <Form.Label>基础 URL</Form.Label>
                  <Form.Control value={apiBaseUrl} onChange={(e) => setApiBaseUrl(e.target.value)} />
                </div>
                <div className="col-md-4">
                  <Form.Label>接口路径</Form.Label>
                  <Form.Control value={apiPath} onChange={(e) => setApiPath(e.target.value)} />
                </div>
                <div className="col-md-4">
                  <Form.Label>模式</Form.Label>
                  <Form.Select value={apiMode} onChange={(e) => setApiMode(e.target.value as 'structured' | 'natural')}>
                    <option value="structured">结构化</option>
                    <option value="natural">自然语言</option>
                  </Form.Select>
                </div>
              </div>
              <Form.Group>
                <Form.Label>接口需求（可选）</Form.Label>
                <Form.Control
                  as="textarea"
                  rows={2}
                  value={apiRequirement}
                  onChange={(e) => setApiRequirement(e.target.value)}
                  placeholder="默认使用全局需求"
                />
              </Form.Group>
              <div className="d-flex gap-3 flex-wrap">
                {testTypeOptions.map((item) => (
                  <Form.Check
                    key={item}
                    inline
                    id={`pipeline-api-type-${item}`}
                    type="checkbox"
                    label={testTypeLabel[item] || item}
                    checked={apiTestTypes.includes(item)}
                    onChange={() => toggleApiType(item)}
                  />
                ))}
              </div>

              <hr className="my-1" />
              <h6 className="mb-0">评估配置</h6>
              <div className="d-flex gap-4 flex-wrap">
                <Form.Check
                  type="switch"
                  id="pipeline-eval-testcase"
                  label="测试用例评估"
                  checked={runTestcaseEval}
                  onChange={(e) => setRunTestcaseEval(e.target.checked)}
                />
                <Form.Check
                  type="switch"
                  id="pipeline-eval-ui"
                  label="UI 自动化评估"
                  checked={runUiEval}
                  onChange={(e) => setRunUiEval(e.target.checked)}
                />
                <Form.Check
                  type="switch"
                  id="pipeline-eval-api"
                  label="接口评估"
                  checked={runApiEval}
                  onChange={(e) => setRunApiEval(e.target.checked)}
                />
              </div>
              {runTestcaseEval && (
                <Form.Group>
                  <Form.Label>基线测试用例（用于对比）</Form.Label>
                  <Form.Control
                    as="textarea"
                    rows={3}
                    value={baselineTestCases}
                    onChange={(e) => setBaselineTestCases(e.target.value)}
                    placeholder="请粘贴人工修订后的基线测试用例。"
                  />
                </Form.Group>
              )}

              <hr className="my-1" />
              <div className="d-flex justify-content-between align-items-center">
                <h6 className="mb-0">智能体循环</h6>
                <span className="small text-muted">
                  {agentDefaultsState === 'loading' && '正在加载项目默认配置...'}
                  {agentDefaultsState === 'saving' && '正在保存项目默认配置...'}
                  {agentDefaultsState === 'ready' && '项目默认配置已同步'}
                </span>
              </div>
              <div className="d-flex gap-4 flex-wrap">
                <Form.Check
                  type="switch"
                  id="pipeline-agent-enabled"
                  label="启用智能体循环"
                  checked={agentEnabled}
                  onChange={(e) => setAgentEnabled(e.target.checked)}
                />
                <Form.Check
                  type="switch"
                  id="pipeline-agent-planner"
                  label="规划 LLM"
                  checked={agentPlannerLLM}
                  disabled={!agentEnabled}
                  onChange={(e) => setAgentPlannerLLM(e.target.checked)}
                />
                <Form.Check
                  type="switch"
                  id="pipeline-agent-reviewer"
                  label="评审 LLM"
                  checked={agentReviewerLLM}
                  disabled={!agentEnabled}
                  onChange={(e) => setAgentReviewerLLM(e.target.checked)}
                />
                <Form.Check
                  type="switch"
                  id="pipeline-agent-executor-parallel"
                  label="执行器并行"
                  checked={agentExecutorParallel}
                  disabled={!agentEnabled}
                  onChange={(e) => setAgentExecutorParallel(e.target.checked)}
                />
                <Form.Check
                  type="switch"
                  id="pipeline-agent-auto-retry"
                  label="评审自动重试"
                  checked={agentAutoRetryEnabled}
                  disabled={!agentEnabled}
                  onChange={(e) => setAgentAutoRetryEnabled(e.target.checked)}
                />
              </div>
              <div className="row g-3">
                <div className="col-md-4">
                  <Form.Label>智能体上下文字符数</Form.Label>
                  <Form.Control
                    type="number"
                    min={800}
                    max={12000}
                    value={agentMaxContextChars}
                    disabled={!agentEnabled}
                    onChange={(e) => setAgentMaxContextChars(Math.max(800, Math.min(12000, Number(e.target.value) || 800)))}
                  />
                </div>
                <div className="col-md-4">
                  <Form.Label>执行器工作线程数</Form.Label>
                  <Form.Control
                    type="number"
                    min={1}
                    max={8}
                    value={agentExecutorWorkers}
                    disabled={!agentEnabled || !agentExecutorParallel}
                    onChange={(e) => setAgentExecutorWorkers(Math.max(1, Math.min(8, Number(e.target.value) || 1)))}
                  />
                </div>
                <div className="col-md-4">
                  <Form.Label>最大自动重试次数</Form.Label>
                  <Form.Control
                    type="number"
                    min={0}
                    max={3}
                    value={agentMaxAutoRetries}
                    disabled={!agentEnabled || !agentAutoRetryEnabled}
                    onChange={(e) => setAgentMaxAutoRetries(Math.max(0, Math.min(3, Number(e.target.value) || 0)))}
                  />
                </div>
                <div className="col-md-4">
                  <Form.Label>重试策略</Form.Label>
                  <Form.Select
                    value={agentRetryPolicy}
                    disabled={!agentEnabled || !agentAutoRetryEnabled}
                    onChange={(e) => setAgentRetryPolicy(e.target.value as 'conservative' | 'balanced' | 'aggressive')}
                  >
                    <option value="conservative">保守</option>
                    <option value="balanced">均衡</option>
                    <option value="aggressive">激进</option>
                  </Form.Select>
                </div>
              </div>
            </Card.Body>
          </Card>
        </div>

        <div className="col-lg-5">
          <Card className="border-0 shadow-sm mb-3">
            <Card.Body>
              <div className="d-flex justify-content-between align-items-center mb-3">
                <h6 className="mb-0">阶段状态</h6>
                <div className="d-flex gap-2 align-items-center">
                  {activeRunId && <Badge bg="dark">运行 #{activeRunId}</Badge>}
                  <Badge bg={runStatus === 'idle' ? 'secondary' : runStatus === 'success' ? 'success' : runStatus === 'failed' ? 'danger' : 'primary'}>
                    {runStatusLabel[runStatus]}
                  </Badge>
                </div>
              </div>
              <Table size="sm" className="mb-2 align-middle">
                <thead>
                  <tr>
                    <th>阶段</th>
                    <th>状态</th>
                    <th>消息</th>
                  </tr>
                </thead>
                <tbody>
                  {stageRows.map((row) => (
                    <tr key={row.key}>
                      <td>{stageLabel[row.key]}</td>
                      <td>
                        <Badge bg={statusVariant(row.status)}>{stageStatusLabel[row.status]}</Badge>
                      </td>
                      <td className="small text-muted">{row.message || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </Table>

              {activeRunId && runStatus !== 'running' && runStatus !== 'pending' && (
                <div className="d-flex gap-2 mt-2">
                  <Button variant="outline-primary" size="sm" onClick={resumeRun}>
                    恢复运行
                  </Button>
                  <Form.Select
                    size="sm"
                    value={retryFromStage}
                    onChange={(e) => setRetryFromStage(e.target.value as StageKey)}
                    style={{ maxWidth: 170 }}
                  >
                    {stageOrder.map((stage) => (
                      <option key={stage} value={stage}>{stageLabel[stage]}</option>
                    ))}
                  </Form.Select>
                  <Button variant="outline-secondary" size="sm" onClick={retryRun}>
                    从该阶段重试
                  </Button>
                </div>
              )}
            </Card.Body>
          </Card>

          <Card className="border-0 shadow-sm mb-3">
            <Card.Body>
              <div className="d-flex justify-content-between align-items-center mb-2">
                <h6 className="mb-0">运行历史</h6>
                <Button variant="outline-secondary" size="sm" onClick={refreshHistory} disabled={historyLoading || !projectId}>
                  {historyLoading ? <Spinner size="sm" /> : '刷新'}
                </Button>
              </div>
              <div style={{ maxHeight: 220, overflow: 'auto' }}>
                <Table size="sm" hover className="mb-0">
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>状态</th>
                      <th>创建时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((item) => (
                      <tr key={item.id} onClick={() => openHistoryRun(item.id)} style={{ cursor: 'pointer' }}>
                        <td>#{item.id}</td>
                        <td>
                          <Badge bg={item.status === 'success' ? 'success' : item.status === 'failed' ? 'danger' : 'primary'}>
                            {runStatusLabel[item.status]}
                          </Badge>
                        </td>
                        <td className="small text-muted">{item.created_at ? new Date(item.created_at).toLocaleString() : '-'}</td>
                      </tr>
                    ))}
                    {history.length === 0 && (
                      <tr>
                        <td colSpan={3} className="small text-muted text-center py-3">暂无运行记录。</td>
                      </tr>
                    )}
                  </tbody>
                </Table>
              </div>
            </Card.Body>
          </Card>

          <Card className="border-0 shadow-sm mb-3">
            <Card.Body>
              <div className="d-flex justify-content-between align-items-center mb-2">
                <h6 className="mb-0">工作流追踪</h6>
                <Button
                  variant="outline-secondary"
                  size="sm"
                  onClick={() => refreshTraces()}
                  disabled={traceLoading || !activeRunId}
                >
                  {traceLoading ? <Spinner size="sm" /> : '刷新'}
                </Button>
              </div>
              <div style={{ maxHeight: 240, overflow: 'auto' }}>
                <Table size="sm" className="mb-0 align-middle">
                  <thead>
                    <tr>
                      <th>时间</th>
                      <th>阶段</th>
                      <th>动作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {traces.map((item) => (
                      <tr
                        key={item.id}
                        style={{ cursor: 'pointer' }}
                        onClick={() => setSelectedTraceId(item.id)}
                        className={item.id === selectedTraceId ? 'table-active' : ''}
                      >
                        <td className="small text-muted">
                          {item.created_at ? new Date(item.created_at).toLocaleTimeString() : '-'}
                        </td>
                        <td className="small">
                          {(traceKindLabel[item.kind] || item.kind)}/{(stageLabel[item.stage as StageKey] || item.stage)}
                        </td>
                        <td className="small text-muted">{item.action || '-'}</td>
                      </tr>
                    ))}
                    {traces.length === 0 && (
                      <tr>
                        <td colSpan={3} className="small text-muted text-center py-3">
                          {activeRunId ? '当前运行暂无追踪事件。' : '请选择一条运行记录查看追踪信息。'}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </Table>
              </div>
              <Form.Group className="mt-2">
                <Form.Label className="small">追踪详情</Form.Label>
                <Form.Control
                  as="textarea"
                  rows={5}
                  readOnly
                  value={selectedTrace ? toText(selectedTrace.details) : ''}
                  placeholder="点击上方追踪行可查看详情。"
                />
              </Form.Group>
            </Card.Body>
          </Card>

          <Card className="border-0 shadow-sm">
            <Card.Body className="d-flex flex-column gap-2">
              <h6 className="mb-0">流水线输出</h6>
              <Form.Group>
                <Form.Label className="small">生成的测试用例</Form.Label>
                <Form.Control as="textarea" rows={4} value={generatedCases} readOnly />
              </Form.Group>
              <Form.Group>
                <Form.Label className="small">UI 脚本</Form.Label>
                <Form.Control as="textarea" rows={3} value={uiScript} readOnly />
              </Form.Group>
              <Form.Group>
                <Form.Label className="small">UI 执行结果</Form.Label>
                <Form.Control as="textarea" rows={3} value={uiExecutionResult} readOnly />
              </Form.Group>
              <Form.Group>
                <Form.Label className="small">接口脚本</Form.Label>
                <Form.Control as="textarea" rows={3} value={apiScript} readOnly />
              </Form.Group>
              <Form.Group>
                <Form.Label className="small">接口执行结果</Form.Label>
                <Form.Control as="textarea" rows={3} value={apiExecutionResult} readOnly />
              </Form.Group>
              <Form.Group>
                <Form.Label className="small">评估输出</Form.Label>
                <Form.Control as="textarea" rows={6} value={evaluationOutput} readOnly />
              </Form.Group>
              <Form.Group>
                <Form.Label className="small">智能体洞察</Form.Label>
                <Form.Control as="textarea" rows={6} value={agentInsights} readOnly />
              </Form.Group>
            </Card.Body>
          </Card>
        </div>
      </div>
    </div>
  );
}
