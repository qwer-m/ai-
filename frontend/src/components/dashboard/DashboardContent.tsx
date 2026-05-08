import { Suspense, lazy, useEffect, useState } from 'react';
import { Container, Spinner } from 'react-bootstrap';
import type { Project } from '../pages/ProjectManagement';
import type { LogEntry } from './model/types';

const APITesting = lazy(() => import('../testing/api/APITesting').then((m) => ({ default: m.APITesting })));
const TestGeneration = lazy(() => import('../testing/casegen/TestGeneration').then((m) => ({ default: m.TestGeneration })));
const UIAutomation = lazy(() => import('../testing/ui/UIAutomation').then((m) => ({ default: m.UIAutomation })));
const APIAutomation = lazy(() => import('../testing/api/APIAutomation').then((m) => ({ default: m.APIAutomation })));
const KnowledgeBase = lazy(() => import('../pages/KnowledgeBase').then((m) => ({ default: m.KnowledgeBase })));
const ProjectManagement = lazy(() => import('../pages/ProjectManagement').then((m) => ({ default: m.ProjectManagement })));
const Evaluation = lazy(() => import('../pages/Evaluation').then((m) => ({ default: m.Evaluation })));

type Props = {
  activeTab: string;
  projectId: number | null;
  projects: Project[];
  projectsLoading: boolean;
  projectsError: string | null;
  logs: LogEntry[];
  onUserLog: (msg: string) => void | Promise<void>;
  onSystemLog: (msg: string) => void | Promise<void>;
  onProjectRefresh: () => void | Promise<void>;
  onSelectProject: (projectId: number | null) => void;
  onConfigError: (msg: string) => void;

  evalGenerated: string;
  setEvalGenerated: (value: string) => void;
  evalModified: string;
  setEvalModified: (value: string) => void;
  evalResult: string | null;
  setEvalResult: (value: string | null) => void;
  recallRetrieved: string;
  setRecallRetrieved: (value: string) => void;
  recallRelevant: string;
  setRecallRelevant: (value: string) => void;
  recallResult: string | null;
  setRecallResult: (value: string | null) => void;
  uiEvalScript: string;
  setUiEvalScript: (value: string) => void;
  uiEvalExec: string;
  setUiEvalExec: (value: string) => void;
  uiEvalOutput: string | null;
  setUiEvalOutput: (value: string | null) => void;
  apiEvalScript: string;
  setApiEvalScript: (value: string) => void;
  apiEvalExec: string;
  setApiEvalExec: (value: string) => void;
  apiEvalOutput: string | null;
  setApiEvalOutput: (value: string | null) => void;
  shouldAutoEval: boolean;
  setShouldAutoEval: (value: boolean) => void;
  onTestGenerated: (data: unknown) => void;
  onGenerationComplete: () => void;
  openProjectCreateSignal: number;
};

const isImmersiveTab = (tab: string) => {
  return tab === 'kb' || tab.startsWith('ui-exec-ui') || tab === 'api-standard' || tab === 'api-ai';
};

type EvaluationViewKey = 'root' | 'testcase' | 'ui' | 'api' | 'rag';

const getEvaluationView = (tab: string): EvaluationViewKey => {
  if (tab === 'eval-testcase') return 'testcase';
  if (tab === 'eval-ui') return 'ui';
  if (tab === 'eval-api') return 'api';
  if (tab === 'eval-rag') return 'rag';
  return 'root';
};

const isEvaluationTab = (tab: string) => {
  return tab === 'eval' || tab === 'eval-testcase' || tab === 'eval-ui' || tab === 'eval-api' || tab === 'eval-rag';
};

export function DashboardContent({
  activeTab,
  projectId,
  projects,
  projectsLoading,
  projectsError,
  logs,
  onUserLog,
  onSystemLog,
  onProjectRefresh,
  onSelectProject,
  onConfigError,
  evalGenerated,
  setEvalGenerated,
  evalModified,
  setEvalModified,
  evalResult,
  setEvalResult,
  recallRetrieved,
  setRecallRetrieved,
  recallRelevant,
  setRecallRelevant,
  recallResult,
  setRecallResult,
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
  onTestGenerated,
  onGenerationComplete,
  openProjectCreateSignal,
}: Props) {
  const immersive = isImmersiveTab(activeTab);
  const evaluationTabActive = isEvaluationTab(activeTab);
  const currentEvaluationView = getEvaluationView(activeTab);
  const [lastEvaluationView, setLastEvaluationView] = useState(currentEvaluationView);

  useEffect(() => {
    if (evaluationTabActive) setLastEvaluationView(currentEvaluationView);
  }, [currentEvaluationView, evaluationTabActive]);

  const commonEvaluationProps = {
    projectId,
    logs,
    evalGenerated,
    setEvalGenerated,
    evalModified,
    setEvalModified,
    evalResult,
    setEvalResult,
    recallRetrieved,
    setRecallRetrieved,
    recallRelevant,
    setRecallRelevant,
    recallResult,
    setRecallResult,
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
  };

  const renderLazyFallback = (message: string) => (
    <div className="d-flex flex-column align-items-center justify-content-center py-5 text-muted small">
      <Spinner animation="border" size="sm" className="mb-2" />
      <span>{message}</span>
    </div>
  );

  return (
    <div
      className={`dashboard-content-host dashboard-content-host-min flex-grow-1 dashboard-tab-${activeTab} position-relative ${immersive ? 'overflow-hidden' : 'dashboard-content-scroll overflow-auto custom-scrollbar'}`}
      data-active-tab={activeTab}
    >
      <Container
        fluid
        className={`p-0 d-flex flex-column h-100 ${immersive ? 'dashboard-content-container-immersive' : 'dashboard-content-container-normal'}`}
      >
        <div className={`d-flex flex-column flex-grow-1 ${immersive ? 'overflow-hidden dashboard-content-min-h-0' : ''}`}>
          {(activeTab === 'api-standard' || activeTab === 'api-ai') ? (
            <Suspense fallback={renderLazyFallback('加载接口测试模块...')}>
              <APITesting
                key={projectId ?? 'api-testing-none'}
                projectId={projectId}
                onLog={(msg: string) => {
                  void onUserLog(msg);
                }}
                view={activeTab === 'api-standard' ? 'standard' : 'ai_debug'}
              />
            </Suspense>
          ) : null}

          <div className={`dashboard-tab-panel ${activeTab === 'api-gen' ? 'is-active' : ''}`}>
            {activeTab === 'api-gen' ? (
              <Suspense fallback={renderLazyFallback('加载用例生成模块...')}>
                <TestGeneration
                  key={projectId ?? 'test-generation-none'}
                  projectId={projectId}
                  isActive={activeTab === 'api-gen'}
                  onLog={(msg: string) => {
                    void onUserLog(msg);
                  }}
                  onGenerated={onTestGenerated}
                  onGenerationComplete={onGenerationComplete}
                  onError={onConfigError}
                />
              </Suspense>
            ) : null}
          </div>

          {(activeTab === 'ui-exec-ui' || activeTab === 'ui-exec-ui-web' || activeTab === 'ui-exec-ui-app' || activeTab === 'ui-exec-ui-regression') ? (
            <Suspense fallback={renderLazyFallback('加载 UI 自动化模块...')}>
              <UIAutomation
                key={projectId ?? 'ui-automation-none'}
                projectId={projectId}
                onLog={(msg: string) => {
                  void onUserLog(msg);
                }}
                view={
                  activeTab === 'ui-exec-ui'
                    ? 'report'
                    : activeTab === 'ui-exec-ui-web'
                      ? 'web'
                      : activeTab === 'ui-exec-ui-app'
                        ? 'app'
                        : 'regression'
                }
              />
            </Suspense>
          ) : null}

          {(activeTab === 'ui-exec-api-orchestration' || activeTab === 'ui-exec-api-batch') ? (
            <Suspense fallback={renderLazyFallback('加载 API 自动化模块...')}>
              <APIAutomation
                key={projectId ?? 'api-automation-none'}
                projectId={projectId}
                onLog={(msg: string) => {
                  void onUserLog(msg);
                }}
                view={activeTab === 'ui-exec-api-orchestration' ? 'orchestration' : 'runner'}
              />
            </Suspense>
          ) : null}

          {activeTab === 'kb' ? (
            <Suspense fallback={renderLazyFallback('加载知识库模块...')}>
              <KnowledgeBase
                projectId={projectId}
                onLog={(msg: string) => {
                  void onSystemLog(msg);
                }}
              />
            </Suspense>
          ) : null}

          {activeTab === 'proj' ? (
            <Suspense fallback={renderLazyFallback('加载项目管理模块...')}>
              <ProjectManagement
                projects={projects}
                loading={projectsLoading}
                error={projectsError}
                onRefresh={onProjectRefresh}
                onSelectProject={onSelectProject}
                onLog={(msg: string) => {
                  void onUserLog(msg);
                }}
                openCreateSignal={openProjectCreateSignal}
              />
            </Suspense>
          ) : null}

          <div className={`dashboard-tab-panel ${evaluationTabActive ? 'is-active' : ''}`} style={{ display: evaluationTabActive ? undefined : 'none' }}>
            <Suspense fallback={renderLazyFallback('加载评测模块...')}>
              <Evaluation
                {...commonEvaluationProps}
                onLog={(msg: string) => {
                  void onUserLog(msg);
                }}
                view={evaluationTabActive ? currentEvaluationView : lastEvaluationView}
                shouldAutoEval={shouldAutoEval}
                setShouldAutoEval={setShouldAutoEval}
              />
            </Suspense>
          </div>
        </div>
      </Container>
    </div>
  );
}

