export type TestGenerationProps = {
  projectId: number | null;
  isActive?: boolean;
  onLog: (msg: string) => void;
  onGenerated: (data: any) => void;
  onGenerationComplete?: () => void;
  onError?: (msg: string) => void;
};

export type TestGenerationMode = 'text' | 'file';

export type CoveragePlan = {
  recommended_total: number;
  coverage_dimensions?: string[];
  modules?: Array<{ name: string; count: number; reasons?: string[] }>;
  reasoning?: string[];
  evidence?: Record<string, unknown>;
  strategy?: {
    system_type?: string;
    impact_scope?: string;
    complexity?: string;
    focus_areas?: string[];
  };
};
