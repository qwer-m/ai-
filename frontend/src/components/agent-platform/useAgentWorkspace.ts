import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  cancelAgentRun,
  createAgentRun,
  decideAgentApproval,
  getActiveAgentRun,
  getAgentCatalog,
  getAgentRun,
  getRequirementDocumentParseStatus,
  listRequirementDocuments,
  resetAgentRunAttempt,
  uploadRequirementDocument,
} from './agentApi';
import type { AgentCatalog, AgentRun, RequirementDocumentOption } from './types';

const TEST_GENERATION_WORKFLOW_KEY = 'test_generation';
const ACTIVE_RUN_STATUSES: AgentRun['status'][] = ['pending', 'running', 'waiting_approval'];

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
  // 新建 Run 默认启用上下文压缩；历史 Run 有明确配置时恢复其实际值。
  const [enableContextCompression, setEnableContextCompression] = useState(true);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [resettingAttempt, setResettingAttempt] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const projectIdRef = useRef(projectId);
  const workspaceRequestRef = useRef(0);
  const documentRequestRef = useRef(0);
  const selectionRevisionRef = useRef(0);
  const requirementDocIdRef = useRef(requirementDocId);
  const submissionRef = useRef(false);
  projectIdRef.current = projectId;
  requirementDocIdRef.current = requirementDocId;

  const selectedWorkflow = useMemo(
    () => catalog.workflows.find(
      (item) => item.workflow_key === TEST_GENERATION_WORKFLOW_KEY,
    ) ?? null,
    [catalog.workflows],
  );

  const refreshRequirementDocuments = useCallback(async () => {
    if (!projectId) return;
    const requestId = ++documentRequestRef.current;
    const response = await listRequirementDocuments(projectId);
    if (projectIdRef.current === projectId && requestId === documentRequestRef.current) {
      setRequirementDocuments((current) => mergeSelectedDocument(
        response,
        current,
        requirementDocIdRef.current,
      ));
    }
    return response;
  }, [projectId]);

  const openRun = useCallback(async (runId: number) => {
    const runResponse = await getAgentRun(runId);
    if (projectIdRef.current === runResponse.run.project_id) {
      setActiveRun(runResponse.run);
      const compression = runResponse.run.input_payload.enable_context_compression;
      const legacyCompression = runResponse.run.input_payload.compress;
      if (typeof compression === 'boolean') {
        setEnableContextCompression(compression);
      } else if (typeof legacyCompression === 'boolean') {
        setEnableContextCompression(legacyCompression);
      } else {
        setEnableContextCompression(true);
      }
    }
    return runResponse.run;
  }, []);

  const loadWorkspace = useCallback(async () => {
    const requestId = ++workspaceRequestRef.current;
    const documentRequestId = ++documentRequestRef.current;
    const selectionRevision = selectionRevisionRef.current;
    if (!projectId) {
      setCatalog({ agents: [], tools: [], workflows: [] });
      setRequirementDocuments([]);
      setActiveRun(null);
      setEnableContextCompression(true);
      return;
    }
    setLoading(true);
    setError(null);
    setActiveRun(null);
    try {
      const [catalogResponse, documentResponse, activeRunResponse] = await Promise.all([
        getAgentCatalog(projectId),
        listRequirementDocuments(projectId),
        getActiveAgentRun(projectId),
      ]);
      if (requestId !== workspaceRequestRef.current || projectIdRef.current !== projectId) return;
      setCatalog(catalogResponse);
      if (documentRequestId === documentRequestRef.current) {
        setRequirementDocuments(documentResponse);
      }
      const active = activeRunResponse.run;
      setActiveRun(active);
      const activeDocumentId = Number(active?.input_payload.requirement_doc_id);
      if (selectionRevision === selectionRevisionRef.current) {
        setRequirementDocId(
          active && Number.isInteger(activeDocumentId) && activeDocumentId > 0
            ? activeDocumentId
            : latestRequirementDocumentId(documentResponse),
        );
      }
      const activeCaseBudget = Number(active?.input_payload.case_budget);
      if (active && Number.isInteger(activeCaseBudget) && activeCaseBudget > 0) {
        setCaseBudget(activeCaseBudget);
      }
      const activeCompression = active?.input_payload.enable_context_compression;
      const legacyCompression = active?.input_payload.compress;
      if (typeof activeCompression === 'boolean') {
        setEnableContextCompression(activeCompression);
      } else if (typeof legacyCompression === 'boolean') {
        setEnableContextCompression(legacyCompression);
      } else {
        setEnableContextCompression(true);
      }
      if (!catalogResponse.workflows.some(
        (item) => item.workflow_key === TEST_GENERATION_WORKFLOW_KEY,
      )) {
        throw new Error('当前项目未配置测试用例生成工作流，请先完成系统初始化');
      }
      if (active) void openRun(active.id);
    } catch (reason) {
      if (requestId === workspaceRequestRef.current && projectIdRef.current === projectId) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (requestId === workspaceRequestRef.current) setLoading(false);
    }
  }, [openRun, projectId]);

  useEffect(() => {
    void loadWorkspace();
  }, [loadWorkspace]);

  const selectedParseStatus = requirementDocuments.find(
    (document) => document.id === requirementDocId,
  )?.parse_status;

  useEffect(() => {
    if (!projectId || !requirementDocId || !selectedParseStatus
      || !['pending', 'parsing'].includes(selectedParseStatus)) return;
    const documentId = requirementDocId;
    const pollingProjectId = projectId;
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const requestId = ++documentRequestRef.current;
        const response = await getRequirementDocumentParseStatus(documentId);
        if (stopped || projectIdRef.current !== pollingProjectId
          || requirementDocIdRef.current !== documentId
          || requestId !== documentRequestRef.current) return;
        setRequirementDocuments((current) => current.map((document) =>
          document.id === documentId
            ? { ...document, parse_status: response.parse_status, parse_error: response.parse_error }
            : document,
        ));
        if (['pending', 'parsing'].includes(response.parse_status)) {
          timer = window.setTimeout(() => void poll(), 2000);
        } else {
          void refreshRequirementDocuments();
        }
      } catch (reason) {
        if (!stopped) setError(reason instanceof Error ? reason.message : String(reason));
        if (!stopped) timer = window.setTimeout(() => void poll(), 2000);
      }
    };
    timer = window.setTimeout(() => void poll(), 2000);
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [projectId, refreshRequirementDocuments, requirementDocId, selectedParseStatus]);

  useEffect(() => {
    if (!activeRun || !['pending', 'running'].includes(activeRun.status)) return;
    const runId = activeRun.id;
    const runProjectId = activeRun.project_id;
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        await openRun(runId);
      } catch (reason) {
        if (!stopped && projectIdRef.current === runProjectId) {
          const message = reason instanceof Error ? reason.message : String(reason);
          setError(message);
        }
      }
      if (!stopped) timer = window.setTimeout(() => void poll(), 2000);
    };
    timer = window.setTimeout(() => void poll(), 2000);
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [activeRun?.id, activeRun?.status, openRun]);

  useEffect(() => {
    if (!projectId || !activeRun || ACTIVE_RUN_STATUSES.includes(activeRun.status)) return;
    let stopped = false;
    void getActiveAgentRun(projectId).then((response) => {
      const replacement = response.run;
      if (!stopped && replacement && replacement.id !== activeRun.id) {
        setActiveRun(replacement);
        void openRun(replacement.id);
      }
    }).catch((reason) => {
      if (!stopped) setError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => {
      stopped = true;
    };
  }, [activeRun?.id, activeRun?.status, openRun, projectId]);

  const runWorkflow = async () => {
    if (submissionRef.current) return;
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
    submissionRef.current = true;
    setSubmitting(true);
    setError(null);
    const runProjectId = projectId;
    try {
      const response = await createAgentRun({
        project_id: projectId,
        workflow_key: TEST_GENERATION_WORKFLOW_KEY,
        input_payload: {
          requirement: '',
          requirement_doc_id: requirementDocId,
          case_budget: Math.max(1, Math.min(200, Number(caseBudget) || 20)),
          batch_case_limit: 5,
          enable_context_compression: enableContextCompression,
        },
      });
      if (projectIdRef.current !== runProjectId) return;
      setActiveRun(response.run);
      onLog(response.status === 'already_active'
        ? `已恢复正在执行的 Run #${response.run.id}`
        : '已开始本次 Agent 生成');
      await openRun(response.run.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      submissionRef.current = false;
      setSubmitting(false);
    }
  };

  const uploadRequirement = async (file: File) => {
    if (!projectId) return;
    const uploadProjectId = projectId;
    selectionRevisionRef.current += 1;
    documentRequestRef.current += 1;
    setUploading(true);
    setError(null);
    try {
      const response = await uploadRequirementDocument(projectId, file);
      if (projectIdRef.current !== uploadProjectId) return;
      if (!response.success) throw new Error('需求文档上传失败');
      setRequirementDocId(response.id);
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
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setUploading(false);
    }
  };

  const cancelRun = async () => {
    if (!activeRun) return;
    setError(null);
    try {
      const response = await cancelAgentRun(activeRun.id);
      setActiveRun(response.run);
      onLog('已取消本次 Agent 生成');
      await openRun(activeRun.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const resetRunAttempt = async () => {
    if (!activeRun || ['pending', 'running', 'waiting_approval'].includes(activeRun.status)) return;
    setResettingAttempt(true);
    setError(null);
    try {
      const response = await resetAgentRunAttempt(activeRun.id);
      setActiveRun(response.run);
      onLog(response.status === 'already_reset' ? '本次运行次数已经是 1' : '已重置本次运行次数');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setResettingAttempt(false);
    }
  };

  const decideApproval = async (approvalId: number, approved: boolean) => {
    setError(null);
    try {
      await decideAgentApproval(approvalId, approved);
      if (activeRun) await openRun(activeRun.id);
      onLog(`审批 #${approvalId} 已${approved ? '通过' : '拒绝'}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  return {
    catalog,
    activeRun,
    requirementDocuments,
    selectedWorkflow,
    requirementDocId,
    caseBudget,
    enableContextCompression,
    loading,
    submitting,
    resettingAttempt,
    uploading,
    error,
    setCaseBudget,
    setEnableContextCompression,
    refreshRequirementDocuments,
    uploadRequirement,
    runWorkflow,
    cancelRun,
    resetRunAttempt,
    decideApproval,
  };
}
