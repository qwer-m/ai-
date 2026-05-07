import { useEffect, useMemo, useRef, useState } from 'react';
import { cleanStreamingContent } from '../../../test-generation/streamContent';
import { useRagDebugStore } from '../../../test-generation/debug/debugStore';
import { api } from '../../../../utils/api';
import { extractFirstJsonArray, getUniqueCaseCount } from './testGenerationCaseUtils';
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
type GenerationFunnelMetrics = {
  rawPreviewCount: number;
  reviewCandidateCount: number | null;
  reviewSelectedCount: number | null;
  judgeRejectedOrPendingCount: number | null;
  finalCount: number;
};

export function useTestGenerationController({ projectId, isActive = true, onLog, onGenerated, onGenerationComplete, onError }: TestGenerationProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const protoInputRef = useRef<HTMLInputElement>(null);
  const uploadZoneRef = useRef<HTMLDivElement>(null);
  const previousProjectIdRef = useRef<number | null | undefined>(undefined);
  const lastFinalSyncKeyRef = useRef<string>('');
  const ingestDebugEvent = useRagDebugStore((s) => s.ingestDiag);
  const resetDebugState = useRagDebugStore((s) => s.reset);
  const setResultDebugState = useRagDebugStore((s) => s.setResultState);
  const genDiag = useRagDebugStore((s) => s.genDiag);
  const generationConvergence = useRagDebugStore((s) => s.generationConvergence);
  const reviewDecisionSummary = useRagDebugStore((s) => s.reviewDecisionSummary);
  const judgeSummary = useRagDebugStore((s) => s.judgeSummary);
  const generationSummary = useRagDebugStore((s) => s.generationSummary);

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
  const [isEstimating, setIsEstimating] = useState(false);
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
    const finalCountRaw = Number(generationSummary?.final_count ?? genDiag?.generated_count ?? displayCaseCount);

    return {
      rawPreviewCount: Number.isFinite(previewCaseCount) ? previewCaseCount : 0,
      reviewCandidateCount: Number.isFinite(reviewCandidateCountRaw) ? reviewCandidateCountRaw : null,
      reviewSelectedCount: Number.isFinite(reviewSelectedCountRaw) ? reviewSelectedCountRaw : null,
      judgeRejectedOrPendingCount: Number.isFinite(judgeRejectedOrPendingRaw) ? judgeRejectedOrPendingRaw : null,
      finalCount: Number.isFinite(finalCountRaw) ? finalCountRaw : displayCaseCount,
    };
  }, [
    reviewDecisionSummary,
    generationConvergence,
    judgeSummary,
    generationSummary,
    genDiag,
    previewCaseCount,
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
    // Reset debug store only when project changes; keep it when this page is temporarily hidden.
    if (previousProjectIdRef.current === undefined) {
      previousProjectIdRef.current = projectId;
      return;
    }
    if (previousProjectIdRef.current !== projectId) {
      resetDebugState();
      previousProjectIdRef.current = projectId;
    }
  }, [projectId, resetDebugState]);

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
    // 涓枃娉ㄩ噴锛氭妸缁撴灉鏉ユ簮璇婃柇鏄庣‘鎵撳埌鎺у埗鍙帮紝渚夸簬鎺掓煡鈥滈瑙堟€?鏈€缁堟€佲€濆垏鎹㈤棶棰樸€?    console.info(`[TG_RESULT_DIAG] resultSource=${resultSource}`);
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
    setIsEstimating,
    setDuplicateData,
    setShowDuplicateModal,
    duplicateData,
    setToastType,
    setToastMsg,
    onLog,
    onGenerated,
    onGenerationComplete,
    onError,
    onDebugEvent: ingestDebugEvent,
    onDebugReset: resetDebugState,
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
    isEstimating,
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
