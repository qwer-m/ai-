import { Badge, Button, Collapse, Nav } from 'react-bootstrap';
import { FaChevronDown, FaMoon, FaServer, FaSun } from 'react-icons/fa';
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

type NavGroup = {
  key: string;
  label: string;
  itemKeys: string[];
};

const SIDEBAR_GROUPS: NavGroup[] = [
  { key: 'core', label: '核心流程', itemKeys: ['api-gen', 'kb', 'proj'] },
  { key: 'automation', label: '自动化执行', itemKeys: ['ui-exec-ui', 'api', 'ui-exec-api'] },
  { key: 'quality', label: '质量与诊断', itemKeys: ['eval'] },
];

function buildGroupedItems(navItems: DashboardNavItem[]) {
  const itemMap = new Map<string, DashboardNavItem>(navItems.map((item) => [item.key, item]));
  const consumed = new Set<string>();
  const grouped = SIDEBAR_GROUPS.map((group) => {
    const items: DashboardNavItem[] = [];
    group.itemKeys.forEach((key) => {
      const item = itemMap.get(key);
      if (item) {
        items.push(item);
        consumed.add(key);
      }
    });
    return { ...group, items };
  }).filter((group) => group.items.length > 0);

  const leftovers = navItems.filter((item) => !consumed.has(item.key));
  if (leftovers.length > 0) {
    grouped.push({ key: 'others', label: '其他', itemKeys: [], items: leftovers });
  }
  return grouped;
}

export function DashboardSidebar({
  userName,
  themeMode,
  navItems,
  activeTab,
  expandedKeys,
  onSelectTab,
  onToggleExpand,
  onToggleTheme,
}: Props) {
  const groupedItems = buildGroupedItems(navItems);

  const renderNavItem = (item: DashboardNavItem) => {
    const isCurrent = item.key === activeTab || item.children?.some((child) => child.key === activeTab);

    return (
      <div key={item.key} className="d-flex flex-column">
      <Nav.Link
        as="button"
        eventKey={item.key}
        className={`sidebar-link dashboard-sidebar-link-row d-flex align-items-center gap-3 px-3 py-2 rounded-lg transition-all ${isCurrent ? 'active-pro fw-bold' : 'text-secondary hover-bg-light'}`}
        onClick={(event) => {
          event.preventDefault();
          if (item.children && item.children.length > 0) {
            onToggleExpand(item.key);
            onSelectTab(item.children[0].key);
            return;
          }
          onSelectTab(item.key);
        }}
      >
        <span className={isCurrent ? 'text-primary' : 'text-tertiary'}>{item.icon}</span>
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
    );
  };

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
        <Nav variant="pills" className="flex-column gap-3" activeKey={activeTab}>
          {groupedItems.map((group) => (
            <div key={group.key} className="dashboard-nav-group d-flex flex-column gap-2">
              <div className="dashboard-nav-group-title">{group.label}</div>
              <div className="d-flex flex-column gap-2">
                {group.items.map((item) => renderNavItem(item))}
              </div>
            </div>
          ))}
        </Nav>
      </div>
    </div>
  );
}
