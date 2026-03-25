import type { ReactNode } from "react";
import type { ResponseTab, TestResult } from "./utils/types";

export type ResponseViewMode = "json" | "html" | "headers";

export type ResponseFormat =
  | "JSON"
  | "XML"
  | "HTML"
  | "JavaScript"
  | "Raw"
  | "Hex"
  | "Base64";

export type ResponsePanelProps = {
  loading: boolean;
  responseTab: ResponseTab;
  setResponseTab: (tab: ResponseTab) => void;
  responseDetailedCookies: any;
  responseCookies: any;
  responseHeaders: any;
  sentHeaders: any;
  sentCookies: any;
  responseStatus: number | null;
  responseTime: number | null;
  responseBody: any;
  responseFormat: ResponseFormat;
  setResponseFormat: (value: ResponseFormat) => void;
  responseViewMode: ResponseViewMode;
  setResponseViewMode: (value: ResponseViewMode) => void;
  aiAnalysis: string | null;
  testResult: TestResult | null;
  renderDashboard: (report: NonNullable<TestResult["structured_report"]>) => ReactNode;
  handleAnalyzeResponse: () => void;
  isAnalyzing: boolean;
  scriptTests: { name: string; passed: boolean; error?: string }[];
};
