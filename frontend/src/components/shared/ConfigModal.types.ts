export type DetectedService = {
  url: string;
  success: true;
  latency: number;
  models: Array<{ id: string; object?: string }>;
};

export type ModelRoute = 'main' | 'turbo' | 'review' | 'vision';
export type ReasoningEffort = 'none' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';
export type ReasoningCapability = {
  effort?: ReasoningEffort | null;
  supported_efforts: ReasoningEffort[];
  status: 'verified' | 'request_accepted' | 'unavailable' | 'error';
  accepted_effort?: ReasoningEffort | null;
  message: string;
  checked_at?: string;
};
export type ReasoningCapabilities = Partial<Record<ModelRoute, ReasoningCapability>>;
export type ReasoningSelections = Partial<Record<ModelRoute, ReasoningEffort>>;
export const REASONING_OPTIONS: Array<{ value: ReasoningEffort; label: string }> = [
  { value: 'none', label: '关' }, { value: 'low', label: '低' },
  { value: 'medium', label: '中（medium）' }, { value: 'high', label: '高' },
  { value: 'xhigh', label: 'xhigh' }, { value: 'max', label: 'max' },
];
