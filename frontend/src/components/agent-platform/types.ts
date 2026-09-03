export type RunStatus = 'pending' | 'running' | 'waiting_approval' | 'success' | 'failed' | 'cancelled';

export type AgentRunExecutionLimits = {
  max_requests?: number;
  max_input_tokens?: number;
  max_output_tokens?: number;
  max_total_tokens?: number;
};

export type AgentDefinition = {
  id: number;
  // 全局内置模板不绑定具体项目；项目自定义覆盖才有 project_id。
  project_id: number | null;
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
  node_type: 'agent' | 'agent_network' | 'agent_map' | 'tool';
  reference_key: string;
  depends_on: string[];
  max_attempts: number;
  time_budget_seconds?: number | null;
  input_mapping: Record<string, string>;
  map_config: Record<string, unknown> | null;
};

export type WorkflowDisplayStage = {
  stage_key: string;
  label: string;
  description: string;
  node_keys: string[];
};

export type WorkflowGraphDefinition = {
  execution_mode: 'dag';
  nodes: WorkflowNode[];
  output_node_key: string;
  input_schema: Record<string, unknown>;
  display_stages: WorkflowDisplayStage[];
};

export type AgentNetworkDefinition = {
  execution_mode: 'agent_network';
  entry_agent_key: string;
  input_schema: Record<string, unknown>;
  max_attempts: number;
  time_budget_seconds: number | null;
  required_artifact_key: string | null;
};

export type AgentWorkflowDefinition = WorkflowGraphDefinition | AgentNetworkDefinition;

export type AgentWorkflow = {
  id: number;
  project_id: number;
  workflow_key: string;
  name: string;
  description: string;
  version: number;
  enabled: boolean;
  builtin: boolean;
  definition: AgentWorkflowDefinition;
  created_at: string;
  updated_at: string;
};

export type AgentNodeRun = {
  id: number;
  node_key: string;
  node_type: 'agent' | 'agent_network' | 'agent_map' | 'tool';
  agent_definition_id: number | null;
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
  run_attempt: number;
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
  parse_status: 'pending' | 'parsing' | 'success' | 'failed';
  parse_error: string | null;
};

export type RequirementDocumentUpload = {
  success: boolean;
  id: number;
  filename: string;
  parse_status: RequirementDocumentOption['parse_status'];
};

export type RequirementDocumentParseStatus = {
  id: number;
  parse_status: RequirementDocumentOption['parse_status'];
  parse_error: string | null;
};
