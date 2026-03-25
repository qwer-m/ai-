import { useEffect } from 'react';
import { api, getAuthHeaders } from '../../../../utils/api';
import { cleanStreamingContent, parseMultipleJsonArrays } from '../../../test-generation/streamContent';
import type { TestGenerationMode } from '../../../test-generation/types';
import {
  deduplicateStandardCases,
  getErrorText,
  getUniqueCaseCount,
  normalizeStandardCases,
  parseStreamingArrayContent,
  translateError,
  validateStandardCases,
} from './testGenerationCaseUtils';

type Args = {
  projectId: number | null;
  mode: TestGenerationMode;
  requirement: string;
  file: File | null;
  protoFile: File | null;
  docType: string;
  compress: boolean;
  force: boolean;
  expectedCount: number;
  appendCount: number;
  textResult: any;
  fileResult: any;
  textStreamingContent: string;
  fileStreamingContent: string;
  setTextResult: (value: any) => void;
  setFileResult: (value: any) => void;
  setTextStreamingContent: (value: string) => void;
  setFileStreamingContent: (value: string) => void;
  setExpectedCount: (value: number) => void;
  setLoading: (value: boolean) => void;
  setError: (value: string | null) => void;
  setPollStatus: (value: string) => void;
  setIsEstimating: (value: boolean) => void;
  setDuplicateData: (value: any) => void;
  setShowDuplicateModal: (value: boolean) => void;
  duplicateData: any;
  setToastType: (type: 'success' | 'error') => void;
  setToastMsg: (msg: string | null) => void;
  onLog: (msg: string) => void;
  onGenerated: (data: any) => void;
  onGenerationComplete?: () => void;
  onError?: (msg: string) => void;
};

export function useTestGenerationGeneration({
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
}: Args) {
  useEffect(() => {
    const estimate = async () => {
      setIsEstimating(true);
      try {
        const formData = new FormData();
        formData.append('project_id', String(projectId || 0));
        formData.append('doc_type', 'requirement');
        if (mode === 'text') {
          if (!requirement.trim()) {
            setExpectedCount(20);
            return;
          }
          formData.append('requirement', requirement);
        } else {
          if (!file) {
            setExpectedCount(20);
            return;
          }
          formData.append('file', file);
        }
        const res = await api.upload<{ count: number }>('/api/estimate-test-count', formData);
        if (res && typeof res.count === 'number') setExpectedCount(res.count);
      } catch (e) {
        const msg = await translateError(e);
        setToastType('error');
        setToastMsg(`Smart estimation failed, default value applied. Error: ${msg}`);
      } finally {
        setIsEstimating(false);
      }
    };
    const timer = setTimeout(estimate, mode === 'text' ? 800 : 600);
    return () => clearTimeout(timer);
  }, [requirement, file, mode, projectId, setExpectedCount, setIsEstimating, setToastMsg, setToastType]);

  const getExistingCases = (isText: boolean) => {
    const existing = isText ? textResult : fileResult;
    if (Array.isArray(existing)) return deduplicateStandardCases(normalizeStandardCases(existing));
    const stream = isText ? textStreamingContent : fileStreamingContent;
    return parseStreamingArrayContent(stream);
  };

  const handleGenerateStream = async (isText: boolean, forceOverride?: boolean, appendMode?: boolean) => {
    if (!navigator.onLine) return alert('Network is offline, cannot generate.');
    if (!projectId) return alert('Please select a project first.');
    if (isText && !requirement.trim()) return alert('Please enter requirement text.');
    if (!isText && !file) return alert('Please select a file.');

    const setCurrentResult = isText ? setTextResult : setFileResult;
    const setCurrentStreamingContent = isText ? setTextStreamingContent : setFileStreamingContent;
    const existingCases = appendMode ? getExistingCases(isText) : [];

    let targetVal = expectedCount;
    if (appendMode) {
      const currentCount = getUniqueCaseCount(existingCases);
      targetVal = currentCount < expectedCount
        ? currentCount + Math.min(25, expectedCount - currentCount)
        : currentCount + Math.min(25, appendCount);
    }
    const safeExpectedCount = Math.max(1, Math.floor(Number(targetVal) || 1));
    if (!appendMode && safeExpectedCount !== expectedCount) setExpectedCount(safeExpectedCount);

    setLoading(true);
    setError(null);
    if (!appendMode) setCurrentResult(null);
    setCurrentStreamingContent('');
    setPollStatus('Generating in real time...');
    onLog(isText ? 'Starting text-mode test generation...' : `Starting file-mode test generation: ${file?.name || ''}`);

    const formData = new FormData();
    formData.append('project_id', String(projectId));
    formData.append('doc_type', isText ? 'requirement' : docType);
    formData.append('compress', String(compress));
    formData.append('expected_count', String(safeExpectedCount));
    formData.append('force', String(forceOverride !== undefined ? forceOverride : force));
    if (appendMode) formData.append('append', 'true');
    if (isText) formData.append('requirement_text', requirement);
    else if (file) {
      formData.append('file', file);
      if (docType === 'incomplete' && protoFile) formData.append('prototype_file', protoFile);
    }

    try {
      const resp = await fetch('/api/generate-tests-stream', { method: 'POST', headers: { ...getAuthHeaders() }, body: formData });
      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw new Error(errData.error || `HTTP ${resp.status}`);
      }
      const reader = resp.body?.getReader();
      if (!reader) throw new Error('No response body');
      const decoder = new TextDecoder();
      let rawText = '';
      let duplicateDetected = false;
      let buffer = '';
      let pendingDuplicateJson: string | null = null;
      let lastParseTime = 0;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        if (pendingDuplicateJson !== null) {
          pendingDuplicateJson += buffer;
          buffer = '';
          try {
            const jsonStr = pendingDuplicateJson.startsWith(':') ? pendingDuplicateJson.substring(1) : pendingDuplicateJson;
            const prevData = JSON.parse(jsonStr);
            setDuplicateData(prevData);
            setShowDuplicateModal(true);
            onLog('Duplicate document detected, waiting for confirmation...');
            reader.cancel();
            return;
          } catch {
            continue;
          }
        }

        const statusMatch = buffer.match(/@@STATUS@@:(.*?)(?:\r?\n)/);
        if (statusMatch) {
          const statusMsg = statusMatch[1].trim();
          setPollStatus(statusMsg);
          onLog(statusMsg);
          buffer = buffer.replace(statusMatch[0], '');
        }

        if (!duplicateDetected && buffer.includes('@@DUPLICATE@@')) {
          duplicateDetected = true;
          const idx = buffer.indexOf('@@DUPLICATE@@');
          const after = buffer.slice(idx + '@@DUPLICATE@@'.length);
          buffer = buffer.slice(0, idx);
          pendingDuplicateJson = after;
          try {
            const jsonStr = pendingDuplicateJson.startsWith(':') ? pendingDuplicateJson.substring(1) : pendingDuplicateJson;
            const prevData = JSON.parse(jsonStr);
            setDuplicateData(prevData);
            setShowDuplicateModal(true);
            onLog('Duplicate document detected, waiting for confirmation...');
            reader.cancel();
            return;
          } catch {
            // keep waiting for more data
          }
        }

        const potentialTags = ['@@STATUS@@:', '@@DUPLICATE@@'];
        let safeEndIndex = buffer.length;
        const searchLimit = Math.max(0, buffer.length - 20);
        for (let i = buffer.length - 1; i >= searchLimit; i--) {
          const suffix = buffer.slice(i);
          if (potentialTags.some((tag) => tag.startsWith(suffix))) safeEndIndex = i;
        }
        const flushText = buffer.slice(0, safeEndIndex);
        buffer = buffer.slice(safeEndIndex);
        if (flushText) {
          rawText += flushText;
          setCurrentStreamingContent(rawText);
        }

        if (Date.now() - lastParseTime > 500) {
          lastParseTime = Date.now();
          const parsed = parseMultipleJsonArrays(rawText);
          const normalizedNew = normalizeStandardCases(parsed);
          if (normalizedNew.length > 0) {
            const dedupedNew = deduplicateStandardCases(normalizedNew);
            setCurrentResult(appendMode ? deduplicateStandardCases([...normalizeStandardCases(existingCases), ...dedupedNew]) : dedupedNew);
          }
        }
      }

      if (pendingDuplicateJson !== null) {
        try {
          const jsonStr = pendingDuplicateJson.startsWith(':') ? pendingDuplicateJson.substring(1) : pendingDuplicateJson;
          const prevData = JSON.parse(jsonStr);
          setDuplicateData(prevData);
          setShowDuplicateModal(true);
          onLog('Duplicate document detected, waiting for confirmation...');
          return;
        } catch {
          setDuplicateData({ id: null });
          setShowDuplicateModal(true);
          return;
        }
      }

      if (buffer) {
        rawText += buffer.replace(/@@STATUS@@:.*$/g, '');
        setCurrentStreamingContent(rawText);
      }

      const skipNormalize = typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('skipNormalize') === '1';
      const cleaned = cleanStreamingContent(rawText).trim();
      if (!cleaned) throw new Error('Generation result is empty; check model config, quota, or network and retry.');
      const errMatches = Array.from(cleaned.matchAll(/(?:^|\r?\n)Error:\s*([^\r\n]+)/g));
      let json: any[] = [];
      try { json = parseMultipleJsonArrays(rawText); } catch {}
      let finalGeneratedData: any[] = [];
      if (skipNormalize) {
        const merged = appendMode ? [...(Array.isArray(existingCases) ? existingCases : []), ...json] : json;
        finalGeneratedData = deduplicateStandardCases(normalizeStandardCases(merged));
      } else {
        const normalizedNew = normalizeStandardCases(json);
        if (normalizedNew.length === 0) {
          if (errMatches.length > 0) {
            const lastErr = errMatches[errMatches.length - 1]?.[1] || '';
            throw new Error(lastErr ? `Generation failed: ${lastErr}` : 'Generation failed: backend returned an error');
          }
          if (Array.isArray(json) && json.length === 0) throw new Error('Generation result is an empty array, please retry');
          throw new Error('Generation result is not an array of case objects; ensure the model returns a JSON array of objects');
        }
        const validNew = validateStandardCases(normalizedNew);
        if (!validNew.ok) throw new Error(`Generation result does not match the standard JSON structure: ${validNew.error}`);
        if (appendMode) {
          const merged = deduplicateStandardCases(normalizeStandardCases([...(normalizeStandardCases(existingCases)), ...normalizedNew]));
          const validMerged = validateStandardCases(merged);
          if (!validMerged.ok) throw new Error(`Merged result does not match the standard JSON structure: ${validMerged.error}`);
          finalGeneratedData = merged;
        } else {
          finalGeneratedData = deduplicateStandardCases(normalizedNew);
        }
      }

      if (!appendMode && finalGeneratedData.length > safeExpectedCount) finalGeneratedData = finalGeneratedData.slice(0, safeExpectedCount);
      if (!Array.isArray(finalGeneratedData) || finalGeneratedData.length === 0) throw new Error('Generation completed but no valid test cases were produced');
      setCurrentResult(finalGeneratedData);
      onGenerated(finalGeneratedData);
      onLog('Generation completed');
      if (onGenerationComplete) onGenerationComplete();
    } catch (e) {
      const raw = getErrorText(e);
      const msg = await translateError(e);
      setError(msg);
      onLog(`Generation failed: ${msg}`);
      if (onError && (raw.includes('401') || raw.includes('QUOTA') || raw.includes('API Key not set'))) onError(msg);
    } finally {
      setLoading(false);
      setPollStatus('');
    }
  };

  const handleDuplicateConfirm = () => {
    setShowDuplicateModal(false);
    void handleGenerateStream(mode === 'text', true);
  };

  const handleDuplicateCancel = async () => {
    if (duplicateData?.id) {
      try {
        setLoading(true);
        const data = await api.get<any>(`/api/test-generations/${duplicateData.id}`);
        if (mode === 'text') {
          setTextResult(data);
          setTextStreamingContent(JSON.stringify(data, null, 2));
        } else {
          setFileResult(data);
          setFileStreamingContent(JSON.stringify(data, null, 2));
        }
        onGenerated(data);
        onLog('Loaded historical generation result');
      } catch (e) {
        const msg = await translateError(e);
        onLog(`Failed to load history: ${msg}`);
      } finally {
        setLoading(false);
      }
    }
    setShowDuplicateModal(false);
  };

  return {
    handleGenerateStream,
    handleDuplicateConfirm,
    handleDuplicateCancel,
  };
}
