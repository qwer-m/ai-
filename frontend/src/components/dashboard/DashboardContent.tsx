import { Container } from 'react-bootstrap';
import { APIAutomation } from '../testing/api/APIAutomation';
import { APITesting } from '../testing/api/APITesting';
import { Evaluation } from '../pages/Evaluation';
import { KnowledgeBase } from '../pages/KnowledgeBase';
import { ProjectManagement, type Project } from '../pages/ProjectManagement';
import { TestGeneration } from '../testing/casegen/TestGeneration';
import { UIAutomation } from '../testing/ui/UIAutomation';
import type { LogEntry } from './model/types';

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
};

const isImmersiveTab = (tab: string) => {
  return tab === 'kb' || tab.startsWith('ui-exec-ui') || tab === 'api-standard' || tab === 'api-ai';
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
}: Props) {
  const immersive = isImmersiveTab(activeTab);

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

  return (
    <div
      className={`flex-grow-1 ${immersive ? 'p-0' : 'p-4'} pb-0 position-relative ${immersive ? 'overflow-hidden' : 'overflow-auto custom-scrollbar'}`}
      style={{ minWidth: 0 }}
    >
      <Container
        fluid
        className={`p-0 d-flex flex-column ${immersive ? 'h-100 position-absolute top-0 start-0 w-100' : ''}`}
        style={immersive ? { height: '100%', minHeight: 0 } : { minHeight: '100%' }}
      >
        <div className={`d-flex flex-column ${immersive ? 'flex-grow-1 overflow-hidden' : ''}`} style={immersive ? { minHeight: 0 } : {}}>
          {(activeTab === 'api-standard' || activeTab === 'api-ai') && (
            <APITesting
              key={projectId ?? 'api-testing-none'}
              projectId={projectId}
              onLog={(msg) => {
                void onUserLog(msg);
              }}
              view={activeTab === 'api-standard' ? 'standard' : 'ai_debug'}
            />
          )}

          <div style={{ display: activeTab === 'api-gen' ? 'block' : 'none', height: '100%' }}>
            <TestGeneration
              key={projectId ?? 'test-generation-none'}
              projectId={projectId}
              isActive={activeTab === 'api-gen'}
              onLog={(msg) => {
                void onUserLog(msg);
              }}
              onGenerated={onTestGenerated}
              onGenerationComplete={onGenerationComplete}
              onError={onConfigError}
            />
          </div>

          {(activeTab === 'ui-exec-ui' || activeTab === 'ui-exec-ui-web' || activeTab === 'ui-exec-ui-app' || activeTab === 'ui-exec-ui-regression') && (
            <UIAutomation
              key={projectId ?? 'ui-automation-none'}
              projectId={projectId}
              onLog={(msg) => {
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
          )}

          {(activeTab === 'ui-exec-api-orchestration' || activeTab === 'ui-exec-api-batch') && (
            <APIAutomation
              key={projectId ?? 'api-automation-none'}
              projectId={projectId}
              onLog={(msg) => {
                void onUserLog(msg);
              }}
              view={activeTab === 'ui-exec-api-orchestration' ? 'orchestration' : 'runner'}
            />
          )}

          {activeTab === 'kb' ? (
            <KnowledgeBase
              projectId={projectId}
              onLog={(msg) => {
                void onSystemLog(msg);
              }}
            />
          ) : null}

          {activeTab === 'proj' ? (
            <ProjectManagement
              projects={projects}
              loading={projectsLoading}
              error={projectsError}
              onRefresh={onProjectRefresh}
              onSelectProject={onSelectProject}
              onLog={(msg) => {
                void onUserLog(msg);
              }}
            />
          ) : null}

          {activeTab === 'eval' ? (
            <Evaluation
              {...commonEvaluationProps}
              onLog={(msg) => {
                void onUserLog(msg);
              }}
              view="root"
              shouldAutoEval={shouldAutoEval}
              setShouldAutoEval={setShouldAutoEval}
            />
          ) : null}

          {activeTab === 'eval-testcase' ? (
            <Evaluation
              {...commonEvaluationProps}
              onLog={(msg) => {
                void onUserLog(msg);
              }}
              view="testcase"
              shouldAutoEval={shouldAutoEval}
              setShouldAutoEval={setShouldAutoEval}
            />
          ) : null}

          {activeTab === 'eval-ui' ? (
            <Evaluation
              {...commonEvaluationProps}
              onLog={(msg) => {
                void onUserLog(msg);
              }}
              view="ui"
            />
          ) : null}

          {activeTab === 'eval-api' ? (
            <Evaluation
              {...commonEvaluationProps}
              onLog={(msg) => {
                void onUserLog(msg);
              }}
              view="api"
            />
          ) : null}

          {activeTab === 'eval-rag' ? (
            <Evaluation
              {...commonEvaluationProps}
              onLog={(msg) => {
                void onUserLog(msg);
              }}
              view="rag"
            />
          ) : null}
        </div>
      </Container>
    </div>
  );
}

