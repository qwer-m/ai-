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
    skipNextDraftPersistRef.current = true;
    setEvalGenerated('');
    setEvalModified('');
    setEvalResult(null);
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (!raw) return;
      const draft = JSON.parse(raw);
      if (typeof draft?.evalModified === 'string') setEvalModified(draft.evalModified);
      if (typeof draft?.uploadedReferenceFilename === 'string') actions.setUploadedReferenceFilename(draft.uploadedReferenceFilename);
      if (typeof draft?.loadedReferenceFilename === 'string') actions.setLoadedReferenceFilename(draft.loadedReferenceFilename);
    } catch {
      // 草稿只恢复人工输入；生成结果和评测身份必须重新从服务器读取。
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
          evalModified,
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
          setEvalModified={actions.setReferenceText}
          evalResult={evalResult}
          loading={actions.loading}
          runHistory={actions.runHistory}
          baselineText={actions.baselineText}
          onBaselineChange={actions.setBaselineText}
          selectedRunId={actions.selectedRunId}
          onSelectRunId={actions.selectRunId}
          onLoadRunById={actions.loadRunById}
          onFileChange={actions.setReferenceFile}
          uploadedReferenceFilename={actions.uploadedReferenceFilename}
          loadedReferenceFilename={actions.loadedReferenceFilename}
          onEvaluate={actions.evaluateTestCases}
          history={actions.history}
          projectId={projectId}
          verifiedEvaluation={actions.verifiedEvaluation}
          sourceLoaded={actions.sourceLoaded}
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
