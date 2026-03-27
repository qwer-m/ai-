import { Badge, Button, Collapse, Nav, Spinner } from 'react-bootstrap';
import { FaCheckCircle, FaChevronDown, FaExclamationTriangle, FaMoon, FaServer, FaSun } from 'react-icons/fa';
import type { DashboardNavItem, HealthResponse } from './model/types';

type Props = {
  userName?: string;
  themeMode: 'light' | 'dark';
  navItems: DashboardNavItem[];
  activeTab: string;
  expandedKeys: string[];
  healthLoading: boolean;
  healthError: string | null;
  health: HealthResponse | null;
  onSelectTab: (key: string) => void;
  onToggleExpand: (key: string) => void;
  onToggleTheme: () => void;
};

export function DashboardSidebar({
  userName,
  themeMode,
  navItems,
  activeTab,
  expandedKeys,
  healthLoading,
  healthError,
  health,
  onSelectTab,
  onToggleExpand,
  onToggleTheme,
}: Props) {
  return (
    <div className="dashboard-sidebar dashboard-sidebar-surface d-flex flex-column glass-panel rounded-3 flex-shrink-0 overflow-hidden border-0">
      <div className="p-4 border-bottom border-secondary-subtle bg-body bg-opacity-50 dashboard-sidebar-head">
        <div className="d-flex align-items-center justify-content-between">
          <h1 className="h5 mb-0 text-gradient fw-bold d-flex align-items-center gap-2 text-nowrap">
            <FaServer className="text-primary-500" /> AI测试平台
            <Badge bg="light" text="secondary" className="ms-1 fw-normal opacity-75 dashboard-pro-badge">
              PRO
            </Badge>
          </h1>

          <Button
            variant="light"
            size="sm"
            className="theme-toggle-btn d-flex align-items-center justify-content-center"
            onClick={onToggleTheme}
            title={themeMode === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
          >
            {themeMode === 'dark' ? <FaSun className="text-warning" /> : <FaMoon className="text-secondary" />}
          </Button>
        </div>
        {userName ? <div className="small text-secondary mt-2">你好，{userName}</div> : null}
      </div>

      <div className="flex-grow-1 p-3 overflow-auto custom-scrollbar dashboard-sidebar-nav-wrap dashboard-sidebar-scroll-gutter">
        <Nav variant="pills" className="flex-column gap-2" activeKey={activeTab}>
          {navItems.map((item) => (
            <div key={item.key} className="d-flex flex-column">
              <Nav.Link
                as="button"
                eventKey={item.key}
                className={`sidebar-link dashboard-sidebar-link-row d-flex align-items-center gap-3 px-3 py-2 rounded-lg transition-all ${activeTab === item.key ? 'active-pro shadow-sm bg-primary text-white fw-bold' : 'text-secondary hover-bg-light'}`}
                onClick={(event) => {
                  event.preventDefault();
                  onSelectTab(item.key);
                }}
              >
                <span className={activeTab === item.key ? 'text-white' : 'text-tertiary'}>{item.icon}</span>
                <span className={`flex-grow-1 text-start ${item.key === 'eval' ? 'text-nowrap text-truncate dashboard-sidebar-overflow-hidden' : ''}`}>
                  {item.label}
                </span>
                {item.children ? (
                  <span
                    className="d-flex align-items-center justify-content-center hover-bg-light rounded-circle dashboard-sidebar-toggle-icon"
                    onClick={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
                      onToggleExpand(item.key);
                    }}
                  >
                    <FaChevronDown className={`transition-transform ${expandedKeys.includes(item.key) ? 'rotate-180' : ''}`} size={10} />
                  </span>
                ) : null}
              </Nav.Link>

              <Collapse in={Boolean(item.children) && expandedKeys.includes(item.key)}>
                <div className="mt-1 ms-4 ps-2 border-start border-light">
                  <div className="d-flex flex-column gap-1">
                    {item.children?.map((child) => (
                      <Nav.Link
                        key={child.key}
                        as="button"
                        eventKey={child.key}
                        className={`sidebar-link d-flex align-items-center px-3 py-1 rounded transition-all small ${activeTab === child.key ? 'text-primary fw-bold bg-primary bg-opacity-10' : 'text-muted hover-text-primary'}`}
                        onClick={(event) => {
                          event.preventDefault();
                          onSelectTab(child.key);
                        }}
                      >
                        <span className="me-2 d-flex align-items-center justify-content-center dashboard-sidebar-child-icon">
                          {child.icon}
                        </span>
                        <span className="text-nowrap text-truncate dashboard-sidebar-overflow-hidden">
                          {child.label}
                        </span>
                      </Nav.Link>
                    ))}
                  </div>
                </div>
              </Collapse>
            </div>
          ))}
        </Nav>
      </div>

      <div className="p-2 bg-body bg-opacity-25 overflow-hidden dashboard-sidebar-health">
        {healthLoading ? (
          <div className="text-center text-muted x-small">
            <Spinner size="sm" animation="border" className="dashboard-health-spinner" /> 检查服务状态...
          </div>
        ) : (
          <div className="d-flex flex-row gap-1">
            <div className="d-flex justify-content-center align-items-center x-small p-1 px-2 rounded bg-body bg-opacity-50 border flex-fill text-nowrap dashboard-health-chip">
              <span className="text-secondary fw-medium me-1">MySQL</span>
              {healthError ? (
                <Badge bg="danger" className="p-1 dashboard-health-badge">错误</Badge>
              ) : health?.mysql?.ok ? (
                <span className="text-success d-flex align-items-center fw-bold dashboard-health-status">
                  <FaCheckCircle className="me-1" />正常
                </span>
              ) : (
                <span className="text-danger d-flex align-items-center fw-bold dashboard-health-status">
                  <FaExclamationTriangle className="me-1" />异常
                </span>
              )}
            </div>
            <div className="d-flex justify-content-center align-items-center x-small p-1 px-2 rounded bg-body bg-opacity-50 border flex-fill text-nowrap dashboard-health-chip">
              <span className="text-secondary fw-medium me-1">Redis</span>
              {healthError ? (
                <Badge bg="danger" className="p-1 dashboard-health-badge">错误</Badge>
              ) : health?.redis?.ok ? (
                <span className="text-success d-flex align-items-center fw-bold dashboard-health-status">
                  <FaCheckCircle className="me-1" />正常
                </span>
              ) : (
                <span className="text-warning d-flex align-items-center fw-bold dashboard-health-status">
                  <FaExclamationTriangle className="me-1" />未连接
                </span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
