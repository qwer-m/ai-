import { api } from '../../../utils/api';

export type PrioritySamplePoolPayload = {
  project_id: number;
  generation_id: number | null;
  samples: any[];
  updated_at?: string | null;
  artifact_doc_id?: number | null;
};

export async function fetchPrioritySamplePool(projectId: number) {
  return api.get<PrioritySamplePoolPayload>(`/api/test-generations/projects/${projectId}/priority-sample-pool`);
}

export async function savePrioritySamplePool(projectId: number, payload: { generation_id?: number | null; samples: any[] }) {
  return api.put<PrioritySamplePoolPayload>(`/api/test-generations/projects/${projectId}/priority-sample-pool`, {
    generation_id: payload.generation_id ?? null,
    samples: Array.isArray(payload.samples) ? payload.samples : [],
  });
}

export async function deletePrioritySamplePoolItem(projectId: number, payload: { generation_id?: number | null; sample_id: string; delete_reason?: string }) {
  return api.post<PrioritySamplePoolPayload>(`/api/test-generations/projects/${projectId}/priority-sample-pool/delete-sample`, {
    generation_id: payload.generation_id ?? null,
    sample_id: payload.sample_id,
    delete_reason: payload.delete_reason ?? '',
  });
}

export async function addPrioritySamplePoolItems(projectId: number, payload: { generation_id?: number | null; samples: any[] }) {
  return api.post<PrioritySamplePoolPayload>(`/api/test-generations/projects/${projectId}/priority-sample-pool/add-samples`, {
    generation_id: payload.generation_id ?? null,
    samples: Array.isArray(payload.samples) ? payload.samples : [],
  });
}

export async function updatePrioritySamplePoolItem(projectId: number, payload: { generation_id?: number | null; sample_id: string; patch: Record<string, unknown> }) {
  return api.post<PrioritySamplePoolPayload>(`/api/test-generations/projects/${projectId}/priority-sample-pool/update-sample`, {
    generation_id: payload.generation_id ?? null,
    sample_id: payload.sample_id,
    patch: payload.patch ?? {},
  });
}

export async function confirmPrioritySamplePoolItem(projectId: number, payload: { generation_id?: number | null; sample_id: string; patch?: Record<string, unknown> }) {
  return api.post<PrioritySamplePoolPayload>(`/api/test-generations/projects/${projectId}/priority-sample-pool/confirm-sample`, {
    generation_id: payload.generation_id ?? null,
    sample_id: payload.sample_id,
    patch: payload.patch ?? {},
  });
}

export async function fetchPrioritySamplePoolConsistency(projectId: number) {
  return api.get<{
    project_id: number;
    generation_id?: number | null;
    json_sample_count: number;
    json_pattern_count: number;
    consistency: Record<string, { json_count?: number; table_count?: number; ok?: boolean } | boolean>;
  }>(`/api/test-generations/projects/${projectId}/priority-sample-pool/consistency`);
}

export async function bulkArchivePrioritySamplePoolItems(projectId: number, payload: { generation_id?: number | null; sample_ids: string[]; delete_reason?: string }) {
  return api.post<PrioritySamplePoolPayload>(`/api/test-generations/projects/${projectId}/priority-sample-pool/bulk-archive`, {
    generation_id: payload.generation_id ?? null,
    sample_ids: Array.isArray(payload.sample_ids) ? payload.sample_ids : [],
    delete_reason: payload.delete_reason ?? '',
  });
}

