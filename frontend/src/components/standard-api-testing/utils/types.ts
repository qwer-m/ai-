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
  disableCookieJar: boolean;
  maxRedirects: number;
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

export type RunSubTab = "params" | "headers" | "authorization" | "body" | "scripts" | "settings";

export const isRunSubTab = (value: string): value is RunSubTab =>
  ["params", "headers", "authorization", "body", "scripts", "settings"].includes(value);

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

  headers?: { key: string; value: string; desc: string }[];
  params?: { key: string; value: string; desc: string }[];
  bodyMode?: BodyMode;
  rawType?: RawType;
  bodyContent?: string;

  preScript?: string;
  postScript?: string;
  timestamp?: number;
  testConfig?: {
    pre_script?: string;
    post_script?: string;
  };
};

export type StandardInterfaceUpdate = {
  name?: string;
  description?: string | null;
  project_id?: number | null;
  parent_id?: number | null;
  type?: "request" | "folder";
  method?: string | null;
  base_url?: string | null;
  api_path?: string | null;
  headers?: KeyValueItem[] | null;
  params?: KeyValueItem[] | null;
  body_mode?: BodyMode | null;
  raw_type?: RawType | null;
  body_content?: string | null;
  test_config?: {
    pre_script?: string;
    post_script?: string;
  } | null;
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
