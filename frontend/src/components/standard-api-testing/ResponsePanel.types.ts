import type { ResponseTab } from "./utils/types";

export type ResponseViewMode = "json" | "html" | "headers";

export type ResponseFormat =
  | "JSON"
  | "XML"
  | "HTML"
  | "JavaScript"
  | "Raw"
  | "Hex"
  | "Base64";

export type DetailedCookie = {
  value: string;
  domain: string;
  path: string;
  secure: boolean;
  expires: number | null;
};

export type ResponsePanelProps = {
  loading: boolean;
  responseTab: ResponseTab;
  setResponseTab: (tab: ResponseTab) => void;
  responseDetailedCookies: Record<string, DetailedCookie>;
  responseCookies: Record<string, string>;
  responseHeaders: Record<string, string>;
  sentHeaders: Record<string, string>;
  sentCookies: Record<string, string>;
  responseStatus: number | null;
  responseTime: number | null;
  responseBody: string | null;
  responseFormat: ResponseFormat;
  setResponseFormat: (value: ResponseFormat) => void;
  responseViewMode: ResponseViewMode;
  setResponseViewMode: (value: ResponseViewMode) => void;
  aiAnalysis: string | null;
  handleAnalyzeResponse: () => void;
  isAnalyzing: boolean;
  scriptTests: { name: string; passed: boolean; error?: string }[];
};
