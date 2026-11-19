import { useEffect, useMemo, useState } from 'react';
import { Badge, Button, Form, Modal, Spinner } from 'react-bootstrap';
import {
  FaBan,
  FaCheck,
  FaClock,
  FaDownload,
  FaFileAlt,
  FaPlay,
  FaRobot,
  FaUndo,
  FaTimes,
  FaUpload,
} from 'react-icons/fa';
import { useAgentWorkspace } from '../agent-platform/useAgentWorkspace';
import { AgentErrorDetails, AgentNodeDetailModal, JsonView } from './AgentNodeDetailModal';
import { AgentConflictModal } from './AgentConflictModal';
import { actualModelDisplay, executionAgent, executionDisplayStages, executionName, networkActivity, nodeProgress, workspaceExecution } from '../agent-platform/runPresentation';
import type { ExecutionAgent } from '../agent-platform/runPresentation';
import type {
  AgentNodeRun,
  AgentRun,
  AgentRunEvent,
  RunStatus,
} from '../agent-platform/types';

type Props = {
  projectId: number | null;
  onLog: (message: string) => void;
};

type BusinessStageStatus = RunStatus | 'waiting';

const statusLabel: Record<RunStatus, string> = {
  pending: '等待中',
  running: '运行中',
  waiting_approval: '等待确认',
  success: '成功',
  failed: '失败',
  cancelled: '已取消',
};

const statusVariant: Record<RunStatus, string> = {
  pending: 'secondary',
  running: 'primary',
  waiting_approval: 'warning',
  success: 'success',
  failed: 'danger',
  cancelled: 'dark',
};

const businessStageLabel: Record<BusinessStageStatus, string> = {
  ...statusLabel,
  waiting: '未开始',
};

const businessStageVariant: Record<BusinessStageStatus, string> = {
  ...statusVariant,
  waiting: 'secondary',
};

const parseStatusLabel = {
  pending: '等待解析',
  parsing: '解析中',
  success: '可使用',
  failed: '解析失败',
} as const;

function eventLabel(event: AgentRunEvent): string {
  const labels: Record<string, string> = {
    run_started: '运行开始',
    run_finished: '运行结束',
    run_completed: '运行完成',
    run_failed: '运行失败',
    run_cancelled: '运行取消',
    run_retry_spawned: '已创建重试运行',
    run_created_from_retry: '已从失败任务继续',
    run_history_pruned: '历史轨迹已清理',
    run_dispatch_failed: '运行派发失败',
    node_started: '节点开始',
    node_finished: '节点结束',
    node_completed: '节点完成',
    node_failed: '节点失败',
    node_retry_scheduled: '节点安排重试',
    node_completed_batches: '节点批次完成',
    tool_started: '工具开始',
    tool_finished: '工具结束',
    tool_failed: '工具失败',
    map_item_started: '批次项开始',
    map_item_completed: '批次项完成',
    map_item_failed: '批次项失败',
    agent_action_started: '自主行动开始',
    agent_action_completed: '自主行动完成',
    agent_action_resumed: '自主行动已恢复',
    agent_action_failed: '自主行动失败',
    agent_specialist_result_saved: '专家结果已保存',
    approval_requested: '请求确认',
    approval_decided: '确认完成',
    conflicts_requested: '请求冲突确认',
    approval_approved: '确认已通过',
    approval_rejected: '确认已拒绝',
    retry_scheduled: '安排重试',
    run_recovery_deadline_expired: '恢复期限已过',
    run_recovered: '运行已恢复',
    run_dispatch_task_id_ignored: '忽略旧派发任务',
    run_dispatch_failure_ignored: '忽略重复派发失败',
    run_attempt_reset: '已重置运行尝试',
    error: '执行异常',
  };
  return labels[event.event_type] || event.event_type;
}

function eventDetail(event: AgentRunEvent, run: AgentRun | null): string {
  const payload = event.payload;
  const agentKey = stringValue(payload.agent_key);
  if (event.event_type === 'agent_specialist_result_saved' && agentKey) {
    return `${executionName(run, 'agents', agentKey)} · 专业任务已验收`;
  }
  const toolKey = stringValue(payload.tool_key);
  if (event.event_type.startsWith('agent_action_') && toolKey) {
    return executionName(run, 'tools', toolKey);
  }
  for (const key of ['message', 'reason', 'node_key', 'tool_key']) {
    const value = payload[key];
    if (typeof value === 'string' && value.trim()) return value;
  }
  return `序号 ${event.sequence}`;
}

function localizedGeneratedCases(cases: Record<string, unknown>[]): Record<string, unknown> {
  return {
    测试用例: cases.map((testCase) => {
      const actions: unknown[] = [];
      const expectedResults: unknown[] = [];
      const steps = Array.isArray(testCase.steps) ? testCase.steps : [];

      for (const step of steps) {
        if (step && typeof step === 'object' && !Array.isArray(step)) {
          const stepValue = step as Record<string, unknown>;
          actions.push(stepValue.action);
          expectedResults.push(stepValue.expected);
        } else {
          actions.push(step);
        }
      }

      return {
        用例编号: testCase.case_id,
        用例标题: testCase.title,
        测试模块: testCase.module,
        前置条件: testCase.preconditions,
        执行步骤: actions,
        预期结果: expectedResults,
        用例级别: testCase.priority,
      };
    }),
  };
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function formatDuration(startedAt: string | null | undefined, finishedAt: string | null | undefined, nowMs: number) {
  if (!startedAt) return '';
  const parseExecutionTimestamp = (value: string) => {
    const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
    return Date.parse(normalized);
  };
  const startMs = parseExecutionTimestamp(startedAt);
  const finishMs = finishedAt ? parseExecutionTimestamp(finishedAt) : nowMs;
  if (!Number.isFinite(startMs) || !Number.isFinite(finishMs)) return '';
  const totalSeconds = Math.max(0, Math.floor((finishMs - startMs) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds]
    .map((value) => value.toString().padStart(2, '0'))
    .join(':');
}

function formatDurationSeconds(totalSeconds: number): string {
  const normalizedSeconds = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(normalizedSeconds / 3600);
  const minutes = Math.floor((normalizedSeconds % 3600) / 60);
  const seconds = normalizedSeconds % 60;
  return [hours, minutes, seconds]
    .map((value) => value.toString().padStart(2, '0'))
    .join(':');
}

function nodeTiming(nodeRun: AgentNodeRun, nowMs: number) {
  const restore = objectValue(nodeRun.sdk_state.checkpoint_restore);
  const resultCache = objectValue(nodeRun.sdk_state.result_cache);
  const sourceDurationValue = restore.source_duration_seconds ?? resultCache.source_duration_seconds;
  const sourceDurationSeconds = typeof sourceDurationValue === 'number'
    ? sourceDurationValue
    : Number.NaN;
  const waitSeconds = Number(nodeRun.sdk_state.confirmation_wait_seconds || 0);
  const duration = formatDuration(nodeRun.started_at, nodeRun.finished_at
    ? new Date(new Date(nodeRun.finished_at).getTime() - waitSeconds * 1000).toISOString()
    : null, nowMs - (nodeRun.finished_at ? 0 : waitSeconds * 1000));
  const cacheHit = resultCache.hit === true;
  const checkpointRestored = Boolean(restore.source_node_run_id);
  const reused = cacheHit || checkpointRestored;
  return {
    duration,
    reused,
    reuseLabel: cacheHit ? '缓存复用' : '检查点复用',
    sourceDuration: reused && Number.isFinite(sourceDurationSeconds)
      ? formatDurationSeconds(sourceDurationSeconds)
      : '',
  };
}

function agentRole(nodeRun: AgentNodeRun, agent: ExecutionAgent | undefined): string {
  const subagentKeys = agent?.runtime_config.subagent_keys;
  if (nodeRun.node_type === 'agent_map' && Array.isArray(subagentKeys) && subagentKeys.length > 0) {
    return '动态子网络 · 分配任务执行';
  }
  if (nodeRun.node_type === 'agent_map') return '子智能体 · 分配任务执行';
  if (nodeRun.node_type === 'agent_network') return '主 Agent · 按状态自主决策';
  if (agent?.agent_key.includes('review') || agent?.agent_key.includes('auditor')) return '专项复核智能体';
  return '协作智能体';
}

function stageStatus(run: AgentRun | null, nodeKeys: readonly string[]): BusinessStageStatus {
  const latestByNode = new Map<string, AgentNodeRun>();
  for (const node of run?.nodes ?? []) {
    if (!nodeKeys.includes(node.node_key)) continue;
    const current = latestByNode.get(node.node_key);
    if (!current || node.attempt > current.attempt || (
      node.attempt === current.attempt && node.id > current.id
    )) {
      latestByNode.set(node.node_key, node);
    }
  }
  const nodes = Array.from(latestByNode.values());
  if (nodes.some((node) => node.status === 'failed')) return 'failed';
  if (nodes.some((node) => node.status === 'waiting_approval')) return 'waiting_approval';
  if (nodes.some((node) => ['pending', 'running'].includes(node.status))) return 'running';
  if (nodes.length === nodeKeys.length && nodes.every((node) => node.status === 'success')) return 'success';
  if (nodes.some((node) => node.status === 'success')) return 'running';
  if (nodes.some((node) => node.status === 'cancelled')) return 'cancelled';
  return 'waiting';
}

function mapParallelism(nodeRun: AgentNodeRun, configuredConcurrency = 1) {
  const parallelism = objectValue(nodeRun.sdk_state.parallelism);
  const maxConcurrency = Number(parallelism.max_concurrency ?? configuredConcurrency);
  const activeInstances = Number(parallelism.active_instances ?? 0);
  const items = Array.isArray(nodeRun.sdk_state.items)
    ? nodeRun.sdk_state.items.map(objectValue)
    : [];
  return {
    maxConcurrency: Number.isFinite(maxConcurrency) && maxConcurrency > 0 ? maxConcurrency : 1,
    activeInstances: Number.isFinite(activeInstances) && activeInstances > 0 ? activeInstances : 0,
    items,
  };
}

function generatedCases(run: AgentRun | null): Record<string, unknown>[] {
  if (!run) return [];
  const artifact = objectValue(run.test_generation_result);
  const cases = Array.isArray(artifact.test_cases) ? artifact.test_cases : [];
  return cases.map(objectValue);
}

function activatedAgentRuns(run: AgentRun | null) {
  const latestByNode = new Map<string, AgentRun['nodes'][number]>();
  for (const nodeRun of run?.nodes ?? []) {
    if (!['agent', 'agent_network', 'agent_map'].includes(nodeRun.node_type)) continue;
    const current = latestByNode.get(nodeRun.node_key);
    if (!current || nodeRun.attempt > current.attempt || (
      nodeRun.attempt === current.attempt && nodeRun.id > current.id
    )) {
      latestByNode.set(nodeRun.node_key, nodeRun);
    }
  }
  return Array.from(latestByNode.values());
}

export function AgentWorkspace({ projectId, onLog }: Props) {
  const controller = useAgentWorkspace({ projectId, onLog });
  const [selectedStageKey, setSelectedStageKey] = useState<string | null>(null);
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);
  const [clockMs, setClockMs] = useState(() => Date.now());
  const [openConflictApprovalId, setOpenConflictApprovalId] = useState<number | null>(null);
  const conflictApproval = controller.activeRun?.approvals.find((item) =>
    item.status === 'pending' && item.request_payload.kind === 'source_conflicts');

  const selectedDocument = controller.requirementDocuments.find(
    (document) => document.id === controller.requirementDocId,
  );
  const cases = useMemo(() => generatedCases(controller.activeRun), [controller.activeRun]);
  const agentRuns = useMemo(
    () => activatedAgentRuns(controller.activeRun),
    [controller.activeRun],
  );
  const runBusy = Boolean(
    controller.activeRun && ['pending', 'running', 'waiting_approval'].includes(controller.activeRun.status),
  );
  const resetAttemptDisabled = Boolean(
    !controller.activeRun
    || ['pending', 'running', 'waiting_approval'].includes(controller.activeRun.status)
    || controller.activeRun.run_attempt === 1
    || controller.resettingAttempt
    || controller.submitting,
  );
  const retryAvailable = Boolean(controller.activeRun
    && ['failed', 'cancelled'].includes(controller.activeRun.status));
  const retryTitle = controller.uploading
    ? '需求文档正在上传，请等待上传完成'
    : controller.resettingAttempt
      ? '正在重置执行次数，请稍候'
      : `沿用 Run #${controller.activeRun?.id} 的原需求和 ${controller.activeRun?.input_payload.case_budget} 条用例目标，继续未完成任务`;
  const resetAttemptTitle = !controller.activeRun
    ? '当前没有可重置的运行'
    : ['pending', 'running', 'waiting_approval'].includes(controller.activeRun.status)
      ? '运行执行或等待审批时不能重置次数'
      : controller.activeRun.run_attempt === 1
        ? '执行次数已经是 1'
        : '将本次运行的执行次数重置为 1';
  const confirmationPausedAt = conflictApproval ? String(conflictApproval.request_payload.paused_at) : null;
  const runEnd = controller.activeRun?.finished_at || (confirmationPausedAt
    ? `${confirmationPausedAt}${/Z$|[+-]\d\d:\d\d$/.test(confirmationPausedAt) ? '' : 'Z'}` : null);
  const waitMs = Number(controller.activeRun?.run_context.confirmation_wait_seconds || 0) * 1000;
  const runDuration = controller.activeRun
    ? formatDuration(
      controller.activeRun.started_at,
      runEnd ? new Date(new Date(runEnd).getTime() - waitMs).toISOString() : null,
      clockMs - waitMs,
    )
    : '';
  const selectedNodeRun = agentRuns.find((node) => node.node_key === selectedNodeKey);
  const selectedAgent = selectedNodeRun ? executionAgent(controller.activeRun, selectedNodeRun) : undefined;
  const execution = workspaceExecution(controller.activeRun, controller.selectedWorkflow);
  const workflowNodes = execution.definition?.execution_mode === 'dag' ? execution.definition.nodes : [];
  const hierarchyStages = executionDisplayStages(execution.definition);
  const networkRun = agentRuns.find((node) => node.node_key === 'agent_network' && node.node_type === 'agent_network');
  const activity = networkRun ? networkActivity(controller.activeRun, networkRun) : null;
  const currentNodeStage = controller.activeRun?.current_node_key
    ? hierarchyStages.find((stage) => stage.node_keys.includes(controller.activeRun?.current_node_key ?? ''))
    : undefined;
  const selectedStage = hierarchyStages.find((stage) => stage.stage_key === selectedStageKey)
    ?? currentNodeStage
    ?? hierarchyStages[0];
  const visibleAgentRuns = selectedStage
    ? agentRuns.filter((nodeRun) => selectedStage.node_keys.includes(nodeRun.node_key))
    : agentRuns;
  const selectedWorkflowNode = workflowNodes.find(
    (node) => node.node_key === selectedNodeRun?.node_key,
  );
  const selectedConfiguredConcurrency = Number(
    selectedWorkflowNode?.map_config?.max_concurrency ?? 1,
  );
  const selectedParallelism = selectedNodeRun
    ? mapParallelism(selectedNodeRun, selectedConfiguredConcurrency)
    : null;
  const selectedModel = selectedNodeRun
    ? actualModelDisplay(selectedNodeRun, selectedAgent)
    : null;
  const selectedNodeTiming = selectedNodeRun && controller.activeRun
    ? nodeTiming(selectedNodeRun, clockMs)
    : null;
  const sourceReady = Boolean(selectedDocument && selectedDocument.parse_status === 'success');
  const hierarchyConfigured = hierarchyStages.length > 0;

  useEffect(() => {
    const currentNodeKey = controller.activeRun?.current_node_key;
    const currentStage = currentNodeKey
      ? hierarchyStages.find((stage) => stage.node_keys.includes(currentNodeKey))
      : undefined;
    setSelectedStageKey(currentStage?.stage_key ?? hierarchyStages[0]?.stage_key ?? null);
  }, [controller.selectedWorkflow?.id, controller.activeRun?.id, execution.definition?.execution_mode]);

  useEffect(() => {
    setSelectedNodeKey(null);
  }, [controller.activeRun?.id]);

  useEffect(() => {
    setSelectedNodeKey((current) => {
      if (current && agentRuns.some((node) => node.node_key === current)) return current;
      return null;
    });
  }, [agentRuns]);

  useEffect(() => {
    if (!runBusy) return undefined;
    setClockMs(Date.now());
    const timer = window.setInterval(() => setClockMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [runBusy]);

  if (!projectId) {
    return <div className="agent-workspace-empty">请先选择项目</div>;
  }

  return (
    <div className="agent-workspace">
      <header className="agent-workspace-header">
        <div>
          <h2>Agent 测试生成</h2>
          <span>上传真实需求，启动后查看按需激活的 Agent 与最终用例</span>
        </div>
      </header>

      {controller.error && (
        <div className="agent-workspace-error" role="alert" aria-live="assertive">
          {controller.error}
        </div>
      )}

      <main className="agent-workspace-content">
        <section className="agent-intake-grid" aria-label="需求输入与实际 Agent 工作">
          <article className="agent-card agent-source-card">
            <div className="agent-card-heading">
              <span className="agent-step-number" aria-hidden="true"><FaFileAlt /></span>
              <div>
                <h3>需求来源</h3>
                <p>上传本次生成使用的真实需求文档</p>
              </div>
            </div>

            <label
              className={`agent-upload-zone ${controller.uploading ? 'is-uploading' : ''}`}
              aria-busy={controller.uploading}
            >
              {controller.uploading ? <Spinner size="sm" /> : <FaUpload />}
              <span>{controller.uploading
                ? '正在上传…'
                : selectedDocument ? '重新上传需求文档' : '上传需求文档'}</span>
              <small>{selectedDocument
                ? `${selectedDocument.filename} · ${parseStatusLabel[selectedDocument.parse_status]}`
                : '上传后将准备页面资产，完成后可开始生成'}</small>
              <input
                type="file"
                aria-label="上传需求文档"
                disabled={controller.uploading || controller.submitting}
                onChange={(event) => {
                  const file = event.currentTarget.files?.[0];
                  if (file) void controller.uploadRequirement(file);
                  event.currentTarget.value = '';
                }}
              />
            </label>

            <div className="agent-generation-settings">
              <Form.Group controlId="agent-case-budget">
                <Form.Label>用例数量</Form.Label>
                <Form.Control
                  type="number"
                  min={1}
                  max={200}
                  value={controller.caseBudget}
                  onChange={(event) => controller.setCaseBudget(Number(event.target.value))}
                  disabled={controller.submitting || runBusy}
                />
              </Form.Group>
              <Button
                className="agent-run-button"
                onClick={() => void controller.runWorkflow()}
                disabled={
                  controller.submitting
                  || runBusy
                  || !controller.selectedWorkflow
                  || !sourceReady
                }
                title={runBusy ? '已有生成任务正在等待或执行' : '开始生成测试用例'}
              >
                {controller.submitting && !controller.retrying
                  ? <Spinner size="sm" aria-hidden="true" /> : <FaPlay aria-hidden="true" />} 开始生成
              </Button>
              {retryAvailable && (
                <Button
                  className="agent-run-button"
                  variant="outline-primary"
                  onClick={() => void controller.retryRun()}
                  disabled={controller.submitting || controller.uploading || controller.resettingAttempt}
                  aria-busy={controller.retrying}
                  title={retryTitle}
                >
                  {controller.retrying
                    ? <Spinner size="sm" aria-hidden="true" /> : <FaUndo aria-hidden="true" />}
                  {' '}{controller.retrying ? '正在续跑' : '继续失败任务'}
                </Button>
              )}
            </div>
            <Form.Text>默认总额度 30 万 token，最长执行 20 分钟，每批最多编写 20 条用例。</Form.Text>
          </article>

          <article className="agent-card agent-runtime-card">
          <div className="agent-section-heading">
            <div>
              <h3>实际 Agent 工作</h3>
              <p>仅展示本次生成中按需激活并真实执行的 Agent</p>
            </div>
            <div className="agent-active-run-tools">
              <Button
                size="sm"
                variant="outline-secondary"
                onClick={() => void controller.resetRunAttempt()}
                disabled={resetAttemptDisabled}
                title={resetAttemptTitle}
              >
                {controller.resettingAttempt ? <Spinner size="sm" /> : <FaUndo />} 重置次数
              </Button>
              {controller.activeRun && (
                <>
                <div className="agent-active-run-chip">
                  <span>本次运行</span>
                  <small>执行次数 {controller.activeRun.run_attempt}</small>
                  <Badge bg={statusVariant[controller.activeRun.status]}>
                    {statusLabel[controller.activeRun.status]}
                  </Badge>
                  {runDuration && (
                    <small className="agent-run-duration">
                      <FaClock aria-hidden="true" />
                      {runBusy ? '已用时' : '总用时'} {runDuration}
                    </small>
                  )}
                </div>
                {runBusy && (
                  <Button size="sm" variant="outline-danger" onClick={() => void controller.cancelRun()}>
                    <FaBan /> 取消
                  </Button>
                )}
                </>
              )}
            </div>
          </div>

          {execution.error && (
            <AgentErrorDetails className="agent-run-error" message={`${execution.error}。无法确认本次运行的执行结构，请检查运行契约。`} />
          )}

          {hierarchyConfigured && (
            <div className="agent-hierarchy" aria-label="本次运行执行结构">
              {hierarchyStages.map((stage, index) => {
                const stageState = stageStatus(controller.activeRun, stage.node_keys);
                return (
                  <div className="agent-stage-container" key={stage.stage_key}>
                  <button
                    type="button"
                    className={`agent-hierarchy-stage is-${stageState}${selectedStage?.stage_key === stage.stage_key ? ' is-selected' : ''}`}
                    onClick={() => {
                      setSelectedStageKey(stage.stage_key);
                      setSelectedNodeKey(null);
                    }}
                    aria-pressed={selectedStage?.stage_key === stage.stage_key}
                    aria-controls="agent-stage-node-list"
                  >
                    <span className="agent-hierarchy-index">{index + 1}</span>
                    <div>
                      <strong>{stage.label}</strong>
                      <small>{stage.description}</small>
                    </div>
                    <Badge bg={businessStageVariant[stageState]}>
                      {businessStageLabel[stageState]}
                    </Badge>
                  </button>
                  {conflictApproval && controller.activeRun?.nodes.some((node) =>
                    node.id === conflictApproval.node_run_id && stage.node_keys.includes(node.node_key)) && (
                    <Button className="agent-pending-confirmation" size="sm" variant="warning"
                      onClick={() => setOpenConflictApprovalId(conflictApproval.id)} aria-haspopup="dialog">确认规则</Button>
                  )}
                  </div>
                );
              })}
            </div>
          )}

          {controller.runEvents.length > 0 && (
            <div className="agent-event-trace" aria-label="执行轨迹">
              <div className="agent-event-trace-heading">
                <strong>执行轨迹</strong>
                <small>已接收 {controller.runEvents.length} 条事件</small>
              </div>
              <ol>
                {controller.runEvents.slice(-8).reverse().map((event) => (
                  <li key={`${event.run_id}-${event.sequence}`}>
                    <span className="agent-event-sequence">#{event.sequence}</span>
                    <span className="agent-event-type">{eventLabel(event)}</span>
                    <span className="agent-event-detail">{eventDetail(event, controller.activeRun)}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {!hierarchyConfigured && conflictApproval && (
            <div className="agent-pending-confirmation-panel" role="alert">
              <div>
                <strong>任务需要人工确认</strong>
                <span>当前 Agent 已暂停，完成确认后才会继续执行。</span>
              </div>
              <Button
                variant="warning"
                onClick={() => setOpenConflictApprovalId(conflictApproval.id)}
                aria-haspopup="dialog"
              >
                确认规则
              </Button>
            </div>
          )}

          {controller.activeRun?.error_message && (
            <AgentErrorDetails
              className="agent-run-error"
              message={controller.activeRun.error_message}
            />
          )}

          {controller.activeRun && agentRuns.length > 0 ? (
            <>
              <span className="visually-hidden" aria-live="polite">
                {selectedStage
                  ? `${selectedStage.label}阶段已激活 ${visibleAgentRuns.length} 个智能体`
                  : `本次运行已激活 ${visibleAgentRuns.length} 个智能体`}
              </span>
              {visibleAgentRuns.length > 0 ? (
                <div
                  className="agent-node-list"
                  id="agent-stage-node-list"
                  aria-label={selectedStage ? `${selectedStage.label}阶段的智能体` : '本次运行的智能体'}
                >
            {visibleAgentRuns.map((nodeRun, index) => {
              const agent = executionAgent(controller.activeRun, nodeRun);
              const workflowNode = workflowNodes.find(
                (item) => item.node_key === nodeRun.node_key,
              );
              const configuredConcurrency = Number(workflowNode?.map_config?.max_concurrency ?? 1);
              const parallelism = mapParallelism(nodeRun, configuredConcurrency);
              const isDynamicMap = nodeRun.node_type === 'agent_map'
                && Array.isArray(agent?.runtime_config.subagent_keys)
                && agent.runtime_config.subagent_keys.length > 0;
              const executionMode = nodeRun.node_type === 'agent_map' && parallelism.maxConcurrency > 1
                ? `${isDynamicMap ? '动态子网络 · ' : ''}并发 ${parallelism.maxConcurrency} 个独立实例`
                : agentRole(nodeRun, agent);
              const timing = nodeTiming(nodeRun, clockMs);
              return (
                <button
                  type="button"
                  className={selectedNodeKey === nodeRun.node_key ? 'agent-node-row is-selected' : 'agent-node-row'}
                  key={nodeRun.node_key}
                  onClick={() => setSelectedNodeKey(nodeRun.node_key)}
                  aria-haspopup="dialog"
                  aria-controls={selectedNodeKey === nodeRun.node_key ? 'agent-node-detail-dialog' : undefined}
                >
                  <span className="agent-node-index">{index + 1}</span>
                  <span className="agent-node-copy">
                    <span className="agent-node-title">
                      <strong>{agent?.name || nodeRun.node_key}</strong>
                      {timing.reused ? (
                        <small className="agent-node-duration is-restored">
                          <FaUndo aria-hidden="true" /> {timing.reuseLabel} · 本次 {timing.duration}
                        </small>
                      ) : timing.duration && (
                        <small className="agent-node-duration">
                          <FaClock aria-hidden="true" /> {timing.duration}
                        </small>
                      )}
                    </span>
                    <small>{executionMode}{nodeProgress(nodeRun) ? ` · ${nodeProgress(nodeRun)}` : ''}</small>
                  </span>
                  <Badge bg={statusVariant[nodeRun.status]}>{statusLabel[nodeRun.status]}</Badge>
                </button>
              );
            })}
                </div>
              ) : (
                <div className="agent-stage-empty" id="agent-stage-node-list" role="status">
                  <strong>{selectedStage?.label || '当前阶段'}尚未激活智能体</strong>
                  <span>该阶段执行到 Agent 节点后会在这里展示真实运行记录。</span>
                </div>
              )}

              {activity && (activity.action || activity.children.length > 0) && (
                <section className="agent-instance-section" aria-label="主 Agent 工具与专业任务">
                  {activity.action && (
                    <div className="agent-instance-heading">
                      <strong>{activity.action.status === 'started' ? '当前工具' : '最近工具'}：{activity.action.name}</strong>
                      <span>
                        {activity.action.status === 'completed' ? '已完成' : activity.action.status === 'failed' ? '失败' : '执行中'}
                        {activity.action.completedChildCount !== null && activity.action.completedChildCount > 0 ? ` · 已验收 ${activity.action.completedChildCount} 个专业任务` : ''}
                      </span>
                    </div>
                  )}
                  {activity.children.length > 0 && (
                    <>
                      <div className="agent-instance-heading"><strong>专业任务执行回执</strong><span>按本次运行已保存并校验的结果统计</span></div>
                      <div className="agent-instance-grid">
                        {activity.children.map((child) => (
                          <div className="agent-instance-card" key={child.key}>
                            <strong>{child.name}</strong><span>已验收 {child.count} 个任务</span>
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                </section>
              )}

              {(controller.activeRun.approvals ?? []).filter((item) => item.status === 'pending' && item.request_payload.kind !== 'source_conflicts').map((approval) => (
                <div className="agent-approval agent-current-approval" key={approval.id}>
                  <strong>审批 #{approval.id}</strong>
                  <JsonView value={approval.request_payload} />
                  <div>
                    <Button size="sm" variant="success" onClick={() => void controller.decideApproval(approval.id, true)}>
                      <FaCheck /> 通过
                    </Button>
                    <Button size="sm" variant="outline-danger" onClick={() => void controller.decideApproval(approval.id, false)}>
                      <FaTimes /> 拒绝
                    </Button>
                  </div>
                </div>
              ))}
            </>
          ) : (
            <div className="agent-runtime-empty" id="agent-stage-node-list">
              <FaRobot />
              <strong>{controller.activeRun ? 'Agent 尚未激活' : '尚未启动生成'}</strong>
              <span>{controller.activeRun
                ? `本次运行${statusLabel[controller.activeRun.status]}，尚未记录已激活的 Agent。`
                : '上传需求并点击“开始生成”后，这里会按实际激活顺序展示 Agent。'}</span>
            </div>
          )}
          </article>
        </section>

        <section className="agent-card agent-cases-section">
          <div className="agent-section-heading">
            <div>
              <h3>生成的测试用例</h3>
              <p>这里展示已由平台校验并持久化的最终用例，不展示中间候选结果</p>
            </div>
            <div className="agent-active-run-tools">
              <Button
                size="sm"
                variant="outline-secondary"
                onClick={() => void controller.exportTestCases()}
                disabled={cases.length === 0 || controller.exporting}
                title="导出当前已持久化的测试用例"
              >
                {controller.exporting ? <Spinner size="sm" /> : <FaDownload />} 导出 Excel
              </Button>
              <Badge bg={cases.length > 0 ? 'primary' : 'secondary'}>{cases.length} 条</Badge>
            </div>
          </div>

          {cases.length > 0 ? (
            <div className="agent-cases-json" aria-label="最终测试用例 JSON">
              <JsonView value={localizedGeneratedCases(cases)} />
            </div>
          ) : (
            <div className="agent-cases-empty">
              <FaRobot />
              <strong>尚无可展示的最终用例</strong>
              <span>{retryAvailable
                ? `本次生成${controller.activeRun?.status === 'cancelled' ? '已取消' : '失败'}，尚无最终用例。`
                : '选择需求并启动生成后，最终用例会在这里逐条展示。'}</span>
            </div>
          )}
        </section>
      </main>

      <AgentNodeDetailModal
        show={Boolean(selectedNodeRun)}
        onHide={() => setSelectedNodeKey(null)}
        nodeRun={selectedNodeRun}
        agent={selectedAgent}
        role={selectedNodeRun ? agentRole(selectedNodeRun, selectedAgent) : ''}
        model={selectedModel}
        timing={selectedNodeTiming}
        progress={selectedNodeRun ? nodeProgress(selectedNodeRun) : null}
        statusLabel={selectedNodeRun ? businessStageLabel[selectedNodeRun.status] : ''}
        parallelism={selectedParallelism}
      />

      {conflictApproval && <AgentConflictModal key={conflictApproval.id} approval={conflictApproval}
        show={openConflictApprovalId === conflictApproval.id} onHide={() => setOpenConflictApprovalId(null)}
        submitError={controller.error}
        onComplete={(answers) => controller.decideApproval(conflictApproval.id, true, answers)} />}
      <Modal
        show={controller.showReusePrompt}
        onHide={controller.dismissReusePrompt}
        centered
        backdrop="static"
      >
        <Modal.Header closeButton>
          <Modal.Title>已存在生成结果</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          <p className="mb-2">
            检测到相同内容的需求文档已生成
            {' '}{controller.reuseCandidate?.case_count ?? 0} 条测试用例。
          </p>
          <div className="text-muted small">
            来源：{controller.reuseCandidate?.source_filename || '原需求文档'}
            {controller.reuseCandidate?.run_id
              ? ` · Run #${controller.reuseCandidate.run_id}`
              : ''}
          </div>
        </Modal.Body>
        <Modal.Footer>
          <Button
            variant="outline-secondary"
            onClick={() => void controller.regenerateGeneration()}
            disabled={controller.submitting}
          >
            <FaUndo /> 重新生成并替换
          </Button>
          <Button
            variant="primary"
            onClick={() => void controller.reuseExistingGeneration()}
            disabled={controller.submitting}
          >
            <FaCheck /> 复用已有结果
          </Button>
        </Modal.Footer>
      </Modal>
    </div>
  );
}
