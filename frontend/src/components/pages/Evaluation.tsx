import { useEffect } from 'react';
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

  useEffect(() => {
    if (!actions.toastMsg) return;
    emitFeedback({
      level: actions.toastMsg.type === 'success' ? 'success' : 'error',
      title: actions.toastMsg.type === 'success' ? '操作成功' : '操作失败',
      message: actions.toastMsg.msg,
    });
    actions.setToastMsg(null);
  }, [actions.toastMsg, actions.setToastMsg]);

  return (
    <div className="bento-grid align-content-start evaluation-shell workbench-shell">
      {showRoot ? (
        <EvaluationOverviewPanel
          diag={actions.latestDiag}
          qm={actions.latestQm}
          onExportHistory={actions.exportHistory}
        />
      ) : null}

      {showTestcase ? (
        <TestCaseCoveragePanel
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
          loadedCompareFilename={actions.loadedCompareFilename}
          onCompare={actions.compareTestCases}
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
      ) : null}

      {showRag ? (
        <RagValidationPanel
          projectId={projectId}
          onLog={onLog}
        />
      ) : null}

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
