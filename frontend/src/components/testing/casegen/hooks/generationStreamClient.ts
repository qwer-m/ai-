import { api, getAuthHeaders } from '../../../../utils/api';

type GenerationStreamFormDataInput = {
  projectId: number;
  isText: boolean;
  requirement: string;
  file: File | null;
  protoFile: File | null;
  docType: string;
  compress: boolean;
  expectedCount: number;
  force: boolean;
  appendMode: boolean;
  previousGenerationId: number | null;
  enableSamplePoolFeedback: boolean;
};

export function buildGenerationStreamFormData({
  projectId,
  isText,
  requirement,
  file,
  protoFile,
  docType,
  compress,
  expectedCount,
  force,
  appendMode,
  previousGenerationId,
  enableSamplePoolFeedback,
}: GenerationStreamFormDataInput): FormData {
  const formData = new FormData();
  formData.append('project_id', String(projectId));
  formData.append('doc_type', isText ? 'requirement' : docType);
  formData.append('compress', String(compress));
  formData.append('expected_count', String(expectedCount));
  formData.append('force', String(force));
  formData.append('enable_sample_pool_feedback', String(enableSamplePoolFeedback));
  if (appendMode) formData.append('append', 'true');
  if (appendMode && previousGenerationId) {
    formData.append('previous_generation_id', String(previousGenerationId));
  }

  if (isText) {
    formData.append('requirement_text', requirement);
  } else if (file) {
    formData.append('file', file);
    if (docType === 'incomplete' && protoFile) {
      formData.append('prototype_file', protoFile);
    }
  }

  return formData;
}

export async function openGenerationStream(
  formData: FormData,
): Promise<ReadableStreamDefaultReader<Uint8Array>> {
  const response = await api.raw('/api/generate-tests-stream', {
    method: 'POST',
    headers: { ...getAuthHeaders() },
    body: formData,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({} as { error?: string }));
    throw new Error(errorData.error || `HTTP ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error('No response body');
  return reader;
}
