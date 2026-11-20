import type { ReactNode } from 'react';

export type LogEntry = {
  id: number;
  project_id: number;
  log_type: 'user' | 'system';
  message: string;
  created_at: string;
};

export type HealthResponse = {
  mysql?: { ok: boolean; details?: string };
  redis?: { ok: boolean; details?: string; host?: string; port?: number };
};

export type DashboardNavItem = {
  key: string;
  label: string;
  icon: ReactNode;
};
