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
  shouldAutoEval,
  setShouldAutoEval,
}: EvaluationProps) {
  const actions = useEvaluationActions({
    projectId,
    logs,
    onLog,
    view,
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
      if (typeof draft?.evalResult === 'string') setEvalResult(draft.evalResult);
      if (typeof draft?.supplementText === 'string') actions.setSupplementText(draft.supplementText);
      if (typeof draft?.uploadedCompareFilename === 'string') actions.setUploadedCompareFilename(draft.uploadedCompareFilename);
      if (typeof draft?.loadedCompareFilename === 'string') actions.setLoadedCompareFilename(draft.loadedCompareFilename);
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
          supplementText: actions.supplementText,
          uploadedCompareFilename: actions.uploadedCompareFilename,
          loadedCompareFilename: actions.loadedCompareFilename,
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
    actions.supplementText,
    actions.uploadedCompareFilename,
    actions.loadedCompareFilename,
  ]);

  return (
    <div className="bento-grid align-content-start evaluation-shell workbench-shell">
      <div style={{ display: showRoot ? 'contents' : 'none' }}>
        <EvaluationOverviewPanel
          diag={actions.latestDiag}
          qm={actions.latestQm}
          onExportHistory={actions.exportHistory}
        />
      </div>

      <div style={{ display: showTestcase ? 'contents' : 'none' }}>
        <TestCaseCoveragePanel
          projectId={projectId}
          evalGenerated={evalGenerated}
          setEvalGenerated={setEvalGenerated}
          evalModified={evalModified}
          setEvalModified={setEvalModified}
          evalResult={evalResult}
          loading={actions.loading}
          genHistory={actions.genHistory}
          selectedGenerationId={actions.selectedGenerationId}
          onSelectGenerationId={actions.setSelectedGenerationId}
          onLoadGenerationById={actions.loadGenerationById}
          onFileChange={actions.setCompareFile}
          uploadedCompareFilename={actions.uploadedCompareFilename}
          compareFile={actions.file}
          loadedCompareFilename={actions.loadedCompareFilename}
          onCompare={actions.compareTestCases}
          onInvalidateEvaluation={() => setEvalResult(null)}
          history={actions.history}
          showSupplement={actions.showSupplement}
          setShowSupplement={actions.setShowSupplement}
          supplementText={actions.supplementText}
          setSupplementText={actions.setSupplementText}
          supplementImages={actions.supplementImages}
          setSupplementImages={actions.setSupplementImages}
          savedDocId={actions.savedDocId}
          lastSavedContent={actions.lastSavedContent}
          handleSupplementPaste={actions.handleSupplementPaste}
          handleSupplementFilesChange={actions.handleSupplementFilesChange}
          onSaveKnowledge={actions.handleSaveKnowledge}
          savingKnowledge={actions.loading === 'save_knowledge'}
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
