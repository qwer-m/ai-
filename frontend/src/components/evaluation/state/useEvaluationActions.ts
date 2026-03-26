import type { ChangeEvent, ClipboardEvent } from 'react';
import { useEffect } from 'react';
import {
  compareTestCasesRequest,
  evaluateApiRequest,
  evaluateUiRequest,
  fetchGenerationBundle,
  fetchGenerationDetail,
  fetchGenerationHistory,
  fetchLatestSupplement,
  fetchProjectLogs,
  maxSupplementImages,
  saveKnowledgeRequest,
  translateError,
} from './evaluationService';
import type { DefectAnalysis, EvaluationProps } from './types';
import { useEvaluationResources } from './useEvaluationResources';

type UseEvaluationActionsParams = Pick<
  EvaluationProps,
  | 'projectId'
  | 'logs'
  | 'onLog'
  | 'view'
  | 'evalGenerated'
  | 'setEvalGenerated'
  | 'evalModified'
  | 'setEvalModified'
  | 'evalResult'
  | 'setEvalResult'
  | 'uiEvalScript'
  | 'uiEvalExec'
  | 'setUiEvalOutput'
  | 'apiEvalScript'
  | 'apiEvalExec'
  | 'setApiEvalOutput'
  | 'shouldAutoEval'
  | 'setShouldAutoEval'
>;

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
}: UseEvaluationActionsParams) {
  const resources = useEvaluationResources({ projectId, logs, view, evalResult });
  const normalizeHistoryTitle = (text: string) => {
    const raw = (text || '').split(/[\n|]/)[0]?.trim();
    return raw || '未命名需求';
  };
  const buildSourceKey = (text: string) => normalizeHistoryTitle(text).replace(/\s+/g, ' ').trim().toLowerCase();

  const addSupplementImages = (files: File[]) => {
    if (files.length === 0) return;
    const imageFiles = files.filter((f) => f.type.startsWith('image/'));
    const nonImages = files.filter((f) => !f.type.startsWith('image/'));

    if (nonImages.length > 0) resources.setToastMsg({ type: 'error', msg: '仅支持图片文件' });
    if (imageFiles.length === 0) return;

    resources.setSupplementImages((prev) => {
      const next = [...prev];
      for (const img of imageFiles) {
        if (next.length >= maxSupplementImages) break;
        next.push(img);
      }
      if (prev.length + imageFiles.length > maxSupplementImages) {
        resources.setToastMsg({ type: 'error', msg: `最多只能上传 ${maxSupplementImages} 张图片` });
      }
      return next;
    });
  };

  /** 支持在补充描述框直接粘贴截图，降低用户补录证据的操作成本。 */
  const handleSupplementPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const items = Array.from(e.clipboardData?.items || []);
    const imageFiles = items
      .filter((item) => item.type.startsWith('image/'))
      .map((item) => item.getAsFile())
      .filter(Boolean) as File[];

    if (imageFiles.length > 0) {
      e.preventDefault();
      addSupplementImages(imageFiles);
    }
  };

  const handleSupplementFilesChange = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length > 0) addSupplementImages(files);
    e.target.value = '';
  };

  const handleSaveKnowledge = async (defectAnalysis: DefectAnalysis) => {
    if (!projectId) return alert('请先选择项目');

    try {
      resources.setLoading('save_knowledge');
      const formData = new FormData();
      formData.append('project_id', String(projectId));
      formData.append('defect_analysis', JSON.stringify(defectAnalysis));
      formData.append('user_supplement', resources.supplementText);
      if (resources.historySourceKey) formData.append('source_key', resources.historySourceKey);
      if (resources.historySourceTitle) formData.append('source_title', resources.historySourceTitle);
      if (resources.selectedGenerationId) formData.append('generation_id', String(resources.selectedGenerationId));
      if (resources.supplementImages.length > 0) {
        resources.supplementImages.forEach((f) => formData.append('files', f));
      }
      if (resources.savedDocId) formData.append('doc_id', String(resources.savedDocId));

      const res = await saveKnowledgeRequest(formData);
      if (res?.success) {
        const replaced = Boolean(res?.result?.replaced_previous);
        const persist = res?.result?.persist_summary || {};
        const ocrSummary = res?.result?.ocr_summary || {};
        const ocrTotal = Number(ocrSummary.total || 0);
        const ocrOk = Number(ocrSummary.ok || 0);
        const persisted = Number(persist.attachments_embedded || 0);
        const expected = Number(persist.attachments_expected || 0);

        const logMsg = replaced
          ? `同文档知识已覆盖更新（doc_id=${res.result.id}）`
          : `新文档知识已入库（doc_id=${res.result.id}）`;
        onLog(logMsg);
        onLog(`OCR校验：${ocrOk}/${ocrTotal}，附件持久化校验：${persisted}/${expected}`);
        resources.setSavedDocId(res.result.id);
        resources.setLastSavedContent(resources.supplementText);

        const ocrModel = typeof res?.result?.ocr_model === 'string' ? res.result.ocr_model : '';
        const actionLabel = replaced ? '覆盖更新成功' : '录入成功';
        const ocrMsg = ocrTotal > 0 ? `OCR ${ocrOk}/${ocrTotal}` : '无OCR附件';
        const persistMsg = expected > 0 ? `持久化 ${persisted}/${expected}` : '文本已持久化';
        const msg = `知识库${actionLabel}（${ocrModel || '默认模型'}，${ocrMsg}，${persistMsg}）`;
        resources.setToastMsg({ type: 'success', msg });
        resources.setSupplementImages([]);
        resources.setShowSupplement(false);
      } else {
        resources.setToastMsg({ type: 'error', msg: '知识库录入失败：接口未返回成功状态' });
      }
    } catch (e) {
      const msg = await translateError(e);
      resources.setToastMsg({ type: 'error', msg });
    } finally {
      resources.setLoading(null);
    }
  };

  const loadGenerationById = async (id: number) => {
    if (!id) return;
    try {
      resources.setSelectedGenerationId(id);
      const bundle = await fetchGenerationBundle(id);
      const fallback = await fetchGenerationDetail(id);
      const source = bundle?.generation || fallback;
      if (!source) return;
      const sourceTitle = bundle?.generation?.history_title || normalizeHistoryTitle(source.requirement_text || '');
      const sourceKey = bundle?.generation?.history_key || buildSourceKey(source.requirement_text || sourceTitle);
      resources.setHistorySourceTitle(sourceTitle);
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

      let content = source;
      if (source.generated_result) content = source.generated_result;

      if (typeof content === 'string') {
        try {
          const parsed = JSON.parse(content);
          setEvalGenerated(JSON.stringify(parsed, null, 2));
        } catch {
          setEvalGenerated(content);
        }
      } else {
        setEvalGenerated(JSON.stringify(content, null, 2));
      }

      const comparison = bundle?.comparison;
      if (comparison) {
        setEvalModified(comparison.modified_test_case || '');
        setEvalResult(comparison.comparison_result || null);
        resources.setFile(null);
        resources.setLoadedCompareFilename(comparison.source_filename || 'history_compare.txt');
        onLog('已从历史加载测试用例、对比内容与质量评估结果');
      } else {
        setEvalModified('');
        setEvalResult(null);
        resources.setFile(null);
        resources.setLoadedCompareFilename('');
        const hint = bundle?.comparison_status === 'missing'
          ? '该历史记录暂无已保存的对比文件与质量评估结果，请先点击“开始评估质量”生成一次。'
          : '未找到对应历史评估记录。';
        resources.setToastMsg({ type: 'error', msg: hint });
        onLog(hint);
      }
    } catch {
      // 保持原有行为：历史加载失败时不打断页面编辑流程。
    }
  };

  const compareTestCases = async () => {
    if (!projectId) return alert('请先选择项目');
    if (!evalGenerated || (!evalModified && !resources.file)) {
      return alert('请填写测试用例内容或上传文件');
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

      const data = await compareTestCasesRequest(formData);
      setEvalResult(data.result || '');
      if (projectId) {
        try {
          const refreshed = await fetchGenerationHistory(projectId);
          if (Array.isArray(refreshed)) resources.setGenHistory(refreshed);
        } catch {
          // 忽略刷新失败，不影响本次评估结果展示
        }
      }
    } catch (e) {
      const msg = await translateError(e);
      setEvalResult(msg);
    } finally {
      resources.setLoading(null);
    }
  };

  /** 自动评估只触发一次，避免和用户手动编辑互相覆盖。 */
  useEffect(() => {
    if (shouldAutoEval && evalGenerated && !resources.loading && setShouldAutoEval) {
      onLog('测试用例生成完毕，自动触发质量评估...');
      void compareTestCases();
      setShouldAutoEval(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shouldAutoEval, evalGenerated, resources.loading]);

  const evaluateUi = async () => {
    if (!projectId) return alert('请先选择项目');

    resources.setLoading('ui');
    setUiEvalOutput(null);
    onLog('评估 UI 自动化...');

    try {
      const data = await evaluateUiRequest({
        script: uiEvalScript,
        execution_result: uiEvalExec,
        project_id: projectId,
        journey_json: resources.uiEvalJourney || undefined,
      });
      setUiEvalOutput(data.result || '');
    } catch (e) {
      const msg = await translateError(e);
      setUiEvalOutput(msg);
    } finally {
      resources.setLoading(null);
    }
  };

  const evaluateApi = async () => {
    if (!projectId) return alert('请先选择项目');

    resources.setLoading('api');
    setApiEvalOutput(null);
    onLog('评估接口测试...');

    try {
      const data = await evaluateApiRequest({
        script: apiEvalScript,
        execution_result: apiEvalExec,
        project_id: projectId,
        openapi_spec: resources.apiEvalSpec || undefined,
      });
      setApiEvalOutput(data.result || '');
    } catch (e) {
      const msg = await translateError(e);
      setApiEvalOutput(msg);
    } finally {
      resources.setLoading(null);
    }
  };

  const exportHistory = async () => {
    if (!projectId) return alert('请先选择项目');

    try {
      const allLogs = await fetchProjectLogs(projectId);
      const qmLogs = allLogs
        .filter((l: any) => typeof l.message === 'string' && l.message.startsWith('GEN_QM:'))
        .slice(0, 50);

      if (qmLogs.length === 0) return alert('暂无历史质量指标');

      const header = [
        'created_at',
        'positive',
        'negative',
        'edge',
        'functional_count',
        'non_functional_count',
        'avg_steps',
        'pending',
        'generated_count',
      ];
      const rows = qmLogs.map((l: any) => {
        let qm: any = {};
        try {
          qm = JSON.parse(l.message.substring('GEN_QM:'.length));
        } catch {
          qm = {};
        }

        const ts = l.created_at || '';
        return [
          ts,
          qm.positive || 0,
          qm.negative || 0,
          qm.edge || 0,
          qm.functional_count || 0,
          qm.non_functional_count || 0,
          qm.avg_steps || 0,
          qm.pending || 0,
          qm.generated_count || 0,
        ].join(',');
      });

      const csv = `${header.join(',')}\n${rows.join('\n')}`;
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'quality_metrics_history.csv';
      document.body.appendChild(a);
      a.click();
      a.remove();

      onLog('已导出历史质量指标');
    } catch (e) {
      alert(`导出失败: ${e}`);
    }
  };

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
  };
}

export type EvaluationActions = ReturnType<typeof useEvaluationActions>;
