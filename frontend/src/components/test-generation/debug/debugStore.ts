import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import type {
  CoverageResult,
  CaseQualityGateEvent,
  FeedbackControlStateEvent,
  GenDiagEvent,
  GenDiagSummaryEvent,
  GenerationContextCompressionEvent,
  GenerationConvergenceEvent,
  GenerationQualityLedgerEvent,
  GenerationSummaryEvent,
  JudgeDecisionTableEvent,
  JudgeSummaryEvent,
  MemoryFabricDiagEvent,
  PersistenceGateEvent,
  RequirementParseEvent,
  ReviewDecisionSummaryEvent,
  ReviewDecisionTableCompactEvent,
  StreamBatchTokenUsageEvent,
} from './diagParser';
import { parseGenDiagEvent } from './diagParser';

export type DebugStage = {
  bizKey: string;
  stage: 'primary' | 'gap' | 'review';
  caseCount: number;
  ts: number;
};

export type ResultDebugState = {
  mode: 'text' | 'file';
  resultSource: 'none' | 'streaming_preview' | 'final_persisted';
  generationId: number | null;
  isFinalResultLoaded: boolean;
  previewCaseCount?: number;
  finalCaseCount?: number;
  displayCaseCount?: number;
  ts: number;
};

export type ExecutionSuiteDebugState = {
  generationId: number | null;
  caseCount?: number;
  suiteCount?: number;
  runnableSuiteCount?: number;
  linearExecutable?: boolean;
  executionReadiness?: string;
  mainSuiteId?: string;
  warnings?: string[];
  ts: number;
};

export type JudgeDecisionTableMeta = {
  rowCount?: number;
  rowCountTotal?: number;
  rowCountRejectPending?: number;
  rowsScope?: string;
  rowEvidenceIncomplete?: boolean;
};

export type DebugState = {
  projectId?: number | null;
  generationMode?: string;
  bizKeys: string[];
  currentBizKey?: string;
  stages: DebugStage[];
  coverage?: CoverageResult;
  genDiag?: GenDiagSummaryEvent;
  generationConvergence?: GenerationConvergenceEvent;
  reviewDecisionSummary?: ReviewDecisionSummaryEvent;
  reviewDecisionTableCompactRows?: Array<Record<string, unknown>>;
  judgeSummary?: JudgeSummaryEvent;
  judgeDecisionTableRows?: Array<Record<string, unknown>>;
  judgeDecisionTableMeta?: JudgeDecisionTableMeta;
  generationSummary?: GenerationSummaryEvent;
  generationContextCompression?: GenerationContextCompressionEvent;
  feedbackControlState?: FeedbackControlStateEvent;
  generationQualityLedger?: GenerationQualityLedgerEvent;
  memoryFabricDiag?: MemoryFabricDiagEvent;
  streamBatchTokenUsageRows?: StreamBatchTokenUsageEvent[];
  persistenceGate?: PersistenceGateEvent;
  caseQualityGate?: CaseQualityGateEvent;
  requirementParse?: RequirementParseEvent;
  resultState?: ResultDebugState;
  executionSuiteState?: ExecutionSuiteDebugState;
  lastUpdatedAt?: number;
  ingestDiag: (event: unknown) => void;
  setResultState: (payload: Omit<ResultDebugState, 'ts'>) => void;
  setExecutionSuiteState: (payload: Omit<ExecutionSuiteDebugState, 'ts'>) => void;
  resetForProject: (projectId: number | null) => void;
  reset: () => void;
};

const INITIAL_STATE = {
  projectId: undefined as number | null | undefined,
  generationMode: undefined as string | undefined,
  bizKeys: [] as string[],
  currentBizKey: undefined as string | undefined,
  stages: [] as DebugStage[],
  coverage: undefined as CoverageResult | undefined,
  genDiag: undefined as GenDiagSummaryEvent | undefined,
  generationConvergence: undefined as GenerationConvergenceEvent | undefined,
  reviewDecisionSummary: undefined as ReviewDecisionSummaryEvent | undefined,
  reviewDecisionTableCompactRows: undefined as Array<Record<string, unknown>> | undefined,
  judgeSummary: undefined as JudgeSummaryEvent | undefined,
  judgeDecisionTableRows: undefined as Array<Record<string, unknown>> | undefined,
  judgeDecisionTableMeta: undefined as JudgeDecisionTableMeta | undefined,
  generationSummary: undefined as GenerationSummaryEvent | undefined,
  generationContextCompression: undefined as GenerationContextCompressionEvent | undefined,
  feedbackControlState: undefined as FeedbackControlStateEvent | undefined,
  generationQualityLedger: undefined as GenerationQualityLedgerEvent | undefined,
  memoryFabricDiag: undefined as MemoryFabricDiagEvent | undefined,
  streamBatchTokenUsageRows: undefined as StreamBatchTokenUsageEvent[] | undefined,
  persistenceGate: undefined as PersistenceGateEvent | undefined,
  caseQualityGate: undefined as CaseQualityGateEvent | undefined,
  requirementParse: undefined as RequirementParseEvent | undefined,
  resultState: undefined as ResultDebugState | undefined,
  executionSuiteState: undefined as ExecutionSuiteDebugState | undefined,
  lastUpdatedAt: undefined as number | undefined,
};
const DEBUG_STORE_STORAGE_KEY = 'tg_rag_debug_store_v1';

function normalizeStage(input: string | undefined): 'primary' | 'gap' | 'review' {
  const stage = String(input || '').trim().toLowerCase();
  if (stage === 'gap' || stage === 'review') return stage;
  return 'primary';
}

function upsertStage(stages: DebugStage[], incoming: DebugStage): DebugStage[] {
  const idx = stages.findIndex((s) => s.bizKey === incoming.bizKey && s.stage === incoming.stage);
  if (idx < 0) return [...stages, incoming];
  const next = [...stages];
  next[idx] = incoming;
  return next;
}

function applyEvent(state: DebugState, event: GenDiagEvent): Partial<DebugState> {
  const now = Date.now();

  if (event.kind === 'generation_mode') {
    return {
      generationMode: String(event.mode || '') || state.generationMode,
      bizKeys: Array.isArray(event.biz_keys) ? event.biz_keys.map((x) => String(x)) : state.bizKeys,
      currentBizKey: String(event.current_biz_key || '') || state.currentBizKey,
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'generation_stage') {
    const stage = normalizeStage(event.stage);
    const bizKey = state.currentBizKey || 'global';
    const caseCount = Number(event.case_count || 0);
    return {
      stages: upsertStage(state.stages, { bizKey, stage, caseCount, ts: now }),
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'biz_key_pass_stage') {
    const stage = normalizeStage(event.stage);
    const bizKey = String(event.biz_key || state.currentBizKey || 'global');
    const caseCount = Number(event.case_count || 0);
    return {
      currentBizKey: bizKey,
      stages: upsertStage(state.stages, { bizKey, stage, caseCount, ts: now }),
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'coverage_check') {
    const coveragePayload = event.data && typeof event.data === 'object'
      ? event.data
      : event;
    return {
      coverage: coveragePayload as CoverageResult,
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'gen_diag') {
    return {
      genDiag: event,
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'generation_convergence') {
    return {
      generationConvergence: event,
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'review_decision_summary') {
    return {
      reviewDecisionSummary: event,
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'judge_summary') {
    return {
      judgeSummary: event,
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'judge_decision_table') {
    const tableEvent = event as JudgeDecisionTableEvent;
    const rows = Array.isArray((event as JudgeDecisionTableEvent).rows)
      ? (event as JudgeDecisionTableEvent).rows?.filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
      : [];
    return {
      judgeDecisionTableRows: rows,
      judgeDecisionTableMeta: {
        rowCount: Number(tableEvent.row_count),
        rowCountTotal: Number(tableEvent.row_count_total),
        rowCountRejectPending: Number(tableEvent.row_count_reject_pending),
        rowsScope: String(tableEvent.rows_scope || ''),
        rowEvidenceIncomplete: Boolean(tableEvent.row_evidence_incomplete),
      },
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'review_decision_table_compact') {
    const rows = Array.isArray((event as ReviewDecisionTableCompactEvent).rows)
      ? (event as ReviewDecisionTableCompactEvent).rows?.filter((item): item is Record<string, unknown> => !!item && typeof item === 'object')
      : [];
    return {
      reviewDecisionTableCompactRows: rows,
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'generation_summary') {
    return {
      generationSummary: event,
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'generation_context_compression') {
    return {
      generationContextCompression: event,
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'feedback_control_state') {
    return {
      feedbackControlState: event,
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'generation_quality_ledger') {
    return {
      generationQualityLedger: event,
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'memory_fabric_diag') {
    return {
      memoryFabricDiag: event as MemoryFabricDiagEvent,
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'stream_batch_token_usage') {
    const incoming = event as StreamBatchTokenUsageEvent;
    const rows = Array.isArray(state.streamBatchTokenUsageRows) ? [...state.streamBatchTokenUsageRows] : [];
    const idx = rows.findIndex((row) => (
      Number(row.batch_index) === Number(incoming.batch_index)
      && Number(row.attempt || 1) === Number(incoming.attempt || 1)
      && String(row.request_id || '') === String(incoming.request_id || '')
    ));
    if (idx >= 0) rows[idx] = incoming;
    else rows.push(incoming);
    rows.sort((a, b) => (
      Number(a.batch_index || 0) - Number(b.batch_index || 0)
      || Number(a.attempt || 0) - Number(b.attempt || 0)
    ));
    return {
      streamBatchTokenUsageRows: rows.slice(-80),
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'persistence_gate') {
    return {
      persistenceGate: event,
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'case_quality_gate') {
    return {
      caseQualityGate: event,
      lastUpdatedAt: now,
    };
  }

  if (event.kind === 'requirement_parse') {
    return {
      requirementParse: event,
      lastUpdatedAt: now,
    };
  }

  return {};
}

export const useRagDebugStore = create<DebugState>()(
  persist(
    (set) => ({
      ...INITIAL_STATE,

      ingestDiag: (raw: unknown) => {
        const event = parseGenDiagEvent(raw);
        if (!event) return;
        set((state) => applyEvent(state as DebugState, event));
      },

      setResultState: (payload) => {
        const now = Date.now();
        set({
          resultState: {
            ...payload,
            ts: now,
          },
          lastUpdatedAt: now,
        });
      },

      setExecutionSuiteState: (payload) => {
        const now = Date.now();
        set({
          executionSuiteState: {
            ...payload,
            ts: now,
          },
          lastUpdatedAt: now,
        });
      },

      resetForProject: (projectId) => {
        set({ ...INITIAL_STATE, projectId });
      },

      reset: () => {
        set({ ...INITIAL_STATE });
      },
    }),
    {
      name: DEBUG_STORE_STORAGE_KEY,
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        projectId: state.projectId,
        generationMode: state.generationMode,
        bizKeys: state.bizKeys,
        currentBizKey: state.currentBizKey,
        stages: state.stages,
        coverage: state.coverage,
        genDiag: state.genDiag,
        generationConvergence: state.generationConvergence,
        reviewDecisionSummary: state.reviewDecisionSummary,
        reviewDecisionTableCompactRows: state.reviewDecisionTableCompactRows,
        judgeSummary: state.judgeSummary,
        judgeDecisionTableRows: state.judgeDecisionTableRows,
        judgeDecisionTableMeta: state.judgeDecisionTableMeta,
        generationSummary: state.generationSummary,
        generationContextCompression: state.generationContextCompression,
        feedbackControlState: state.feedbackControlState,
        generationQualityLedger: state.generationQualityLedger,
        memoryFabricDiag: state.memoryFabricDiag,
        streamBatchTokenUsageRows: state.streamBatchTokenUsageRows,
        persistenceGate: state.persistenceGate,
        caseQualityGate: state.caseQualityGate,
        requirementParse: state.requirementParse,
        resultState: state.resultState,
        executionSuiteState: state.executionSuiteState,
        lastUpdatedAt: state.lastUpdatedAt,
      }),
    }
  )
);

export function selectTotalCaseCount(state: DebugState): number {
  if (!state.stages.length) return 0;
  const perBizMax = new Map<string, number>();
  for (const row of state.stages) {
    perBizMax.set(row.bizKey, Math.max(perBizMax.get(row.bizKey) || 0, Number(row.caseCount || 0)));
  }
  let total = 0;
  for (const v of perBizMax.values()) total += v;
  return total;
}
