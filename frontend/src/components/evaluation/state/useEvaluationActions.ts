import { useEffect, useRef, useState } from 'react';
import {
  evaluateTestCasesRequest,
  evaluateApiRequest,
  evaluateUiRequest,
  fetchAgentRunBundle,
  fetchAgentRunHistory,
  normalizeAutomationEvaluationReport,
  normalizeQualityReport,
  readTestEvaluationArtifact,
} from './evaluationService';
import { translateError } from '../../../utils/api';
import { getAgentRun, getAgentRunSummary } from '../../agent-platform/agentApi';
import { useEvaluationResources } from './useEvaluationResources';
import type { EvaluationProps, TestEvaluationArtifact } from './types';

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
  const scopeRef = useRef({ projectId, revision: 0 });
  if (scopeRef.current.projectId !== projectId) {
    scopeRef.current = { projectId, revision: scopeRef.current.revision + 1 };
  }
  const submittingRef = useRef(false);
  const [loadedSource, setLoadedSource] = useState<{ projectId: number; runId: number } | null>(null);
  const [loadedEvaluation, setLoadedEvaluation] = useState<{
    projectId: number;
    artifact: TestEvaluationArtifact;
  } | null>(null);
  const currentScope = () => ({ ...scopeRef.current });
  const isCurrentScope = (scope: ReturnType<typeof currentScope>) => (
    scope.projectId === scopeRef.current.projectId && scope.revision === scopeRef.current.revision
  );

  useEffect(() => {
    submittingRef.current = false;
    setLoadedSource(null);
    setLoadedEvaluation(null);
    resources.setLoading(null);
  }, [projectId]);

  useEffect(() => {
    // IndexedDB 恢复的文件与本地文本互斥，提交时也只发送一个人工版本。
    if (resources.file) setEvalModified('');
  }, [resources.file, setEvalModified]);

  const refreshRunHistory = async (scope: ReturnType<typeof currentScope>) => {
    if (!scope.projectId) return;
    try {
      const nextHistory = await fetchAgentRunHistory(scope.projectId);
      if (isCurrentScope(scope) && Array.isArray(nextHistory)) resources.setRunHistory(nextHistory);
    } catch {
      // 历史刷新失败不影响当前评估结果展示。
    }
  };

  const stopWaitingForPreviousRequest = () => {
    // 只终止当前页面等待，服务端的真实评测任务仍可从历史回读。
    scopeRef.current.revision += 1;
    submittingRef.current = false;
    resources.setLoading(null);
  };

  const setReferenceFile = (file: File | null) => {
    stopWaitingForPreviousRequest();
    setLoadedEvaluation(null);
    setEvalResult(null);
    if (file) setEvalModified('');
    resources.setFile(file);
    resources.setLoadedReferenceFilename('');
    resources.setUploadedReferenceFilename(file?.name || '');
  };

  const setReferenceText = (text: string) => {
    stopWaitingForPreviousRequest();
    setLoadedEvaluation(null);
    setEvalResult(null);
    resources.setFile(null);
    resources.setUploadedReferenceFilename('');
    resources.setLoadedReferenceFilename('');
    setEvalModified(text);
  };

  const selectRunId = (id: number | null) => {
    if (submittingRef.current) onLog('已停止等待原评测；后台运行会继续保留，可通过历史记录查看结果。');
    stopWaitingForPreviousRequest();
    resources.setSelectedRunId(id);
    resources.setBaselineText('');
    resources.setFile(null);
    resources.setUploadedReferenceFilename('');
    resources.setLoadedReferenceFilename('');
    setEvalGenerated('');
    setEvalModified('');
    setLoadedSource(null);
    setLoadedEvaluation(null);
    setEvalResult(null);
  };

  const loadRunById = async (id: number) => {
    if (!id) return;
    selectRunId(id);
    const scope = currentScope();
    try {
      const bundle = await fetchAgentRunBundle(id);
      if (!isCurrentScope(scope)) return;
      const run = bundle.run;
      if (!run || run.project_id !== scope.projectId) throw new Error('未找到当前项目的生成用例产物');

      setEvalGenerated(JSON.stringify(run.test_cases, null, 2));
      setLoadedSource({ projectId: run.project_id, runId: run.run_id });

      const evaluationArtifact = bundle?.evaluation_artifact;
      if (evaluationArtifact) {
        setLoadedEvaluation({ projectId: run.project_id, artifact: evaluationArtifact });
        resources.setBaselineText(evaluationArtifact.evaluation.requirement_baseline.groups.map(g => g.point).join('\n'));
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
    } catch (reason) {
      if (isCurrentScope(scope)) resources.setToastMsg({
        type: 'error', msg: reason instanceof Error ? reason.message : String(reason),
      });
    }
  };

  const waitForEvaluationRun = async (
    evaluationRunId: number, sourceRunId: number, scope: ReturnType<typeof currentScope>,
  ) => {
    for (let attempt = 0; attempt < 240; attempt += 1) {
      await wait(2000);
      if (!isCurrentScope(scope)) return null;
      const { run } = await getAgentRunSummary(evaluationRunId);
      if (!isCurrentScope(scope)) return null;
      if (run.project_id !== scope.projectId) throw new Error('评测运行与当前项目不一致');
      const status = run.status;
      if (status === 'success') {
        // 读取本次评测自己的产物，生成 Run 上的最近报告可能已被下一轮更新。
        const { run: completedRun } = await getAgentRun(evaluationRunId);
        if (!isCurrentScope(scope)) return null;
        const artifact = readTestEvaluationArtifact(completedRun, sourceRunId, evaluationRunId);
        if (!artifact) throw new Error('评测 Run 已完成，但缺少评测产物');
        const report = normalizeQualityReport(artifact.evaluation);
        if (!report) throw new Error('评测 Run 产物结构无效');
        return { report, artifact };
      }
      if (status === 'failed' || status === 'cancelled') {
        throw new Error(String(run?.error_message || `评测 Run 结束：${status}`));
      }
    }
    throw new Error('评测 Run 等待超时，可在 Agent 工作台查看执行状态');
  };

  const evaluateTestCases = async () => {
    if (submittingRef.current) return;
    if (!projectId) {
      window.alert('请先选择项目');
      return;
    }
    if (!resources.selectedRunId) {
      window.alert('请先选择已完成测试用例生成的 Agent Run');
      return;
    }
    if (loadedSource?.projectId !== projectId || loadedSource?.runId !== resources.selectedRunId) {
      resources.setToastMsg({ type: 'error', msg: '请先从服务器加载当前项目的生成记录，再开始评测。' });
      return;
    }
    if (!evalModified.trim() && !resources.file) {
      window.alert('请填写测试用例内容或上传文件');
      return;
    }

    submittingRef.current = true;
    const scope = currentScope();
    const sourceRunId = resources.selectedRunId;
    resources.setLoading('eval');
    setLoadedEvaluation(null);
    setEvalResult(null);
    onLog('评估测试用例质量...');

    try {
      const formData = new FormData();
      if (!resources.file && evalModified.trim()) formData.append('reference_content', evalModified.trim());
      formData.append('project_id', String(projectId));
      formData.append('run_id', String(sourceRunId));
      const groups = resources.baselineText.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
      formData.append('baseline_groups', JSON.stringify(groups));
      if (resources.file) formData.append('file', resources.file);
      if (resources.file?.name) resources.setUploadedReferenceFilename(resources.file.name);

      const response = await evaluateTestCasesRequest(formData);
      if (!isCurrentScope(scope)) return;
      const evaluationRunId = Number(response?.run?.id || 0);
      if (!evaluationRunId) {
        throw new Error('评测接口未返回有效的 Agent Run');
      }
      onLog(`用例评测 Agent Run 已启动：run_id=${evaluationRunId}`);
      const result = await waitForEvaluationRun(evaluationRunId, sourceRunId, scope);
      if (!result || !isCurrentScope(scope)) return;
      setLoadedEvaluation({ projectId, artifact: result.artifact });
      setEvalResult(result.report);
      await refreshRunHistory(scope);
      if (!isCurrentScope(scope)) return;
      onLog('用例评测 Agent Run 已完成。');
    } catch (error) {
      const message = await translateError(error);
      if (!isCurrentScope(scope)) return;
      setEvalResult(null);
      resources.setToastMsg({ type: 'error', msg: message });
      onLog(message);
    } finally {
      if (isCurrentScope(scope)) {
        submittingRef.current = false;
        resources.setLoading(null);
      }
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
    verifiedEvaluation: loadedSource?.projectId === projectId
      && loadedSource.runId === resources.selectedRunId
      && loadedEvaluation?.projectId === projectId
      && loadedEvaluation.artifact.source_run_id === resources.selectedRunId
      ? loadedEvaluation.artifact : null,
    sourceLoaded: loadedSource?.projectId === projectId && loadedSource.runId === resources.selectedRunId,
    selectRunId,
    evaluateTestCases,
    evaluateUi,
    evaluateApi,
    exportHistory,
    loadRunById,
    setReferenceFile,
    setReferenceText,
  };
}
