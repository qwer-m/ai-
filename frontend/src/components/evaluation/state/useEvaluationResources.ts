import { useEffect, useState } from 'react';
import {
  fetchEvaluationHistory,
  fetchAgentRunHistory,
} from './evaluationService';
import type {
  EvaluationHistoryPoint,
  EvaluationRunRecord,
  EvaluationView,
  LoadingType,
  QualityReport,
  ToastMessage,
} from './types';

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
  view: EvaluationView;
  evalResult: QualityReport | null;
};

/**
 * 资源状态层：只负责页面状态与数据加载副作用。
 * 评估动作（提交、导出、入库）由 useEvaluationActions 承担，避免单个 hook 职责过重。
 */
export function useEvaluationResources({
  projectId,
  view,
  evalResult,
}: UseEvaluationResourcesParams) {
  /**
   * 历史趋势图只保留可绘制的点，避免后端某些历史项没有指标时把整张图“拉空”。
   */
  const normalizeHistory = (list: EvaluationHistoryPoint[]): EvaluationHistoryPoint[] => (
    list.filter((item) =>
        item.precision !== null
        || item.recall !== null
        || item.f1_score !== null
        || item.semantic_similarity !== null,
    )
  );

  const [loading, setLoading] = useState<LoadingType>(null);
  const [file, setFile] = useState<File | null>(null);
  const [uploadedReferenceFilename, setUploadedReferenceFilename] = useState('');
  const [loadedReferenceFilename, setLoadedReferenceFilename] = useState('');
  const [history, setHistory] = useState<EvaluationHistoryPoint[]>([]);
  const [runHistory, setRunHistory] = useState<EvaluationRunRecord[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [toastMsg, setToastMsg] = useState<ToastMessage | null>(null);

  const [uiEvalJourney, setUiEvalJourney] = useState('');
  const [apiEvalSpec, setApiEvalSpec] = useState('');

  useEffect(() => {
    let cancelled = false;
    setHistory([]);
    setRunHistory([]);
    setSelectedRunId(null);
    setFile(null);
    setUploadedReferenceFilename('');
    setLoadedReferenceFilename('');
    void loadEvaluationDraftFile(projectId)
      .then((storedFile) => {
        if (cancelled || !storedFile) return;
        setFile(storedFile);
        setUploadedReferenceFilename(storedFile.name || '');
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
      .then((res) => {
        setHistory(normalizeHistory(res.history));
      })
      .catch(() => {
        setHistory([]);
      });
  }, [projectId, evalResult]);

  useEffect(() => {
    if (!projectId || (view !== 'root' && view !== 'testcase')) return;

    void fetchAgentRunHistory(projectId)
      .then(setRunHistory)
      .catch(() => {
        setRunHistory([]);
      });
  }, [projectId, view]);

  return {
    loading,
    setLoading,
    file,
    setFile: setPersistentFile,
    uploadedReferenceFilename,
    setUploadedReferenceFilename,
    loadedReferenceFilename,
    setLoadedReferenceFilename,
    history,
    setHistory,
    runHistory,
    setRunHistory,
    selectedRunId,
    setSelectedRunId,
    toastMsg,
    setToastMsg,
    uiEvalJourney,
    setUiEvalJourney,
    apiEvalSpec,
    setApiEvalSpec,
  };
}
