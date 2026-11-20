import { Badge, Button, Nav } from 'react-bootstrap';
import { FaMoon, FaServer, FaSun } from 'react-icons/fa';
import type { DashboardNavItem, HealthResponse } from './model/types';

type Props = {
  userName?: string;
  themeMode: 'light' | 'dark';
  navItems: DashboardNavItem[];
  activeTab: string;
  healthLoading: boolean;
  healthError: string | null;
  health: HealthResponse | null;
  onSelectTab: (key: string) => void;
  onToggleTheme: () => void;
};

type NavGroup = {
  key: string;
  label: string;
  itemKeys: string[];
};

const SIDEBAR_GROUPS: NavGroup[] = [
  { key: 'core', label: '核心流程', itemKeys: ['agent-generation', 'kb', 'proj'] },
  { key: 'quality', label: '质量与诊断', itemKeys: ['eval-testcase'] },
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
  onSelectTab,
  onToggleTheme,
}: Props) {
  const groupedItems = buildGroupedItems(navItems);

  const renderNavItem = (item: DashboardNavItem) => {
    const isCurrent = item.key === activeTab;

    return (
      <Nav.Link
        key={item.key}
        as="button"
        eventKey={item.key}
        aria-current={isCurrent ? 'page' : undefined}
        className={`sidebar-link dashboard-sidebar-link-row d-flex align-items-center gap-3 px-3 py-2 rounded-lg transition-all ${isCurrent ? 'active-pro fw-bold' : 'text-secondary hover-bg-light'}`}
        onClick={() => onSelectTab(item.key)}
      >
        <span aria-hidden="true" className={isCurrent ? 'text-primary' : 'text-tertiary'}>{item.icon}</span>
        <span className="flex-grow-1 text-start">{item.label}</span>
      </Nav.Link>
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
