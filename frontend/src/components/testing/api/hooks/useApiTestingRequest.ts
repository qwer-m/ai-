import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
  type UIEvent,
} from 'react';
import { api } from '../../../../utils/api';
import type {
  ResponseFormat,
  ResponseViewMode,
  DetailedCookie,
} from '../../../standard-api-testing/ResponsePanel.types';
import { translateError } from '../../../standard-api-testing/utils/requestUi';
import {
  buildDebugRequestPayload,
  createRuntimeContext,
  executePostResponseScript,
  executePreRequestScript,
  resolveActiveEnv,
  type ScriptTest,
} from '../../../standard-api-testing/utils/requestExecution';
import type {
  AuthApiKey,
  AuthBasicCredentials,
  AuthType,
  BodyMode,
  EnvConfig,
  FormDataItem,
  KeyValueItem,
  RawType,
  RequestSettings,
  ResponseTab,
  RunSubTab,
} from '../../../standard-api-testing/utils/types';
import {
  createEmptyFormData,
  createEmptyKeyValue,
  resolveTargetContentType,
} from './useApiTestingRequestUtils';

type DebugRequestResponse = {
  status_code: number;
  headers: Record<string, string>;
  body: string;
  cookies: Record<string, DetailedCookie>;
  elapsed_ms: number;
  is_binary: boolean;
  url: string;
};

type AnalyzeResponseResult = {
  analysis: string;
};

type UseApiTestingRequestParams = {
  onLog: (message: string) => void;
  apiPath: string;
  savedEnvs: EnvConfig[];
  setSavedEnvs: Dispatch<SetStateAction<EnvConfig[]>>;
  getEnvBaseUrlValue: (key: string) => string;
  cookieJar: Record<string, string>;
  setCookieJar: Dispatch<SetStateAction<Record<string, string>>>;
};

type BinaryFile = { name: string; data: string };
type ScriptTab = 'pre' | 'post';

const AUTO_GENERATED_DESCRIPTION = 'Auto-generated';

const DEFAULT_REQUEST_SETTINGS: RequestSettings = {
  timeout: 0,
  followRedirects: true,
  verifySSL: false,
  httpVersion: 'HTTP/1.x',
  disableCookieJar: false,
  maxRedirects: 10,
};

function extractCookieValues(cookies: Record<string, DetailedCookie>): Record<string, string> {
  return Object.fromEntries(
    Object.entries(cookies).map(([name, cookie]) => [name, cookie.value]),
  );
}

export function useApiTestingRequest(params: UseApiTestingRequestParams) {
  const {
    onLog,
    apiPath,
    savedEnvs,
    setSavedEnvs,
    getEnvBaseUrlValue,
    cookieJar,
    setCookieJar,
  } = params;

  const [method, setMethod] = useState('POST');
  const [loading, setLoading] = useState(false);
  const [responseStatus, setResponseStatus] = useState<number | null>(null);
  const [responseTime, setResponseTime] = useState<number | null>(null);
  const [responseBody, setResponseBody] = useState<string | null>(null);
  const [responseHeaders, setResponseHeaders] = useState<Record<string, string>>({});
  const [responseCookies, setResponseCookies] = useState<Record<string, string>>({});
  const [responseDetailedCookies, setResponseDetailedCookies] = useState<
    Record<string, DetailedCookie>
  >({});
  const [sentHeaders, setSentHeaders] = useState<Record<string, string>>({});
  const [sentCookies, setSentCookies] = useState<Record<string, string>>({});
  const [sentBody, setSentBody] = useState<string | null>(null);
  const [scriptTests, setScriptTests] = useState<ScriptTest[]>([]);
  const [responseViewMode, setResponseViewMode] = useState<ResponseViewMode>('json');
  const [responseFormat, setResponseFormat] = useState<ResponseFormat>('JSON');
  const [aiAnalysis, setAiAnalysis] = useState<string | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [runSubTab, setRunSubTab] = useState<RunSubTab>('params');
  const [responseTab, setResponseTab] = useState<ResponseTab>('report');

  const [bodyMode, setBodyMode] = useState<BodyMode>('raw');
  const [rawType, setRawType] = useState<RawType>('JSON');
  const [formDataParams, setFormDataParams] = useState<FormDataItem[]>([
    createEmptyFormData(),
  ]);
  const [xWwwFormUrlencodedParams, setXWwwFormUrlencodedParams] = useState<KeyValueItem[]>([
    createEmptyKeyValue(),
  ]);
  const [binaryFile, setBinaryFile] = useState<BinaryFile | null>(null);
  const [graphqlQuery, setGraphqlQuery] = useState('');
  const [graphqlVariables, setGraphqlVariables] = useState('');
  const [queryParams, setQueryParams] = useState<KeyValueItem[]>([createEmptyKeyValue()]);
  const [headers, setHeaders] = useState<KeyValueItem[]>([createEmptyKeyValue()]);
  const [authType, setAuthType] = useState<AuthType>('none');
  const [authToken, setAuthToken] = useState('');
  const [authBasic, setAuthBasic] = useState<AuthBasicCredentials>({
    username: '',
    password: '',
  });
  const [authApiKey, setAuthApiKey] = useState<AuthApiKey>({
    key: '',
    value: '',
    addTo: 'header',
  });
  const [activeScriptTab, setActiveScriptTab] = useState<ScriptTab>('pre');
  const [preRequestScript, setPreRequestScript] = useState('');
  const [postResponseScript, setPostResponseScript] = useState('');
  const [requestSettings, setRequestSettings] = useState<RequestSettings>(DEFAULT_REQUEST_SETTINGS);
  const [bodyContent, setBodyContent] = useState('');

  const [bodyBulkText, setBodyBulkText] = useState('');
  const [formDataBulkText, setFormDataBulkText] = useState('');
  const [paramsBulkText, setParamsBulkText] = useState('');
  const [headersBulkText, setHeadersBulkText] = useState('');
  const [isBulkEditBody, setIsBulkEditBody] = useState(false);
  const [isBulkEditFormData, setIsBulkEditFormData] = useState(false);
  const [isBulkEditParams, setIsBulkEditParams] = useState(false);
  const [isBulkEditHeaders, setIsBulkEditHeaders] = useState(false);

  const bodyHighlighterRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const targetContentType = resolveTargetContentType(
      bodyMode,
      rawType,
      bodyContent,
      formDataParams,
      xWwwFormUrlencodedParams,
    );

    setHeaders((previousHeaders) => {
      const nextHeaders = [...previousHeaders];
      const contentTypeIndex = nextHeaders.findIndex(
        (header) => header.key.toLowerCase() === 'content-type',
      );

      if (targetContentType) {
        if (contentTypeIndex >= 0) {
          const contentTypeHeader = nextHeaders[contentTypeIndex];
          if (
            contentTypeHeader.value !== targetContentType &&
            contentTypeHeader.desc === AUTO_GENERATED_DESCRIPTION
          ) {
            nextHeaders[contentTypeIndex] = {
              ...contentTypeHeader,
              value: targetContentType,
            };
            return nextHeaders;
          }
          return previousHeaders;
        }

        const emptyHeaderIndex = nextHeaders.findIndex(
          (header) => !header.key && !header.value,
        );
        const generatedHeader: KeyValueItem = {
          key: 'Content-Type',
          value: targetContentType,
          desc: AUTO_GENERATED_DESCRIPTION,
        };

        if (emptyHeaderIndex !== -1 && nextHeaders.length === 1) {
          nextHeaders[emptyHeaderIndex] = generatedHeader;
        } else {
          nextHeaders.push(generatedHeader);
        }
        return nextHeaders;
      }

      if (
        contentTypeIndex >= 0 &&
        nextHeaders[contentTypeIndex].desc === AUTO_GENERATED_DESCRIPTION
      ) {
        nextHeaders.splice(contentTypeIndex, 1);
        if (nextHeaders.length === 0) nextHeaders.push(createEmptyKeyValue());
        return nextHeaders;
      }

      return previousHeaders;
    });
  }, [bodyContent, bodyMode, formDataParams, rawType, xWwwFormUrlencodedParams]);

  const handleBodyScroll = useCallback((event: UIEvent<HTMLTextAreaElement>) => {
    if (!bodyHighlighterRef.current) return;
    bodyHighlighterRef.current.scrollTop = event.currentTarget.scrollTop;
    bodyHighlighterRef.current.scrollLeft = event.currentTarget.scrollLeft;
  }, []);

  const handleSendRequest = useCallback(async () => {
    setLoading(true);
    setScriptTests([]);
    setAiAnalysis(null);

    try {
      const activeEnv = resolveActiveEnv(savedEnvs, apiPath);
      const runtime = createRuntimeContext(activeEnv, getEnvBaseUrlValue);
      const { envInterface, substitute, getRuntimeVariables } = runtime;

      if (preRequestScript.trim()) {
        try {
          executePreRequestScript(preRequestScript, envInterface);
        } catch (error) {
          onLog(`Pre-request script failed: ${await translateError(error)}`);
        }
      }

      const {
        fullUrl,
        reqHeaders,
        reqParams,
        finalBody,
        isBase64,
      } = buildDebugRequestPayload({
        apiPath,
        headers,
        queryParams,
        authType,
        authToken,
        authBasic,
        authApiKey,
        bodyMode,
        bodyContent,
        xWwwFormUrlencodedParams,
        formDataParams,
        binaryFile,
        graphqlQuery,
        graphqlVariables,
        substitute,
      });

      const requestCookies = requestSettings.disableCookieJar ? {} : cookieJar;
      const response = await api.post<DebugRequestResponse>('/api/standard/request', {
        method,
        url: fullUrl,
        headers: reqHeaders,
        params: reqParams,
        cookies: requestCookies,
        body: finalBody,
        is_base64_body: isBase64,
        timeout_ms: requestSettings.timeout,
        verify_ssl: requestSettings.verifySSL,
        follow_redirects: requestSettings.followRedirects,
        max_redirects: requestSettings.maxRedirects,
        http_version: requestSettings.httpVersion,
      });

      const receivedCookies = extractCookieValues(response.cookies);
      setResponseStatus(response.status_code);
      setResponseTime(response.elapsed_ms);
      setResponseBody(response.body);
      setResponseHeaders(response.headers);
      setSentHeaders(reqHeaders);
      setSentCookies(requestCookies);
      setSentBody(finalBody ?? null);
      setResponseCookies(receivedCookies);
      setResponseDetailedCookies(response.cookies);
      if (!requestSettings.disableCookieJar) {
        setCookieJar((currentCookies) => ({ ...currentCookies, ...receivedCookies }));
      }

      if (postResponseScript.trim()) {
        try {
          const tests = executePostResponseScript({
            script: postResponseScript,
            envInterface,
            response: {
              body: response.body,
              headers: response.headers,
              status: response.status_code,
              time: response.elapsed_ms,
            },
          });
          setScriptTests(tests);
          if (tests.length > 0) setResponseTab('test_results');
        } catch (error) {
          onLog(`Post-response script failed: ${await translateError(error)}`);
        }
      }

      if (activeEnv) {
        setSavedEnvs((currentEnvs) =>
          currentEnvs.map((env) =>
            env.id === activeEnv.id
              ? { ...env, variables: getRuntimeVariables() }
              : env,
          ),
        );
      }

      onLog(`请求成功：${response.status_code}（${response.elapsed_ms}ms）`);
    } catch (error) {
      const message = await translateError(error);
      onLog(`Request failed: ${message}`);
      setResponseStatus(0);
      setResponseBody(message);
    } finally {
      setLoading(false);
    }
  }, [
    apiPath,
    authApiKey,
    authBasic,
    authToken,
    authType,
    binaryFile,
    bodyContent,
    bodyMode,
    cookieJar,
    formDataParams,
    getEnvBaseUrlValue,
    graphqlQuery,
    graphqlVariables,
    headers,
    method,
    onLog,
    postResponseScript,
    preRequestScript,
    queryParams,
    requestSettings.followRedirects,
    requestSettings.httpVersion,
    requestSettings.maxRedirects,
    requestSettings.timeout,
    requestSettings.verifySSL,
    requestSettings.disableCookieJar,
    savedEnvs,
    setCookieJar,
    setSavedEnvs,
    xWwwFormUrlencodedParams,
  ]);

  const handleAnalyzeResponse = useCallback(async () => {
    if (!responseBody || responseStatus === null) return;

    setIsAnalyzing(true);
    try {
      const result = await api.post<AnalyzeResponseResult>('/api/standard/analyze_response', {
        method,
        url: apiPath,
        headers: sentHeaders,
        body: sentBody,
        response_status: responseStatus,
        response_headers: responseHeaders,
        response_body: responseBody,
        error: null,
      });
      setAiAnalysis(result.analysis);
    } catch (error) {
      const message = await translateError(error);
      onLog(`AI analysis failed: ${message}`);
      setAiAnalysis(`Analysis failed: ${message}`);
    } finally {
      setIsAnalyzing(false);
    }
  }, [
    apiPath,
    method,
    onLog,
    responseBody,
    responseHeaders,
    responseStatus,
    sentBody,
    sentHeaders,
  ]);

  return {
    method,
    setMethod,
    loading,
    responseStatus,
    setResponseStatus,
    responseTime,
    setResponseTime,
    responseBody,
    setResponseBody,
    responseHeaders,
    setResponseHeaders,
    responseCookies,
    setResponseCookies,
    responseDetailedCookies,
    setResponseDetailedCookies,
    sentHeaders,
    sentCookies,
    sentBody,
    scriptTests,
    setScriptTests,
    responseViewMode,
    setResponseViewMode,
    responseFormat,
    setResponseFormat,
    aiAnalysis,
    isAnalyzing,
    runSubTab,
    setRunSubTab,
    responseTab,
    setResponseTab,
    bodyMode,
    setBodyMode,
    rawType,
    setRawType,
    formDataParams,
    setFormDataParams,
    xWwwFormUrlencodedParams,
    setXWwwFormUrlencodedParams,
    binaryFile,
    setBinaryFile,
    graphqlQuery,
    setGraphqlQuery,
    graphqlVariables,
    setGraphqlVariables,
    queryParams,
    setQueryParams,
    headers,
    setHeaders,
    authType,
    setAuthType,
    authToken,
    setAuthToken,
    authBasic,
    setAuthBasic,
    authApiKey,
    setAuthApiKey,
    activeScriptTab,
    setActiveScriptTab,
    preRequestScript,
    setPreRequestScript,
    postResponseScript,
    setPostResponseScript,
    requestSettings,
    setRequestSettings,
    bodyContent,
    setBodyContent,
    bodyBulkText,
    setBodyBulkText,
    formDataBulkText,
    setFormDataBulkText,
    paramsBulkText,
    setParamsBulkText,
    headersBulkText,
    setHeadersBulkText,
    isBulkEditBody,
    setIsBulkEditBody,
    isBulkEditFormData,
    setIsBulkEditFormData,
    isBulkEditParams,
    setIsBulkEditParams,
    isBulkEditHeaders,
    setIsBulkEditHeaders,
    bodyHighlighterRef,
    handleBodyScroll,
    handleSendRequest,
    handleAnalyzeResponse,
  };
}
