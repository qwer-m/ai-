import { useEffect, useRef } from 'react';
import { AutomationEvaluationPanels } from '../evaluation/AutomationEvaluationPanels';
import { EvaluationOverviewPanel } from '../evaluation/EvaluationOverviewPanel';
import { RagValidationPanel } from '../evaluation/RagValidationPanel';
import { TestCaseCoveragePanel } from '../evaluation/TestCaseCoveragePanel';
import type { EvaluationProps } from '../evaluation/state/types';
import { useEvaluationActions } from '../evaluation/state/useEvaluationActions';
import { emitFeedback } from '../../utils/feedback';

export function Evaluation({
  projectId,
  onLog,
  view = 'root',
  evalGenerated,
  setEvalGenerated,
  evalModified,
  setEvalModified,
  evalResult,
  setEvalResult,
  uiEvalScript,
  setUiEvalScript,
  uiEvalExec,
  setUiEvalExec,
  uiEvalOutput,
  setUiEvalOutput,
  apiEvalScript,
  setApiEvalScript,
  apiEvalExec,
  setApiEvalExec,
  apiEvalOutput,
  setApiEvalOutput,
}: EvaluationProps) {
  const actions = useEvaluationActions({
    projectId,
    onLog,
    view,
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
  });

  const showRoot = view === 'root';
  const showTestcase = view === 'testcase';
  const showUi = view === 'ui';
  const showApi = view === 'api';
  const showRag = view === 'rag';
  const storageKey = `evaluation:testcase:draft:${projectId ?? 'none'}`;
  const hydratedStorageKeyRef = useRef('');
  const skipNextDraftPersistRef = useRef(false);

  useEffect(() => {
    if (!actions.toastMsg) return;
    emitFeedback({
      level: actions.toastMsg.type === 'success' ? 'success' : 'error',
      title: actions.toastMsg.type === 'success' ? '操作成功' : '操作失败',
      message: actions.toastMsg.msg,
    });
    actions.setToastMsg(null);
  }, [actions.toastMsg, actions.setToastMsg]);

  useEffect(() => {
    if (hydratedStorageKeyRef.current === storageKey) return;
    hydratedStorageKeyRef.current = storageKey;
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (!raw) return;
      const draft = JSON.parse(raw);
      skipNextDraftPersistRef.current = true;
      if (typeof draft?.evalGenerated === 'string') setEvalGenerated(draft.evalGenerated);
      if (typeof draft?.evalModified === 'string') setEvalModified(draft.evalModified);
      if (draft?.evalResult && typeof draft.evalResult === 'object') setEvalResult(draft.evalResult);
      if (typeof draft?.uploadedReferenceFilename === 'string') actions.setUploadedReferenceFilename(draft.uploadedReferenceFilename);
      if (typeof draft?.loadedReferenceFilename === 'string') actions.setLoadedReferenceFilename(draft.loadedReferenceFilename);
    } catch {
      // Ignore malformed local drafts; the current in-memory state remains authoritative.
    }
  }, [storageKey, setEvalGenerated, setEvalModified, setEvalResult, actions]);

  useEffect(() => {
    if (hydratedStorageKeyRef.current !== storageKey) return;
    if (skipNextDraftPersistRef.current) {
      skipNextDraftPersistRef.current = false;
      return;
    }
    try {
      window.localStorage.setItem(
        storageKey,
        JSON.stringify({
          evalGenerated,
          evalModified,
          evalResult,
          uploadedReferenceFilename: actions.uploadedReferenceFilename,
          loadedReferenceFilename: actions.loadedReferenceFilename,
          updatedAt: new Date().toISOString(),
        }),
      );
    } catch {
      // localStorage may be unavailable or full; this should not block evaluation.
    }
  }, [
    storageKey,
    evalGenerated,
    evalModified,
    evalResult,
    actions.uploadedReferenceFilename,
    actions.loadedReferenceFilename,
  ]);

  return (
    <div className="bento-grid align-content-start evaluation-shell workbench-shell">
      <div style={{ display: showRoot ? 'contents' : 'none' }}>
        <EvaluationOverviewPanel
          latestRun={actions.runHistory[0] || null}
          onExportHistory={actions.exportHistory}
        />
      </div>

      <div style={{ display: showTestcase ? 'contents' : 'none' }}>
        <TestCaseCoveragePanel
          evalGenerated={evalGenerated}
          evalModified={evalModified}
          setEvalModified={setEvalModified}
          evalResult={evalResult}
          loading={actions.loading}
          runHistory={actions.runHistory}
          selectedRunId={actions.selectedRunId}
          onSelectRunId={actions.setSelectedRunId}
          onLoadRunById={actions.loadRunById}
          onFileChange={actions.setReferenceFile}
          uploadedReferenceFilename={actions.uploadedReferenceFilename}
          loadedReferenceFilename={actions.loadedReferenceFilename}
          onEvaluate={actions.evaluateTestCases}
          onInvalidateEvaluation={() => setEvalResult(null)}
          history={actions.history}
        />
      </div>

      <div style={{ display: showRag ? 'contents' : 'none' }}>
        <RagValidationPanel
          projectId={projectId}
          onLog={onLog}
        />
      </div>

      <AutomationEvaluationPanels
        showUi={showUi}
        showApi={showApi}
        loading={actions.loading}
        uiEvalScript={uiEvalScript}
        setUiEvalScript={setUiEvalScript}
        uiEvalJourney={actions.uiEvalJourney}
        setUiEvalJourney={actions.setUiEvalJourney}
        uiEvalExec={uiEvalExec}
        setUiEvalExec={setUiEvalExec}
        uiEvalOutput={uiEvalOutput}
        onEvaluateUi={actions.evaluateUi}
        apiEvalScript={apiEvalScript}
        setApiEvalScript={setApiEvalScript}
        apiEvalSpec={actions.apiEvalSpec}
        setApiEvalSpec={actions.setApiEvalSpec}
        apiEvalExec={apiEvalExec}
        setApiEvalExec={setApiEvalExec}
        apiEvalOutput={apiEvalOutput}
        onEvaluateApi={actions.evaluateApi}
      />
    </div>
  );
}
