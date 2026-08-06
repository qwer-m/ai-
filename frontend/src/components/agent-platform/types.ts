export type RunStatus = 'pending' | 'running' | 'waiting_approval' | 'success' | 'failed' | 'cancelled';

export type AgentRunExecutionLimits = {
  max_requests?: number;
  max_input_tokens?: number;
  max_output_tokens?: number;
  max_total_tokens?: number;
};

export type AgentDefinition = {
  id: number;
  project_id: number;
  agent_key: string;
  name: string;
  description: string;
  instructions: string;
  model: string;
  output_schema: Record<string, unknown>;
  runtime_config: Record<string, unknown>;
  version: number;
  enabled: boolean;
  builtin: boolean;
  created_at: string;
  updated_at: string;
};

export type AgentTool = {
  id: number;
  project_id: number;
  tool_key: string;
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  risk_level: string;
  requires_approval: boolean;
  enabled: boolean;
  builtin: boolean;
  created_at: string;
  updated_at: string;
};

export type WorkflowNode = {
  node_key: string;
  node_type: 'agent' | 'agent_map' | 'tool';
  reference_key: string;
  depends_on: string[];
  max_attempts: number;
  input_mapping: Record<string, string>;
  map_config: Record<string, unknown> | null;
};

export type AgentWorkflow = {
  id: number;
  project_id: number;
  workflow_key: string;
  name: string;
  description: string;
  version: number;
  enabled: boolean;
  builtin: boolean;
  definition: {
    nodes: WorkflowNode[];
    output_node_key: string;
    input_schema: Record<string, unknown>;
  };
  created_at: string;
  updated_at: string;
};

export type AgentNodeRun = {
  id: number;
  node_key: string;
  node_type: 'agent' | 'agent_map' | 'tool';
  status: RunStatus;
  attempt: number;
  input_payload: Record<string, unknown>;
  output_payload: Record<string, unknown>;
  sdk_state: Record<string, unknown>;
  error_message: string;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

export type AgentApproval = {
  id: number;
  run_id: number;
  node_run_id: number;
  status: 'pending' | 'approved' | 'rejected';
  request_payload: Record<string, unknown>;
  decision_payload: Record<string, unknown>;
  requested_at: string;
  decided_at: string | null;
};

export type AgentRun = {
  id: number;
  project_id: number;
  workflow_definition_id: number;
  status: RunStatus;
  current_node_key: string | null;
  input_payload: Record<string, unknown>;
  run_context: Record<string, unknown>;
  output_payload: Record<string, unknown>;
  error_message: string;
  parent_run_id: number | null;
  task_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  nodes: AgentNodeRun[];
  approvals: AgentApproval[];
};

export type AgentRunEvent = {
  id: number;
  run_id: number;
  node_run_id: number | null;
  sequence: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type AgentCatalog = {
  agents: AgentDefinition[];
  tools: AgentTool[];
  workflows: AgentWorkflow[];
};

export type RequirementDocumentOption = {
  id: number;
  filename: string;
  doc_type: string;
  content_preview: string;
  linked_test_case_count: number;
};
