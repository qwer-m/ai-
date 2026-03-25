import { Button, Form } from 'react-bootstrap';
import { FaCog, FaPlus, FaServer, FaSignOutAlt } from 'react-icons/fa';
import type { Project } from '../pages/ProjectManagement';

type Props = {
  projectId: number | null;
  projects: Project[];
  projectsLoading: boolean;
  onSelectProject: (projectId: number | null) => void;
  onOpenPipeline: () => void;
  onCreateProject: () => void;
  onOpenConfig: () => void;
  onLogout: () => void;
};

export function DashboardTopBar({
  projectId,
  projects,
  projectsLoading,
  onSelectProject,
  onOpenPipeline,
  onCreateProject,
  onOpenConfig,
  onLogout,
}: Props) {
  return (
    <div
      className="bg-body bg-opacity-50 border-bottom border-secondary-subtle px-4 py-3 d-flex justify-content-between align-items-center backdrop-blur"
      style={{ height: '64px' }}
    >
      <div className="d-flex align-items-center gap-2">
        <div
          className="bg-body text-secondary px-3 py-1 rounded fw-bold shadow-sm d-flex align-items-center justify-content-center border"
          style={{ height: '36px', fontSize: '0.875rem', whiteSpace: 'nowrap', minWidth: 'fit-content' }}
        >
          项目
        </div>
        <Form.Select
          value={projectId ?? ''}
          onChange={(event) => onSelectProject(event.target.value ? Number(event.target.value) : null)}
          disabled={projectsLoading || projects.length === 0}
          style={{ minWidth: '120px', maxWidth: '200px', height: '36px' }}
          size="sm"
          className="input-pro border-0 shadow-sm bg-body-tertiary text-secondary position-relative"
        >
          {projects.length > 0 ? (
            projects.map((project) => (
              <option key={project.id} value={project.id}>{project.name}</option>
            ))
          ) : (
            <option value="">请创建项目</option>
          )}
        </Form.Select>
      </div>

      <div className="d-flex gap-2">
        <Button
          variant="light"
          size="sm"
          onClick={onOpenPipeline}
          className="btn-light-pro d-flex align-items-center gap-2 bg-body shadow-sm border-0 text-secondary"
          title="全局编排"
        >
          <div className="position-relative">
            <FaServer />
            <span className="position-absolute top-0 start-100 translate-middle p-1 bg-danger border border-light rounded-circle" />
          </div>
        </Button>

        <div className="vr mx-2 opacity-25" />

        <Button variant="primary" size="sm" onClick={onCreateProject} className="btn-pro-primary d-flex align-items-center gap-2">
          <FaPlus /> <span className="d-none d-md-inline">新建项目</span>
        </Button>

        <Button
          variant="light"
          size="sm"
          onClick={onOpenConfig}
          className="btn-light-pro d-flex align-items-center gap-2 bg-body shadow-sm border-0 text-secondary"
        >
          <FaCog />
        </Button>

        <Button
          variant="light"
          size="sm"
          onClick={onLogout}
          className="btn-light-pro d-flex align-items-center gap-2 bg-body shadow-sm border-0 text-secondary"
          title="Logout"
        >
          <FaSignOutAlt />
        </Button>
      </div>
    </div>
  );
}
