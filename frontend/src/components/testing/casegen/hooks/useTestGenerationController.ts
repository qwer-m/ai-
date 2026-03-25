import { useEffect, useMemo, useRef, useState } from 'react';
import { cleanStreamingContent } from '../../../test-generation/streamContent';
import { extractFirstJsonArray, getUniqueCaseCount } from './testGenerationCaseUtils';
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

export function useTestGenerationController({ projectId, isActive = true, onLog, onGenerated, onGenerationComplete, onError }: TestGenerationProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const protoInputRef = useRef<HTMLInputElement>(null);
  const uploadZoneRef = useRef<HTMLDivElement>(null);

  const [mode, setMode] = useState<TestGenerationMode>(() => (readStoredString('tg_mode') as TestGenerationMode) || 'text');
  const [requirement, setRequirement] = useState(() => readStoredString(getProjectKey(projectId, 'tg_requirement')) || '');
  const [file, setFile] = useState<File | null>(null);
  const [docType, setDocType] = useState(() => readStoredString('tg_docType') || 'requirement');
  const [protoFile, setProtoFile] = useState<File | null>(null);
  const [force, setForce] = useState(false);
  const [isDragActive, setIsDragActive] = useState(false);
  const [compress, setCompress] = useState(() => readStoredString('tg_compress') === 'true');
  const [expectedCount, setExpectedCount] = useState(() => readStoredNumber(getProjectKey(projectId, 'tg_expectedCount'), 20));
  const [appendCount, setAppendCount] = useState(() => readStoredNumber(getProjectKey(projectId, 'tg_appendCount'), 10));
  const [isEstimating, setIsEstimating] = useState(false);
  const [loading, setLoading] = useState(false);
  const [pollStatus, setPollStatus] = useState('');
  const [textResult, setTextResult] = useState<any>(() => readStoredJSON(getProjectKey(projectId, 'tg_text_result'), null));
  const [textStreamingContent, setTextStreamingContent] = useState(() => readStoredString(getProjectKey(projectId, 'tg_text_streaming_content')));
  const [fileResult, setFileResult] = useState<any>(() => readStoredJSON(getProjectKey(projectId, 'tg_file_result'), null));
  const [fileStreamingContent, setFileStreamingContent] = useState(() => readStoredString(getProjectKey(projectId, 'tg_file_streaming_content')));
  const [savedFileName, setSavedFileName] = useState(() => readStoredString(getProjectKey(projectId, 'tg_savedFileName')));
  const [error, setError] = useState<string | null>(null);
  const [showDuplicateModal, setShowDuplicateModal] = useState(false);
  const [duplicateData, setDuplicateData] = useState<any>(null);
  const [showHint, setShowHint] = useState(true);
  const [toastMsg, setToastMsg] = useState<string | null>(null);
  const [toastType, setToastType] = useState<'success' | 'error'>('error');

  const result = mode === 'text' ? textResult : fileResult;
  const streamingContent = mode === 'text' ? textStreamingContent : fileStreamingContent;
  const parsedStream = useMemo(() => extractFirstJsonArray(cleanStreamingContent(streamingContent)), [streamingContent]);
  const hasJsonInResultBox = useMemo(() => Boolean(
    (Array.isArray(result) && result.length > 0) ||
    (result && typeof result === 'object') ||
    (Array.isArray(parsedStream) && parsedStream.length > 0)
  ), [result, parsedStream]);
  const currentTotal = useMemo(() => hasJsonInResultBox ? getUniqueCaseCount(result) : 0, [hasJsonInResultBox, result]);
  const targetTotal = expectedCount + appendCount;
  const isLimitReached = hasJsonInResultBox && currentTotal >= targetTotal;
  const stats = useMemo(() => ({ count: getUniqueCaseCount(result) }), [result]);

  useEffect(() => {
    if (!isActive) setToastMsg(null);
  }, [isActive]);

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
    projectId,
    hasJsonInResultBox,
    isLimitReached,
    stats,
    result,
    streamingContent,
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
    currentTotal,
    targetTotal,
  };
}

export type TestGenerationController = ReturnType<typeof useTestGenerationController>;
