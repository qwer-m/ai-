import {
  FaClipboardCheck,
  FaCode,
  FaDatabase,
  FaFileCode,
  FaFolder,
  FaGlobe,
  FaLayerGroup,
  FaMobileAlt,
  FaMousePointer,
  FaNetworkWired,
  FaPlay,
  FaRedo,
  FaRobot,
  FaSearch,
} from 'react-icons/fa';
import type { DashboardNavItem } from './types';

export const dashboardNavItems: DashboardNavItem[] = [
  { key: 'api-gen', label: '测试用例', icon: <FaFileCode /> },
  {
    key: 'ui-exec-ui',
    label: 'UI自动化',
    icon: <FaMousePointer />,
    children: [
      { key: 'ui-exec-ui-web', label: 'WEB自动化', icon: <FaGlobe /> },
      { key: 'ui-exec-ui-app', label: 'APP自动化', icon: <FaMobileAlt /> },
      { key: 'ui-exec-ui-regression', label: '回归测试', icon: <FaRedo /> },
    ],
  },
  {
    key: 'api',
    label: '接口测试',
    icon: <FaNetworkWired />,
    children: [
      { key: 'api-standard', label: '标准接口测试', icon: <FaCode /> },
      { key: 'api-ai', label: '模型调试', icon: <FaRobot /> },
    ],
  },
  {
    key: 'ui-exec-api',
    label: '接口自动化',
    icon: <FaNetworkWired />,
    children: [
      { key: 'ui-exec-api-orchestration', label: '自动化编排', icon: <FaLayerGroup /> },
      { key: 'ui-exec-api-batch', label: '批量运行', icon: <FaPlay /> },
    ],
  },
  {
    key: 'eval',
    label: '质量评估与召回',
    icon: <FaClipboardCheck />,
    children: [
      { key: 'eval-testcase', label: '测试用例质量评估', icon: <FaClipboardCheck /> },
      { key: 'eval-ui', label: '界面自动化评估', icon: <FaRobot /> },
      { key: 'eval-api', label: '接口测试评估', icon: <FaNetworkWired /> },
      { key: 'eval-rag', label: 'RAG校验', icon: <FaSearch /> },
    ],
  },
  { key: 'kb', label: '知识库管理', icon: <FaDatabase /> },
  { key: 'proj', label: '项目管理', icon: <FaFolder /> },
];

export function getAllDashboardNavKeys(items: DashboardNavItem[]) {
  return items.flatMap((item) => [item.key, ...(item.children ? item.children.map((child) => child.key) : [])]);
}

export function normalizeDashboardActiveTab(saved: string | null, items: DashboardNavItem[]) {
  if (saved === 'ui-exec-ui') return 'ui-exec-ui-web';
  if (!saved) return 'api-gen';

  const validKeys = getAllDashboardNavKeys(items);
  return validKeys.includes(saved) ? saved : 'api-gen';
}

export function findParentKeyByChild(items: DashboardNavItem[], childKey: string) {
  const parent = items.find((item) => item.children?.some((child) => child.key === childKey));
  return parent?.key ?? null;
}

export function getDashboardLabelByKey(items: DashboardNavItem[], key: string) {
  for (const item of items) {
    if (item.key === key) return item.label;
    const child = item.children?.find((c) => c.key === key);
    if (child) return child.label;
  }
  return key;
}
