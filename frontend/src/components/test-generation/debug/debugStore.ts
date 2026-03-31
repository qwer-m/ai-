import { create } from 'zustand';
import type { CoverageResult, GenDiagEvent } from './diagParser';
import { parseGenDiagEvent } from './diagParser';
import { mockGenDiagEvents } from './mockGenDiag';

export type DebugStage = {
  bizKey: string;
  stage: 'primary' | 'gap' | 'review';
  caseCount: number;
  ts: number;
};

export type DebugState = {
  generationMode?: string;
  bizKeys: string[];
  currentBizKey?: string;
  stages: DebugStage[];
  coverage?: CoverageResult;
  lastUpdatedAt?: number;
  ingestDiag: (event: unknown) => void;
  reset: () => void;
  loadMock: () => void;
};

const INITIAL_STATE = {
  generationMode: undefined as string | undefined,
  bizKeys: [] as string[],
  currentBizKey: undefined as string | undefined,
  stages: [] as DebugStage[],
  coverage: undefined as CoverageResult | undefined,
  lastUpdatedAt: undefined as number | undefined,
};

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
    return {
      coverage: event.data || state.coverage,
      lastUpdatedAt: now,
    };
  }

  return {};
}

export const useRagDebugStore = create<DebugState>((set, get) => ({
  ...INITIAL_STATE,

  // 中文注释：统一入口，兼容对象与字符串日志输入。
  ingestDiag: (raw: unknown) => {
    const event = parseGenDiagEvent(raw);
    if (!event) return;
    set((state) => applyEvent(state as DebugState, event));
  },

  // 中文注释：每次新生成前调用，避免上次调试数据残留。
  reset: () => {
    set({ ...INITIAL_STATE });
  },

  // 中文注释：本地联调辅助，不依赖后端也可查看调试面板。
  loadMock: () => {
    get().reset();
    for (const item of mockGenDiagEvents) {
      get().ingestDiag(item);
    }
  },
}));

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

