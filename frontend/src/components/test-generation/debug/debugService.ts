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

export async function deletePrioritySamplePoolItem(projectId: number, payload: { generation_id?: number | null; sample_id: string }) {
  return api.post<PrioritySamplePoolPayload>(`/api/test-generations/projects/${projectId}/priority-sample-pool/delete-sample`, {
    generation_id: payload.generation_id ?? null,
    sample_id: payload.sample_id,
  });
}

