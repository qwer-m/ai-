import { useState } from 'react';
import 'bootstrap/dist/css/bootstrap.min.css';
import '../../theme.css';
import '../../App.css';
import { ConfigModal } from '../shared/ConfigModal';
import { LogPanel } from '../shared/LogPanel';
import { dashboardNavItems, getDashboardLabelByKey } from '../dashboard/model/dashboardNavigation';
import { DashboardContent } from '../dashboard/DashboardContent';
import { DashboardSidebar } from '../dashboard/DashboardSidebar';
import { DashboardTopBar } from '../dashboard/DashboardTopBar';
import { useDashboardController } from '../dashboard/useDashboardController';
import { useAuth } from '../../contexts/AuthContext';
import { useNavigate } from 'react-router-dom';

export const Dashboard = () => {
  const { logout, user } = useAuth();
  const navigate = useNavigate();
  const [openProjectCreateSignal, setOpenProjectCreateSignal] = useState(0);

  const controller = useDashboardController();

  const handleLogout = () => {
    logout();
    navigate('/login', { replace: true });
  };

  const handleOpenCreateProject = () => {
    controller.setActiveTab('proj');
    setOpenProjectCreateSignal((prev) => prev + 1);
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
            activeTabLabel={getDashboardLabelByKey(dashboardNavItems, controller.activeTab)}
            healthLoading={controller.healthLoading}
            healthError={controller.healthError}
            health={controller.health}
            onSelectProject={controller.setProjectId}
            onCreateProject={handleOpenCreateProject}
            onOpenConfig={controller.handleOpenConfig}
            onLogout={handleLogout}
          />

          <DashboardContent
            activeTab={controller.activeTab}
            projectId={controller.projectId}
            projects={controller.projects}
            projectsLoading={controller.projectsLoading}
            projectsError={controller.projectsError}
            onUserLog={(msg) => controller.handleLog(msg, 'user')}
            onSystemLog={(msg) => controller.handleLog(msg, 'system')}
            onProjectRefresh={controller.fetchProjects}
            onSelectProject={controller.setProjectId}
            evalGenerated={controller.evalGenerated}
            setEvalGenerated={controller.setEvalGenerated}
            evalModified={controller.evalModified}
            setEvalModified={controller.setEvalModified}
            evalResult={controller.evalResult}
            setEvalResult={controller.setEvalResult}
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
            openProjectCreateSignal={openProjectCreateSignal}
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

      <ConfigModal
        show={controller.showConfig}
        onHide={controller.handleCloseConfig}
        initialError={controller.configError}
      />
    </div>
  );
};
