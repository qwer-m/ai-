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

export type LoadingType = 'eval' | 'recall' | 'ui' | 'api' | 'save_knowledge' | null;

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

export type RequirementBaseline = {
  requirement_points?: string[];
  ai_requirement_gaps?: string[];
  human_requirement_gaps?: string[];
  ai_unanchored_points?: string[];
  human_added_value?: string[];
  both_missing_points?: string[];
  missing_in_generated?: string[];
  missing_in_modified?: string[];
  covered_by_both?: string[];
  generated_coverage_count?: number;
  modified_coverage_count?: number;
  generated_coverage_rate?: number;
  modified_coverage_rate?: number;
  summary?: string;
  heuristic?: {
    requirement_points?: string[];
    missing_in_generated?: string[];
    missing_in_modified?: string[];
    both_missing_points?: string[];
    covered_by_both?: string[];
    generated_coverage_count?: number;
    modified_coverage_count?: number;
    generated_coverage_rate?: number;
    modified_coverage_rate?: number;
  };
};

export type ParsedQualityReport = {
  metrics: QualityMetrics;
  defectAnalysis: DefectAnalysis;
  requirementBaseline?: RequirementBaseline;
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
