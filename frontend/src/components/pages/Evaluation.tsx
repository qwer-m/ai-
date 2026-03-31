import { Toast, ToastContainer } from 'react-bootstrap';
import { AutomationEvaluationPanels } from '../evaluation/AutomationEvaluationPanels';
import { EvaluationOverviewPanel } from '../evaluation/EvaluationOverviewPanel';
import { RagValidationPanel } from '../evaluation/RagValidationPanel';
import { TestCaseCoveragePanel } from '../evaluation/TestCaseCoveragePanel';
import type { EvaluationProps } from '../evaluation/state/types';
import { useEvaluationActions } from '../evaluation/state/useEvaluationActions';

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

  return (
    <div className="bento-grid h-100 align-content-start evaluation-shell">
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

      {/*
        全局 Toast 统一挂载在页面根部，确保各子面板触发的提示都在同一视觉层级，
        避免拆分后出现提示位置漂移或重复容器。
      */}
      <ToastContainer
        containerPosition="fixed"
        className="p-3 top-50 start-50 translate-middle evaluation-toast-container"
      >
        {actions.toastMsg ? (
          <Toast
            onClose={() => actions.setToastMsg(null)}
            show={Boolean(actions.toastMsg)}
            delay={3000}
            autohide
            bg={actions.toastMsg.type === 'success' ? 'success' : 'danger'}
            className="evaluation-toast-card"
          >
            <Toast.Header>
              <strong className="me-auto">{actions.toastMsg.type === 'success' ? '成功' : '错误'}</strong>
            </Toast.Header>
            <Toast.Body className="text-white">{actions.toastMsg.msg}</Toast.Body>
          </Toast>
        ) : null}
      </ToastContainer>
    </div>
  );
}
