import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getApiRequestErrorText } from '../../utils/api';
import {
  cancelAgentRun,
  createAgentRun,
  decideAgentApproval,
  exportAgentRunTestCases,
  getActiveAgentRun,
  getAgentCatalog,
  getAgentRun,
  getAgentRunSummary,
  getAgentRuns,
  getGenerationReuseCandidate,
  getRequirementDocumentParseStatus,
  listRequirementDocuments,
  resetAgentRunAttempt,
  retryAgentRun,
  uploadRequirementDocument,
} from './agentApi';
import { rememberRequirement } from './requirementSelection';
import type {
  AgentCatalog,
  AgentRun,
  GenerationRunInput,
  GenerationReuseCandidate,
  RequirementDocumentOption,
} from './types';
import {
  isWorkspaceRequestCurrent,
  releaseWorkspaceRequest,
  type WorkspaceRequestScope,
} from './workspaceRequestScope';

const TEST_GENERATION_WORKFLOW_KEY = 'test_generation';
const ACTIVE_RUN_STATUSES: AgentRun['status'][] = ['pending', 'running', 'waiting_approval'];

type WorkspaceRead = 'workspace' | 'documentList' | 'documentStatus' | 'activeRun'
  | 'runDetail' | 'runSummary';

function latestRequirementDocumentId(documents: RequirementDocumentOption[]): number | null {
  return documents.reduce<number | null>(
    (latest, document) => latest === null || document.id > latest ? document.id : latest,
    null,
  );
}

function mergeSelectedDocument(
  documents: RequirementDocumentOption[],
  currentDocuments: RequirementDocumentOption[],
  selectedId: number | null,
): RequirementDocumentOption[] {
  if (!selectedId || documents.some((document) => document.id === selectedId)) return documents;
  const selected = currentDocuments.find((document) => document.id === selectedId);
  return selected ? [...documents, selected] : documents;
}

type Options = {
  projectId: number | null;
  onLog: (message: string) => void;
};

export function useAgentWorkspace({ projectId, onLog }: Options) {
  const [catalog, setCatalog] = useState<AgentCatalog>({ agents: [], tools: [], workflows: [] });
  const [activeRun, setActiveRun] = useState<AgentRun | null>(null);
  const [requirementDocuments, setRequirementDocuments] = useState<RequirementDocumentOption[]>([]);
  const [requirementDocId, setRequirementDocId] = useState<number | null>(null);
  const [caseBudget, setCaseBudget] = useState(20);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [resettingAttempt, setResettingAttempt] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [reuseCandidate, setReuseCandidate] = useState<GenerationReuseCandidate | null>(null);
  const [showReusePrompt, setShowReusePrompt] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [readErrors, setReadErrors] = useState<Partial<Record<WorkspaceRead, string | null>>>({});
  const projectIdRef = useRef(projectId);
  const workspaceRequestRef = useRef(0);
  const documentRequestRef = useRef(0);
  const selectionRevisionRef = useRef(0);
  const requirementDocIdRef = useRef(requirementDocId);
  const activeRunRef = useRef(activeRun);
  const runVersionRef = useRef(0);
  const runRequestRef = useRef(0);
  const submissionRef = useRef<WorkspaceRequestScope | null>(null);
  const runMutationRef = useRef<WorkspaceRequestScope | null>(null);
  const uploadRequestRef = useRef<WorkspaceRequestScope | null>(null);
  const exportRequestRef = useRef<WorkspaceRequestScope | null>(null);

  // 项目切换立即作废旧请求；即使 A -> B -> A，也不能接受第一次 A 的响应。
  if (projectIdRef.current !== projectId) {
    projectIdRef.current = projectId;
    workspaceRequestRef.current += 1;
  }

  const captureScope = useCallback((options: { run?: boolean; selection?: boolean } = {}) => ({
    projectId: projectIdRef.current,
    workspaceVersion: workspaceRequestRef.current,
    ...(options.run ? { runId: activeRunRef.current?.id ?? null, runVersion: runVersionRef.current } : {}),
    ...(options.selection ? { selectionVersion: selectionRevisionRef.current } : {}),
  }), []);

  const isCurrentScope = useCallback((scope: WorkspaceRequestScope) => isWorkspaceRequestCurrent(
    scope,
    captureScope({ run: true, selection: true }),
  ), [captureScope]);

  const setReadError = useCallback((source: WorkspaceRead, reason: unknown) => {
    // 各读取链路独立恢复，轮询成功只清理同一个读取来源的旧提示。
    const message = reason === null ? null : getApiRequestErrorText(reason);
    setReadErrors((current) => (current[source] ?? null) === message
      ? current
      : { ...current, [source]: message });
  }, []);

  const selectRequirementDocument = useCallback((documentId: number | null) => {
    if (requirementDocIdRef.current !== documentId) setReadError('documentStatus', null);
    requirementDocIdRef.current = documentId;
    setRequirementDocId(documentId);
    if (projectId && documentId) rememberRequirement(projectId, documentId);
  }, [projectId, setReadError]);

  const commitRun = useCallback((scope: WorkspaceRequestScope, run: AgentRun | null) => {
    if (!isCurrentScope(scope) || (run && run.project_id !== scope.projectId)) return false;
    if ((activeRunRef.current?.id ?? null) !== (run?.id ?? null)) {
      runVersionRef.current += 1;
      runRequestRef.current += 1;
      setReadErrors((current) => ({ ...current, runDetail: null, runSummary: null }));
    }
    activeRunRef.current = run;
    setActiveRun(run);
    // 请求主动打开新运行时，将后续详情请求及收尾绑定到它刚刚选中的运行。
    if (scope.runVersion !== undefined) {
      scope.runId = run?.id ?? null;
      scope.runVersion = runVersionRef.current;
    }
    return true;
  }, [isCurrentScope]);

  const beginRunMutation = (scope: WorkspaceRequestScope) => {
    if (runMutationRef.current && isCurrentScope(runMutationRef.current)) return false;
    runMutationRef.current = scope;
    runRequestRef.current += 1;
    return true;
  };

  const beginSubmission = (scope: WorkspaceRequestScope) => {
    if ((submissionRef.current && isCurrentScope(submissionRef.current)) || !beginRunMutation(scope)) return false;
    submissionRef.current = scope;
    setSubmitting(true);
    return true;
  };

  const finishSubmission = (scope: WorkspaceRequestScope) => {
    releaseWorkspaceRequest(scope, runMutationRef, captureScope());
    if (releaseWorkspaceRequest(scope, submissionRef, captureScope())) {
      setSubmitting(false);
      setRetrying(false);
    }
  };

  const selectedWorkflow = useMemo(
    () => catalog.workflows.find(
      (item) => item.workflow_key === TEST_GENERATION_WORKFLOW_KEY,
    ) ?? null,
    [catalog.workflows],
  );

  const refreshRequirementDocuments = useCallback(async () => {
    const scope = captureScope();
    if (!scope.projectId) return;
    const requestId = ++documentRequestRef.current;
    try {
      const response = await listRequirementDocuments(scope.projectId);
      if (!isCurrentScope(scope) || requestId !== documentRequestRef.current) return;
      setReadError('documentList', null);
      setRequirementDocuments((current) => mergeSelectedDocument(
        response,
        current,
        requirementDocIdRef.current,
      ));
      return response;
    } catch (reason) {
      if (isCurrentScope(scope) && requestId === documentRequestRef.current) {
        setReadError('documentList', reason);
      }
    }
  }, [captureScope, isCurrentScope, setReadError]);

  const openRun = useCallback(async (scope: WorkspaceRequestScope, runId: number) => {
    if (!isCurrentScope(scope)) return null;
    const requestId = ++runRequestRef.current;
    try {
      const runResponse = await getAgentRun(runId);
      if (requestId !== runRequestRef.current || !commitRun(scope, runResponse.run)) return null;
      setReadError('runDetail', null);
      return runResponse.run;
    } catch (reason) {
      if (requestId === runRequestRef.current && isCurrentScope(scope)) {
        setReadError('runDetail', reason);
      }
      return null;
    }
  }, [commitRun, isCurrentScope, setReadError]);

  const loadWorkspace = useCallback(async () => {
    workspaceRequestRef.current += 1;
    const scope = captureScope();
    const documentRequestId = ++documentRequestRef.current;
    const selectionRevision = selectionRevisionRef.current;
    submissionRef.current = null;
    runMutationRef.current = null;
    uploadRequestRef.current = null;
    exportRequestRef.current = null;
    setSubmitting(false);
    setRetrying(false);
    setResettingAttempt(false);
    setUploading(false);
    setExporting(false);
    setError(null);
    setReadErrors({});
    commitRun(scope, null);
    selectRequirementDocument(null);
    setCatalog({ agents: [], tools: [], workflows: [] });
    setRequirementDocuments([]);
    setReuseCandidate(null);
    setShowReusePrompt(false);
    if (!projectId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [catalogResponse, documentResponse, activeRunResponse] = await Promise.all([
        getAgentCatalog(projectId),
        listRequirementDocuments(projectId),
        getActiveAgentRun(projectId, TEST_GENERATION_WORKFLOW_KEY),
      ]);
      if (!isCurrentScope(scope)) return;
      const workflow = catalogResponse.workflows.find(
        (item) => item.workflow_key === TEST_GENERATION_WORKFLOW_KEY,
      );
      // 优先恢复活动运行；没有活动运行时，保留最近一次生成的结果及失败续跑入口。
      const restoredRun = activeRunResponse.run
        ? (await getAgentRun(activeRunResponse.run.id)).run
        : workflow
          ? (await getAgentRuns(projectId, { workflowKey: workflow.workflow_key, limit: 1 }))[0] ?? null
          : null;
      if (!isCurrentScope(scope)) return;
      setCatalog(catalogResponse);
      if (documentRequestId === documentRequestRef.current) {
        setRequirementDocuments(documentResponse);
      }
      commitRun(scope, restoredRun);
      const restoredDocumentId = Number(restoredRun?.input_payload.requirement_doc_id);
      if (selectionRevision === selectionRevisionRef.current) {
        selectRequirementDocument(
          restoredRun && Number.isInteger(restoredDocumentId) && restoredDocumentId > 0
            ? restoredDocumentId
            : latestRequirementDocumentId(documentResponse),
        );
      }
      const restoredCaseBudget = Number(restoredRun?.input_payload.case_budget);
      if (restoredRun && Number.isInteger(restoredCaseBudget) && restoredCaseBudget > 0) {
        setCaseBudget(restoredCaseBudget);
      }
      if (!workflow) {
        throw new Error('当前项目未配置测试用例生成工作流，请先完成系统初始化');
      }
    } catch (reason) {
      if (isCurrentScope(scope)) {
        setReadError('workspace', reason);
      }
    } finally {
      if (isCurrentScope(scope)) setLoading(false);
    }
  }, [captureScope, commitRun, isCurrentScope, projectId, selectRequirementDocument, setReadError]);

  useEffect(() => {
    void loadWorkspace();
    return () => {
      workspaceRequestRef.current += 1;
    };
  }, [loadWorkspace]);

  const selectedParseStatus = requirementDocuments.find(
    (document) => document.id === requirementDocId,
  )?.parse_status;

  useEffect(() => {
    if (!projectId || uploading || !requirementDocId || !selectedParseStatus
      || !['pending', 'parsing'].includes(selectedParseStatus)) return;
    const documentId = requirementDocId;
    const scope = captureScope({ selection: true });
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      if (stopped || !isCurrentScope(scope)) return;
      const requestId = ++documentRequestRef.current;
      let keepPolling = true;
      try {
        const response = await getRequirementDocumentParseStatus(documentId);
        if (stopped || !isCurrentScope(scope)
          || requirementDocIdRef.current !== documentId
          || requestId !== documentRequestRef.current) return;
        setReadError('documentStatus', null);
        setRequirementDocuments((current) => current.map((document) =>
          document.id === documentId
            ? { ...document, parse_status: response.parse_status, parse_error: response.parse_error }
            : document,
        ));
        keepPolling = ['pending', 'parsing'].includes(response.parse_status);
        if (!keepPolling) {
          void refreshRequirementDocuments();
        }
      } catch (reason) {
        if (!stopped && isCurrentScope(scope) && requirementDocIdRef.current === documentId
          && requestId === documentRequestRef.current) {
          setReadError('documentStatus', reason);
        }
      } finally {
        if (keepPolling && !stopped && isCurrentScope(scope)
          && requirementDocIdRef.current === documentId) {
          timer = window.setTimeout(() => void poll(), 2000);
        }
      }
    };
    timer = window.setTimeout(() => void poll(), 2000);
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [captureScope, isCurrentScope, projectId, refreshRequirementDocuments, requirementDocId, selectedParseStatus, setReadError, uploading]);

  useEffect(() => {
    if (!activeRun || activeRun.project_id !== projectId
      || !['pending', 'running', 'waiting_approval'].includes(activeRun.status)) return;
    const runId = activeRun.id;
    const scope = captureScope({ run: true });
    let stopped = false;
    let timer: number | undefined;
    let summaryPollCount = 0;
    const poll = async () => {
      if (stopped || !isCurrentScope(scope)) return;
      if (!runMutationRef.current || !isCurrentScope(runMutationRef.current)) {
        try {
          const summary = (await getAgentRunSummary(runId)).run;
          if (stopped || !isCurrentScope(scope)) return;
          setReadError('runSummary', null);
          summaryPollCount += 1;
          const statusChanged = summary.status !== activeRun.status
            || summary.current_node_key !== activeRun.current_node_key;
          const refreshDetails = statusChanged || summaryPollCount % 5 === 0;
          if (refreshDetails && isCurrentScope(scope)) {
            await openRun(scope, runId);
          }
        } catch (reason) {
          if (!stopped && isCurrentScope(scope)) {
            setReadError('runSummary', reason);
          }
        }
      }
      if (!stopped && isCurrentScope(scope)) timer = window.setTimeout(() => void poll(), 2000);
    };
    timer = window.setTimeout(() => void poll(), 2000);
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [activeRun?.id, activeRun?.status, activeRun?.current_node_key, captureScope, isCurrentScope, openRun, projectId, setReadError]);

  useEffect(() => {
    if (!projectId || !activeRun || activeRun.project_id !== projectId
      || ACTIVE_RUN_STATUSES.includes(activeRun.status)) return;
    const scope = captureScope({ run: true });
    const requestId = runRequestRef.current;
    let stopped = false;
    void getActiveAgentRun(projectId, TEST_GENERATION_WORKFLOW_KEY).then(async (response) => {
      if (stopped || !isCurrentScope(scope) || requestId !== runRequestRef.current) return;
      setReadError('activeRun', null);
      const replacement = response.run;
      if (!runMutationRef.current && replacement && replacement.id !== activeRun.id
        && commitRun(scope, replacement)) {
        await openRun(scope, replacement.id);
      }
    }).catch((reason) => {
      if (!stopped && isCurrentScope(scope) && requestId === runRequestRef.current) {
        setReadError('activeRun', reason);
      }
    });
    return () => {
      stopped = true;
    };
  }, [activeRun?.id, activeRun?.status, captureScope, commitRun, isCurrentScope, openRun, projectId, setReadError]);

  const createAndOpenRun = async (scope: WorkspaceRequestScope, disableResultCache: boolean) => {
    if (!scope.projectId || !requirementDocId || !isCurrentScope(scope)) return;
    const response = await createAgentRun({
      project_id: scope.projectId,
      workflow_key: TEST_GENERATION_WORKFLOW_KEY,
      input_payload: {
        requirement: '',
        requirement_doc_id: requirementDocId,
        case_budget: Math.max(1, Math.min(200, Number(caseBudget) || 20)),
        batch_case_limit: 20,
        ...(disableResultCache ? { disable_result_cache: true } : {}),
      } satisfies GenerationRunInput,
    });
    if (!commitRun(scope, response.run)) return;
    onLog(response.status === 'already_active'
      ? `已恢复正在执行的 Run #${response.run.id}`
      : disableResultCache ? '已开始重新生成，成功后将替换同源结果' : '已开始本次 Agent 生成');
    await openRun(scope, response.run.id);
  };

  const runWorkflow = async () => {
    if (submissionRef.current && isCurrentScope(submissionRef.current)) return;
    if (activeRun && ACTIVE_RUN_STATUSES.includes(activeRun.status)) {
      setError(`已有 Run #${activeRun.id} 正在等待或执行，请先等待完成或取消该运行。`);
      return;
    }
    if (!projectId || !selectedWorkflow || !requirementDocId) {
      setError('请先上传需求文档，并等待解析完成。');
      return;
    }
    const selectedDocument = requirementDocuments.find((document) => document.id === requirementDocId);
    if (selectedDocument && selectedDocument.parse_status !== 'success') {
      setError('需求文档尚未解析成功，请等待解析完成后再启动生成。');
      return;
    }
    const scope = captureScope({ run: true, selection: true });
    if (!beginSubmission(scope)) return;
    setError(null);
    try {
      const response = await getGenerationReuseCandidate(
        projectId,
        requirementDocId,
        selectedWorkflow.workflow_key,
      );
      if (!isCurrentScope(scope)) return;
      if (response.candidate) {
        setReuseCandidate(response.candidate);
        setShowReusePrompt(true);
        return;
      }
      await createAndOpenRun(scope, false);
    } catch (reason) {
      if (isCurrentScope(scope)) setError(getApiRequestErrorText(reason));
    } finally {
      finishSubmission(scope);
    }
  };

  const reuseExistingGeneration = async () => {
    if (!reuseCandidate) return;
    const candidate = reuseCandidate;
    const scope = captureScope({ run: true, selection: true });
    if (!beginSubmission(scope)) return;
    setError(null);
    setShowReusePrompt(false);
    try {
      const run = await openRun(scope, candidate.run_id);
      if (!run || !isCurrentScope(scope)) return;
      onLog(`已复用 Run #${candidate.run_id} 的 ${candidate.case_count} 条测试用例`);
      setReuseCandidate(null);
    } catch (reason) {
      if (isCurrentScope(scope)) setError(getApiRequestErrorText(reason));
    } finally {
      finishSubmission(scope);
    }
  };

  const retryRun = async () => {
    if (resettingAttempt || uploading || !projectId || !activeRun
      || activeRun.project_id !== projectId || !['failed', 'cancelled'].includes(activeRun.status)) return;
    const sourceRun = activeRun;
    const scope = captureScope({ run: true });
    const sourceDocumentId = Number(sourceRun.input_payload.requirement_doc_id);
    const sourceCaseBudget = Number(sourceRun.input_payload.case_budget);
    if (!Number.isInteger(sourceDocumentId) || sourceDocumentId <= 0
      || !Number.isInteger(sourceCaseBudget) || sourceCaseBudget <= 0) {
      setError('原运行缺少有效的需求文档或用例数量，无法继续失败任务。');
      return;
    }
    if (!beginSubmission(scope)) return;
    setRetrying(true);
    setError(null);
    setReuseCandidate(null);
    setShowReusePrompt(false);
    // 续跑由后端沿用原输入，先同步控件，避免误以为使用了编辑后的参数。
    selectionRevisionRef.current += 1;
    documentRequestRef.current += 1;
    selectRequirementDocument(sourceDocumentId);
    setCaseBudget(sourceCaseBudget);
    try {
      const response = await retryAgentRun(sourceRun.id);
      if (!commitRun(scope, response.run)) return;
      onLog(`已继续 Run #${sourceRun.id} 的未完成任务，新运行为 Run #${response.run.id}，沿用原运行参数`);
    } catch (reason) {
      if (isCurrentScope(scope)) setError(getApiRequestErrorText(reason));
    } finally {
      finishSubmission(scope);
    }
  };

  const regenerateGeneration = async () => {
    if (!reuseCandidate) return;
    const scope = captureScope({ run: true, selection: true });
    if (!beginSubmission(scope)) return;
    setError(null);
    setShowReusePrompt(false);
    try {
      await createAndOpenRun(scope, true);
      if (!isCurrentScope(scope)) return;
      setReuseCandidate(null);
    } catch (reason) {
      if (isCurrentScope(scope)) setError(getApiRequestErrorText(reason));
    } finally {
      finishSubmission(scope);
    }
  };

  const uploadRequirement = async (file: File) => {
    if (!projectId) return;
    selectionRevisionRef.current += 1;
    documentRequestRef.current += 1;
    const scope = captureScope({ selection: true });
    uploadRequestRef.current = scope;
    setUploading(true);
    setError(null);
    setReuseCandidate(null);
    setShowReusePrompt(false);
    try {
      const response = await uploadRequirementDocument(projectId, file);
      if (!isCurrentScope(scope)) return;
      if (!response.success) throw new Error('需求文档上传失败');
      selectRequirementDocument(response.id);
      setRequirementDocuments((current) => {
        const uploaded: RequirementDocumentOption = {
          id: response.id,
          filename: response.filename,
          doc_type: 'requirement',
          content_preview: '',
          linked_test_case_count: 0,
          parse_status: response.parse_status,
          parse_error: null,
        };
        const existingIndex = current.findIndex((document) => document.id === response.id);
        if (existingIndex < 0) return [...current, uploaded];
        return current.map((document) => document.id === response.id ? uploaded : document);
      });
      onLog(`需求文档“${response.filename}”已上传，正在准备页面资产`);
    } catch (reason) {
      if (isCurrentScope(scope)) setError(getApiRequestErrorText(reason));
    } finally {
      if (releaseWorkspaceRequest(scope, uploadRequestRef, captureScope())) setUploading(false);
    }
  };

  const cancelRun = async () => {
    if (!activeRun || activeRun.project_id !== projectId) return;
    const scope = captureScope({ run: true });
    if (!beginRunMutation(scope)) return;
    setError(null);
    try {
      const response = await cancelAgentRun(activeRun.id);
      if (!commitRun(scope, response.run)) return;
      onLog('已取消本次 Agent 生成');
      await openRun(scope, activeRun.id);
    } catch (reason) {
      if (isCurrentScope(scope)) setError(getApiRequestErrorText(reason));
    } finally {
      releaseWorkspaceRequest(scope, runMutationRef, captureScope());
    }
  };

  const resetRunAttempt = async () => {
    if (!activeRun || activeRun.project_id !== projectId || ACTIVE_RUN_STATUSES.includes(activeRun.status)) return;
    const scope = captureScope({ run: true });
    if (!beginRunMutation(scope)) return;
    setResettingAttempt(true);
    setError(null);
    try {
      const response = await resetAgentRunAttempt(activeRun.id);
      if (!commitRun(scope, response.run)) return;
      onLog(response.status === 'already_reset' ? '本次运行次数已经是 1' : '已重置本次运行次数');
    } catch (reason) {
      if (isCurrentScope(scope)) setError(getApiRequestErrorText(reason));
    } finally {
      if (releaseWorkspaceRequest(scope, runMutationRef, captureScope())) setResettingAttempt(false);
    }
  };

  const decideApproval = async (approvalId: number, approved: boolean, answers: Record<string, string> = {}) => {
    if (!activeRun || activeRun.project_id !== projectId) return false;
    const scope = captureScope({ run: true });
    if (!beginRunMutation(scope)) return false;
    setError(null);
    try {
      await decideAgentApproval(approvalId, approved, '', answers);
      if (!isCurrentScope(scope)) return false;
      const run = await openRun(scope, activeRun.id);
      if (!run || !isCurrentScope(scope)) return false;
      onLog(`审批 #${approvalId} 已${approved ? '通过' : '拒绝'}`);
      return true;
    } catch (reason) {
      if (isCurrentScope(scope)) setError(getApiRequestErrorText(reason));
      return false;
    } finally {
      releaseWorkspaceRequest(scope, runMutationRef, captureScope());
    }
  };

  const exportTestCases = async () => {
    if (!activeRun || activeRun.project_id !== projectId || exporting) return;
    const scope = captureScope({ run: true });
    exportRequestRef.current = scope;
    setExporting(true);
    setError(null);
    try {
      const blob = await exportAgentRunTestCases(activeRun.id);
      if (!isCurrentScope(scope)) return;
      const documentId = Number(activeRun.input_payload.requirement_doc_id);
      const sourceDocument = requirementDocuments.find((item) => item.id === documentId);
      const sourceName = String(sourceDocument?.filename || '').replace(/\.[^.]+$/, '');
      const safeName = sourceName.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').trim();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${safeName || `Run_${activeRun.id}`}_测试用例.xlsx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      onLog(`已导出 Run #${activeRun.id} 的 Excel 测试用例`);
    } catch (reason) {
      if (isCurrentScope(scope)) setError(getApiRequestErrorText(reason));
    } finally {
      if (releaseWorkspaceRequest(scope, exportRequestRef, captureScope())) setExporting(false);
    }
  };

  return {
    catalog,
    activeRun,
    requirementDocuments,
    selectedWorkflow,
    requirementDocId,
    caseBudget,
    loading,
    submitting,
    retrying,
    resettingAttempt,
    uploading,
    exporting,
    reuseCandidate,
    showReusePrompt,
    error: error || Object.values(readErrors).filter(Boolean).join('；') || null,
    setCaseBudget,
    refreshRequirementDocuments,
    uploadRequirement,
    runWorkflow,
    retryRun,
    reuseExistingGeneration,
    regenerateGeneration,
    dismissReusePrompt: () => setShowReusePrompt(false),
    cancelRun,
    resetRunAttempt,
    decideApproval,
    exportTestCases,
  };
}
