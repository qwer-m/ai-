import type { ChangeEvent, ClipboardEvent } from 'react';

export type EvaluationView = 'root' | 'testcase' | 'ui' | 'api' | 'rag';

export type EvaluationProps = {
  projectId: number | null;
  logs: any[];
  onLog: (msg: string) => void;
  view?: EvaluationView;
  evalGenerated: string;
  setEvalGenerated: (v: string) => void;
  evalModified: string;
  setEvalModified: (v: string) => void;
  evalResult: string | null;
  setEvalResult: (v: string | null) => void;
  recallRetrieved: string;
  setRecallRetrieved: (v: string) => void;
  recallRelevant: string;
  setRecallRelevant: (v: string) => void;
  recallResult: string | null;
  setRecallResult: (v: string | null) => void;
  uiEvalScript: string;
  setUiEvalScript: (v: string) => void;
  uiEvalExec: string;
  setUiEvalExec: (v: string) => void;
  uiEvalOutput: string | null;
  setUiEvalOutput: (v: string | null) => void;
  apiEvalScript: string;
  setApiEvalScript: (v: string) => void;
  apiEvalExec: string;
  setApiEvalExec: (v: string) => void;
  apiEvalOutput: string | null;
  setApiEvalOutput: (v: string | null) => void;
  shouldAutoEval?: boolean;
  setShouldAutoEval?: (v: boolean) => void;
};

export type LoadingType = 'eval' | 'recall' | 'ui' | 'api' | null;

export type ToastMessage = {
  type: 'success' | 'error';
  msg: string;
};

export type MetricHistoryItem = {
  created_at?: string;
  precision?: number;
  recall?: number;
  f1_score?: number;
  semantic_similarity?: number;
};

export type QualityMetrics = {
  precision?: number;
  recall?: number;
  f1_score?: number;
  semantic_similarity?: number;
};

export type DefectAnalysis = {
  missing_points?: string[];
  hallucinations?: string[];
  modifications?: string[];
};

export type ParsedQualityReport = {
  metrics: QualityMetrics;
  defectAnalysis: DefectAnalysis;
  summary: string;
} | null;

export type ParsedApiReport = {
  similarity?: number | string;
  score?: number | string;
  coverage?: number | string;
  analysis?: string;
} | null;

export type SupplementHandlers = {
  handleSupplementPaste: (e: ClipboardEvent<HTMLTextAreaElement>) => void;
  handleSupplementFilesChange: (e: ChangeEvent<HTMLInputElement>) => void;
};
