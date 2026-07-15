import { api } from '../../../../utils/api';
import { parseGenDiagEvent } from '../../../test-generation/debug/diagParser';
import type { TestGenerationMode } from '../../../test-generation/types';
import {
  deduplicateStandardCases,
  getErrorText,
  getUniqueCaseCount,
  normalizeStandardCases,
  parseStreamingArrayContent,
  translateError,
} from './testGenerationCaseUtils';
import {
  MAX_FINAL_RESULT_FETCH_RETRIES,
  normalizeHistoryCases,
  parsePersistedGenerationIdFromLine,
  sleep,
} from './useTestGenerationGeneration.helpers';
import {
  buildGenerationStreamFormData,
  openGenerationStream,
} from './generationStreamClient';
import {
  assembleFinalGeneratedCases,
  buildStreamingPreviewCases,
} from './generationResultAssembler';
import {
  consumeCompleteControlLines,
  parseDuplicatePayload,
  splitDuplicateTag,
  splitFlushableStreamText,
  stripTrailingControlLines,
} from './generationStreamProtocol';

type ResultSource = 'none' | 'streaming_preview' | 'final_persisted';

type UseTestGenerationGenerationArgs = {
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
  textGenerationId: number | null;
  fileGenerationId: number | null;
  textResult: any;
  fileResult: any;
  textStreamingContent: string;
  fileStreamingContent: string;
  setTextResult: (value: any) => void;
  setFileResult: (value: any) => void;
  setTextStreamingContent: (value: string) => void;
  setFileStreamingContent: (value: string) => void;
  setTextStreamingParsedResult: (value: any) => void;
  setFileStreamingParsedResult: (value: any) => void;
  setTextFinalResult: (value: any) => void;
  setFileFinalResult: (value: any) => void;
  setTextResultSource: (value: ResultSource) => void;
  setFileResultSource: (value: ResultSource) => void;
  setTextIsFinalResultLoaded: (value: boolean) => void;
  setFileIsFinalResultLoaded: (value: boolean) => void;
  setTextGenerationId: (value: number | null) => void;
  setFileGenerationId: (value: number | null) => void;
  setExpectedCount: (value: number) => void;
  setLoading: (value: boolean) => void;
  setError: (value: string | null) => void;
  setPollStatus: (value: string) => void;
  setDuplicateData: (value: any) => void;
  setShowDuplicateModal: (value: boolean) => void;
  duplicateData: any;
  onLog: (message: string) => void;
  onGenerated: (data: any) => void;
  onGenerationComplete?: () => void;
  onError?: (message: string) => void;
  onDebugEvent?: (event: unknown) => void;
  onDebugReset?: () => void;
  enableSamplePoolFeedback: boolean;
};

type CurrentModeState = {
  setResult: (value: any) => void;
  setStreamingContent: (value: string) => void;
  setStreamingParsedResult: (value: any) => void;
  setFinalResult: (value: any) => void;
  setResultSource: (value: ResultSource) => void;
  setIsFinalResultLoaded: (value: boolean) => void;
  setGenerationId: (value: number | null) => void;
};

async function fetchFinalPersistedResult(
  generationId: number,
  onLog: (message: string) => void,
): Promise<any[] | null> {
  onLog(`Detected generation_id=${generationId}, fetching final persisted result...`);

  for (let attempt = 1; attempt <= MAX_FINAL_RESULT_FETCH_RETRIES; attempt++) {
    try {
      const response = await api.get<any>(`/api/test-generations/${generationId}`);
      const cases = normalizeHistoryCases(response);
      if (cases.length > 0) return cases;
      throw new Error('persisted_result_not_ready');
    } catch (error) {
      const message = getErrorText(error);
      if (attempt >= MAX_FINAL_RESULT_FETCH_RETRIES) {
        onLog(`Failed to fetch final persisted result: ${message}`);
        return null;
      }

      onLog(`Final result not ready, retrying (${attempt}/${MAX_FINAL_RESULT_FETCH_RETRIES})...`);
      await sleep(300 * attempt);
    }
  }

  return null;
}

function shouldOpenConfigForError(message: string): boolean {
  return message.includes('401')
    || message.includes('QUOTA')
    || message.includes('API Key not set');
}

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
  textGenerationId,
  fileGenerationId,
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
  onDebugEvent,
  onDebugReset,
  enableSamplePoolFeedback,
}: UseTestGenerationGenerationArgs) {
  const getCurrentModeState = (isText: boolean): CurrentModeState => ({
    setResult: isText ? setTextResult : setFileResult,
    setStreamingContent: isText ? setTextStreamingContent : setFileStreamingContent,
    setStreamingParsedResult: isText ? setTextStreamingParsedResult : setFileStreamingParsedResult,
    setFinalResult: isText ? setTextFinalResult : setFileFinalResult,
    setResultSource: isText ? setTextResultSource : setFileResultSource,
    setIsFinalResultLoaded: isText ? setTextIsFinalResultLoaded : setFileIsFinalResultLoaded,
    setGenerationId: isText ? setTextGenerationId : setFileGenerationId,
  });

  const getCurrentGenerationId = (isText: boolean): number | null => (
    isText ? textGenerationId : fileGenerationId
  );

  const getExistingCases = (isText: boolean) => {
    const existing = isText ? textResult : fileResult;
    if (Array.isArray(existing)) {
      return deduplicateStandardCases(normalizeStandardCases(existing));
    }
    return parseStreamingArrayContent(isText ? textStreamingContent : fileStreamingContent);
  };

  const handleGenerateStream = async (
    isText: boolean,
    forceOverride?: boolean,
    appendMode?: boolean,
  ) => {
    if (!navigator.onLine) return alert('Network is offline, cannot generate.');
    if (!projectId) return alert('Please select a project first.');
    if (isText && !requirement.trim()) return alert('Please enter requirement text.');
    if (!isText && !file) return alert('Please select a file.');

    const currentState = getCurrentModeState(isText);
    const shouldAppend = Boolean(appendMode);
    const existingCases = shouldAppend ? getExistingCases(isText) : [];
    const previousGenerationId = shouldAppend ? getCurrentGenerationId(isText) : null;

    let targetExpectedCount = expectedCount;
    if (shouldAppend) {
      const currentCount = getUniqueCaseCount(existingCases);
      targetExpectedCount = currentCount + Math.min(25, Math.max(1, appendCount));
    }

    const safeExpectedCount = Math.max(1, Math.floor(Number(targetExpectedCount) || 1));
    if (!shouldAppend && safeExpectedCount !== expectedCount) {
      setExpectedCount(safeExpectedCount);
    }

    onDebugReset?.();
    setLoading(true);
    setError(null);
    currentState.setResultSource('streaming_preview');
    currentState.setIsFinalResultLoaded(false);
    currentState.setGenerationId(null);
    if (!shouldAppend) {
      currentState.setResult(null);
      currentState.setStreamingParsedResult(null);
    }
    currentState.setFinalResult(null);
    currentState.setStreamingContent('');
    setPollStatus('Generating in real time...');
    onLog(isText ? 'Starting text-mode test generation...' : `Starting file-mode test generation: ${file?.name || ''}`);

    const formData = buildGenerationStreamFormData({
      projectId,
      isText,
      requirement,
      file,
      protoFile,
      docType,
      compress,
      expectedCount: safeExpectedCount,
      force: forceOverride !== undefined ? forceOverride : force,
      appendMode: shouldAppend,
      previousGenerationId,
      enableSamplePoolFeedback,
    });

    try {
      const reader = await openGenerationStream(formData);
      const decoder = new TextDecoder();
      let rawText = '';
      let duplicateDetected = false;
      let buffer = '';
      let pendingDuplicateJson: string | null = null;
      let lastParseTime = 0;
      let persistedGenerationId: number | null = null;

      const setDetectedGenerationId = (line: string) => {
        const diagnosticEvent = parseGenDiagEvent(line);
        if (diagnosticEvent) onDebugEvent?.(diagnosticEvent);

        const generationId = parsePersistedGenerationIdFromLine(line);
        if (generationId) {
          persistedGenerationId = generationId;
          currentState.setGenerationId(generationId);
        }
      };

      const showDuplicateDocument = (data: unknown) => {
        setDuplicateData(data);
        setShowDuplicateModal(true);
        onLog('Duplicate document detected, waiting for confirmation...');
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        if (pendingDuplicateJson !== null) {
          pendingDuplicateJson += buffer;
          buffer = '';
          const duplicatePayload = parseDuplicatePayload(pendingDuplicateJson);
          if (duplicatePayload.ok) {
            showDuplicateDocument(duplicatePayload.data);
            void reader.cancel();
            return;
          }
          continue;
        }

        buffer = consumeCompleteControlLines(buffer, {
          onStatus: (statusMessage) => {
            setPollStatus(statusMessage);
            onLog(statusMessage);
            const diagnosticEvent = parseGenDiagEvent(statusMessage);
            if (diagnosticEvent) onDebugEvent?.(diagnosticEvent);
          },
          onContextDebug: (rawPayload) => {
            try {
              const parsedPayload = JSON.parse(rawPayload);
              onLog(`context_debug: ${rawPayload}`);
              const diagnosticEvent = parseGenDiagEvent(parsedPayload) || parseGenDiagEvent(rawPayload);
              if (diagnosticEvent) onDebugEvent?.(diagnosticEvent);
            } catch {
              onLog(`context_debug_parse_failed: ${rawPayload}`);
            }
          },
        });

        if (!duplicateDetected) {
          const duplicateSplit = splitDuplicateTag(buffer);
          if (duplicateSplit.duplicateDetected) {
            duplicateDetected = true;
            buffer = duplicateSplit.before;
            pendingDuplicateJson = duplicateSplit.payload;

            const duplicatePayload = parseDuplicatePayload(pendingDuplicateJson);
            if (duplicatePayload.ok) {
              showDuplicateDocument(duplicatePayload.data);
              void reader.cancel();
              return;
            }
          } else {
            buffer = duplicateSplit.buffer;
          }
        }

        const { flushText, remainder } = splitFlushableStreamText(buffer);
        buffer = remainder;

        if (flushText) {
          rawText += flushText;
          currentState.setStreamingContent(rawText);
          for (const line of flushText.split(/\r?\n/)) {
            setDetectedGenerationId(line);
          }
        }

        if (Date.now() - lastParseTime > 500) {
          lastParseTime = Date.now();
          const previewCases = buildStreamingPreviewCases(rawText, shouldAppend, existingCases);
          if (previewCases && previewCases.length > 0) {
            currentState.setStreamingParsedResult(previewCases);
            currentState.setResult(previewCases);
          }
        }
      }

      if (pendingDuplicateJson !== null) {
        const duplicatePayload = parseDuplicatePayload(pendingDuplicateJson);
        if (duplicatePayload.ok) {
          showDuplicateDocument(duplicatePayload.data);
          return;
        }
        setDuplicateData({ id: null });
        setShowDuplicateModal(true);
        return;
      }

      if (buffer) {
        for (const line of buffer.split(/\r?\n/)) {
          setDetectedGenerationId(line);
        }
        rawText += stripTrailingControlLines(buffer);
        currentState.setStreamingContent(rawText);
      }

      const previewCases = assembleFinalGeneratedCases({
        rawText,
        appendMode: shouldAppend,
        existingCases,
        expectedCount: safeExpectedCount,
        onLog,
      });
      currentState.setStreamingParsedResult(previewCases);
      currentState.setResult(previewCases);
      currentState.setResultSource('streaming_preview');
      currentState.setIsFinalResultLoaded(false);

      let generatedCases = previewCases;
      if (persistedGenerationId) {
        const persistedCases = await fetchFinalPersistedResult(persistedGenerationId, onLog);
        if (persistedCases && persistedCases.length > 0) {
          generatedCases = persistedCases;
          currentState.setFinalResult(persistedCases);
          currentState.setResult(persistedCases);
          currentState.setResultSource('final_persisted');
          currentState.setIsFinalResultLoaded(true);
          currentState.setGenerationId(persistedGenerationId);
          setPollStatus('已切换为最终结果');
          onLog(`Final persisted result loaded (generation_id=${persistedGenerationId}, cases=${persistedCases.length}).`);
        } else {
          setPollStatus('生成完成（预览）');
          onLog('Using streaming preview because persisted result fetch failed.');
        }
      } else {
        setPollStatus('生成完成（预览）');
        onLog('No persisted generation id detected in stream; keep streaming preview result.');
      }

      onGenerated(generatedCases);
      onLog('Generation completed');
      onGenerationComplete?.();
    } catch (error) {
      const rawMessage = getErrorText(error);
      const translatedMessage = await translateError(error);
      const displayMessage = rawMessage.includes('Generation failed:')
        || rawMessage.includes('Error:')
        || rawMessage.includes('HTTP ')
        ? rawMessage
        : translatedMessage;

      setError(displayMessage);
      setPollStatus('生成失败');
      onLog(`Generation failed: ${displayMessage}`);
      if (rawMessage && rawMessage !== translatedMessage) {
        onLog(`Generation failed(raw): ${rawMessage}`);
      }
      if (onError && shouldOpenConfigForError(rawMessage)) {
        onError(translatedMessage);
      }
    } finally {
      setLoading(false);
    }
  };

  return {
    handleGenerateStream,
    handleDuplicateConfirm: () => {
      setShowDuplicateModal(false);
      void handleGenerateStream(mode === 'text', true);
    },
    handleDuplicateCancel: async () => {
      if (duplicateData?.id) {
        try {
          setLoading(true);
          const generationId = Number(duplicateData.id);
          const historyResponse = await api.get<any>(`/api/test-generations/${generationId}`);
          const historyCases = normalizeHistoryCases(historyResponse);
          const result = historyCases.length > 0 ? historyCases : historyResponse;

          if (mode === 'text') {
            setTextResult(result);
            setTextStreamingParsedResult(null);
            setTextFinalResult(result);
            setTextResultSource('final_persisted');
            setTextIsFinalResultLoaded(true);
            setTextGenerationId(Number.isFinite(generationId) ? generationId : null);
            setTextStreamingContent(JSON.stringify(result, null, 2));
          } else {
            setFileResult(result);
            setFileStreamingParsedResult(null);
            setFileFinalResult(result);
            setFileResultSource('final_persisted');
            setFileIsFinalResultLoaded(true);
            setFileGenerationId(Number.isFinite(generationId) ? generationId : null);
            setFileStreamingContent(JSON.stringify(result, null, 2));
          }

          onGenerated(result);
          onLog('Loaded historical generation result');
        } catch (error) {
          const message = await translateError(error);
          onLog(`Failed to load history: ${message}`);
        } finally {
          setLoading(false);
        }
      }

      setShowDuplicateModal(false);
    },
  };
}
