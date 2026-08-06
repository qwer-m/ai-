
export type EvaluationView = 'root' | 'testcase' | 'ui' | 'api' | 'rag';

export type EvaluationProps = {
  projectId: number | null;
  onLog: (msg: string) => void;
  view?: EvaluationView;
  evalGenerated: string;
  setEvalGenerated: (v: string) => void;
  evalModified: string;
  setEvalModified: (v: string) => void;
  evalResult: QualityReport | null;
  setEvalResult: (v: QualityReport | null) => void;
  uiEvalScript: string;
  setUiEvalScript: (v: string) => void;
  uiEvalExec: string;
  setUiEvalExec: (v: string) => void;
  uiEvalOutput: AutomationEvaluationReport | null;
  setUiEvalOutput: (v: AutomationEvaluationReport | null) => void;
  apiEvalScript: string;
  setApiEvalScript: (v: string) => void;
  apiEvalExec: string;
  setApiEvalExec: (v: string) => void;
  apiEvalOutput: AutomationEvaluationReport | null;
  setApiEvalOutput: (v: AutomationEvaluationReport | null) => void;
};

export type AgentRunStatus =
  | 'pending'
  | 'running'
  | 'waiting_approval'
  | 'success'
  | 'failed'
  | 'cancelled';

export type GeneratedTestCase = {
  case_id: string;
  title: string;
  module: string;
  priority: 'P0' | 'P1' | 'P2';
  preconditions: string[];
  steps: Array<{
    action: string;
    expected: string;
  }>;
  tags: string[];
};

export type QualityMetrics = {
  precision: number;
  recall: number;
  f1_score: number;
  semantic_similarity: number;
};

export type DefectAnalysis = {
  missing_points: string[];
  hallucinations: string[];
  modifications: string[];
};

export type RequirementBaseline = {
  requirement_points: string[];
  ai_requirement_gaps: string[];
  human_requirement_gaps: string[];
  ai_unanchored_points: string[];
  human_added_value: string[];
  both_missing_points: string[];
  covered_by_both: string[];
  generated_coverage_count: number;
  modified_coverage_count: number;
  generated_coverage_rate: number;
  modified_coverage_rate: number;
  summary: string;
};

export type QualityReportPayload = {
  metrics: QualityMetrics;
  defect_analysis: DefectAnalysis;
  requirement_baseline: RequirementBaseline;
  summary: string;
};

export type QualityReport = {
  metrics: QualityMetrics;
  defectAnalysis: DefectAnalysis;
  requirementBaseline: RequirementBaseline;
  summary: string;
};

export type TestGenerationArtifact = {
  project_id: number;
  run_id: number;
  requirement: string;
  evidence: {
    source: {
      kind: 'inline' | 'knowledge_document';
      document_id: number | null;
      filename: string;
      doc_type: string;
      content_hash: string;
    };
  };
  case_count: number;
  test_cases: GeneratedTestCase[];
};

export type TestEvaluationArtifact = {
  source_run_id: number;
  evaluation_run_id: number;
  project_id: number;
  requirement: string;
  reference_content: string;
  evaluation: QualityReportPayload;
  upload: {
    filename: string;
    content_type: string;
    size: number;
    ocr: {
      source: string;
      ok: boolean;
      cloud_fallback: boolean;
      error: string;
    };
  };
};

export type EvaluationRunRecord = {
  run_id: number;
  project_id: number;
  status: AgentRunStatus;
  current_node_key: string | null;
  parent_run_id: number | null;
  requirement_text: string;
  case_count: number;
  test_cases: GeneratedTestCase[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  has_evaluation: boolean;
  evaluation_artifact: TestEvaluationArtifact | null;
};

export type EvaluationHistoryPoint = {
  id: string;
  type: 'agent_run_evaluation';
  created_at: string;
  preview: string;
  precision: number | null;
  recall: number | null;
  f1_score: number | null;
  semantic_similarity: number | null;
};

export type LoadingType = 'eval' | 'ui' | 'api' | null;

export type ToastMessage = {
  type: 'success' | 'error';
  msg: string;
};

export type AutomationEvaluationStatus = 'success' | 'failed' | 'unknown';

export type AutomationEvaluationCriterion = {
  key: string;
  name: string;
  score: number;
  analysis: string;
};

export type AutomationEvaluationCoverage = {
  rate: number | null;
  covered_items: string[];
  missing_items: string[];
  explanation: string;
};

export type AutomationEvaluationReport = {
  summary: string;
  overall_score: number;
  execution_status: AutomationEvaluationStatus;
  criteria: AutomationEvaluationCriterion[];
  coverage: AutomationEvaluationCoverage;
  risks: string[];
  recommendations: string[];
};
