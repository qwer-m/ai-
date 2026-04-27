import type { Dispatch, MutableRefObject, ReactNode, SetStateAction } from "react";
import type {
  AuthApiKey,
  AuthBasicCredentials,
  AuthType,
  BodyMode,
  FormDataItem,
  KeyValueItem,
  RawType,
  RequestSettings,
  ResponseTab,
  TestResult,
} from "./utils/types";

export type StandardApiTestingRequestWorkspaceProps = {
  showSidebar: boolean;
  setShowSidebar: Dispatch<SetStateAction<boolean>>;
  isDragging: boolean;
  requestHeight: number;
  handleRequestBarMouseDown: (e: React.MouseEvent) => void;
  mainContentRef: MutableRefObject<HTMLDivElement | null>;
  method: string;
  setMethod: Dispatch<SetStateAction<string>>;
  apiPath: string;
  setApiPath: Dispatch<SetStateAction<string>>;
  inputRef: MutableRefObject<HTMLInputElement | null>;
  highlighterRef: MutableRefObject<HTMLDivElement | null>;
  bodyHighlighterRef: MutableRefObject<HTMLDivElement | null>;
  activeEnvTag: string | null;
  showPopup: boolean;
  setShowPopup: Dispatch<SetStateAction<boolean>>;
  handleInputMouseMove: (e: React.MouseEvent<HTMLInputElement>) => void;
  handleInputMouseLeave: () => void;
  handlePopupMouseEnter: () => void;
  handlePopupMouseLeave: () => void;
  getEnvBaseUrlValue: (tag: string) => string;
  setEnvBaseUrlValue: (tag: string, val: string) => void;
  handleApiPathBlur: () => void;
  loading: boolean;
  handleSendRequest: () => void;
  handleSaveInterfaceClick: () => void;
  handleSaveEnv: () => void;
  mode: "natural" | "structured";
  setMode: Dispatch<SetStateAction<"natural" | "structured">>;
  requirement: string;
  setRequirement: Dispatch<SetStateAction<string>>;
  handleRun: () => void;
  runSubTab: string;
  setRunSubTab: (key: string) => void;
  queryParams: KeyValueItem[];
  setQueryParams: Dispatch<SetStateAction<KeyValueItem[]>>;
  headers: KeyValueItem[];
  setHeaders: Dispatch<SetStateAction<KeyValueItem[]>>;
  authType: AuthType;
  setAuthType: Dispatch<SetStateAction<AuthType>>;
  authToken: string;
  setAuthToken: Dispatch<SetStateAction<string>>;
  authBasic: AuthBasicCredentials;
  setAuthBasic: Dispatch<SetStateAction<AuthBasicCredentials>>;
  authApiKey: AuthApiKey;
  setAuthApiKey: Dispatch<SetStateAction<AuthApiKey>>;
  activeScriptTab: "pre" | "post";
  setActiveScriptTab: Dispatch<SetStateAction<"pre" | "post">>;
  preRequestScript: string;
  setPreRequestScript: Dispatch<SetStateAction<string>>;
  postResponseScript: string;
  setPostResponseScript: Dispatch<SetStateAction<string>>;
  requestSettings: RequestSettings;
  setRequestSettings: Dispatch<SetStateAction<RequestSettings>>;
  bodyMode: BodyMode;
  setBodyMode: Dispatch<SetStateAction<BodyMode>>;
  rawType: RawType;
  setRawType: Dispatch<SetStateAction<RawType>>;
  bodyContent: string;
  setBodyContent: Dispatch<SetStateAction<string>>;
  formDataParams: FormDataItem[];
  setFormDataParams: Dispatch<SetStateAction<FormDataItem[]>>;
  xWwwFormUrlencodedParams: KeyValueItem[];
  setXWwwFormUrlencodedParams: Dispatch<SetStateAction<KeyValueItem[]>>;
  binaryFile: { name: string; data: string } | null;
  setBinaryFile: Dispatch<SetStateAction<{ name: string; data: string } | null>>;
  graphqlQuery: string;
  setGraphqlQuery: Dispatch<SetStateAction<string>>;
  graphqlVariables: string;
  setGraphqlVariables: Dispatch<SetStateAction<string>>;
  isBulkEditFormData: boolean;
  setIsBulkEditFormData: Dispatch<SetStateAction<boolean>>;
  formDataBulkText: string;
  setFormDataBulkText: Dispatch<SetStateAction<string>>;
  isBulkEditBody: boolean;
  setIsBulkEditBody: Dispatch<SetStateAction<boolean>>;
  bodyBulkText: string;
  setBodyBulkText: Dispatch<SetStateAction<string>>;
  isBulkEditParams: boolean;
  setIsBulkEditParams: Dispatch<SetStateAction<boolean>>;
  paramsBulkText: string;
  setParamsBulkText: Dispatch<SetStateAction<string>>;
  isBulkEditHeaders: boolean;
  setIsBulkEditHeaders: Dispatch<SetStateAction<boolean>>;
  headersBulkText: string;
  setHeadersBulkText: Dispatch<SetStateAction<string>>;
  handleBodyScroll: (e: React.UIEvent<HTMLTextAreaElement>) => void;
  responseTab: ResponseTab;
  setResponseTab: Dispatch<SetStateAction<ResponseTab>>;
  responseDetailedCookies: Record<string, string>;
  responseCookies: Record<string, string>;
  responseHeaders: Record<string, string>;
  sentHeaders: Record<string, string>;
  sentCookies: Record<string, string>;
  responseStatus: number | null;
  responseTime: number | null;
  responseBody: string | null;
  responseFormat: "JSON" | "XML" | "HTML" | "JavaScript" | "Raw" | "Hex" | "Base64";
  setResponseFormat: Dispatch<SetStateAction<"JSON" | "XML" | "HTML" | "JavaScript" | "Raw" | "Hex" | "Base64">>;
  responseViewMode: "json" | "html" | "headers";
  setResponseViewMode: Dispatch<SetStateAction<"json" | "html" | "headers">>;
  aiAnalysis: string | null;
  testResult: TestResult | null;
  handleAnalyzeResponse: () => void;
  isAnalyzing: boolean;
  scriptTests: { name: string; passed: boolean; error?: string }[];
  renderDashboard: (report: NonNullable<TestResult["structured_report"]>) => ReactNode;
  onOpenCookieManager: () => void;
};
