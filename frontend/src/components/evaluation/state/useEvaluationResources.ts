import { useEffect, useMemo, useState } from 'react';
import {
  fetchEvaluationHistory,
  fetchGenerationHistory,
  parseLatestPrefixedJson,
} from './evaluationService';
import type { EvaluationView, LoadingType, ToastMessage } from './types';

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
  const [loading, setLoading] = useState<LoadingType>(null);
  const [file, setFile] = useState<File | null>(null);
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
    setSavedDocId(null);
    setSupplementText('');
    setLastSavedContent('');
    setSelectedGenerationId(null);
    setHistorySourceKey('');
    setHistorySourceTitle('');
  }, [projectId]);

  /**
   * 评估结果变更后主动刷新历史趋势，保证图表与当前结果同源。
   */
  useEffect(() => {
    if (!projectId) return;

    void fetchEvaluationHistory(projectId)
      .then((res: any) => {
        if (Array.isArray(res?.history)) {
          setHistory(res.history);
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
    setFile,
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
