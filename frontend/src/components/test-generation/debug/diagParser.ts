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

const VALID_KINDS = new Set(['generation_mode', 'generation_stage', 'biz_key_pass_stage', 'coverage_check', 'generation_persisted']);

// 中文注释：统一解析 GEN_DIAG 输入，兼容对象、日志行字符串、纯 JSON 字符串。
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

  // 中文注释：优先处理 "GEN_DIAG:{...}" 格式。
  const idx = text.indexOf('GEN_DIAG:');
  if (idx >= 0) {
    const maybeJson = text.slice(idx + 'GEN_DIAG:'.length).trim();
    return parseGenDiagEvent(safeParseJson(maybeJson));
  }

  // 中文注释：其次处理整行就是 JSON 的情况。
  if (text.startsWith('{') && text.endsWith('}')) {
    return parseGenDiagEvent(safeParseJson(text));
  }

  return null;
}

function safeParseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}
