import { useState } from 'react';
import { Modal } from 'react-bootstrap';
import 'bootstrap/dist/css/bootstrap.min.css';
import '../../theme.css';
import '../../App.css';
import { ConfigModal } from '../shared/ConfigModal';
import { LogPanel } from '../shared/LogPanel';
import { PipelineOrchestration } from './PipelineOrchestration';
import { dashboardNavItems } from '../dashboard/model/dashboardNavigation';
import { DashboardContent } from '../dashboard/DashboardContent';
import { DashboardSidebar } from '../dashboard/DashboardSidebar';
import { DashboardTopBar } from '../dashboard/DashboardTopBar';
import { useDashboardController } from '../dashboard/useDashboardController';
import { useAuth } from '../../contexts/AuthContext';
import { useNavigate } from 'react-router-dom';

export const Dashboard = () => {
  const { logout, user } = useAuth();
  const navigate = useNavigate();
  const [showPipelineModal, setShowPipelineModal] = useState(false);

  const controller = useDashboardController();

  const handleLogout = () => {
    logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="dashboard-shell dashboard-workspace d-flex flex-column h-100 w-100 overflow-hidden bg-app">
      <div className="flex-grow-1 d-flex overflow-hidden p-3 pb-0 dashboard-body-layout">
        <DashboardSidebar
          userName={user?.username}
          themeMode={controller.themeMode}
          navItems={dashboardNavItems}
          activeTab={controller.activeTab}
          expandedKeys={controller.expandedKeys}
          healthLoading={controller.healthLoading}
          healthError={controller.healthError}
          health={controller.health}
          onSelectTab={controller.setActiveTab}
          onToggleExpand={controller.toggleExpand}
          onToggleTheme={controller.handleToggleTheme}
        />

        <div className="dashboard-main dashboard-main-surface flex-grow-1 d-flex flex-column glass-panel rounded-3 overflow-hidden position-relative border-0">
          <DashboardTopBar
            projectId={controller.projectId}
            projects={controller.projects}
            projectsLoading={controller.projectsLoading}
            onSelectProject={controller.setProjectId}
            onOpenPipeline={() => setShowPipelineModal(true)}
            onCreateProject={() => controller.setActiveTab('proj')}
            onOpenConfig={controller.handleOpenConfig}
            onLogout={handleLogout}
          />

          <DashboardContent
            activeTab={controller.activeTab}
            projectId={controller.projectId}
            projects={controller.projects}
            projectsLoading={controller.projectsLoading}
            projectsError={controller.projectsError}
            logs={controller.logs}
            onUserLog={(msg) => controller.handleLog(msg, 'user')}
            onSystemLog={(msg) => controller.handleLog(msg, 'system')}
            onProjectRefresh={controller.fetchProjects}
            onSelectProject={controller.setProjectId}
            onConfigError={controller.openConfigWithError}
            evalGenerated={controller.evalGenerated}
            setEvalGenerated={controller.setEvalGenerated}
            evalModified={controller.evalModified}
            setEvalModified={controller.setEvalModified}
            evalResult={controller.evalResult}
            setEvalResult={controller.setEvalResult}
            recallRetrieved={controller.recallRetrieved}
            setRecallRetrieved={controller.setRecallRetrieved}
            recallRelevant={controller.recallRelevant}
            setRecallRelevant={controller.setRecallRelevant}
            recallResult={controller.recallResult}
            setRecallResult={controller.setRecallResult}
            uiEvalScript={controller.uiEvalScript}
            setUiEvalScript={controller.setUiEvalScript}
            uiEvalExec={controller.uiEvalExec}
            setUiEvalExec={controller.setUiEvalExec}
            uiEvalOutput={controller.uiEvalOutput}
            setUiEvalOutput={controller.setUiEvalOutput}
            apiEvalScript={controller.apiEvalScript}
            setApiEvalScript={controller.setApiEvalScript}
            apiEvalExec={controller.apiEvalExec}
            setApiEvalExec={controller.setApiEvalExec}
            apiEvalOutput={controller.apiEvalOutput}
            setApiEvalOutput={controller.setApiEvalOutput}
            shouldAutoEval={controller.shouldAutoEval}
            setShouldAutoEval={controller.setShouldAutoEval}
            onTestGenerated={controller.handleTestGenerated}
            onGenerationComplete={controller.handleGenerationComplete}
          />
        </div>
      </div>

      <div className="flex-shrink-0 w-100 position-relative">
        <LogPanel
          userLogs={controller.userLogs}
          systemLogs={controller.systemLogs}
          loading={controller.logsLoading}
          error={controller.logsError}
          onClear={controller.clearLogs}
        />
      </div>

      <Modal show={showPipelineModal} onHide={() => setShowPipelineModal(false)} size="xl" centered>
        <Modal.Header closeButton>
          <Modal.Title>全局编排</Modal.Title>
        </Modal.Header>
        <Modal.Body className="p-0 dashboard-pipeline-modal-body">
          <PipelineOrchestration
            key={`pipeline-modal-${controller.projectId ?? 'none'}`}
            projectId={controller.projectId}
            onLog={(msg) => {
              void controller.handleLog(msg, 'user');
            }}
          />
        </Modal.Body>
      </Modal>

      <ConfigModal
        show={controller.showConfig}
        onHide={controller.handleCloseConfig}
        initialError={controller.configError}
      />
    </div>
  );
};
