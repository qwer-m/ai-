import { Suspense, lazy } from 'react';
import { Container, Spinner } from 'react-bootstrap';
import type { Project } from '../pages/ProjectManagement';

const AgentWorkspace = lazy(() => import('../pages/AgentWorkspace').then((m) => ({ default: m.AgentWorkspace })));
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
  openProjectCreateSignal: number;
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
  openProjectCreateSignal,
}: Props) {
  const immersive = activeTab === 'kb';
  const evaluationTabActive = activeTab === 'eval-testcase';

  const renderLazyFallback = (message: string) => (
    <div className="d-flex flex-column align-items-center justify-content-center py-5 text-muted small">
      <Spinner animation="border" size="sm" className="mb-2" />
      <span>{message}</span>
    </div>
  );

  return (
    <div
      className={`dashboard-content-host dashboard-content-host-min flex-grow-1 dashboard-tab-${activeTab} position-relative ${immersive ? 'overflow-hidden' : 'dashboard-content-scroll overflow-auto'}`}
      data-active-tab={activeTab}
    >
      <Container
        fluid
        className={`p-0 d-flex flex-column h-100 ${immersive ? 'dashboard-content-container-immersive' : 'dashboard-content-container-normal'}`}
      >
        <div className={`d-flex flex-column flex-grow-1 ${immersive ? 'overflow-hidden dashboard-content-min-h-0' : ''}`}>
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

          {activeTab === 'kb' ? (
            <Suspense fallback={renderLazyFallback('加载知识库模块...')}>
              <KnowledgeBase
                key={projectId ?? 'knowledge-base-none'}
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
                key={projectId ?? 'no-project'}
                active={evaluationTabActive}
                projectId={projectId}
                onLog={(msg: string) => {
                  void onUserLog(msg);
                }}
              />
            </Suspense>
          </div>
        </div>
      </Container>
    </div>
  );
}

