export type GenerationModeEvent = {
  kind: 'generation_mode';
  mode?: string;
  biz_keys?: string[];
  current_biz_key?: string;
};

export type GenerationStageEvent = {
  kind: 'generation_stage';
  stage?: string;
  case_count?: number;
};

export type BizKeyPassStageEvent = {
  kind: 'biz_key_pass_stage';
  biz_key?: string;
  stage?: string;
  case_count?: number;
};

export type CoverageRuleDiagnostic = {
  rule_id?: string;
  covered?: boolean;
  coverage_types?: string[];
  missing_types?: string[];
  rule_text?: string;
  biz_key?: string;
};

export type CoverageResult = {
  total_rules?: number;
  covered_rules?: number;
  missing_rules?: number;
  rule_diagnostics?: CoverageRuleDiagnostic[];
};

export type CoverageCheckEvent = {
  kind: 'coverage_check';
  data?: CoverageResult;
};

export type GenerationPersistedEvent = {
  kind: 'generation_persisted';
  generation_id?: number;
  project_id?: number;
  request_id?: string;
};

export type GenDiagSummaryEvent = {
  kind: 'gen_diag';
  generated_count?: number;
  expected_count?: number;
  mode?: string;
};

export type GenerationConvergenceEvent = {
  kind: 'generation_convergence';
  candidate_count_before_review?: number;
  review_selected_count?: number;
  final_count?: number;
};

export type ReviewDecisionSummaryEvent = {
  kind: 'review_decision_summary';
  candidate_total?: number;
  retained_total?: number;
  dropped_total?: number;
  flow_order?: string[];
  flow_labels?: Record<string, string>;
  flow_stage_breakdown?: Record<string, number>;
  flow_missing_stages?: string[];
  flow_missing_stage_count?: number;
  flow_misordered_count?: number;
  flow_governance_applied?: boolean;
  flow_reordered?: boolean;
  scenario_duplicate_cluster_count?: number;
  scenario_duplicate_case_count?: number;
  scenario_duplicate_pruned_count?: number;
  fact_profile_source?: string;
  fact_profile_confidence?: number;
  fact_profile_confirmed_count?: number;
  fact_profile_forbidden_count?: number;
  fact_profile_pending_count?: number;
  project_profile_source?: string;
  project_profile_confidence?: number;
  drop_by_review_llm_count?: number;
  drop_by_review_gate_count?: number;
  drop_by_pre_gate_dedup_count?: number;
  drop_by_post_review_dedup_count?: number;
};

export type JudgeSummaryEvent = {
  kind: 'judge_summary';
  reject_count?: number;
  pending_count?: number;
  rejected_out_count?: number;
  pending_out_count?: number;
  confirmed_pass_out_count?: number;
  repaired_pass_out_count?: number;
  pass_count?: number;
  repairable_count?: number;
};

export type JudgeDecisionTableEvent = {
  kind: 'judge_decision_table';
  rows?: Array<Record<string, unknown>>;
  row_count?: number;
  row_count_total?: number;
  row_count_reject_pending?: number;
  rows_scope?: string;
  row_evidence_incomplete?: boolean;
};

export type GenerationSummaryEvent = {
  kind: 'generation_summary';
  final_count?: number;
  status?: string;
};

export type GenerationContextCompressionEvent = {
  kind: 'generation_context_compression';
  compression_ratio?: number;
  retained_chunk_count?: number;
  relevance_distribution?: Record<string, unknown>;
};

export type FeedbackControlStateEvent = {
  kind: 'feedback_control_state';
  control_state_applied?: boolean;
  generation_coverage_mode?: string;
  fact_profile_source?: string;
  fact_profile_confidence?: number;
  fact_profile_confirmed_count?: number;
  fact_profile_pending_count?: number;
  fact_profile_forbidden_count?: number;
  project_profile_source?: string;
  project_profile_confidence?: number;
  project_profile_flow_count?: number;
  must_cover_rules_count?: number;
  quality_fix_hints_count?: number;
  preferred_patterns_count?: number;
  forbidden_patterns_count?: number;
  source_meta?: Record<string, unknown>;
};

export type GenerationQualityLedgerEvent = {
  kind: 'generation_quality_ledger';
  generation_id?: number;
  generation_mode?: string;
  final_count?: number;
  quality_assessment?: string;
  stop_reason?: string[];
  coverage?: Record<string, unknown>;
  funnel?: Record<string, unknown>;
  review?: Record<string, unknown>;
  judge?: Record<string, unknown>;
  context?: Record<string, unknown>;
  control?: Record<string, unknown>;
};

export type ReviewDecisionTableCompactEvent = {
  kind: 'review_decision_table_compact';
  rows?: Array<Record<string, unknown>>;
  row_count?: number;
};

export type MemoryFabricDiagEvent = {
  kind: 'memory_fabric_diag';
  [key: string]: unknown;
};

export type StreamBatchTokenUsageEvent = {
  kind: 'stream_batch_token_usage';
  batch_index?: number;
  total_batches?: number;
  attempt?: number;
  requested_count?: number;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  token_source?: string;
  estimate_method?: string;
  model?: string;
  current_biz_key?: string;
  request_id?: string;
  multi_pass?: boolean;
  generation_mode?: string;
};

export type GenDiagEvent =
  | GenerationModeEvent
  | GenerationStageEvent
  | BizKeyPassStageEvent
  | CoverageCheckEvent
  | GenerationPersistedEvent
  | GenDiagSummaryEvent
  | GenerationConvergenceEvent
  | ReviewDecisionSummaryEvent
  | JudgeSummaryEvent
  | JudgeDecisionTableEvent
  | GenerationSummaryEvent
  | GenerationContextCompressionEvent
  | FeedbackControlStateEvent
  | GenerationQualityLedgerEvent
  | ReviewDecisionTableCompactEvent
  | MemoryFabricDiagEvent
  | StreamBatchTokenUsageEvent;

const VALID_KINDS = new Set([
  'generation_mode',
  'generation_stage',
  'biz_key_pass_stage',
  'coverage_check',
  'generation_persisted',
  'gen_diag',
  'generation_convergence',
  'review_decision_summary',
  'judge_summary',
  'judge_decision_table',
  'generation_summary',
  'generation_context_compression',
  'feedback_control_state',
  'generation_quality_ledger',
  'review_decision_table_compact',
  'memory_fabric_diag',
  'stream_batch_token_usage',
]);

export function parseGenDiagEvent(input: unknown): GenDiagEvent | null {
  if (!input) return null;

  if (typeof input === 'object') {
    const obj = input as Record<string, unknown>;
    const kind = String(obj.kind || '').trim();
    if (!VALID_KINDS.has(kind)) return null;
    return obj as GenDiagEvent;
  }

  if (typeof input !== 'string') return null;
  const text = input.trim();
  if (!text) return null;

  const idx = text.indexOf('GEN_DIAG:');
  if (idx >= 0) {
    const segments = text.split('GEN_DIAG:').slice(1);
    for (const segment of segments) {
      const payload = normalizeDiagPayloadText(segment);
      const parsed = parseGenDiagEvent(safeParseJson(payload));
      if (parsed) return parsed;
    }
  }

  const normalizedText = normalizeDiagPayloadText(text);
  if (normalizedText.startsWith('{') && normalizedText.endsWith('}')) {
    return parseGenDiagEvent(safeParseJson(normalizedText));
  }

  return null;
}

function normalizeDiagPayloadText(text: string): string {
  let value = String(text || '').trim();

  // Backward compatibility: some stream chunks ended with literal "\\n".
  while (value.endsWith('\\n') || value.endsWith('\\r\\n')) {
    if (value.endsWith('\\r\\n')) {
      value = value.slice(0, -4).trim();
      continue;
    }
    value = value.slice(0, -2).trim();
  }

  return value;
}

function safeParseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}
