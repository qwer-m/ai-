export type DetectedService = {
  url: string;
  success: true;
  latency: number;
  models: Array<{ id: string; object?: string }>;
};
