import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  cancelAgentRun,
  createAgentRun,
  decideAgentApproval,
  getAgentCatalog,
  getAgentRun,
  getAgentRunEvents,
  listRequirementDocuments,
  listAgentRuns,
  retryAgentRun,
} from './agentApi';
import type { AgentCatalog, AgentRun, AgentRunEvent, RequirementDocumentOption } from './types';

type Options = {
  projectId: number | null;
  onLog: (message: string) => void;
};

export function useAgentWorkspace({ projectId, onLog }: Options) {
  const [catalog, setCatalog] = useState<AgentCatalog>({ agents: [], tools: [], workflows: [] });
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [activeRun, setActiveRun] = useState<AgentRun | null>(null);
  const [events, setEvents] = useState<AgentRunEvent[]>([]);
  const [requirementDocuments, setRequirementDocuments] = useState<RequirementDocumentOption[]>([]);
  const [workflowKey, setWorkflowKey] = useState('');
  const [requirement, setRequirement] = useState('');
  const [requirementDocId, setRequirementDocId] = useState<number | null>(null);
  const [caseBudget, setCaseBudget] = useState(20);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedWorkflow = useMemo(
    () => catalog.workflows.find((item) => item.workflow_key === workflowKey) ?? null,
    [catalog.workflows, workflowKey],
  );

  const refreshRuns = useCallback(async () => {
    if (!projectId) return;
    const response = await listAgentRuns(projectId);
    setRuns(response.items);
  }, [projectId]);

  const openRun = useCallback(async (runId: number) => {
    const [runResponse, eventResponse] = await Promise.all([
      getAgentRun(runId),
      getAgentRunEvents(runId),
    ]);
    setActiveRun(runResponse.run);
    setEvents(eventResponse.items);
    return runResponse.run;
  }, []);

  const loadWorkspace = useCallback(async () => {
    if (!projectId) {
      setCatalog({ agents: [], tools: [], workflows: [] });
      setRuns([]);
      setRequirementDocuments([]);
      setActiveRun(null);
      setEvents([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [catalogResponse, runResponse, documentResponse] = await Promise.all([
        getAgentCatalog(projectId),
        listAgentRuns(projectId),
        listRequirementDocuments(projectId),
      ]);
      setCatalog(catalogResponse);
      setRuns(runResponse.items);
      setRequirementDocuments(documentResponse);
      setWorkflowKey((current) => (
        current
        || catalogResponse.workflows.find((item) => item.workflow_key === 'test_generation')?.workflow_key
        || catalogResponse.workflows[0]?.workflow_key
        || ''
      ));
      if (runResponse.items[0]) await openRun(runResponse.items[0].id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [openRun, projectId]);

  useEffect(() => {
    void loadWorkspace();
  }, [loadWorkspace]);

  useEffect(() => {
    if (!activeRun || !['pending', 'running'].includes(activeRun.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const updated = await openRun(activeRun.id);
        if (!['pending', 'running'].includes(updated.status)) {
          await refreshRuns();
          onLog(`Agent Run #${updated.id} 已结束，状态：${updated.status}`);
        }
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [activeRun?.id, activeRun?.status, onLog, openRun, refreshRuns]);

  const runWorkflow = async () => {
    if (!projectId || !workflowKey || (!requirementDocId && !requirement.trim())) {
      setError('请选择项目和工作流，并选择需求文档或输入真实需求。');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const response = await createAgentRun({
        project_id: projectId,
        workflow_key: workflowKey,
        input_payload: {
          requirement: requirement.trim(),
          requirement_doc_id: requirementDocId,
          case_budget: Math.max(1, Math.min(200, Number(caseBudget) || 20)),
        },
      });
      setActiveRun(response.run);
      setEvents([]);
      await refreshRuns();
      onLog(`已提交 Agent Run #${response.run.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const retryRun = async () => {
    if (!activeRun) return;
    setError(null);
    try {
      const response = await retryAgentRun(activeRun.id);
      setActiveRun(response.run);
      setEvents([]);
      await refreshRuns();
      onLog(`已从 Run #${activeRun.id} 创建重试 Run #${response.run.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const cancelRun = async () => {
    if (!activeRun) return;
    setError(null);
    try {
      const response = await cancelAgentRun(activeRun.id);
      setActiveRun(response.run);
      await refreshRuns();
      onLog(`已取消 Agent Run #${activeRun.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
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
    runs,
    activeRun,
    events,
    requirementDocuments,
    workflowKey,
    selectedWorkflow,
    requirement,
    requirementDocId,
    caseBudget,
    loading,
    submitting,
    error,
    setWorkflowKey,
    setRequirement,
    setRequirementDocId,
    setCaseBudget,
    loadWorkspace,
    openRun,
    runWorkflow,
    retryRun,
    cancelRun,
    decideApproval,
  };
}
