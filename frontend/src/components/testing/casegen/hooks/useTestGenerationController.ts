import { useEffect, useMemo, useRef, useState } from 'react';
import { cleanStreamingContent } from '../../../test-generation/streamContent';
import { useRagDebugStore } from '../../../test-generation/debug/debugStore';
import { api } from '../../../../utils/api';
import { extractFirstJsonArray, getErrorText, getUniqueCaseCount, translateError } from './testGenerationCaseUtils';
import { normalizeHistoryCases } from './useTestGenerationGeneration.helpers';
import { useTestGenerationFileHandlers } from './useTestGenerationFileHandlers';
import { useTestGenerationGeneration } from './useTestGenerationGeneration';
import { useTestGenerationPersistence } from './useTestGenerationPersistence';
import { useTestGenerationResultActions } from './useTestGenerationResultActions';
import type { TestGenerationMode, TestGenerationProps } from '../../../test-generation/types';

const readStoredString = (key: string, fallback = '') => {
  if (typeof window === 'undefined') return fallback;
  return window.localStorage.getItem(key) ?? fallback;
};

const readStoredNumber = (key: string, fallback: number) => {
  const value = Number(readStoredString(key, String(fallback)));
  return Number.isFinite(value) ? value : fallback;
};

const readStoredJSON = <T,>(key: string, fallback: T) => {
  try {
    const raw = readStoredString(key, '');
    return raw ? JSON.parse(raw) as T : fallback;
  } catch {
    return fallback;
  }
};

const getProjectKey = (projectId: number | null, base: string) => (projectId ? `${base}_${projectId}` : base);
const SAMPLE_POOL_FEEDBACK_STORAGE_KEY = 'tg_enable_sample_pool_feedback';

type ResultSource = 'none' | 'streaming_preview' | 'final_persisted';
type GenerationErrorInsight = {
  title: string;
  details: string[];
};
type GenerationFunnelMetrics = {
  rawPreviewCount: number;
  reviewCandidateCount: number | null;
  reviewSelectedCount: number | null;
  judgeInputCount: number | null;
  judgeRejectedOrPendingCount: number | null;
  finalCount: number;
};
type ExecutionSuiteResponse = {
  case_count?: number;
  suite_count?: number;
  runnable_suite_count?: number;
  linear_executable?: boolean;
  execution_readiness?: string;
  main_suite_id?: string;
  warnings?: unknown[];
};
type GenerationOptimizeResponse = {
  status?: string;
  message?: string;
  generation_id?: number;
  source_generation_id?: number;
  cases?: any[];
  generated_result?: any;
  diagnostics?: unknown[];
  case_quality_gate?: Record<string, unknown>;
  persistence_gate?: Record<string, unknown>;
  optimization_summary?: Record<string, unknown>;
};

const QUALITY_GATE_REASON_LABELS: Record<string, string> = {
  final_count_below_min_acceptable: '最终保留数量低于最低可接受值',
  quality_score_critical: '质量评分过低',
  judge_rejected_above_threshold: 'Judge 拒绝数量超过阈值',
  final_scenario_duplicates_above_threshold: '最终重复用例过多',
  final_flow_misordered_above_threshold: '最终流程顺序异常过多',
  reasoning_leakage_detected: '结果中混入了推理痕迹',
  role_mismatch_above_threshold: '角色错配过多',
};
const SOFT_QUALITY_GATE_REASONS = new Set(['final_count_below_min_acceptable']);

function safeNumber(value: unknown): number | null {
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

function formatQualityReason(reason: string): string {
  const key = String(reason || '').trim();
  return QUALITY_GATE_REASON_LABELS[key] || key || '未知原因';
}

function summarizeRejectClusters(input: unknown): string | null {
  if (!input || typeof input !== 'object') return null;
  const entries = Object.entries(input as Record<string, unknown>)
    .map(([key, value]) => ({ key: String(key || '').trim(), count: safeNumber(value) ?? 0 }))
    .filter((item) => item.key && item.count > 0)
    .sort((a, b) => b.count - a.count)
    .slice(0, 3);
  if (!entries.length) return null;
  return entries.map((item) => `${item.key}×${item.count}`).join('，');
}

function normalizeFailureReasons(input: unknown): string[] {
  return Array.isArray(input)
    ? input.map((item) => String(item || '').trim()).filter(Boolean)
    : [];
}

function hasFailedQualityGate(input: unknown): boolean {
  if (!input || typeof input !== 'object') return false;
  const gate = input as Record<string, unknown>;
  const failureReasons = normalizeFailureReasons(gate.failure_reasons ?? gate.failed_checks);
  const hardFailureReasons = failureReasons.filter((reason) => !SOFT_QUALITY_GATE_REASONS.has(reason));
  if (gate.passed === false && gate.blocked !== true) return hardFailureReasons.length > 0;
  return hardFailureReasons.includes('quality_score_critical') || hardFailureReasons.length > 0;
}

function hasLowQualityLedger(input: unknown): boolean {
  if (!input || typeof input !== 'object') return false;
  const ledger = input as Record<string, unknown>;
  if (hasFailedQualityGate(ledger.case_quality_gate)) return true;
  const grade = String(ledger.quality_score_grade || '').trim().toLowerCase();
  if (grade === 'critical' || grade === 'low') return true;
  const score = Number(ledger.quality_score);
  return Number.isFinite(score) && score <= 60;
}

function hasFailedPersistenceGate(input: unknown): boolean {
  if (!input || typeof input !== 'object') return false;
  const gate = input as Record<string, unknown>;
  if (gate.passed === false || gate.blocked === true) return true;
  if (String(gate.failure_code || '').trim()) return true;
  const execution = gate.execution_plan_validation;
  if (execution && typeof execution === 'object' && (execution as Record<string, unknown>).passed === false) {
    return true;
  }
  return false;
}

function shouldOpenConfigForError(...messages: unknown[]): boolean {
  const text = messages.map((item) => String(item || '')).join(' ');
  return [
    '401',
    'Invalid API-key',
    'Invalid API key',
    'QUOTA',
    'quota',
    'API Key not set',
    'api key not set',
    'Unauthorized',
  ].some((token) => text.includes(token));
}

export function useTestGenerationController({ projectId, isActive = true, onLog, onGenerated, onGenerationComplete, onError }: TestGenerationProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const protoInputRef = useRef<HTMLInputElement>(null);
  const uploadZoneRef = useRef<HTMLDivElement>(null);
  const lastFinalSyncKeyRef = useRef<string>('');
  const ingestDebugEvent = useRagDebugStore((s) => s.ingestDiag);
  const debugStoreProjectId = useRagDebugStore((s) => s.projectId);
  const resetDebugStateForProject = useRagDebugStore((s) => s.resetForProject);
  const setResultDebugState = useRagDebugStore((s) => s.setResultState);
  const setExecutionSuiteDebugState = useRagDebugStore((s) => s.setExecutionSuiteState);
  const genDiag = useRagDebugStore((s) => s.genDiag);
  const generationConvergence = useRagDebugStore((s) => s.generationConvergence);
  const reviewDecisionSummary = useRagDebugStore((s) => s.reviewDecisionSummary);
  const judgeSummary = useRagDebugStore((s) => s.judgeSummary);
  const generationSummary = useRagDebugStore((s) => s.generationSummary);
  const generationQualityLedger = useRagDebugStore((s) => s.generationQualityLedger);
  const caseQualityGate = useRagDebugStore((s) => s.caseQualityGate);
  const persistenceGate = useRagDebugStore((s) => s.persistenceGate);
  const judgeDecisionTableRows = useRagDebugStore((s) => s.judgeDecisionTableRows);
  const judgeDecisionTableMeta = useRagDebugStore((s) => s.judgeDecisionTableMeta);
  const reviewDecisionTableCompactRows = useRagDebugStore((s) => s.reviewDecisionTableCompactRows);

  const initialTextResult = readStoredJSON(getProjectKey(projectId, 'tg_text_result'), null);
  const initialFileResult = readStoredJSON(getProjectKey(projectId, 'tg_file_result'), null);

  const [mode, setMode] = useState<TestGenerationMode>(() => (readStoredString('tg_mode') as TestGenerationMode) || 'text');
  const [requirement, setRequirement] = useState(() => readStoredString(getProjectKey(projectId, 'tg_requirement')) || '');
  const [file, setFile] = useState<File | null>(null);
  const [docType, setDocType] = useState(() => readStoredString('tg_docType') || 'requirement');
  const [protoFile, setProtoFile] = useState<File | null>(null);
  const [force, setForce] = useState(false);
  const [enableSamplePoolFeedback, setEnableSamplePoolFeedback] = useState(() => readStoredString(SAMPLE_POOL_FEEDBACK_STORAGE_KEY, 'true') !== 'false');
  const [isDragActive, setIsDragActive] = useState(false);
  const [compress, setCompress] = useState(() => readStoredString('tg_compress') === 'true');
  const [expectedCount, setExpectedCount] = useState(() => readStoredNumber(getProjectKey(projectId, 'tg_expectedCount'), 20));
  const [appendCount, setAppendCount] = useState(() => readStoredNumber(getProjectKey(projectId, 'tg_appendCount'), 10));
  const [loading, setLoading] = useState(false);
  const [pollStatus, setPollStatus] = useState('');
  const [textResult, setTextResult] = useState<any>(() => initialTextResult);
  const [textStreamingParsedResult, setTextStreamingParsedResult] = useState<any>(null);
  const [textFinalResult, setTextFinalResult] = useState<any>(() => initialTextResult);
  const [textResultSource, setTextResultSource] = useState<ResultSource>(() => (initialTextResult ? 'final_persisted' : 'none'));
  const [textGenerationId, setTextGenerationId] = useState<number | null>(null);
  const [textIsFinalResultLoaded, setTextIsFinalResultLoaded] = useState<boolean>(() => Boolean(initialTextResult));
  const [textStreamingContent, setTextStreamingContent] = useState(() => readStoredString(getProjectKey(projectId, 'tg_text_streaming_content')));
  const [fileResult, setFileResult] = useState<any>(() => initialFileResult);
  const [fileStreamingParsedResult, setFileStreamingParsedResult] = useState<any>(null);
  const [fileFinalResult, setFileFinalResult] = useState<any>(() => initialFileResult);
  const [fileResultSource, setFileResultSource] = useState<ResultSource>(() => (initialFileResult ? 'final_persisted' : 'none'));
  const [fileGenerationId, setFileGenerationId] = useState<number | null>(null);
  const [fileIsFinalResultLoaded, setFileIsFinalResultLoaded] = useState<boolean>(() => Boolean(initialFileResult));
  const [fileStreamingContent, setFileStreamingContent] = useState(() => readStoredString(getProjectKey(projectId, 'tg_file_streaming_content')));
  const [savedFileName, setSavedFileName] = useState(() => readStoredString(getProjectKey(projectId, 'tg_savedFileName')));
  const [error, setError] = useState<string | null>(null);
  const [showDuplicateModal, setShowDuplicateModal] = useState(false);
  const [duplicateData, setDuplicateData] = useState<any>(null);
  const [showHint, setShowHint] = useState(true);
  const [toastMsg, setToastMsg] = useState<string | null>(null);
  const [toastType, setToastType] = useState<'success' | 'error'>('error');
  const [optimizingGeneration, setOptimizingGeneration] = useState(false);

  const result = mode === 'text'
    ? (textFinalResult ?? textStreamingParsedResult ?? textResult)
    : (fileFinalResult ?? fileStreamingParsedResult ?? fileResult);
  const streamingContent = mode === 'text' ? textStreamingContent : fileStreamingContent;
  const resultSource = mode === 'text' ? textResultSource : fileResultSource;
  const generationId = mode === 'text' ? textGenerationId : fileGenerationId;
  const isFinalResultLoaded = mode === 'text' ? textIsFinalResultLoaded : fileIsFinalResultLoaded;
  const previewResult = mode === 'text' ? textStreamingParsedResult : fileStreamingParsedResult;
  const finalResult = mode === 'text' ? textFinalResult : fileFinalResult;
  const parsedStream = useMemo(() => extractFirstJsonArray(cleanStreamingContent(streamingContent)), [streamingContent]);
  const previewCaseCount = useMemo(() => getUniqueCaseCount(previewResult), [previewResult]);
  const finalCaseCount = useMemo(() => getUniqueCaseCount(finalResult), [finalResult]);
  const resultCaseCount = useMemo(() => getUniqueCaseCount(result), [result]);
  const displayCaseCount = useMemo(() => {
    if (resultSource === 'final_persisted' && isFinalResultLoaded && finalCaseCount > 0) return finalCaseCount;
    if (resultSource === 'streaming_preview' && previewCaseCount > 0) return previewCaseCount;
    return resultCaseCount;
  }, [resultSource, isFinalResultLoaded, finalCaseCount, previewCaseCount, resultCaseCount]);
  const hasJsonInResultBox = useMemo(() => Boolean(
    (Array.isArray(result) && result.length > 0) ||
    (result && typeof result === 'object') ||
    (Array.isArray(parsedStream) && parsedStream.length > 0)
  ), [result, parsedStream]);
  const currentTotal = useMemo(() => hasJsonInResultBox ? displayCaseCount : 0, [hasJsonInResultBox, displayCaseCount]);
  const targetTotal = expectedCount + appendCount;
  const isLimitReached = hasJsonInResultBox && currentTotal >= targetTotal;
  const stats = useMemo(() => ({ count: displayCaseCount }), [displayCaseCount]);
  const funnelMetrics = useMemo<GenerationFunnelMetrics>(() => {
    const reviewCandidateCountRaw = Number(
      reviewDecisionSummary?.candidate_total ?? generationConvergence?.candidate_count_before_review
    );
    const reviewSelectedCountRaw = Number(
      generationConvergence?.review_selected_count ?? reviewDecisionSummary?.retained_total
    );
    const rejectedOut = Number(judgeSummary?.rejected_out_count ?? judgeSummary?.reject_count);
    const pendingOut = Number(judgeSummary?.pending_out_count ?? judgeSummary?.pending_count);
    const judgeRejectedOrPendingRaw = rejectedOut + pendingOut;
    const ledgerJudge = generationQualityLedger?.judge && typeof generationQualityLedger.judge === 'object'
      ? generationQualityLedger.judge as Record<string, unknown>
      : {};
    const passCount = Number(judgeSummary?.confirmed_pass_out_count ?? judgeSummary?.pass_count);
    const repairableCount = Number(judgeSummary?.repairable_count ?? judgeSummary?.repaired_pass_out_count);
    const judgeInputFallback = (Number.isFinite(passCount) ? passCount : 0)
      + (Number.isFinite(repairableCount) ? repairableCount : 0)
      + (Number.isFinite(rejectedOut) ? rejectedOut : 0)
      + (Number.isFinite(pendingOut) ? pendingOut : 0);
    const judgeInputRaw = Number(ledgerJudge.total ?? judgeInputFallback);
    const finalCountRaw = Number(generationSummary?.final_count ?? genDiag?.generated_count ?? displayCaseCount);

    return {
      rawPreviewCount: Number.isFinite(previewCaseCount) ? previewCaseCount : 0,
      reviewCandidateCount: Number.isFinite(reviewCandidateCountRaw) ? reviewCandidateCountRaw : null,
      reviewSelectedCount: Number.isFinite(reviewSelectedCountRaw) ? reviewSelectedCountRaw : null,
      judgeInputCount: Number.isFinite(judgeInputRaw) ? judgeInputRaw : null,
      judgeRejectedOrPendingCount: Number.isFinite(judgeRejectedOrPendingRaw) ? judgeRejectedOrPendingRaw : null,
      finalCount: Number.isFinite(finalCountRaw) ? finalCountRaw : displayCaseCount,
    };
  }, [
    reviewDecisionSummary,
    generationConvergence,
    judgeSummary,
    generationQualityLedger,
    generationSummary,
    genDiag,
    previewCaseCount,
    displayCaseCount,
  ]);
  const errorInsight = useMemo<GenerationErrorInsight | null>(() => {
    if (!error || !String(error).includes('LOW_QUALITY_GENERATED_CASES')) return null;

    const effectiveCaseQualityGate = caseQualityGate || generationQualityLedger?.case_quality_gate;
    const caseQualityMetrics = effectiveCaseQualityGate?.metrics && typeof effectiveCaseQualityGate.metrics === 'object'
      ? effectiveCaseQualityGate.metrics as Record<string, unknown>
      : {};
    const caseQualityFailureReasons = Array.isArray(effectiveCaseQualityGate?.failure_reasons)
      ? effectiveCaseQualityGate.failure_reasons.map((item: unknown) => String(item).trim()).filter(Boolean)
      : [];
    const ledgerJudge = generationQualityLedger?.judge && typeof generationQualityLedger.judge === 'object'
      ? generationQualityLedger.judge as Record<string, unknown>
      : {};
    const ledgerFunnel = generationQualityLedger?.funnel && typeof generationQualityLedger.funnel === 'object'
      ? generationQualityLedger.funnel as Record<string, unknown>
      : {};

    const finalCount = safeNumber(caseQualityMetrics.final_count ?? generationSummary?.final_count);
    const minAcceptableFinal = safeNumber(caseQualityMetrics.min_acceptable_final);
    const qualityScore = safeNumber(caseQualityMetrics.quality_score ?? generationQualityLedger?.quality_score);
    const qualityGrade = String(caseQualityMetrics.quality_score_grade ?? generationQualityLedger?.quality_score_grade ?? '').trim();
    const judgeRejected = safeNumber(
      caseQualityMetrics.judge_rejected_count
      ?? judgeSummary?.rejected_out_count
      ?? judgeSummary?.reject_count
      ?? ledgerJudge.rejected_out_count
    );
    const duplicateCount = safeNumber(
      caseQualityMetrics.final_scenario_duplicate_case_count
      ?? reviewDecisionSummary?.final_scenario_duplicate_case_count
    );
    const lowQualityDroppedCount = safeNumber(ledgerFunnel.low_quality_dropped_count);
    const lowQualityExamples = Array.isArray(ledgerFunnel.low_quality_dropped_examples)
      ? ledgerFunnel.low_quality_dropped_examples
        .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
        .map((item) => String(item.case_id || '').trim())
        .filter(Boolean)
        .slice(0, 5)
      : [];
    const rejectClusters = summarizeRejectClusters(ledgerJudge.reason_clusters);

    const details: string[] = [];
    if (caseQualityFailureReasons.length) {
      details.push(`失败原因：${caseQualityFailureReasons.map(formatQualityReason).join('、')}`);
    }
    if (finalCount !== null || minAcceptableFinal !== null) {
      details.push(`最终保留 ${finalCount ?? '-'} 条，最低要求 ${minAcceptableFinal ?? '-'} 条。`);
    }
    if (qualityScore !== null) {
      details.push(`质量评分 ${qualityScore}${qualityGrade ? `（${qualityGrade}）` : ''}。`);
    }
    if (judgeRejected !== null) {
      details.push(`Judge 拒绝/淘汰 ${judgeRejected} 条。`);
    }
    if (duplicateCount !== null) {
      details.push(`最终重复用例 ${duplicateCount} 条。`);
    }
    if (lowQualityDroppedCount !== null && lowQualityDroppedCount > 0) {
      details.push(`低质量预期结果被过滤 ${lowQualityDroppedCount} 条。`);
    }
    if (lowQualityExamples.length) {
      details.push(`低质量样例：${lowQualityExamples.join('、')}。`);
    }
    if (rejectClusters) {
      details.push(`主要拒绝类型：${rejectClusters}。`);
    }

    return {
      title: '生成结果未通过质量闸门（LOW_QUALITY_GENERATED_CASES）',
      details,
    };
  }, [
    error,
    caseQualityGate,
    generationQualityLedger,
    generationSummary?.final_count,
    generationQualityLedger?.quality_score,
    generationQualityLedger?.quality_score_grade,
    judgeSummary?.rejected_out_count,
    judgeSummary?.reject_count,
    reviewDecisionSummary?.final_scenario_duplicate_case_count,
  ]);
  const canOptimizeGeneration = useMemo(() => {
    if (loading || optimizingGeneration) return false;
    const hasSignal = Boolean(
      errorInsight
      || hasFailedQualityGate(caseQualityGate)
      || hasLowQualityLedger(generationQualityLedger)
      || hasFailedPersistenceGate(persistenceGate)
      || (error && String(error).includes('execution_plan_failed'))
    );
    if (!hasSignal) return false;
    if (generationId && resultSource === 'final_persisted' && isFinalResultLoaded) return true;
    return resultSource === 'streaming_preview' && hasJsonInResultBox && displayCaseCount > 0;
  }, [
    generationId,
    loading,
    optimizingGeneration,
    resultSource,
    isFinalResultLoaded,
    errorInsight,
    caseQualityGate,
    generationQualityLedger,
    persistenceGate,
    error,
    hasJsonInResultBox,
    displayCaseCount,
  ]);

  useEffect(() => {
    if (!isActive) setToastMsg(null);
  }, [isActive]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(SAMPLE_POOL_FEEDBACK_STORAGE_KEY, enableSamplePoolFeedback ? 'true' : 'false');
  }, [enableSamplePoolFeedback]);

  useEffect(() => {
    // The test generation page is keyed by projectId, so component refs do not survive
    // project switches. Keep ownership in the persisted debug store itself.
    if (debugStoreProjectId !== projectId) {
      resetDebugStateForProject(projectId);
    }
  }, [projectId, debugStoreProjectId, resetDebugStateForProject]);

  useEffect(() => {
    setResultDebugState({
      mode,
      resultSource,
      generationId,
      isFinalResultLoaded,
      previewCaseCount,
      finalCaseCount,
      displayCaseCount,
    });
    // Keep result-source diagnostics aligned with the debug store.
    console.info(`[TG_RESULT_DIAG] resultSource=${resultSource}`);
    console.info(`[TG_RESULT_DIAG] generationId=${generationId ?? 'null'}`);
    console.info(`[TG_RESULT_DIAG] isFinalResultLoaded=${isFinalResultLoaded ? 'true' : 'false'}`);
  }, [
    mode,
    resultSource,
    generationId,
    isFinalResultLoaded,
    previewCaseCount,
    finalCaseCount,
    displayCaseCount,
    setResultDebugState,
  ]);

  useEffect(() => {
    if (!generationId || !isFinalResultLoaded) {
      setExecutionSuiteDebugState({
        generationId: generationId ?? null,
        warnings: [],
      });
      return;
    }

    let cancelled = false;
    void (async () => {
      try {
        const suite = await api.get<ExecutionSuiteResponse>(`/api/test-generations/${generationId}/execution-suite`);
        if (cancelled) return;
        const warnings = Array.isArray(suite?.warnings)
          ? suite.warnings.map((item: unknown) => String(item)).filter(Boolean)
          : [];
        setExecutionSuiteDebugState({
          generationId,
          caseCount: Number(suite?.case_count || 0),
          suiteCount: Number(suite?.suite_count || 0),
          runnableSuiteCount: Number(suite?.runnable_suite_count || 0),
          linearExecutable: Boolean(suite?.linear_executable),
          executionReadiness: String(suite?.execution_readiness || ''),
          mainSuiteId: String(suite?.main_suite_id || ''),
          warnings,
        });
      } catch (err) {
        if (cancelled) return;
        setExecutionSuiteDebugState({
          generationId,
          warnings: [`执行套件诊断加载失败：${err instanceof Error ? err.message : String(err)}`],
        });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [
    generationId,
    isFinalResultLoaded,
    setExecutionSuiteDebugState,
  ]);

  useEffect(() => {
    if (!generationId || !isFinalResultLoaded) return;
    const expectedFinalCount = Number(generationSummary?.final_count);
    if (!Number.isFinite(expectedFinalCount) || expectedFinalCount <= 0) return;
    if (finalCaseCount === expectedFinalCount) return;

    const syncKey = `${mode}:${generationId}:${expectedFinalCount}:${finalCaseCount}`;
    if (lastFinalSyncKeyRef.current === syncKey) return;
    lastFinalSyncKeyRef.current = syncKey;

    let cancelled = false;
    void (async () => {
      try {
        const data = await api.get(`/api/test-generations/${generationId}`);
        if (cancelled) return;
        const syncedCases = normalizeHistoryCases(data);
        if (!syncedCases.length) return;
        const syncedCount = getUniqueCaseCount(syncedCases);
        if (syncedCount !== expectedFinalCount) {
          onLog?.(`Final result sync skipped: fetched ${syncedCount}, expected ${expectedFinalCount}.`);
          return;
        }
        if (mode === 'text') {
          setTextResult(syncedCases);
          setTextFinalResult(syncedCases);
          setTextResultSource('final_persisted');
          setTextIsFinalResultLoaded(true);
          setTextGenerationId(generationId);
        } else {
          setFileResult(syncedCases);
          setFileFinalResult(syncedCases);
          setFileResultSource('final_persisted');
          setFileIsFinalResultLoaded(true);
          setFileGenerationId(generationId);
        }
        onGenerated(syncedCases);
        onLog?.(`Final result re-synced from persisted result (generation_id=${generationId}, cases=${syncedCount}).`);
      } catch (err) {
        onLog?.(`Final result sync failed: ${err instanceof Error ? err.message : String(err)}`);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [
    generationId,
    isFinalResultLoaded,
    generationSummary?.final_count,
    finalCaseCount,
    mode,
    onGenerated,
    onLog,
    setTextResult,
    setTextFinalResult,
    setTextResultSource,
    setTextIsFinalResultLoaded,
    setTextGenerationId,
    setFileResult,
    setFileFinalResult,
    setFileResultSource,
    setFileIsFinalResultLoaded,
    setFileGenerationId,
  ]);

  useTestGenerationPersistence({
    projectId,
    loading,
    mode,
    requirement,
    docType,
    compress,
    expectedCount,
    appendCount,
    textResult,
    textStreamingContent,
    fileResult,
    fileStreamingContent,
    savedFileName,
    setFile,
    setProtoFile,
    setToastType,
    setToastMsg,
  });

  const fileHandlers = useTestGenerationFileHandlers({
    projectId,
    file,
    showHint,
    uploadZoneRef,
    setShowHint,
    setIsDragActive,
    setFile,
    setFileResult,
    setFileStreamingContent,
    setProtoFile,
    setToastType,
    setToastMsg,
    setSavedFileName,
  });

  const generation = useTestGenerationGeneration({
    projectId,
    mode,
    requirement,
    file,
    protoFile,
    docType,
    compress,
    force,
    enableSamplePoolFeedback,
    expectedCount,
    appendCount,
    textResult,
    fileResult,
    textStreamingContent,
    fileStreamingContent,
    setTextResult,
    setFileResult,
    setTextStreamingContent,
    setFileStreamingContent,
    setTextStreamingParsedResult,
    setFileStreamingParsedResult,
    setTextFinalResult,
    setFileFinalResult,
    setTextResultSource,
    setFileResultSource,
    setTextIsFinalResultLoaded,
    setFileIsFinalResultLoaded,
    setTextGenerationId,
    setFileGenerationId,
    setExpectedCount,
    setLoading,
    setError,
    setPollStatus,
    setDuplicateData,
    setShowDuplicateModal,
    duplicateData,
    onLog,
    onGenerated,
    onGenerationComplete,
    onError,
    onDebugEvent: ingestDebugEvent,
    onDebugReset: () => resetDebugStateForProject(projectId),
  });

  const resultActions = useTestGenerationResultActions({
    mode,
    file,
    result,
    streamingContent,
    savedFileName,
    setTextResult,
    setFileResult,
    setTextStreamingContent,
    setFileStreamingContent,
    onLog,
    setToastType,
    setToastMsg,
  });

  const handleClearCurrent = () => {
    resultActions.handleClearCurrent();
    if (mode === 'text') {
      setTextStreamingParsedResult(null);
      setTextFinalResult(null);
      setTextResultSource('none');
      setTextIsFinalResultLoaded(false);
      setTextGenerationId(null);
    } else {
      setFileStreamingParsedResult(null);
      setFileFinalResult(null);
      setFileResultSource('none');
      setFileIsFinalResultLoaded(false);
      setFileGenerationId(null);
    }
  };

  const handleOptimizeGeneration = async () => {
    if (optimizingGeneration) {
      onLog?.('Optimization already running; waiting for current request to finish.');
      return;
    }
    if (loading) {
      onLog?.('Optimization skipped because generation is still running.');
      return;
    }
    const hasPersistedSource = Boolean(generationId && resultSource === 'final_persisted' && isFinalResultLoaded);
    const previewCases = hasPersistedSource ? [] : normalizeHistoryCases({ generated_result: result });
    if (!hasPersistedSource && !projectId) {
      setToastType('error');
      setToastMsg('优化生成失败：请先选择项目');
      return;
    }
    if (!hasPersistedSource && !previewCases.length) {
      setToastType('error');
      setToastMsg('优化生成失败：当前预览结果没有可优化的用例');
      return;
    }
    setOptimizingGeneration(true);
    const optimizeTargetCount = hasPersistedSource ? displayCaseCount : previewCases.length;
    setPollStatus(`正在分批优化 ${optimizeTargetCount || displayCaseCount} 条用例，当前结果会保留到优化成功后再替换...`);
    onLog?.(`Optimization started for ${hasPersistedSource ? `generation_id=${generationId}` : 'streaming preview'}.`);

    try {
      const payload = hasPersistedSource
        ? await api.post<GenerationOptimizeResponse>(`/api/test-generations/${generationId}/optimize`, {
          apply: true,
        })
        : await api.post<GenerationOptimizeResponse>('/api/test-generations/optimize-preview', {
          project_id: projectId,
          requirement_text: requirement || savedFileName || '',
          cases: previewCases,
          diagnostics: {
            generationQualityLedger,
            caseQualityGate,
            persistenceGate,
            generationSummary,
            reviewDecisionSummary,
            judgeSummary,
            judgeDecisionTableRows,
            judgeDecisionTableMeta,
            reviewDecisionTableCompactRows,
            error,
          },
          apply: true,
          max_new_cases: 24,
        });
      const optimizedCases = Array.isArray(payload?.cases)
        ? normalizeHistoryCases({ generated_result: payload.cases })
        : normalizeHistoryCases(payload);
      if (!optimizedCases.length) {
        throw new Error(payload?.message || '优化接口未返回可展示的用例');
      }

      const nextGenerationId = Number(payload?.generation_id || (hasPersistedSource ? generationId : 0));
      if (!Number.isFinite(nextGenerationId) || nextGenerationId <= 0) {
        throw new Error('优化结果未返回落库 ID');
      }
      if (mode === 'text') {
        setTextResult(optimizedCases);
        setTextStreamingParsedResult(null);
        setTextFinalResult(optimizedCases);
        setTextResultSource('final_persisted');
        setTextIsFinalResultLoaded(true);
        setTextGenerationId(nextGenerationId);
        setTextStreamingContent(JSON.stringify(optimizedCases, null, 2));
      } else {
        setFileResult(optimizedCases);
        setFileStreamingParsedResult(null);
        setFileFinalResult(optimizedCases);
        setFileResultSource('final_persisted');
        setFileIsFinalResultLoaded(true);
        setFileGenerationId(nextGenerationId);
        setFileStreamingContent(JSON.stringify(optimizedCases, null, 2));
      }

      const diagnostics = Array.isArray(payload?.diagnostics) ? payload.diagnostics : [];
      for (const event of diagnostics) {
        ingestDebugEvent(event);
      }
      if (payload?.case_quality_gate) {
        ingestDebugEvent({ kind: 'case_quality_gate', ...payload.case_quality_gate });
      }
      if (payload?.persistence_gate) {
        ingestDebugEvent({ kind: 'persistence_gate', ...payload.persistence_gate });
      }

      setError(null);
      setToastType('success');
      setToastMsg(`优化生成完成，已生成 ${optimizedCases.length} 条用例`);
      setPollStatus('优化生成完成');
      onGenerated(optimizedCases);
      onGenerationComplete?.();
      onLog?.(`Optimization completed (source_generation_id=${payload?.source_generation_id ?? generationId ?? 'preview'}, generation_id=${nextGenerationId}, cases=${optimizedCases.length}).`);
    } catch (err) {
      const raw = getErrorText(err);
      const friendly = await translateError(err);
      const isTimeout = /optimization_model_timeout|timed out|timeout/i.test(`${raw} ${friendly}`);
      const message = isTimeout
        ? '优化生成调用模型超时，已保留当前结果；请稍后重试，系统会使用更小的诊断上下文。'
        : (raw || friendly || '优化生成失败');
      setToastType('error');
      setToastMsg(`优化生成失败：${message}`);
      setPollStatus(isTimeout ? '优化生成超时，已保留原结果' : '优化生成失败，已保留原结果');
      onLog?.(`Optimization failed: ${message}`);
      if (raw && raw !== friendly) {
        onLog?.(`Optimization failed(raw): ${raw}`);
      }
      if (shouldOpenConfigForError(raw, friendly, message)) {
        onError?.(friendly || message);
      }
    } finally {
      setOptimizingGeneration(false);
    }
  };

  return {
    fileInputRef,
    protoInputRef,
    uploadZoneRef,
    mode,
    setMode,
    requirement,
    setRequirement,
    file,
    protoFile,
    isDragActive,
    loading,
    showHint,
    onCloseHint: () => setShowHint(false),
    setShowHint,
    ...fileHandlers,
    docType,
    setDocType,
    compress,
    setCompress,
    expectedCount,
    setExpectedCount,
    appendCount,
    setAppendCount,
    force,
    setForce,
    enableSamplePoolFeedback,
    setEnableSamplePoolFeedback,
    projectId,
    hasJsonInResultBox,
    isLimitReached,
    stats,
    result,
    resultSource,
    generationId,
    isFinalResultLoaded,
    streamingContent,
    previewCaseCount,
    finalCaseCount,
    displayCaseCount,
    funnelMetrics,
    errorInsight,
    canOptimizeGeneration,
    optimizingGeneration,
    handleOptimizeGeneration,
    error,
    setError,
    toastMsg,
    toastType,
    setToastMsg,
    setToastType,
    loadingStatus: pollStatus,
    showDuplicateModal,
    duplicateData,
    statsCount: stats.count,
    ...generation,
    ...resultActions,
    handleClearCurrent,
    currentTotal,
    targetTotal,
  };
}

export type TestGenerationController = ReturnType<typeof useTestGenerationController>;
