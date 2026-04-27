export type DetectedService = {
  url: string;
  success: boolean;
  latency?: number;
  models?: Array<{ id: string; object: string }>;
};
