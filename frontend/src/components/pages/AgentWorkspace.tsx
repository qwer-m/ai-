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
import type {
  AgentDefinition,
  AgentNodeRun,
  AgentRun,
  AgentWorkflow,
  RunStatus,
  WorkflowNode,
} from '../agent-platform/types';

type Props = {
  projectId: number | null;
  onLog: (message: string) => void;
};

type BusinessStageStatus = RunStatus | 'waiting';

const statusLabel: Record<RunStatus, string> = {
  pending: '等待中',
  running: '运行中',
  waiting_approval: '等待审批',
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

function nodeTiming(run: AgentRun | null, nodeRun: AgentNodeRun, nowMs: number) {
  const restore = objectValue(nodeRun.sdk_state.checkpoint_restore);
  const resultCache = objectValue(nodeRun.sdk_state.result_cache);
  const sourceDurationValue = restore.source_duration_seconds ?? resultCache.source_duration_seconds;
  const sourceDurationSeconds = typeof sourceDurationValue === 'number'
    ? sourceDurationValue
    : Number.NaN;
  const duration = formatDuration(nodeRun.started_at, nodeRun.finished_at, nowMs);
  const cacheHit = resultCache.hit === true;
  const checkpointRestored = Boolean(restore.source_node_run_id) || Boolean(
    run?.parent_run_id
    && nodeRun.status === 'success'
    && duration === '00:00:00',
  );
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

function actualModelDisplay(
  nodeRun: AgentNodeRun,
  agent: AgentDefinition | undefined,
) {
  const model = objectValue(nodeRun.sdk_state.model);
  const configuredModel = stringValue(agent?.model);
  return {
    name: stringValue(model.name) || configuredModel || '本次生成未记录',
    source: stringValue(model.source) || (configuredModel ? 'Agent 固定配置' : '等待运行时记录'),
  };
}

function agentRole(nodeRun: AgentNodeRun, agent: AgentDefinition | undefined): string {
  const subagentKeys = agent?.runtime_config.subagent_keys;
  if (nodeRun.node_type === 'agent_map' && Array.isArray(subagentKeys) && subagentKeys.length > 0) {
    return '动态子网络 · 分配任务执行';
  }
  if (nodeRun.node_type === 'agent_map') return '子智能体 · 分配任务执行';
  if (nodeRun.node_type === 'agent_network') return '主智能体 · 动态协作';
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

function mapProgress(nodeRun: AgentNodeRun): string | null {
  const completed = Number(nodeRun.output_payload.completed_count ?? 0);
  const total = Number(nodeRun.output_payload.total_count ?? 0);
  if (!Number.isFinite(total) || total < 1) return null;
  return `已完成 ${Math.max(0, completed)} / ${total} 个分配任务`;
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
    if (!current || nodeRun.attempt >= current.attempt) {
      latestByNode.set(nodeRun.node_key, nodeRun);
    }
  }
  return Array.from(latestByNode.values());
}

function configuredWorkflowNodes(workflow: AgentWorkflow | null): WorkflowNode[] {
  return workflow?.definition.execution_mode === 'dag'
    ? workflow.definition.nodes
    : [];
}

export function AgentWorkspace({ projectId, onLog }: Props) {
  const controller = useAgentWorkspace({ projectId, onLog });
  const [selectedStageKey, setSelectedStageKey] = useState<string | null>(null);
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);
  const [clockMs, setClockMs] = useState(() => Date.now());

  const selectedDocument = controller.requirementDocuments.find(
    (document) => document.id === controller.requirementDocId,
  );
  const cases = useMemo(() => generatedCases(controller.activeRun), [controller.activeRun]);
  const agentRuns = useMemo(
    () => activatedAgentRuns(controller.activeRun),
    [controller.activeRun],
  );
  const runBusy = Boolean(
    controller.activeRun && ['pending', 'running'].includes(controller.activeRun.status),
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
      : `沿用 Run #${controller.activeRun?.id} 的原需求、${controller.activeRun?.input_payload.case_budget} 条用例数量及压缩设置，恢复已完成任务并继续执行`;
  const resetAttemptTitle = !controller.activeRun
    ? '当前没有可重置的运行'
    : ['pending', 'running', 'waiting_approval'].includes(controller.activeRun.status)
      ? '运行执行或等待审批时不能重置次数'
      : controller.activeRun.run_attempt === 1
        ? '执行次数已经是 1'
        : '将本次运行的执行次数重置为 1';
  const runDuration = controller.activeRun
    ? formatDuration(
      controller.activeRun.started_at,
      controller.activeRun.finished_at,
      clockMs,
    )
    : '';
  const selectedNodeRun = agentRuns.find((node) => node.node_key === selectedNodeKey);
  const selectedAgent = selectedNodeRun?.agent_definition_id
    ? controller.catalog.agents.find(
      (agent) => agent.id === selectedNodeRun.agent_definition_id,
    )
    : undefined;
  const workflowNodes = configuredWorkflowNodes(controller.selectedWorkflow);
  const hierarchyStages = controller.selectedWorkflow?.definition.execution_mode === 'dag'
    ? controller.selectedWorkflow.definition.display_stages
    : [];
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
    ? nodeTiming(controller.activeRun, selectedNodeRun, clockMs)
    : null;
  const sourceReady = Boolean(selectedDocument && selectedDocument.parse_status === 'success');
  const hierarchyConfigured = hierarchyStages.length > 0;

  useEffect(() => {
    const currentNodeKey = controller.activeRun?.current_node_key;
    const currentStage = currentNodeKey
      ? hierarchyStages.find((stage) => stage.node_keys.includes(currentNodeKey))
      : undefined;
    setSelectedStageKey(currentStage?.stage_key ?? hierarchyStages[0]?.stage_key ?? null);
  }, [controller.selectedWorkflow?.id, controller.activeRun?.id]);

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
              <Form.Check
                id="agent-context-compression"
                type="switch"
                label="启用上下文压缩"
                checked={controller.enableContextCompression}
                onChange={(event) => controller.setEnableContextCompression(event.target.checked)}
                disabled={controller.submitting || runBusy}
                title="使用平台通用压缩器整理当前需求证据；原始来源锚点仍保留用于校验"
              />
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

          {hierarchyConfigured && (
            <div className="agent-hierarchy" aria-label="Agent 协作流程">
              {hierarchyStages.map((stage, index) => {
                const stageState = stageStatus(controller.activeRun, stage.node_keys);
                return (
                  <button
                    type="button"
                    className={`agent-hierarchy-stage is-${stageState}${selectedStage?.stage_key === stage.stage_key ? ' is-selected' : ''}`}
                    key={stage.stage_key}
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
                );
              })}
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
              const agent = nodeRun.agent_definition_id
                ? controller.catalog.agents.find(
                  (item) => item.id === nodeRun.agent_definition_id,
                )
                : undefined;
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
              const timing = nodeTiming(controller.activeRun, nodeRun, clockMs);
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
                    <small>{executionMode}{mapProgress(nodeRun) ? ` · ${mapProgress(nodeRun)}` : ''}</small>
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

              {(controller.activeRun.approvals ?? []).filter((item) => item.status === 'pending').map((approval) => (
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
                ? '本次生成正在执行确定性准备工作；只有实际调用 Agent 后才会出现在这里。'
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
        progress={selectedNodeRun ? mapProgress(selectedNodeRun) : null}
        statusLabel={selectedNodeRun ? businessStageLabel[selectedNodeRun.status] : ''}
        parallelism={selectedParallelism}
      />

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
