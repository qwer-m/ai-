import { normalizeAutomationEvaluationReport } from '../evaluation/state/evaluationService';
import type { AutomationEvaluationReport } from '../evaluation/state/types';
import type { ImportedUITestCase } from '../testing/ui/ImportedTestCasesView';

export type UIExecutionStatus = 'pending' | 'running' | 'success' | 'failed';
export type UIAutomationType = 'web' | 'app';

export interface NaturalLanguageOperation {
  name: string;
  description: string;
  steps: string[];
}

export interface ExportInfo {
  project_id: number;
  project_name: string;
  root_dir: string;
  script_path: string;
  page_paths: string[];
  manifest_path: string;
  operation_slug: string;
}

export interface UIExecutionSummary {
  id: number;
  task_description: string;
  status: UIExecutionStatus;
  created_at: string;
  automation_type: UIAutomationType;
}

interface AutomationEvaluationArtifact {
  run_id: number;
  project_id: number;
  evaluation_type: 'ui';
  source_execution_id: number | null;
  result: AutomationEvaluationReport;
}

export interface UIExecutionDetail {
  id: number;
  task_description: string;
  generated_script: string | null;
  execution_result: string | null;
  status: UIExecutionStatus;
  screenshot_paths: string[];
  evaluation: {
    run_id: number;
    artifact: AutomationEvaluationArtifact;
  } | null;
  created_at: string;
  automation_type: UIAutomationType;
  url: string | null;
  app_info: string | null;
}

export interface DetectResponse {
  success: boolean;
  message: string;
  data: {
    validated_url?: string;
    app_id?: string;
    activity?: string;
    device_id?: string;
    appium_url?: string;
  };
}

interface SemanticVerification {
  passed: boolean;
  reason: string;
  screenshot_path: string | null;
}

interface ProcessExecutionResult {
  status: 'success' | 'failed';
  stdout: string;
  stderr: string;
  execution_id: number;
  evaluation_run_id: number | null;
  screenshot_paths: string[];
  device_readiness: {
    device_id: string;
    awake: true;
    keyguard_dismissed: true;
  } | null;
  foreground_app: {
    package: string;
    activity: string;
    full_activity: string;
  } | {
    error: string;
  } | null;
  semantic_verification: SemanticVerification | null;
}

interface SystemExecutionFailure {
  status: 'failed';
  error: string;
  execution_id: number;
}

export type UIExecutionResult = ProcessExecutionResult | SystemExecutionFailure;

export type DirectExecutionResponse = UIExecutionResult & {
  operation: NaturalLanguageOperation;
  export: ExportInfo;
};

export interface NaturalRunResponse {
  script: string;
  operation: NaturalLanguageOperation;
  result: UIExecutionResult;
}

export interface ConvertResponse {
  script: string;
  operation: NaturalLanguageOperation;
  export: ExportInfo;
  test_case_id: number;
}

export interface ImportedCasesResponse {
  filename: string;
  case_count: number;
  parse_strategy: 'spreadsheet_rows' | 'structured_text';
  cases: ImportedUITestCase[];
}

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function hasExactKeys(value: JsonRecord, keys: readonly string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === keys.length
    && keys.every((key) => Object.prototype.hasOwnProperty.call(value, key));
}

function hasOnlyKeys(value: JsonRecord, keys: readonly string[]): boolean {
  return Object.keys(value).every((key) => keys.includes(key));
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0;
}

function isStringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string';
}

function isExecutionStatus(value: unknown): value is UIExecutionStatus {
  return typeof value === 'string' && ['pending', 'running', 'success', 'failed'].includes(value);
}

function isAutomationType(value: unknown): value is UIAutomationType {
  return value === 'web' || value === 'app';
}

function invalidResponse(name: string): never {
  throw new Error(`${name} 返回结构不符合当前后端契约。`);
}

function parseOperation(value: unknown, responseName: string): NaturalLanguageOperation {
  if (
    !isRecord(value)
    || !hasExactKeys(value, ['name', 'description', 'steps'])
    || typeof value.name !== 'string'
    || typeof value.description !== 'string'
    || !isStringList(value.steps)
  ) invalidResponse(responseName);
  return { name: value.name, description: value.description, steps: [...value.steps] };
}

function parseExportInfo(value: unknown, responseName: string): ExportInfo {
  const keys = [
    'project_id',
    'project_name',
    'root_dir',
    'script_path',
    'page_paths',
    'manifest_path',
    'operation_slug',
  ];
  if (
    !isRecord(value)
    || !hasExactKeys(value, keys)
    || !isPositiveInteger(value.project_id)
    || typeof value.project_name !== 'string'
    || typeof value.root_dir !== 'string'
    || typeof value.script_path !== 'string'
    || !isStringList(value.page_paths)
    || typeof value.manifest_path !== 'string'
    || typeof value.operation_slug !== 'string'
  ) invalidResponse(responseName);
  return {
    project_id: value.project_id,
    project_name: value.project_name,
    root_dir: value.root_dir,
    script_path: value.script_path,
    page_paths: [...value.page_paths],
    manifest_path: value.manifest_path,
    operation_slug: value.operation_slug,
  };
}

function parseSemanticVerification(value: unknown, responseName: string): SemanticVerification | null {
  if (value === null) return null;
  if (
    !isRecord(value)
    || !hasExactKeys(value, ['passed', 'reason', 'screenshot_path'])
    || typeof value.passed !== 'boolean'
    || typeof value.reason !== 'string'
    || !isNullableString(value.screenshot_path)
  ) invalidResponse(responseName);
  return {
    passed: value.passed,
    reason: value.reason,
    screenshot_path: value.screenshot_path,
  };
}

function parseDeviceReadiness(value: unknown, responseName: string): ProcessExecutionResult['device_readiness'] {
  if (value === null) return null;
  if (
    !isRecord(value)
    || !hasExactKeys(value, ['device_id', 'awake', 'keyguard_dismissed'])
    || typeof value.device_id !== 'string'
    || value.awake !== true
    || value.keyguard_dismissed !== true
  ) invalidResponse(responseName);
  return { device_id: value.device_id, awake: true, keyguard_dismissed: true };
}

function parseForegroundApp(value: unknown, responseName: string): ProcessExecutionResult['foreground_app'] {
  if (value === null) return null;
  if (!isRecord(value)) invalidResponse(responseName);
  if (hasExactKeys(value, ['error']) && typeof value.error === 'string') return { error: value.error };
  if (
    !hasExactKeys(value, ['package', 'activity', 'full_activity'])
    || typeof value.package !== 'string'
    || typeof value.activity !== 'string'
    || typeof value.full_activity !== 'string'
  ) invalidResponse(responseName);
  return {
    package: value.package,
    activity: value.activity,
    full_activity: value.full_activity,
  };
}

function parseExecutionResult(value: unknown, responseName: string): UIExecutionResult {
  if (!isRecord(value) || value.status !== 'failed' && value.status !== 'success') invalidResponse(responseName);
  if (hasExactKeys(value, ['status', 'error', 'execution_id'])) {
    if (value.status !== 'failed' || typeof value.error !== 'string' || !isPositiveInteger(value.execution_id)) {
      invalidResponse(responseName);
    }
    return { status: 'failed', error: value.error, execution_id: value.execution_id };
  }

  const keys = [
    'status',
    'stdout',
    'stderr',
    'execution_id',
    'evaluation_run_id',
    'screenshot_paths',
    'device_readiness',
    'foreground_app',
    'semantic_verification',
  ];
  if (
    !hasExactKeys(value, keys)
    || typeof value.stdout !== 'string'
    || typeof value.stderr !== 'string'
    || !isPositiveInteger(value.execution_id)
    || value.evaluation_run_id !== null && !isPositiveInteger(value.evaluation_run_id)
    || !isStringList(value.screenshot_paths)
  ) invalidResponse(responseName);
  return {
    status: value.status,
    stdout: value.stdout,
    stderr: value.stderr,
    execution_id: value.execution_id,
    evaluation_run_id: value.evaluation_run_id,
    screenshot_paths: [...value.screenshot_paths],
    device_readiness: parseDeviceReadiness(value.device_readiness, responseName),
    foreground_app: parseForegroundApp(value.foreground_app, responseName),
    semantic_verification: parseSemanticVerification(value.semantic_verification, responseName),
  };
}

function parseAutomationArtifact(value: unknown): AutomationEvaluationArtifact {
  const keys = ['run_id', 'project_id', 'evaluation_type', 'source_execution_id', 'result'];
  if (
    !isRecord(value)
    || !hasExactKeys(value, keys)
    || !isPositiveInteger(value.run_id)
    || !isPositiveInteger(value.project_id)
    || value.evaluation_type !== 'ui'
    || value.source_execution_id !== null && !isPositiveInteger(value.source_execution_id)
  ) invalidResponse('UI 自动化执行详情');
  const result = normalizeAutomationEvaluationReport(value.result);
  if (!result) invalidResponse('UI 自动化执行详情');
  return {
    run_id: value.run_id,
    project_id: value.project_id,
    evaluation_type: 'ui',
    source_execution_id: value.source_execution_id,
    result,
  };
}

export function parseUIExecutionHistory(value: unknown): UIExecutionSummary[] {
  if (!Array.isArray(value)) invalidResponse('UI 自动化执行历史');
  return value.map((item) => {
    if (
      !isRecord(item)
      || !hasExactKeys(item, ['id', 'task_description', 'status', 'created_at', 'automation_type'])
      || !isPositiveInteger(item.id)
      || typeof item.task_description !== 'string'
      || !isExecutionStatus(item.status)
      || typeof item.created_at !== 'string'
      || !isAutomationType(item.automation_type)
    ) invalidResponse('UI 自动化执行历史');
    return {
      id: item.id,
      task_description: item.task_description,
      status: item.status,
      created_at: item.created_at,
      automation_type: item.automation_type,
    };
  });
}

export function parseUIExecutionDetail(value: unknown): UIExecutionDetail {
  const keys = [
    'id',
    'task_description',
    'generated_script',
    'execution_result',
    'status',
    'screenshot_paths',
    'evaluation',
    'created_at',
    'automation_type',
    'url',
    'app_info',
  ];
  if (
    !isRecord(value)
    || !hasExactKeys(value, keys)
    || !isPositiveInteger(value.id)
    || typeof value.task_description !== 'string'
    || !isNullableString(value.generated_script)
    || !isNullableString(value.execution_result)
    || !isExecutionStatus(value.status)
    || value.screenshot_paths !== null && !isStringList(value.screenshot_paths)
    || typeof value.created_at !== 'string'
    || !isAutomationType(value.automation_type)
    || !isNullableString(value.url)
    || !isNullableString(value.app_info)
  ) invalidResponse('UI 自动化执行详情');

  let evaluation: UIExecutionDetail['evaluation'] = null;
  if (value.evaluation !== null) {
    if (
      !isRecord(value.evaluation)
      || !hasExactKeys(value.evaluation, ['run_id', 'artifact'])
      || !isPositiveInteger(value.evaluation.run_id)
    ) invalidResponse('UI 自动化执行详情');
    const artifact = parseAutomationArtifact(value.evaluation.artifact);
    if (artifact.run_id !== value.evaluation.run_id) invalidResponse('UI 自动化执行详情');
    evaluation = { run_id: value.evaluation.run_id, artifact };
  }
  return {
    id: value.id,
    task_description: value.task_description,
    generated_script: value.generated_script,
    execution_result: value.execution_result,
    status: value.status,
    screenshot_paths: value.screenshot_paths === null ? [] : [...value.screenshot_paths],
    evaluation,
    created_at: value.created_at,
    automation_type: value.automation_type,
    url: value.url,
    app_info: value.app_info,
  };
}

export function parseDetectResponse(value: unknown): DetectResponse {
  if (
    !isRecord(value)
    || !hasExactKeys(value, ['success', 'message', 'data'])
    || typeof value.success !== 'boolean'
    || typeof value.message !== 'string'
    || !isRecord(value.data)
    || !hasOnlyKeys(value.data, ['validated_url', 'app_id', 'activity', 'device_id', 'appium_url'])
    || !Object.values(value.data).every((item) => typeof item === 'string')
  ) invalidResponse('UI 自动化环境检测');
  return { success: value.success, message: value.message, data: { ...value.data } };
}

export function parseDirectExecutionResponse(value: unknown): DirectExecutionResponse {
  if (!isRecord(value)) invalidResponse('UI 自动化执行');
  const operation = parseOperation(value.operation, 'UI 自动化执行');
  const exportInfo = parseExportInfo(value.export, 'UI 自动化执行');
  const base = { ...value };
  delete base.operation;
  delete base.export;
  return { ...parseExecutionResult(base, 'UI 自动化执行'), operation, export: exportInfo };
}

export function parseNaturalRunResponse(value: unknown): NaturalRunResponse {
  if (
    !isRecord(value)
    || !hasExactKeys(value, ['script', 'operation', 'result'])
    || typeof value.script !== 'string'
  ) invalidResponse('自然语言 UI 执行');
  return {
    script: value.script,
    operation: parseOperation(value.operation, '自然语言 UI 执行'),
    result: parseExecutionResult(value.result, '自然语言 UI 执行'),
  };
}

export function parseConvertResponse(value: unknown): ConvertResponse {
  if (
    !isRecord(value)
    || !hasExactKeys(value, ['script', 'operation', 'export', 'test_case_id'])
    || typeof value.script !== 'string'
    || !isPositiveInteger(value.test_case_id)
  ) invalidResponse('UI 自动化脚本转化');
  return {
    script: value.script,
    operation: parseOperation(value.operation, 'UI 自动化脚本转化'),
    export: parseExportInfo(value.export, 'UI 自动化脚本转化'),
    test_case_id: value.test_case_id,
  };
}

export function parseImportedCasesResponse(value: unknown): ImportedCasesResponse {
  if (
    !isRecord(value)
    || !hasExactKeys(value, ['filename', 'parse_strategy', 'case_count', 'cases'])
    || typeof value.filename !== 'string'
    || value.parse_strategy !== 'spreadsheet_rows' && value.parse_strategy !== 'structured_text'
    || !Number.isInteger(value.case_count)
    || typeof value.case_count !== 'number'
    || value.case_count < 0
    || !Array.isArray(value.cases)
    || value.case_count !== value.cases.length
  ) invalidResponse('UI 测试用例导入');
  const cases = value.cases.map((item) => {
    const keys = [
      'key',
      'source_index',
      'id',
      'description',
      'test_module',
      'preconditions',
      'steps',
      'test_input',
      'expected_result',
      'priority',
    ];
    if (
      !isRecord(item)
      || !hasExactKeys(item, keys)
      || typeof item.key !== 'string'
      || !isPositiveInteger(item.source_index)
      || typeof item.id !== 'string'
      || typeof item.description !== 'string'
      || typeof item.test_module !== 'string'
      || !isStringList(item.preconditions)
      || !isStringList(item.steps)
      || typeof item.test_input !== 'string'
      || typeof item.expected_result !== 'string'
      || typeof item.priority !== 'string'
    ) invalidResponse('UI 测试用例导入');
    return {
      key: item.key,
      source_index: item.source_index,
      id: item.id,
      description: item.description,
      test_module: item.test_module,
      preconditions: [...item.preconditions],
      steps: [...item.steps],
      test_input: item.test_input,
      expected_result: item.expected_result,
      priority: item.priority,
    };
  });
  return {
    filename: value.filename,
    parse_strategy: value.parse_strategy,
    case_count: value.case_count,
    cases,
  };
}

export function executionFailureMessage(result: UIExecutionResult): string {
  if ('error' in result) return result.error.trim() || 'UI 自动化执行发生系统错误。';
  const semanticReason = result.semantic_verification?.passed === false
    ? result.semantic_verification.reason.trim()
    : '';
  const output = result.stderr.trim() || result.stdout.trim();
  const raw = semanticReason || output;
  if (!raw) return '执行失败，后端未返回错误输出。';
  const lines = raw.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  return lines[lines.length - 1] || raw;
}

export function executionDetailFailureMessage(detail: UIExecutionDetail): string {
  const raw = detail.execution_result?.trim() || '';
  if (!raw) return '执行失败，后端执行记录中没有错误输出。';
  const lines = raw.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  return lines[lines.length - 1] || raw;
}
