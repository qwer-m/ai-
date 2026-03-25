export type StandardAPITestingProps = {
  projectId: number | null;
  onLog: (msg: string) => void;
};

export type KeyValueItem = {
  key: string;
  value: string;
  desc: string;
};

export type FormDataItem = KeyValueItem & {
  type: "text" | "file";
  src?: string;
};

export type BodyMode = "none" | "form-data" | "x-www-form-urlencoded" | "raw" | "binary" | "graphql";

export type RawType = "Text" | "JavaScript" | "JSON" | "HTML" | "XML";

export type AuthType = "none" | "bearer" | "basic" | "apikey";

export type RequestSettings = {
  timeout: number;
  followRedirects: boolean;
  verifySSL: boolean;
  httpVersion: string;
  followOriginalHttpMethod: boolean;
  followAuthorizationHeader: boolean;
  removeRefererHeader: boolean;
  strictHttpParser: boolean;
  encodeUrl: boolean;
  disableCookieJar: boolean;
  useServerCipherSuite: boolean;
  maxRedirects: number;
  disabledSSLProtocols: string;
  cipherSuites: string;
};

export type AuthBasicCredentials = {
  username: string;
  password: string;
};

export type AuthApiKey = {
  key: string;
  value: string;
  addTo: "header" | "query";
};

export type ResponseTab = "body" | "cookies" | "headers" | "test_results" | "report";

export type TestResult = {
  script: string;
  result: string; // 原始 stdout/stderr
  structured_report?: {
    total: number;
    passed: number;
    failed: number;
    skipped: number;
    time: number;
    failures: Array<{
      name: string;
      message: string;
      details: string;
      type?: string;
    }>;
  };
};

export type SavedInterface = {
  id: number;
  type: "request" | "folder";
  name: string;
  description?: string;
  parentId: number | null;
  isOpen?: boolean; // 仅前端使用

  // 请求特定字段
  baseUrl?: string;
  apiPath?: string;
  method?: string;
  requirement?: string;
  mode?: "natural" | "structured";

  headers?: { key: string; value: string; desc: string }[];
  params?: { key: string; value: string; desc: string }[];
  bodyMode?: string;
  rawType?: string;
  bodyContent?: string;

  testTypes?: {
    functional: boolean;
    boundary: boolean;
    security: boolean;
  };
  preScript?: string;
  postScript?: string;
  timestamp?: number;
  testConfig?: any;
};

export type EnvConfig = {
  id: string;
  name: string;
  baseUrl: string;
  variables?: Array<{
    key: string;
    value: string;
    enabled: boolean;
  }>;
};
