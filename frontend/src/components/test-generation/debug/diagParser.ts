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

export type GenDiagEvent =
  | GenerationModeEvent
  | GenerationStageEvent
  | BizKeyPassStageEvent
  | CoverageCheckEvent
  | GenerationPersistedEvent;

const VALID_KINDS = new Set([
  'generation_mode',
  'generation_stage',
  'biz_key_pass_stage',
  'coverage_check',
  'generation_persisted',
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
