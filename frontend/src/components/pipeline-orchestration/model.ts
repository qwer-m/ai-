export type StageKey = 'test_generation' | 'ui_automation' | 'api_automation' | 'evaluation';
export type StageStatus = 'idle' | 'pending' | 'running' | 'success' | 'failed' | 'skipped';

export type StageState = {
  status: StageStatus;
  message: string;
  started_at?: string | null;
  ended_at?: string | null;
};

export type PipelineRun = {
  id: number;
  project_id: number;
  status: 'pending' | 'running' | 'success' | 'failed';
  current_stage?: string | null;
  stage_states: Record<StageKey, StageState>;
  artifacts: Record<string, unknown>;
  error_message?: string;
  retry_of_run_id?: number | null;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
};

export type PipelineAgentConfig = {
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

export type ProjectAgentDefaultsResponse = {
  project_id: number;
  agent: PipelineAgentConfig;
  source: 'default' | 'saved';
  updated_at?: string;
};

export type WorkflowTraceItem = {
  id: number;
  created_at?: string;
  kind: string;
  stage: string;
  action: string;
  details: Record<string, unknown>;
};

export type PipelineRunStatus = PipelineRun['status'] | 'idle';

export const stageOrder: StageKey[] = ['test_generation', 'ui_automation', 'api_automation', 'evaluation'];

export const stageLabel: Record<StageKey, string> = {
  test_generation: '测试用例生成',
  ui_automation: 'UI 自动化',
  api_automation: '接口自动化',
  evaluation: '质量评估',
};

export const testTypeOptions = ['Functional', 'Performance', 'Security', 'Boundary'];

export const testTypeLabel: Record<string, string> = {
  Functional: '功能',
  Performance: '性能',
  Security: '安全',
  Boundary: '边界',
};

export const stageStatusLabel: Record<StageStatus, string> = {
  idle: '未开始',
  pending: '排队中',
  running: '运行中',
  success: '成功',
  failed: '失败',
  skipped: '已跳过',
};

export const runStatusLabel: Record<PipelineRunStatus, string> = {
  pending: '排队中',
  running: '运行中',
  success: '成功',
  failed: '失败',
  idle: '未开始',
};

export const traceKindLabel: Record<string, string> = {
  planner: '规划',
  reviewer: '评审',
  executor: '执行',
  orchestrator: '编排',
  system: '系统',
};

export const defaultAgentConfig: PipelineAgentConfig = {
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

export function buildInitialStages(): Record<StageKey, StageState> {
  return {
    test_generation: { status: 'idle', message: '' },
    ui_automation: { status: 'idle', message: '' },
    api_automation: { status: 'idle', message: '' },
    evaluation: { status: 'idle', message: '' },
  };
}

export function statusVariant(status: StageStatus): string {
  if (status === 'success') return 'success';
  if (status === 'failed') return 'danger';
  if (status === 'running') return 'primary';
  if (status === 'pending') return 'warning';
  if (status === 'skipped') return 'secondary';
  return 'light';
}

export function toText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === null || value === undefined) return '';
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
