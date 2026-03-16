import { api } from '../../utils/api';
import type {
  PipelineAgentConfig,
  PipelineRun,
  ProjectAgentDefaultsResponse,
  WorkflowTraceItem,
} from './model';

export async function fetchProjectAgentDefaults(projectId: number) {
  return api.get<ProjectAgentDefaultsResponse>(`/api/projects/${projectId}/pipeline-agent-defaults`);
}

export async function updateProjectAgentDefaults(projectId: number, agent: PipelineAgentConfig) {
  return api.put<ProjectAgentDefaultsResponse>(`/api/projects/${projectId}/pipeline-agent-defaults`, { agent });
}

export async function listPipelineRuns(projectId: number, limit = 30) {
  return api.get<{ items: PipelineRun[] }>(`/api/pipeline/runs?project_id=${projectId}&limit=${limit}`);
}

export async function fetchPipelineRun(runId: number) {
  return api.get<{ run: PipelineRun }>(`/api/pipeline/runs/${runId}`);
}

export async function createPipelineRun(payload: Record<string, unknown>) {
  return api.post<{ run: PipelineRun }>('/api/pipeline/runs', payload);
}

export async function resumePipelineRun(runId: number) {
  return api.post<{ run: PipelineRun; message: string }>(`/api/pipeline/runs/${runId}/resume`, {});
}

export async function retryPipelineRun(runId: number, fromStage: string) {
  return api.post<{ run: PipelineRun; message: string }>(`/api/pipeline/runs/${runId}/retry`, {
    from_stage: fromStage,
  });
}

export async function fetchPipelineTraces(runId: number, limit = 300) {
  return api.get<{ items: WorkflowTraceItem[] }>(`/api/pipeline/runs/${runId}/traces?limit=${limit}`);
}
