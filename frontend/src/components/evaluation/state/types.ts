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
  test_input: string;
};

export type QualityMetrics = {
  precision: number | null;
  recall: number | null;
  f1_score: number | null;
  coverage_jaccard: number | null;
};

export type DefectAnalysis = {
  missing_points: string[];
  hallucinations: string[];
  modifications: string[];
};

export const assertionLabels = {
  detects_fault: '可检出错误', missing_trigger: '缺少实际触发', unobservable: '预期不可判定',
  wrong_oracle: '预期与需求矛盾', unclear_requirement: '需求依据不足', no_case: '无生成用例',
} as const;

export type AssertionEvidence = {
  verdict: keyof typeof assertionLabels; case_id: string; steps: number[]; fault: string;
};

export type RequirementBaseline = {
  groups: Array<{
    point: string; agent: string; manual: string; case_ids: string[]; manual_refs: string[];
    reason: string; difference: string; excluded: boolean; assertion?: AssertionEvidence;
  }>;
  requirement_points: string[];
  ai_requirement_gaps: string[];
  human_requirement_gaps: string[];
  ai_unanchored_points: string[];
  human_added_value: string[];
  both_missing_points: string[];
  covered_by_both: string[];
  generated_coverage_count: number;
  modified_coverage_count: number;
  generated_coverage_rate: number | null;
  modified_coverage_rate: number | null;
  summary: string;
};

export type QualityReportPayload = {
  metrics: QualityMetrics;
  defect_analysis: DefectAnalysis;
  requirement_baseline: RequirementBaseline;
  summary: string;
};

// 评估页面只保留展示所需的产物投影，运行契约由 Agent 模块维护。
export type TestEvaluationArtifact = {
  source_run_id: number;
  evaluation_run_id: number;
  reference_content: string;
  evaluation: QualityReportPayload;
  upload: {
    filename: string;
  };
};


export type EvaluationSource = {
  project_id: number;
  document_id: number;
  run_id: number;
  requirement: string;
  cases: GeneratedTestCase[];
  evaluation: TestEvaluationArtifact | null;
};
