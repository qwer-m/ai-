import { api } from '../../utils/api';
import type {
  AgentApproval,
  AgentCatalog,
  AgentDefinition,
  AgentNodeRun,
  AgentRun,
  AgentRunExecutionLimits,
  AgentTool,
  AgentWorkflow,
  RequirementDocumentOption,
  RequirementDocumentParseStatus,
  RequirementDocumentUpload,
  RunStatus,
  WorkflowNode,
  WorkflowDisplayStage,
} from './types';

type JsonObject = Record<string, unknown>;

const RUN_STATUSES: readonly RunStatus[] = [
  'pending',
  'running',
  'waiting_approval',
  'success',
  'failed',
  'cancelled',
];

function readObject(value: unknown, path: string): JsonObject {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error(`${path} 应为对象`);
  }
  return value as JsonObject;
}

function readArray(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${path} 应为数组`);
  return value;
}

function readString(value: unknown, path: string): string {
  if (typeof value !== 'string') throw new Error(`${path} 应为字符串`);
  return value;
}

function readNullableString(value: unknown, path: string): string | null {
  if (value === null) return null;
  return readString(value, path);
}

function readNumber(value: unknown, path: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`${path} 应为有效数字`);
  }
  return value;
}

function readNullableNumber(value: unknown, path: string): number | null {
  if (value === null) return null;
  return readNumber(value, path);
}

function readBoolean(value: unknown, path: string): boolean {
  if (typeof value !== 'boolean') throw new Error(`${path} 应为布尔值`);
  return value;
}

function readRunStatus(value: unknown, path: string): RunStatus {
  const status = readString(value, path);
  if (!RUN_STATUSES.includes(status as RunStatus)) {
    throw new Error(`${path} 包含未知运行状态: ${status}`);
  }
  return status as RunStatus;
}

function readStringArray(value: unknown, path: string): string[] {
  return readArray(value, path).map((item, index) => readString(item, `${path}[${index}]`));
}

function readStringMap(value: unknown, path: string): Record<string, string> {
  const object = readObject(value, path);
  return Object.fromEntries(
    Object.entries(object).map(([key, item]) => [key, readString(item, `${path}.${key}`)]),
  );
}

function parseAgentDefinition(value: unknown, path: string): AgentDefinition {
  const object = readObject(value, path);
  return {
    id: readNumber(object.id, `${path}.id`),
    project_id: readNullableNumber(object.project_id, `${path}.project_id`),
    agent_key: readString(object.agent_key, `${path}.agent_key`),
    name: readString(object.name, `${path}.name`),
    description: readString(object.description, `${path}.description`),
    instructions: readString(object.instructions, `${path}.instructions`),
    model: readString(object.model, `${path}.model`),
    output_schema: readObject(object.output_schema, `${path}.output_schema`),
    runtime_config: readObject(object.runtime_config, `${path}.runtime_config`),
    version: readNumber(object.version, `${path}.version`),
    enabled: readBoolean(object.enabled, `${path}.enabled`),
    builtin: readBoolean(object.builtin, `${path}.builtin`),
    created_at: readString(object.created_at, `${path}.created_at`),
    updated_at: readString(object.updated_at, `${path}.updated_at`),
  };
}

function parseAgentTool(value: unknown, path: string): AgentTool {
  const object = readObject(value, path);
  return {
    id: readNumber(object.id, `${path}.id`),
    project_id: readNumber(object.project_id, `${path}.project_id`),
    tool_key: readString(object.tool_key, `${path}.tool_key`),
    name: readString(object.name, `${path}.name`),
    description: readString(object.description, `${path}.description`),
    input_schema: readObject(object.input_schema, `${path}.input_schema`),
    output_schema: readObject(object.output_schema, `${path}.output_schema`),
    risk_level: readString(object.risk_level, `${path}.risk_level`),
    requires_approval: readBoolean(object.requires_approval, `${path}.requires_approval`),
    enabled: readBoolean(object.enabled, `${path}.enabled`),
    builtin: readBoolean(object.builtin, `${path}.builtin`),
    created_at: readString(object.created_at, `${path}.created_at`),
    updated_at: readString(object.updated_at, `${path}.updated_at`),
  };
}

function parseWorkflowNode(value: unknown, path: string): WorkflowNode {
  const object = readObject(value, path);
  const nodeType = readString(object.node_type, `${path}.node_type`);
  if (nodeType !== 'agent' && nodeType !== 'agent_network'
    && nodeType !== 'agent_map' && nodeType !== 'tool') {
    throw new Error(`${path}.node_type 包含未知节点类型: ${nodeType}`);
  }
  return {
    node_key: readString(object.node_key, `${path}.node_key`),
    node_type: nodeType,
    reference_key: readString(object.reference_key, `${path}.reference_key`),
    depends_on: readStringArray(object.depends_on, `${path}.depends_on`),
    max_attempts: readNumber(object.max_attempts, `${path}.max_attempts`),
    time_budget_seconds: object.time_budget_seconds == null
      ? null
      : readNumber(object.time_budget_seconds, `${path}.time_budget_seconds`),
    input_mapping: readStringMap(object.input_mapping, `${path}.input_mapping`),
    map_config: object.map_config == null
      ? null
      : readObject(object.map_config, `${path}.map_config`),
  };
}

function parseWorkflowDisplayStage(value: unknown, path: string): WorkflowDisplayStage {
  const object = readObject(value, path);
  return {
    stage_key: readString(object.stage_key, `${path}.stage_key`),
    label: readString(object.label, `${path}.label`),
    description: readString(object.description, `${path}.description`),
    node_keys: readStringArray(object.node_keys, `${path}.node_keys`),
  };
}

function parseAgentWorkflow(value: unknown, path: string): AgentWorkflow {
  const object = readObject(value, path);
  const definition = readObject(object.definition, `${path}.definition`);
  const executionMode = definition.execution_mode == null
    ? 'dag'
    : readString(definition.execution_mode, `${path}.definition.execution_mode`);
  if (executionMode !== 'dag' && executionMode !== 'agent_network') {
    throw new Error(`${path}.definition.execution_mode 包含未知执行模式: ${executionMode}`);
  }
  const parsedDefinition = executionMode === 'agent_network'
    ? {
        execution_mode: 'agent_network' as const,
        entry_agent_key: readString(
          definition.entry_agent_key,
          `${path}.definition.entry_agent_key`,
        ),
        input_schema: readObject(definition.input_schema, `${path}.definition.input_schema`),
        max_attempts: readNumber(definition.max_attempts, `${path}.definition.max_attempts`),
        time_budget_seconds: definition.time_budget_seconds == null
          ? null
          : readNumber(
              definition.time_budget_seconds,
              `${path}.definition.time_budget_seconds`,
            ),
        required_artifact_key: definition.required_artifact_key == null
          ? null
          : readString(
              definition.required_artifact_key,
              `${path}.definition.required_artifact_key`,
            ),
      }
    : {
        execution_mode: 'dag' as const,
        nodes: readArray(definition.nodes, `${path}.definition.nodes`).map((node, index) =>
          parseWorkflowNode(node, `${path}.definition.nodes[${index}]`),
        ),
        output_node_key: readString(
          definition.output_node_key,
          `${path}.definition.output_node_key`,
        ),
        input_schema: readObject(definition.input_schema, `${path}.definition.input_schema`),
        display_stages: definition.display_stages == null
          ? []
          : readArray(definition.display_stages, `${path}.definition.display_stages`).map(
              (stage, index) => parseWorkflowDisplayStage(
                stage,
                `${path}.definition.display_stages[${index}]`,
              ),
            ),
      };
  return {
    id: readNumber(object.id, `${path}.id`),
    project_id: readNumber(object.project_id, `${path}.project_id`),
    workflow_key: readString(object.workflow_key, `${path}.workflow_key`),
    name: readString(object.name, `${path}.name`),
    description: readString(object.description, `${path}.description`),
    version: readNumber(object.version, `${path}.version`),
    enabled: readBoolean(object.enabled, `${path}.enabled`),
    builtin: readBoolean(object.builtin, `${path}.builtin`),
    definition: parsedDefinition,
    created_at: readString(object.created_at, `${path}.created_at`),
    updated_at: readString(object.updated_at, `${path}.updated_at`),
  };
}

function parseAgentNodeRun(value: unknown, path: string): AgentNodeRun {
  const object = readObject(value, path);
  const nodeType = readString(object.node_type, `${path}.node_type`);
  if (nodeType !== 'agent' && nodeType !== 'agent_network'
    && nodeType !== 'agent_map' && nodeType !== 'tool') {
    throw new Error(`${path}.node_type 包含未知节点类型: ${nodeType}`);
  }
  return {
    id: readNumber(object.id, `${path}.id`),
    node_key: readString(object.node_key, `${path}.node_key`),
    node_type: nodeType,
    agent_definition_id: readNullableNumber(
      object.agent_definition_id,
      `${path}.agent_definition_id`,
    ),
    status: readRunStatus(object.status, `${path}.status`),
    attempt: readNumber(object.attempt, `${path}.attempt`),
    input_payload: readObject(object.input_payload, `${path}.input_payload`),
    output_payload: readObject(object.output_payload, `${path}.output_payload`),
    sdk_state: readObject(object.sdk_state, `${path}.sdk_state`),
    error_message: readString(object.error_message, `${path}.error_message`),
    started_at: readNullableString(object.started_at, `${path}.started_at`),
    finished_at: readNullableString(object.finished_at, `${path}.finished_at`),
    created_at: readString(object.created_at, `${path}.created_at`),
  };
}

function parseAgentApproval(value: unknown, path: string): AgentApproval {
  const object = readObject(value, path);
  const status = readString(object.status, `${path}.status`);
  if (status !== 'pending' && status !== 'approved' && status !== 'rejected') {
    throw new Error(`${path}.status 包含未知审批状态: ${status}`);
  }
  return {
    id: readNumber(object.id, `${path}.id`),
    run_id: readNumber(object.run_id, `${path}.run_id`),
    node_run_id: readNumber(object.node_run_id, `${path}.node_run_id`),
    status,
    request_payload: readObject(object.request_payload, `${path}.request_payload`),
    decision_payload: readObject(object.decision_payload, `${path}.decision_payload`),
    requested_at: readString(object.requested_at, `${path}.requested_at`),
    decided_at: readNullableString(object.decided_at, `${path}.decided_at`),
  };
}

function parseAgentRun(value: unknown, path: string): AgentRun {
  const object = readObject(value, path);
  return {
    id: readNumber(object.id, `${path}.id`),
    run_attempt: readNumber(object.run_attempt, `${path}.run_attempt`),
    project_id: readNumber(object.project_id, `${path}.project_id`),
    workflow_definition_id: readNumber(object.workflow_definition_id, `${path}.workflow_definition_id`),
    status: readRunStatus(object.status, `${path}.status`),
    current_node_key: readNullableString(object.current_node_key, `${path}.current_node_key`),
    input_payload: readObject(object.input_payload, `${path}.input_payload`),
    run_context: readObject(object.run_context, `${path}.run_context`),
    output_payload: readObject(object.output_payload, `${path}.output_payload`),
    error_message: readString(object.error_message, `${path}.error_message`),
    parent_run_id: readNullableNumber(object.parent_run_id, `${path}.parent_run_id`),
    task_id: readNullableString(object.task_id, `${path}.task_id`),
    created_at: readString(object.created_at, `${path}.created_at`),
    started_at: readNullableString(object.started_at, `${path}.started_at`),
    finished_at: readNullableString(object.finished_at, `${path}.finished_at`),
    nodes: readArray(object.nodes, `${path}.nodes`).map((node, index) =>
      parseAgentNodeRun(node, `${path}.nodes[${index}]`),
    ),
    approvals: readArray(object.approvals, `${path}.approvals`).map((approval, index) =>
      parseAgentApproval(approval, `${path}.approvals[${index}]`),
    ),
  };
}

function parseCatalog(value: unknown): AgentCatalog {
  const object = readObject(value, 'Agent 目录响应');
  return {
    agents: readArray(object.agents, 'Agent 目录响应.agents').map((agent, index) =>
      parseAgentDefinition(agent, `Agent 目录响应.agents[${index}]`),
    ),
    tools: readArray(object.tools, 'Agent 目录响应.tools').map((tool, index) =>
      parseAgentTool(tool, `Agent 目录响应.tools[${index}]`),
    ),
    workflows: readArray(object.workflows, 'Agent 目录响应.workflows').map((workflow, index) =>
      parseAgentWorkflow(workflow, `Agent 目录响应.workflows[${index}]`),
    ),
  };
}

function parseRunEnvelope(value: unknown, path: string): { run: AgentRun } {
  const object = readObject(value, path);
  return { run: parseAgentRun(object.run, `${path}.run`) };
}

function parseOptionalRunEnvelope(value: unknown, path: string): { run: AgentRun | null } {
  const object = readObject(value, path);
  return {
    run: object.run == null ? null : parseAgentRun(object.run, `${path}.run`),
  };
}

export const getAgentCatalog = async (projectId: number): Promise<AgentCatalog> =>
  parseCatalog(await api.get<unknown>(`/api/agents/catalog?project_id=${projectId}`));

export const getAgentRun = async (runId: number): Promise<{ run: AgentRun }> =>
  parseRunEnvelope(await api.get<unknown>(`/api/agents/runs/${runId}`), 'Agent 运行详情响应');

export const getActiveAgentRun = async (projectId: number): Promise<{ run: AgentRun | null }> =>
  parseOptionalRunEnvelope(
    await api.get<unknown>(`/api/agents/runs/active?project_id=${projectId}`),
    '当前 Agent 运行响应',
  );

export const createAgentRun = (payload: {
  project_id: number;
  workflow_key: string;
  input_payload: Record<string, unknown>;
  execution_limits?: AgentRunExecutionLimits;
}): Promise<{ run: AgentRun; status: 'created' | 'already_active' }> =>
  api.post<unknown>('/api/agents/runs', payload).then((response) => {
    const object = readObject(response, '创建 Agent 运行响应');
    const status = readString(object.status, '创建 Agent 运行响应.status');
    if (status !== 'created' && status !== 'already_active') {
      throw new Error(`创建 Agent 运行响应.status 包含未知状态: ${status}`);
    }
    return {
      run: parseAgentRun(object.run, '创建 Agent 运行响应.run'),
      status,
    };
  });

export const cancelAgentRun = async (runId: number): Promise<{ run: AgentRun; status: string }> => {
  const response = readObject(
    await api.post<unknown>(`/api/agents/runs/${runId}/cancel`, {}),
    '取消 Agent 运行响应',
  );
  return {
    run: parseAgentRun(response.run, '取消 Agent 运行响应.run'),
    status: readString(response.status, '取消 Agent 运行响应.status'),
  };
};

export const resetAgentRunAttempt = async (
  runId: number,
): Promise<{ run: AgentRun; status: string }> => {
  const response = readObject(
    await api.post<unknown>(`/api/agents/runs/${runId}/reset-attempt`, {}),
    '重置 Agent 执行次数响应',
  );
  return {
    run: parseAgentRun(response.run, '重置 Agent 执行次数响应.run'),
    status: readString(response.status, '重置 Agent 执行次数响应.status'),
  };
};

export const decideAgentApproval = async (
  approvalId: number,
  approved: boolean,
  reason = '',
): Promise<{ approval_id: number; status: string }> => {
  const response = readObject(
    await api.post<unknown>(`/api/agents/approvals/${approvalId}/decision`, { approved, reason }),
    'Agent 审批响应',
  );
  return {
    approval_id: readNumber(response.approval_id, 'Agent 审批响应.approval_id'),
    status: readString(response.status, 'Agent 审批响应.status'),
  };
};

type RequirementDocumentPage = {
  documents: RequirementDocumentOption[];
  totalPages: number;
};

const parseRequirementDocumentPage = (value: unknown): RequirementDocumentPage => {
  const response = readObject(value, '需求文档列表响应');
  const pagination = readObject(response.pagination, '需求文档列表响应.pagination');
  const documents = readArray(response.documents, '需求文档列表响应.documents').map((item, index) => {
    const path = `需求文档列表响应.documents[${index}]`;
    const document = readObject(item, path);
    return {
      id: readNumber(document.id, `${path}.id`),
      filename: readString(document.filename, `${path}.filename`),
      doc_type: readString(document.doc_type, `${path}.doc_type`),
      content_preview: readString(document.content_preview, `${path}.content_preview`),
      linked_test_case_count: readArray(document.linked_test_cases, `${path}.linked_test_cases`).length,
      parse_status: readString(document.parse_status, `${path}.parse_status`) as RequirementDocumentOption['parse_status'],
      parse_error: readNullableString(document.parse_error, `${path}.parse_error`),
    };
  });
  return {
    documents,
    totalPages: readNumber(pagination.total_pages, '需求文档列表响应.pagination.total_pages'),
  };
};

export const listRequirementDocuments = async (projectId: number): Promise<RequirementDocumentOption[]> => {
  const pageSize = 200;
  const firstPage = parseRequirementDocumentPage(await api.get<unknown>(
    `/api/knowledge-list?project_id=${projectId}&doc_type=requirement&page=1&page_size=${pageSize}`,
  ));
  if (firstPage.totalPages <= 1) return firstPage.documents;
  const remainingPages = await Promise.all(
    Array.from({ length: firstPage.totalPages - 1 }, (_, index) => index + 2).map(async (page) =>
      parseRequirementDocumentPage(await api.get<unknown>(
        `/api/knowledge-list?project_id=${projectId}&doc_type=requirement&page=${page}&page_size=${pageSize}`,
      )).documents,
    ),
  );
  return [firstPage.documents, ...remainingPages].flat();
};

export const getRequirementDocumentParseStatus = async (
  documentId: number,
): Promise<RequirementDocumentParseStatus> => {
  const response = readObject(
    await api.get<unknown>(`/api/knowledge/${documentId}/parse-status`),
    '需求文档解析状态响应',
  );
  return {
    id: readNumber(response.id, '需求文档解析状态响应.id'),
    parse_status: readString(
      response.parse_status,
      '需求文档解析状态响应.parse_status',
    ) as RequirementDocumentOption['parse_status'],
    parse_error: readNullableString(response.parse_error, '需求文档解析状态响应.parse_error'),
  };
};

export const uploadRequirementDocument = async (
  projectId: number,
  file: File,
): Promise<RequirementDocumentUpload> => {
  const form = new FormData();
  form.append('file', file);
  form.append('project_id', String(projectId));
  form.append('doc_type', 'requirement');
  const response = readObject(
    await api.upload<unknown>('/api/upload-knowledge', form),
    '上传需求文档响应',
  );
  return {
    success: readBoolean(response.success, '上传需求文档响应.success'),
    id: readNumber(response.id, '上传需求文档响应.id'),
    filename: readString(response.filename, '上传需求文档响应.filename'),
    parse_status: readString(
      response.parse_status,
      '上传需求文档响应.parse_status',
    ) as RequirementDocumentOption['parse_status'],
  };
};
