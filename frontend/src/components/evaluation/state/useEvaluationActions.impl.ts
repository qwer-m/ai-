// @ts-nocheck
import { useEffect, useRef } from 'react';
import {
  compareTestCasesRequest,
  evaluateApiRequest,
  evaluateUiRequest,
  fetchCompareTestCaseResult,
  fetchGenerationBundle,
  fetchGenerationDetail,
  fetchGenerationHistory,
  fetchLatestSupplement,
  fetchProjectLogs,
  maxSupplementImages,
  saveKnowledgeRequest,
  translateError,
} from './evaluationService';
import { useEvaluationResources } from './useEvaluationResources';

const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

function parseResultPayload(raw: unknown): any | null {
  if (typeof raw !== 'string') return null;
  const firstOpen = raw.indexOf('{');
  const lastClose = raw.lastIndexOf('}');
  if (firstOpen < 0 || lastClose <= firstOpen) return null;
  try {
    return JSON.parse(raw.slice(firstOpen, lastClose + 1));
  } catch {
    return null;
  }
}

function resultStatus(raw: unknown): string {
  return String(parseResultPayload(raw)?.analysis_status || '');
}

function resultComparisonId(raw: unknown, fallback?: unknown): number | null {
  const direct = Number(fallback);
  if (Number.isFinite(direct) && direct > 0) return direct;
  const parsed = Number(parseResultPayload(raw)?.comparison_id);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function buildPollingTimeoutResult(comparisonId: number): string {
  return JSON.stringify({
    analysis_status: 'polling_timeout',
    analysis_mode: 'llm_background',
    is_final_evaluation: false,
    comparison_id: comparisonId,
    metrics: {},
    defect_analysis: {
      missing_points: [],
      hallucinations: [],
      modifications: [],
    },
    summary: '模型质量评估仍在后台执行，页面已停止自动等待；稍后可从历史记录重新加载结果。',
  }, null, 2);
}

export function useEvaluationActions({
  projectId,
  logs,
  onLog,
  view = 'root',
  evalGenerated,
  setEvalGenerated,
  evalModified,
  setEvalModified,
  evalResult,
  setEvalResult,
  uiEvalScript,
  uiEvalExec,
  setUiEvalOutput,
  apiEvalScript,
  apiEvalExec,
  setApiEvalOutput,
  shouldAutoEval,
  setShouldAutoEval,
}) {
  const resources = useEvaluationResources({ projectId, logs, view, evalResult });
  const activePollsRef = useRef<Set<number>>(new Set());

  const titleFromRequirement = (value: string) => (value || '').split(/[\n|]/)[0]?.trim() || '未命名需求';
  const sourceKeyFromTitle = (value: string) => titleFromRequirement(value).replace(/\s+/g, ' ').trim().toLowerCase();

  const refreshGenerationHistory = async () => {
    if (!projectId) return;
    try {
      const nextHistory = await fetchGenerationHistory(projectId);
      if (Array.isArray(nextHistory)) resources.setGenHistory(nextHistory);
    } catch {
      // 历史刷新失败不影响当前评估结果展示。
    }
  };

  const pollCompareResult = async (comparisonId: number) => {
    onLog(`质量评估已转入后台执行：comparison_id=${comparisonId}`);
    let transientFailures = 0;
    for (let attempt = 1; attempt <= 240; attempt += 1) {
      await wait(attempt <= 10 ? 2000 : 5000);
      let payload: any;
      try {
        payload = await fetchCompareTestCaseResult(comparisonId);
        transientFailures = 0;
      } catch (error) {
        transientFailures += 1;
        if (transientFailures >= 6) throw error;
        if (transientFailures === 1 || transientFailures % 3 === 0) {
          const message = error instanceof Error ? error.message : String(error);
          onLog(`质量评估轮询暂时失败，将继续重试：${message}`);
        }
        continue;
      }
      const result = payload?.result || '';
      if (result) setEvalResult(result);
      const status = payload?.analysis_status || resultStatus(result);
      if (status && status !== 'running') {
        onLog(status === 'completed' ? '质量评估已完成。' : `质量评估结束：${status}`);
        await refreshGenerationHistory();
        return;
      }
      if (attempt % 12 === 0) {
        onLog(`质量评估仍在后台执行：comparison_id=${comparisonId}`);
      }
    }
    setEvalResult(buildPollingTimeoutResult(comparisonId));
    onLog('质量评估仍在后台执行，稍后可从历史记录加载结果。');
    await refreshGenerationHistory();
  };

  const startComparePolling = (comparisonId: number) => {
    if (activePollsRef.current.has(comparisonId)) return;
    activePollsRef.current.add(comparisonId);
    void pollCompareResult(comparisonId)
      .catch(async (error) => {
        const message = await translateError(error);
        setEvalResult(message);
        onLog(`质量评估轮询失败：${message}`);
      })
      .finally(() => {
        activePollsRef.current.delete(comparisonId);
      });
  };

  const handleSupplementFiles = (files: File[]) => {
    if (files.length === 0) return;
    const images = files.filter((file) => file.type.startsWith('image/'));
    if (files.length !== images.length) {
      resources.setToastMsg({ type: 'error', msg: '仅支持图片文件' });
    }
    if (images.length === 0) return;
    resources.setSupplementImages((prev: File[]) => {
      const next = [...prev];
      for (const image of images) {
        if (next.length >= maxSupplementImages) break;
        next.push(image);
      }
      if (prev.length + images.length > maxSupplementImages) {
        resources.setToastMsg({ type: 'error', msg: `最多只能上传 ${maxSupplementImages} 张图片` });
      }
      return next;
    });
  };

  const handleSupplementPaste = (event: any) => {
    const images = Array.from(event.clipboardData?.items || [])
      .filter((item: any) => item.type.startsWith('image/'))
      .map((item: any) => item.getAsFile())
      .filter(Boolean) as File[];
    if (images.length > 0) {
      event.preventDefault();
      handleSupplementFiles(images);
    }
  };

  const handleSupplementFilesChange = (event: any) => {
    const target = event.target as HTMLInputElement;
    const files = Array.from(target.files || []);
    if (files.length > 0) handleSupplementFiles(files);
    target.value = '';
  };

  const setCompareFile = (file: File | null) => {
    resources.setFile(file);
    resources.setLoadedCompareFilename('');
    resources.setUploadedCompareFilename(file?.name || '');
  };

  const handleSaveKnowledge = async (defectAnalysis: any) => {
    if (!projectId) {
      window.alert('请先选择项目');
      return;
    }
    try {
      resources.setLoading('save_knowledge');
      const formData = new FormData();
      formData.append('project_id', String(projectId));
      formData.append('defect_analysis', JSON.stringify(defectAnalysis));
      formData.append('user_supplement', resources.supplementText);
      if (resources.historySourceKey) formData.append('source_key', resources.historySourceKey);
      if (resources.historySourceTitle) formData.append('source_title', resources.historySourceTitle);
      if (resources.selectedGenerationId) formData.append('generation_id', String(resources.selectedGenerationId));
      if (resources.savedDocId) formData.append('doc_id', String(resources.savedDocId));
      resources.supplementImages.forEach((file: File) => formData.append('files', file));

      const response = await saveKnowledgeRequest(formData);
      if (!response?.success) {
        resources.setToastMsg({ type: 'error', msg: '知识库录入失败：接口未返回成功状态' });
        return;
      }

      const replaced = !!response?.result?.replaced_previous;
      const persistSummary = response?.result?.persist_summary || {};
      const ocrSummary = response?.result?.ocr_summary || {};
      const ocrTotal = Number(ocrSummary.total || 0);
      const ocrOk = Number(ocrSummary.ok || 0);
      const embedded = Number(persistSummary.attachments_embedded || 0);
      const expected = Number(persistSummary.attachments_expected || 0);
      const message = replaced
        ? `同文档知识已覆盖更新（doc_id=${response.result.id}）`
        : `新文档知识已入库（doc_id=${response.result.id}）`;
      onLog(message);
      onLog(`OCR校验：${ocrOk}/${ocrTotal}，附件持久化校验：${embedded}/${expected}`);
      resources.setSavedDocId(response.result.id);
      resources.setLastSavedContent(resources.supplementText);
      resources.setSupplementImages([]);
      resources.setShowSupplement(false);
      resources.setToastMsg({
        type: 'success',
        msg: `知识库${replaced ? '覆盖更新成功' : '录入成功'}（${ocrTotal > 0 ? `OCR ${ocrOk}/${ocrTotal}` : '无OCR附件'}，${expected > 0 ? `持久化 ${embedded}/${expected}` : '文本已持久化'}）`,
      });
    } catch (error) {
      resources.setToastMsg({ type: 'error', msg: await translateError(error) });
    } finally {
      resources.setLoading(null);
    }
  };

  const loadGenerationById = async (id: number) => {
    if (!id) return;
    try {
      resources.setSelectedGenerationId(id);
      const bundle = await fetchGenerationBundle(id);
      const detail = await fetchGenerationDetail(id);
      const generation = bundle?.generation || detail;
      if (!generation) return;

      const title = bundle?.generation?.history_title || titleFromRequirement(generation.requirement_text || '');
      const sourceKey = bundle?.generation?.history_key || sourceKeyFromTitle(generation.requirement_text || title);
      resources.setHistorySourceTitle(title);
      resources.setHistorySourceKey(sourceKey);

      if (projectId) {
        try {
          const supplement = await fetchLatestSupplement(projectId, sourceKey);
          if (supplement?.found) {
            resources.setSavedDocId(supplement.doc_id);
            resources.setSupplementText(supplement.supplement || '');
            resources.setLastSavedContent(supplement.supplement || '');
          } else {
            resources.setSavedDocId(null);
            resources.setSupplementText('');
            resources.setLastSavedContent('');
          }
        } catch {
          resources.setSavedDocId(null);
          resources.setSupplementText('');
          resources.setLastSavedContent('');
        }
      }

      const generated = generation.generated_result || generation;
      if (typeof generated === 'string') {
        try {
          setEvalGenerated(JSON.stringify(JSON.parse(generated), null, 2));
        } catch {
          setEvalGenerated(generated);
        }
      } else {
        setEvalGenerated(JSON.stringify(generated, null, 2));
      }

      const comparison = bundle?.comparison;
      if (comparison) {
        setEvalModified(comparison.modified_test_case || '');
        setEvalResult(comparison.comparison_result || null);
        resources.setFile(null);
        resources.setUploadedCompareFilename('');
        resources.setLoadedCompareFilename(comparison.source_filename || 'history_compare.txt');
        onLog('已从历史加载测试用例、对比内容与质量评估结果');
      } else {
        setEvalModified('');
        setEvalResult(null);
        resources.setFile(null);
        resources.setUploadedCompareFilename('');
        resources.setLoadedCompareFilename('');
        const message = bundle?.comparison_status === 'missing'
          ? '该历史记录暂无已保存的对比文件与质量评估结果，请先点击“开始评估质量”生成一次。'
          : '未找到对应历史评估记录。';
        resources.setToastMsg({ type: 'error', msg: message });
        onLog(message);
      }
    } catch {
      // 历史加载失败时保持当前输入，避免清空用户内容。
    }
  };

  const compareTestCases = async () => {
    if (!projectId) {
      window.alert('请先选择项目');
      return;
    }
    if (!evalGenerated || (!evalModified && !resources.file)) {
      window.alert('请填写测试用例内容或上传文件');
      return;
    }

    resources.setSavedDocId(null);
    resources.setSupplementText('');
    resources.setLastSavedContent('');
    resources.setSupplementImages([]);
    resources.setLoading('eval');
    setEvalResult(null);
    onLog('评估测试用例质量...');

    try {
      const formData = new FormData();
      formData.append('generated_test_case', evalGenerated);
      if (evalModified) formData.append('modified_test_case', evalModified);
      formData.append('project_id', String(projectId));
      if (resources.selectedGenerationId) formData.append('generation_id', String(resources.selectedGenerationId));
      if (resources.file) formData.append('file', resources.file);
      if (resources.file?.name) resources.setUploadedCompareFilename(resources.file.name);

      const response = await compareTestCasesRequest(formData);
      const result = response?.result || '';
      setEvalResult(result);

      const comparisonId = resultComparisonId(result, response?.comparison_id || response?.persistence?.comparison_id);
      const status = response?.analysis_status || resultStatus(result);
      if (comparisonId && status === 'running') {
        startComparePolling(comparisonId);
      } else {
        await refreshGenerationHistory();
      }
    } catch (error) {
      setEvalResult(await translateError(error));
    } finally {
      resources.setLoading(null);
    }
  };

  const evaluateUi = async () => {
    if (!projectId) {
      window.alert('请先选择项目');
      return;
    }
    resources.setLoading('ui');
    setUiEvalOutput(null);
    onLog('评估 UI 自动化...');
    try {
      const response = await evaluateUiRequest({
        script: uiEvalScript,
        execution_result: uiEvalExec,
        project_id: projectId,
        journey_json: resources.uiEvalJourney || undefined,
      });
      setUiEvalOutput(response.result || '');
    } catch (error) {
      setUiEvalOutput(await translateError(error));
    } finally {
      resources.setLoading(null);
    }
  };

  const evaluateApi = async () => {
    if (!projectId) {
      window.alert('请先选择项目');
      return;
    }
    resources.setLoading('api');
    setApiEvalOutput(null);
    onLog('评估接口测试...');
    try {
      const response = await evaluateApiRequest({
        script: apiEvalScript,
        execution_result: apiEvalExec,
        project_id: projectId,
        openapi_spec: resources.apiEvalSpec || undefined,
      });
      setApiEvalOutput(response.result || '');
    } catch (error) {
      setApiEvalOutput(await translateError(error));
    } finally {
      resources.setLoading(null);
    }
  };

  const exportHistory = async () => {
    if (!projectId) {
      window.alert('请先选择项目');
      return;
    }
    try {
      const qualityLogs = (await fetchProjectLogs(projectId))
        .filter((item: any) => typeof item.message === 'string' && item.message.startsWith('GEN_QM:'))
        .slice(0, 50);
      if (qualityLogs.length === 0) {
        window.alert('暂无历史质量指标');
        return;
      }
      const header = ['created_at', 'positive', 'negative', 'edge', 'functional_count', 'non_functional_count', 'avg_steps', 'pending', 'generated_count'];
      const rows = qualityLogs.map((item: any) => {
        let metrics: any = {};
        try {
          metrics = JSON.parse(item.message.substring(7));
        } catch {
          metrics = {};
        }
        return [
          item.created_at || '',
          metrics.positive || 0,
          metrics.negative || 0,
          metrics.edge || 0,
          metrics.functional_count || 0,
          metrics.non_functional_count || 0,
          metrics.avg_steps || 0,
          metrics.pending || 0,
          metrics.generated_count || 0,
        ].join(',');
      });
      const blob = new Blob([`${header.join(',')}\n${rows.join('\n')}`], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'quality_metrics_history.csv';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      onLog('已导出历史质量指标');
    } catch (error) {
      window.alert(`导出失败: ${error}`);
    }
  };

  useEffect(() => {
    const comparisonId = resultComparisonId(evalResult);
    if (comparisonId && resultStatus(evalResult) === 'running') {
      startComparePolling(comparisonId);
    }
  }, [evalResult]);

  useEffect(() => {
    if (shouldAutoEval && evalGenerated && !resources.loading) {
      onLog('测试用例生成完毕，自动触发质量评估...');
      void compareTestCases();
      setShouldAutoEval(false);
    }
  }, [shouldAutoEval, evalGenerated, resources.loading]);

  return {
    ...resources,
    compareTestCases,
    evaluateUi,
    evaluateApi,
    exportHistory,
    loadGenerationById,
    handleSaveKnowledge,
    handleSupplementPaste,
    handleSupplementFilesChange,
    setCompareFile,
  };
}

export type EvaluationActions = ReturnType<typeof useEvaluationActions>;
