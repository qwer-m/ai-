import {
  FaClipboardCheck,
  FaDatabase,
  FaFileCode,
  FaFolder,
} from 'react-icons/fa';
import type { DashboardNavItem } from './types';

export const dashboardNavItems: DashboardNavItem[] = [
  { key: 'agent-generation', label: 'Agent 测试生成', icon: <FaFileCode /> },
  { key: 'eval-testcase', label: '测试用例质量评估', icon: <FaClipboardCheck /> },
  { key: 'kb', label: '知识库管理', icon: <FaDatabase /> },
  { key: 'proj', label: '项目管理', icon: <FaFolder /> },
];

export function normalizeDashboardActiveTab(saved: string | null, items: DashboardNavItem[]) {
  return saved && items.some((item) => item.key === saved) ? saved : 'agent-generation';
}

export function getDashboardLabelByKey(items: DashboardNavItem[], key: string) {
  return items.find((item) => item.key === key)?.label ?? key;
}
