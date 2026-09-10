import { Button, Form } from 'react-bootstrap';
import { FaCog, FaPlus, FaSignOutAlt } from 'react-icons/fa';
import type { Project } from '../pages/ProjectManagement';
import type { HealthResponse } from './model/types';

type Props = {
  projectId: number | null;
  projects: Project[];
  projectsLoading: boolean;
  activeTabLabel: string;
  healthLoading: boolean;
  healthError: string | null;
  health: HealthResponse | null;
  onSelectProject: (projectId: number | null) => void;
  onCreateProject: () => void;
  onOpenConfig: () => void;
  onLogout: () => void;
};

export function DashboardTopBar({
  projectId,
  projects,
  projectsLoading,
  onSelectProject,
  onCreateProject,
  onOpenConfig,
  onLogout,
}: Props) {
  return (
    <div className="dashboard-topbar dashboard-topbar-surface dashboard-topbar-shell bg-body bg-opacity-50 border-bottom border-secondary-subtle px-4 py-3 d-flex justify-content-between align-items-end">
      <div className="d-flex align-items-end gap-2 dashboard-project-switcher">
        <div className="dashboard-project-chip dashboard-project-label bg-body text-secondary px-3 py-1 rounded fw-bold shadow-sm d-flex align-items-center justify-content-center border">
          当前项目
        </div>

        <Form.Select
          value={projectId ?? ''}
          onChange={(event) => onSelectProject(event.target.value ? Number(event.target.value) : null)}
          disabled={projectsLoading || projects.length === 0}
          size="sm"
          className="input-pro dashboard-project-select border-0 shadow-sm bg-body-tertiary text-secondary"
        >
          {projects.length > 0 ? (
            projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))
          ) : (
            <option value="">请先创建项目</option>
          )}
        </Form.Select>
      </div>

      <div className="d-flex gap-2 dashboard-topbar-right">
        <div className="d-flex gap-2 dashboard-action-cluster">
          <Button variant="primary" size="sm" onClick={onCreateProject} className="btn-pro-primary d-flex align-items-center gap-2">
            <FaPlus /> <span className="d-none d-md-inline">新建项目</span>
          </Button>

          <Button
            variant="light"
            size="sm"
            onClick={onOpenConfig}
            className="btn-light-pro dashboard-icon-btn d-flex align-items-center justify-content-center bg-body shadow-sm border-0 text-secondary"
            title="系统设置"
          >
            <FaCog />
          </Button>

          <Button
            variant="light"
            size="sm"
            onClick={onLogout}
            className="btn-light-pro dashboard-icon-btn d-flex align-items-center justify-content-center bg-body shadow-sm border-0 text-secondary"
            title="退出登录"
          >
            <FaSignOutAlt />
          </Button>
        </div>
      </div>
    </div>
  );
}
