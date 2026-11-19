import { parseExecutionDefinition } from './executionDefinition.ts';
import { readBoolean, readNumber, readObject } from './jsonContract.ts';
import type {
  AgentNodeRun, AgentRun, AgentWorkflow, AgentWorkflowDefinition, GenerationDomainProjection, WorkflowDisplayStage,
} from './types';

export type ExecutionAgent = {
  agent_key: string;
  name: string;
  description: string;
  instructions: string;
  runtime_config: Record<string, unknown>;
  model: string;
  model_routes: Record<string, unknown>;
};

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function readCount(value: unknown, path: string): number {
  const count = readNumber(value, path);
  if (!Number.isInteger(count) || count < 0) throw new Error(`${path} 应为非负整数`);
  return count;
}

export function parseGenerationProjection(value: unknown, path = '生成进度投影'): GenerationDomainProjection | null {
  if (value === undefined || value === null) return null;
  const projection = readObject(value, path);
  // 历史只有通用日志时不解释旧领域数组，也不把缺失进度填成零。
  if (projection.domain !== 'test_generation' || projection.schema_version !== 4) return null;
  const versions = readObject(projection.versions, `${path}.versions`);
  const progress = readObject(projection.progress, `${path}.progress`);
  return {
    domain: 'test_generation',
    schema_version: 4,
    completed: readBoolean(projection.completed, `${path}.completed`),
    versions: {
      schema_version: readCount(versions.schema_version, `${path}.versions.schema_version`),
      coverage_version: readCount(versions.coverage_version, `${path}.versions.coverage_version`),
      cases_version: readCount(versions.cases_version, `${path}.versions.cases_version`),
    },
    progress: {
      coverage_items: readCount(progress.coverage_items, `${path}.progress.coverage_items`),
      covered_items: readCount(progress.covered_items, `${path}.progress.covered_items`),
      cases: readCount(progress.cases, `${path}.progress.cases`),
      target_cases: readCount(progress.target_cases, `${path}.progress.target_cases`),
      open_issues: readCount(progress.open_issues, `${path}.progress.open_issues`),
      pending_human: readCount(progress.pending_human, `${path}.progress.pending_human`),
      review_checked_cases: readCount(progress.review_checked_cases, `${path}.progress.review_checked_cases`),
      review_current: readBoolean(progress.review_current, `${path}.progress.review_current`),
    },
    candidate_case_count: readCount(projection.candidate_case_count, `${path}.candidate_case_count`),
  };
}

function snapshots(run: AgentRun | null, collection: 'agents' | 'tools') {
  const values = objectValue(run?.run_context.execution_contract)[collection];
  return Array.isArray(values) ? values.map(objectValue) : [];
}

export function workspaceExecution(run: AgentRun | null, workflow: AgentWorkflow | null): {
  definition: AgentWorkflowDefinition | null;
  error: string | null;
} {
  if (!run) return { definition: workflow?.definition ?? null, error: null };
  const workflowSnapshot = objectValue(objectValue(run.run_context.execution_contract).workflow);
  try {
    return {
      definition: parseExecutionDefinition(workflowSnapshot.definition, '运行冻结执行定义'),
      error: null,
    };
  } catch (reason) {
    return {
      definition: null,
      error: reason instanceof Error ? reason.message : '运行冻结执行定义无法读取',
    };
  }
}

export function executionAgent(run: AgentRun | null, node: AgentNodeRun): ExecutionAgent | undefined {
  const snapshot = snapshots(run, 'agents').find((item) => item.id === node.agent_definition_id);
  if (!snapshot) return undefined;
  return {
    agent_key: stringValue(snapshot.key),
    name: stringValue(snapshot.name),
    description: stringValue(snapshot.description),
    instructions: stringValue(snapshot.instructions),
    runtime_config: objectValue(snapshot.runtime_config),
    model: stringValue(snapshot.model),
    model_routes: objectValue(snapshot.model_routes),
  };
}

export function executionName(run: AgentRun | null, collection: 'agents' | 'tools', key: string) {
  return stringValue(snapshots(run, collection).find((item) => item.key === key)?.name) || key;
}

export function actualModelDisplay(node: AgentNodeRun, agent: ExecutionAgent | undefined) {
  const model = objectValue(node.sdk_state.model);
  const recorded = stringValue(model.name);
  if (recorded) return { name: recorded, source: stringValue(model.source) || '运行时记录' };
  const route = stringValue(model.route) || stringValue(agent?.runtime_config.model_route) || 'main';
  const routeModel = stringValue(objectValue(agent?.model_routes[route]).model);
  if (routeModel) return { name: routeModel, source: '执行契约模型路由' };
  if (agent?.model) return { name: agent.model, source: '执行契约定义' };
  return { name: '本次运行未记录模型', source: '未记录' };
}

export function executionDisplayStages(definition: AgentWorkflowDefinition | null): WorkflowDisplayStage[] {
  if (!definition) return [];
  if (definition.execution_mode === 'dag') return definition.display_stages;
  // 自主执行的工具由 Agent 按状态选择，不能继续套用固定 DAG 的业务阶段。
  return [{
    stage_key: 'agent_network',
    label: '主 Agent 自主执行',
    description: '根据目标和当前状态选择工具，检查结果并调整下一步行动',
    node_keys: ['agent_network'],
  }];
}

export function networkActivity(run: AgentRun | null, node: AgentNodeRun) {
  const checkpoint = objectValue(node.sdk_state.action_checkpoint);
  const projection = objectValue(checkpoint.projection);
  const action = objectValue(projection.current_action);
  const toolKey = stringValue(action.tool_key);
  const children = Object.entries(objectValue(projection.saved_child_counts))
    .filter((entry): entry is [string, number] => typeof entry[1] === 'number' && entry[1] > 0)
    .map(([key, count]) => ({ key, name: executionName(run, 'agents', key), count }));
  const completedChildCount = projection.current_action_completed_child_count === undefined
    ? null
    : readCount(projection.current_action_completed_child_count, '专业任务已验收数量');
  return {
    action: toolKey ? {
      key: toolKey,
      name: executionName(run, 'tools', toolKey),
      status: stringValue(action.status),
      completedChildCount,
    } : null,
    children,
  };
}

export function nodeProgress(node: AgentNodeRun): string | null {
  const checkpoint = objectValue(node.sdk_state.action_checkpoint);
  if (node.node_type === 'agent_network') {
    const projection = parseGenerationProjection(checkpoint.projection);
    const parts = [`第 ${node.attempt} 次尝试`];
    if (projection) {
      const progress = projection.progress;
      parts.push(`覆盖目标 ${progress.covered_items}/${progress.coverage_items}`);
      parts.push(`用例 ${progress.cases}/${progress.target_cases}`);
      parts.push(`待处理 ${progress.open_issues} 项`);
      parts.push(`待确认 ${progress.pending_human} 项`);
      parts.push(`独立评审 ${progress.review_checked_cases} 条（${progress.review_current ? '当前版本有效' : '当前版本待复核'}）`);
      if (projection.candidate_case_count > 0) parts.push(`候选 ${projection.candidate_case_count} 条`);
    } else {
      parts.push(Object.keys(checkpoint).length === 0 && ['pending', 'running'].includes(node.status)
        ? '进度尚未记录' : '历史未提供该进度');
    }
    const budget = objectValue(checkpoint.budget);
    const charged = objectValue(budget.charged);
    const limits = objectValue(budget.limits);
    if (typeof charged.attempted_requests === 'number' && typeof limits.attempted_requests === 'number') {
      parts.push(`模型请求 ${charged.attempted_requests}/${limits.attempted_requests}`);
    }
    return parts.join(' · ');
  }
  const completed = Number(node.output_payload.completed_count ?? 0);
  const total = Number(node.output_payload.total_count ?? 0);
  if (!Number.isFinite(total) || total < 1) return null;
  return `已完成 ${Math.max(0, completed)} / ${total} 个分配任务`;
}
