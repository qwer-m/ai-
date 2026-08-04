import { Suspense, lazy, useEffect, useState } from 'react';
import { Container, Spinner } from 'react-bootstrap';
import type { Project } from '../pages/ProjectManagement';
import type { AutomationEvaluationReport, QualityReport } from '../evaluation/state/types';

const StandardAPITesting = lazy(() => import('../testing/api/StandardAPITesting').then((m) => ({ default: m.StandardAPITesting })));
const AgentWorkspace = lazy(() => import('../pages/AgentWorkspace').then((m) => ({ default: m.AgentWorkspace })));
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
  onUserLog: (msg: string) => void | Promise<void>;
  onSystemLog: (msg: string) => void | Promise<void>;
  onProjectRefresh: () => void | Promise<void>;
  onSelectProject: (projectId: number | null) => void;
  evalGenerated: string;
  setEvalGenerated: (value: string) => void;
  evalModified: string;
  setEvalModified: (value: string) => void;
  evalResult: QualityReport | null;
  setEvalResult: (value: QualityReport | null) => void;
  uiEvalScript: string;
  setUiEvalScript: (value: string) => void;
  uiEvalExec: string;
  setUiEvalExec: (value: string) => void;
  uiEvalOutput: AutomationEvaluationReport | null;
  setUiEvalOutput: (value: AutomationEvaluationReport | null) => void;
  apiEvalScript: string;
  setApiEvalScript: (value: string) => void;
  apiEvalExec: string;
  setApiEvalExec: (value: string) => void;
  apiEvalOutput: AutomationEvaluationReport | null;
  setApiEvalOutput: (value: AutomationEvaluationReport | null) => void;
  openProjectCreateSignal: number;
};

const isImmersiveTab = (tab: string) => {
  return tab === 'kb' || tab.startsWith('ui-exec-ui') || tab === 'api-standard';
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
  onUserLog,
  onSystemLog,
  onProjectRefresh,
  onSelectProject,
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
          {activeTab === 'api-standard' ? (
            <Suspense fallback={renderLazyFallback('加载接口测试模块...')}>
              <StandardAPITesting
                key={projectId ?? 'api-testing-none'}
                projectId={projectId}
                onLog={(msg: string) => {
                  void onUserLog(msg);
                }}
              />
            </Suspense>
          ) : null}

          <div className={`dashboard-tab-panel ${activeTab === 'agent-generation' ? 'is-active' : ''}`}>
            {activeTab === 'agent-generation' ? (
              <Suspense fallback={renderLazyFallback('加载 Agent 工作台...')}>
                <AgentWorkspace
                  key={projectId ?? 'agent-workspace-none'}
                  projectId={projectId}
                  onLog={(msg: string) => {
                    void onUserLog(msg);
                  }}
                />
              </Suspense>
            ) : null}
          </div>

          {(activeTab === 'ui-exec-ui-web' || activeTab === 'ui-exec-ui-app' || activeTab === 'ui-exec-ui-regression') ? (
            <Suspense fallback={renderLazyFallback('加载 UI 自动化模块...')}>
              <UIAutomation
                key={projectId ?? 'ui-automation-none'}
                projectId={projectId}
                projectName={projects.find((project) => project.id === projectId)?.name || ''}
                onLog={(msg: string) => {
                  void onUserLog(msg);
                }}
                view={
                  activeTab === 'ui-exec-ui-web'
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
              />
            </Suspense>
          </div>
        </div>
      </Container>
    </div>
  );
}

