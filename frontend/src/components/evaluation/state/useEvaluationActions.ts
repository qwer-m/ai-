import {
  evaluateTestCasesRequest,
  evaluateApiRequest,
  evaluateUiRequest,
  fetchAgentRunBundle,
  fetchAgentRunHistory,
  fetchAgentRunStatus,
  normalizeAutomationEvaluationReport,
  normalizeQualityReport,
  translateError,
} from './evaluationService';
import { useEvaluationResources } from './useEvaluationResources';
import type { EvaluationProps } from './types';

type UseEvaluationActionsParams = Pick<
  EvaluationProps,
  | 'projectId'
  | 'onLog'
  | 'view'
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
>;

const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

export function useEvaluationActions({
  projectId,
  onLog,
  view = 'root',
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
}: UseEvaluationActionsParams) {
  const resources = useEvaluationResources({ projectId, view, evalResult });

  const refreshRunHistory = async () => {
    if (!projectId) return;
    try {
      const nextHistory = await fetchAgentRunHistory(projectId);
      if (Array.isArray(nextHistory)) resources.setRunHistory(nextHistory);
    } catch {
      // 历史刷新失败不影响当前评估结果展示。
    }
  };

  const setReferenceFile = (file: File | null) => {
    resources.setFile(file);
    resources.setLoadedReferenceFilename('');
    resources.setUploadedReferenceFilename(file?.name || '');
  };

  const loadRunById = async (id: number) => {
    if (!id) return;
    try {
      resources.setSelectedRunId(id);
      const bundle = await fetchAgentRunBundle(id);
      const run = bundle.run;
      if (!run) return;

      setEvalGenerated(JSON.stringify(run.test_cases, null, 2));

      const evaluationArtifact = bundle?.evaluation_artifact;
      if (evaluationArtifact) {
        setEvalModified(evaluationArtifact.reference_content || '');
        setEvalResult(normalizeQualityReport(evaluationArtifact.evaluation));
        resources.setFile(null);
        resources.setUploadedReferenceFilename('');
        resources.setLoadedReferenceFilename(evaluationArtifact.upload?.filename || '人工参考内容');
        onLog('已从历史加载测试用例、人工参考内容与质量评估结果');
      } else {
        setEvalModified('');
        setEvalResult(null);
        resources.setFile(null);
        resources.setUploadedReferenceFilename('');
        resources.setLoadedReferenceFilename('');
        const message = bundle?.evaluation_status === 'missing'
          ? '该历史记录暂无已保存的对比文件与质量评估结果，请先点击“开始评估质量”生成一次。'
          : '未找到对应历史评估记录。';
        resources.setToastMsg({ type: 'error', msg: message });
        onLog(message);
      }
    } catch {
      // 历史加载失败时保持当前输入，避免清空用户内容。
    }
  };

  const waitForEvaluationRun = async (evaluationRunId: number, sourceRunId: number) => {
    for (let attempt = 0; attempt < 240; attempt += 1) {
      await wait(2000);
      const run = await fetchAgentRunStatus(evaluationRunId);
      const status = String(run?.status || '');
      if (status === 'success') {
        const bundle = await fetchAgentRunBundle(sourceRunId);
        const evaluation = bundle.evaluation_artifact?.evaluation;
        if (!evaluation) throw new Error('评测 Run 已完成，但源 Run 未生成评测产物');
        const report = normalizeQualityReport(evaluation);
        if (!report) throw new Error('评测 Run 产物结构无效');
        return report;
      }
      if (status === 'failed' || status === 'cancelled') {
        throw new Error(String(run?.error_message || `评测 Run 结束：${status}`));
      }
    }
    throw new Error('评测 Run 等待超时，可在 Agent 工作台查看执行状态');
  };

  const evaluateTestCases = async () => {
    if (!projectId) {
      window.alert('请先选择项目');
      return;
    }
    if (!resources.selectedRunId) {
      window.alert('请先选择已完成测试用例生成的 Agent Run');
      return;
    }
    if (!evalModified && !resources.file) {
      window.alert('请填写测试用例内容或上传文件');
      return;
    }

    resources.setLoading('eval');
    setEvalResult(null);
    onLog('评估测试用例质量...');

    try {
      const formData = new FormData();
      if (evalModified) formData.append('reference_content', evalModified);
      formData.append('project_id', String(projectId));
      formData.append('run_id', String(resources.selectedRunId));
      if (resources.file) formData.append('file', resources.file);
      if (resources.file?.name) resources.setUploadedReferenceFilename(resources.file.name);

      const response = await evaluateTestCasesRequest(formData);
      const evaluationRunId = Number(response?.run?.id || 0);
      if (!evaluationRunId) {
        throw new Error('评测接口未返回有效的 Agent Run');
      }
      onLog(`用例评测 Agent Run 已启动：run_id=${evaluationRunId}`);
      const result = await waitForEvaluationRun(evaluationRunId, resources.selectedRunId);
      setEvalResult(result);
      await refreshRunHistory();
      onLog('用例评测 Agent Run 已完成。');
    } catch (error) {
      const message = await translateError(error);
      setEvalResult(null);
      resources.setToastMsg({ type: 'error', msg: message });
      onLog(message);
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
      const journey = resources.uiEvalJourney.trim()
        ? JSON.parse(resources.uiEvalJourney)
        : undefined;
      if (journey !== undefined && (journey === null || Array.isArray(journey) || typeof journey !== 'object')) {
        throw new Error('用户旅程 JSON 顶层必须是对象');
      }
      const response = await evaluateUiRequest({
        script: uiEvalScript,
        execution_result: uiEvalExec,
        project_id: projectId,
        journey_json: journey,
      });
      const report = normalizeAutomationEvaluationReport(response.result);
      if (!report) throw new Error('UI 自动化评测 Agent 返回的报告结构无效');
      setUiEvalOutput(report);
      onLog(`UI 自动化评测完成：run_id=${response.run_id}`);
    } catch (error) {
      const message = await translateError(error);
      resources.setToastMsg({ type: 'error', msg: message });
      onLog(message);
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
      const report = normalizeAutomationEvaluationReport(response.result);
      if (!report) throw new Error('API 自动化评测 Agent 返回的报告结构无效');
      setApiEvalOutput(report);
      onLog(`API 自动化评测完成：run_id=${response.run_id}`);
    } catch (error) {
      const message = await translateError(error);
      resources.setToastMsg({ type: 'error', msg: message });
      onLog(message);
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
      if (resources.runHistory.length === 0) {
        window.alert('暂无 Agent Run 历史');
        return;
      }
      const escapeCsv = (value: unknown) => `"${String(value ?? '').replace(/"/g, '""')}"`;
      const header = ['run_id', 'status', 'created_at', 'finished_at', 'case_count', 'has_evaluation', 'requirement'];
      const rows = resources.runHistory.map((run) => [
        run.run_id,
        run.status,
        run.created_at,
        run.finished_at,
        run.case_count,
        run.has_evaluation,
        run.requirement_text,
      ].map(escapeCsv).join(','));
      const blob = new Blob([`${header.join(',')}\n${rows.join('\n')}`], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'agent_run_history.csv';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      onLog('已导出 Agent Run 历史');
    } catch (error) {
      window.alert(`导出失败: ${error}`);
    }
  };

  return {
    ...resources,
    evaluateTestCases,
    evaluateUi,
    evaluateApi,
    exportHistory,
    loadRunById,
    setReferenceFile,
  };
}
