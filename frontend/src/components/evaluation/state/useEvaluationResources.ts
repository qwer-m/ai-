import { useEffect, useMemo, useState } from 'react';
import {
  fetchEvaluationHistory,
  fetchGenerationHistory,
  parseLatestPrefixedJson,
} from './evaluationService';
import type { EvaluationView, LoadingType, ToastMessage } from './types';

const EVALUATION_DRAFT_DB = 'ai-test-platform-evaluation-drafts';
const EVALUATION_DRAFT_FILE_STORE = 'compare-files';

function openEvaluationDraftDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = window.indexedDB.open(EVALUATION_DRAFT_DB, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(EVALUATION_DRAFT_FILE_STORE)) {
        db.createObjectStore(EVALUATION_DRAFT_FILE_STORE);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function saveEvaluationDraftFile(projectId: number | null, file: File | null): Promise<void> {
  if (!projectId || typeof window === 'undefined' || !window.indexedDB) return;
  const db = await openEvaluationDraftDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(EVALUATION_DRAFT_FILE_STORE, 'readwrite');
    const store = tx.objectStore(EVALUATION_DRAFT_FILE_STORE);
    const key = String(projectId);
    if (file) {
      store.put(file, key);
    } else {
      store.delete(key);
    }
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

async function loadEvaluationDraftFile(projectId: number | null): Promise<File | null> {
  if (!projectId || typeof window === 'undefined' || !window.indexedDB) return null;
  const db = await openEvaluationDraftDb();
  const file = await new Promise<File | null>((resolve, reject) => {
    const tx = db.transaction(EVALUATION_DRAFT_FILE_STORE, 'readonly');
    const request = tx.objectStore(EVALUATION_DRAFT_FILE_STORE).get(String(projectId));
    request.onsuccess = () => resolve(request.result instanceof File ? request.result : null);
    request.onerror = () => reject(request.error);
  });
  db.close();
  return file;
}

type UseEvaluationResourcesParams = {
  projectId: number | null;
  logs: any[];
  view: EvaluationView;
  evalResult: string | null;
};

/**
 * 资源状态层：只负责页面状态与数据加载副作用。
 * 评估动作（提交、导出、入库）由 useEvaluationActions 承担，避免单个 hook 职责过重。
 */
export function useEvaluationResources({
  projectId,
  logs,
  view,
  evalResult,
}: UseEvaluationResourcesParams) {
  const toNumberOrNull = (value: unknown): number | null => {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() !== '') {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) return parsed;
    }
    return null;
  };

  /**
   * 历史趋势图只保留可绘制的点，避免后端某些历史项没有指标时把整张图“拉空”。
   */
  const normalizeHistory = (list: any[]): any[] => {
    if (!Array.isArray(list)) return [];
    return list
      .map((item) => {
        const precision = toNumberOrNull(item?.precision);
        const recall = toNumberOrNull(item?.recall);
        const f1Score = toNumberOrNull(item?.f1_score);
        const semanticSimilarity = toNumberOrNull(item?.semantic_similarity);
        return {
          ...item,
          precision,
          recall,
          f1_score: f1Score,
          semantic_similarity: semanticSimilarity,
        };
      })
      .filter((item) =>
        item.precision !== null
        || item.recall !== null
        || item.f1_score !== null
        || item.semantic_similarity !== null,
      );
  };

  const [loading, setLoading] = useState<LoadingType>(null);
  const [file, setFile] = useState<File | null>(null);
  const [uploadedCompareFilename, setUploadedCompareFilename] = useState('');
  const [loadedCompareFilename, setLoadedCompareFilename] = useState('');
  const [showSupplement, setShowSupplement] = useState(false);
  const [supplementText, setSupplementText] = useState('');
  const [supplementImages, setSupplementImages] = useState<File[]>([]);

  const [history, setHistory] = useState<any[]>([]);
  const [genHistory, setGenHistory] = useState<any[]>([]);
  const [selectedGenerationId, setSelectedGenerationId] = useState<number | null>(null);
  const [historySourceKey, setHistorySourceKey] = useState('');
  const [historySourceTitle, setHistorySourceTitle] = useState('');
  const [savedDocId, setSavedDocId] = useState<number | null>(null);
  const [lastSavedContent, setLastSavedContent] = useState('');
  const [toastMsg, setToastMsg] = useState<ToastMessage | null>(null);

  const [uiEvalJourney, setUiEvalJourney] = useState('');
  const [apiEvalSpec, setApiEvalSpec] = useState('');

  const latestDiag = useMemo(() => parseLatestPrefixedJson<any>(logs, 'GEN_DIAG:'), [logs]);
  const latestQm = useMemo(() => parseLatestPrefixedJson<any>(logs, 'GEN_QM:'), [logs]);

  useEffect(() => {
    let cancelled = false;
    setSavedDocId(null);
    setSupplementText('');
    setLastSavedContent('');
    setSelectedGenerationId(null);
    setHistorySourceKey('');
    setHistorySourceTitle('');
    setFile(null);
    setUploadedCompareFilename('');
    setLoadedCompareFilename('');
    void loadEvaluationDraftFile(projectId)
      .then((storedFile) => {
        if (cancelled || !storedFile) return;
        setFile(storedFile);
        setUploadedCompareFilename(storedFile.name || '');
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const setPersistentFile = (nextFile: File | null) => {
    setFile(nextFile);
    void saveEvaluationDraftFile(projectId, nextFile).catch(() => {});
  };

  /**
   * 评估结果变更后主动刷新历史趋势，保证图表与当前结果同源。
   */
  useEffect(() => {
    if (!projectId) return;

    void fetchEvaluationHistory(projectId)
      .then((res: any) => {
        if (Array.isArray(res?.history)) {
          setHistory(normalizeHistory(res.history));
          return;
        }
        setHistory([]);
      })
      .catch(() => {
        setHistory([]);
      });
  }, [projectId, evalResult]);

  useEffect(() => {
    if (!projectId || view !== 'testcase') return;

    void fetchGenerationHistory(projectId)
      .then((res: any) => {
        if (Array.isArray(res)) {
          setGenHistory(res);
          return;
        }
        setGenHistory([]);
      })
      .catch(() => {
        setGenHistory([]);
      });
  }, [projectId, view]);

  return {
    loading,
    setLoading,
    file,
    setFile: setPersistentFile,
    uploadedCompareFilename,
    setUploadedCompareFilename,
    loadedCompareFilename,
    setLoadedCompareFilename,
    showSupplement,
    setShowSupplement,
    supplementText,
    setSupplementText,
    supplementImages,
    setSupplementImages,
    history,
    setHistory,
    genHistory,
    setGenHistory,
    selectedGenerationId,
    setSelectedGenerationId,
    historySourceKey,
    setHistorySourceKey,
    historySourceTitle,
    setHistorySourceTitle,
    savedDocId,
    setSavedDocId,
    lastSavedContent,
    setLastSavedContent,
    toastMsg,
    setToastMsg,
    latestDiag,
    latestQm,
    uiEvalJourney,
    setUiEvalJourney,
    apiEvalSpec,
    setApiEvalSpec,
  };
}

export type EvaluationResources = ReturnType<typeof useEvaluationResources>;
