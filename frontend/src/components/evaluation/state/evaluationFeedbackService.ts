import { api } from '../../../utils/api';

export type EvaluationFeedbackRecord = {
  id: number;
  project_id: number;
  origin_run_id: number;
  revision: string;
  status: 'pending' | 'active' | 'revoked';
  confirmed_at: string | null;
  revocation_reason: string;
  content: {
    kind: 'test_case_evaluation_difference';
    category: string;
    text: string;
    difference_text: string;
    evaluation_run_id: number;
    source_run_id: number;
    input_hashes: Record<string, string>;
    applicability?: string;
    invalidation_condition?: string;
  };
  evidence_refs: Record<string, unknown>[];
};

export type FeedbackConfirmation = {
  revision: string;
  text: string;
  applicability: string;
  invalidation_condition: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isId(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0;
}

function parseFeedback(value: unknown, projectId: number, sourceRunId: number): EvaluationFeedbackRecord {
  if (!isRecord(value) || !isRecord(value.content)) throw new Error('项目反馈记录格式无效');
  const content = value.content;
  if (!isId(value.id) || value.project_id !== projectId || value.origin_run_id !== sourceRunId
    || value.source_kind !== 'validated_feedback' || value.scope !== 'project'
    || value.scope_id !== String(projectId) || typeof value.revision !== 'string' || !value.revision
    || !['pending', 'active', 'revoked'].includes(String(value.status))
    || content.kind !== 'test_case_evaluation_difference'
    || typeof content.category !== 'string' || typeof content.text !== 'string'
    || typeof content.difference_text !== 'string' || !isId(content.evaluation_run_id)
    || content.source_run_id !== sourceRunId || !isRecord(content.input_hashes)
    || !Object.values(content.input_hashes).every((hash) => typeof hash === 'string')
    || !Array.isArray(value.evidence_refs) || !value.evidence_refs.every(isRecord)
    || (content.applicability !== undefined && typeof content.applicability !== 'string')
    || (content.invalidation_condition !== undefined && typeof content.invalidation_condition !== 'string')
    || (value.confirmed_at !== null && typeof value.confirmed_at !== 'string')
    || typeof value.revocation_reason !== 'string'
  ) throw new Error('反馈的项目、运行身份或内容契约无效，请重新加载');
  return value as unknown as EvaluationFeedbackRecord;
}

export async function listEvaluationFeedback(projectId: number, sourceRunId: number) {
  const query = new URLSearchParams({
    project_id: String(projectId),
    source_kind: 'validated_feedback',
    origin_run_id: String(sourceRunId),
  });
  const response = await api.get<unknown>(`/api/agents/context-records?${query.toString()}`);
  if (!isRecord(response) || !Array.isArray(response.items)) throw new Error('项目反馈列表格式无效');
  return response.items
    .filter((item) => isRecord(item) && isRecord(item.content)
      && item.content.kind === 'test_case_evaluation_difference')
    .map((item) => parseFeedback(item, projectId, sourceRunId));
}

export async function confirmEvaluationFeedback(
  record: EvaluationFeedbackRecord,
  confirmation: FeedbackConfirmation,
): Promise<EvaluationFeedbackRecord> {
  const value = await api.post<unknown>(`/api/agents/context-records/${record.id}/confirm`, confirmation);
  return parseFeedback(value, record.project_id, record.origin_run_id);
}

export async function dismissEvaluationFeedback(record: EvaluationFeedbackRecord, reason: string) {
  const action = record.status === 'active' ? 'revoke' : 'reject';
  const value = await api.post<unknown>(`/api/agents/context-records/${record.id}/${action}`, {
    revision: record.revision,
    reason,
  });
  return parseFeedback(value, record.project_id, record.origin_run_id);
}
