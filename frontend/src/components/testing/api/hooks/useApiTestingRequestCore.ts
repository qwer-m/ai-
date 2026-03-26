import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction, type UIEvent } from 'react';
import { api } from '../../../../utils/api';
import { translateError } from '../../../standard-api-testing/utils/requestUi';
import {
  buildDebugRequestPayload,
  createRuntimeContext,
  executePostResponseScript,
  executePreRequestScript,
  resolveActiveEnv,
  type ScriptTest,
} from '../../../standard-api-testing/utils/requestExecution';
import {
  createEmptyFormData,
  createEmptyKeyValue,
  resolveTargetContentType,
} from './useApiTestingRequestUtils';
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
  TestResult,
} from '../../../standard-api-testing/utils/types';

type TestTypes = {
  functional: boolean;
  boundary: boolean;
  security: boolean;
};

type UseApiTestingRequestParams = {
  projectId: number | null;
  onLog: (msg: string) => void;
  apiPath: string;
  savedEnvs: EnvConfig[];
  setSavedEnvs: Dispatch<SetStateAction<EnvConfig[]>>;
  getEnvBaseUrlValue: (tag: string) => string;
  cookieJar: Record<string, string>;
  setCookieJar: Dispatch<SetStateAction<Record<string, string>>>;
};

export function useApiTestingRequest({
  projectId,
  onLog,
  apiPath,
  savedEnvs,
  setSavedEnvs,
  getEnvBaseUrlValue,
  cookieJar,
  setCookieJar,
}: UseApiTestingRequestParams) {
  const [mode, setMode] = useState<'natural' | 'structured'>('natural');
  const [requirement, setRequirement] = useState('');
  const [method, setMethod] = useState('POST');
  const [testTypes, setTestTypes] = useState<TestTypes>({
    functional: true,
    boundary: false,
    security: false,
  });
  const [loading, setLoading] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [responseStatus, setResponseStatus] = useState<number | null>(null);
  const [responseTime, setResponseTime] = useState<number | null>(null);
  const [responseBody, setResponseBody] = useState<string | null>(null);
  const [responseHeaders, setResponseHeaders] = useState<Record<string, string>>({});
  const [responseCookies, setResponseCookies] = useState<Record<string, string>>({});
  const [responseDetailedCookies, setResponseDetailedCookies] = useState<Record<string, string>>({});
  const [sentHeaders, setSentHeaders] = useState<Record<string, string>>({});
  const [sentCookies, setSentCookies] = useState<Record<string, string>>({});
  const [sentBody, setSentBody] = useState<string | null>(null);
  const [scriptTests, setScriptTests] = useState<ScriptTest[]>([]);
  const [responseViewMode, setResponseViewMode] = useState<'json' | 'html' | 'headers'>('json');
  const [responseFormat, setResponseFormat] = useState<'JSON' | 'XML' | 'HTML' | 'JavaScript' | 'Raw' | 'Hex' | 'Base64'>('JSON');
  const [aiAnalysis, setAiAnalysis] = useState<string | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [runSubTab, setRunSubTab] = useState('params');
  const [responseTab, setResponseTab] = useState<ResponseTab>('report');
  const [bodyMode, setBodyMode] = useState<BodyMode>('raw');
  const [rawType, setRawType] = useState<RawType>('JSON');
  const [formDataParams, setFormDataParams] = useState<FormDataItem[]>([createEmptyFormData()]);
  const [xWwwFormUrlencodedParams, setXWwwFormUrlencodedParams] = useState<KeyValueItem[]>([createEmptyKeyValue()]);
  const [binaryFile, setBinaryFile] = useState<{ name: string; data: string } | null>(null);
  const [graphqlQuery, setGraphqlQuery] = useState('');
  const [graphqlVariables, setGraphqlVariables] = useState('');
  const [queryParams, setQueryParams] = useState<KeyValueItem[]>([createEmptyKeyValue()]);
  const [headers, setHeaders] = useState<KeyValueItem[]>([createEmptyKeyValue()]);
  const [authType, setAuthType] = useState<AuthType>('none');
  const [authToken, setAuthToken] = useState('');
  const [authBasic, setAuthBasic] = useState<AuthBasicCredentials>({ username: '', password: '' });
  const [authApiKey, setAuthApiKey] = useState<AuthApiKey>({ key: '', value: '', addTo: 'header' });
  const [activeScriptTab, setActiveScriptTab] = useState<'pre' | 'post'>('pre');
  const [preRequestScript, setPreRequestScript] = useState('');
  const [postResponseScript, setPostResponseScript] = useState('');
  const [requestSettings, setRequestSettings] = useState<RequestSettings>({
    timeout: 0,
    followRedirects: true,
    verifySSL: false,
    httpVersion: 'HTTP/1.x',
    followOriginalHttpMethod: false,
    followAuthorizationHeader: false,
    removeRefererHeader: false,
    strictHttpParser: false,
    encodeUrl: true,
    disableCookieJar: false,
    useServerCipherSuite: false,
    maxRedirects: 10,
    disabledSSLProtocols: '',
    cipherSuites: '',
  });
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
    const targetType = resolveTargetContentType(bodyMode, rawType, bodyContent, formDataParams, xWwwFormUrlencodedParams);

    setHeaders((prev) => {
      const next = [...prev];
      const index = next.findIndex((item) => item.key.toLowerCase() === 'content-type');

      if (targetType) {
        if (index >= 0) {
          if (next[index].value !== targetType && next[index].desc === 'Auto-generated') {
            next[index] = { ...next[index], value: targetType };
            return next;
          }
          return prev;
        }

        const emptyIndex = next.findIndex((item) => !item.key && !item.value);
        if (emptyIndex !== -1 && next.length === 1) {
          next[emptyIndex] = { key: 'Content-Type', value: targetType, desc: 'Auto-generated' };
        } else {
          next.push({ key: 'Content-Type', value: targetType, desc: 'Auto-generated' });
        }
        return next;
      }

      if (index >= 0 && next[index].desc === 'Auto-generated') {
        next.splice(index, 1);
        if (next.length === 0) {
          next.push(createEmptyKeyValue());
        }
        return next;
      }

      return prev;
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
      const runtimeContext = createRuntimeContext(activeEnv, getEnvBaseUrlValue);
      const { envInterface, substitute, getRuntimeVariables } = runtimeContext;

      if (preRequestScript && preRequestScript.trim()) {
        try {
          executePreRequestScript(preRequestScript, envInterface);
        } catch (error) {
          const msg = await translateError(error);
          onLog(`Pre-request script failed: ${msg}`);
        }
      }

      const { fullUrl, reqHeaders, reqParams, finalBody, isBase64 } = buildDebugRequestPayload({
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

      const res = await api.post<any>('/api/debug/request', {
        method,
        url: fullUrl,
        headers: reqHeaders,
        params: reqParams,
        cookies: cookieJar,
        body: finalBody,
        is_base64_body: isBase64,
        timeout: requestSettings.timeout / 1000,
        verify_ssl: requestSettings.verifySSL,
        follow_redirects: requestSettings.followRedirects,
        max_redirects: requestSettings.maxRedirects,
        http_version: requestSettings.httpVersion,
      });

      setResponseStatus(res.status);
      setResponseTime(res.time);
      setResponseBody(res.body);
      setResponseHeaders(res.headers);
      setSentHeaders(reqHeaders);
      setSentCookies(cookieJar);
      setSentBody(finalBody || null);
      setResponseCookies(res.cookies || {});
      setResponseDetailedCookies(res.detailed_cookies || {});

      if (res.cookies) {
        setCookieJar((prev) => ({ ...prev, ...res.cookies }));
      }

      if (postResponseScript && postResponseScript.trim()) {
        try {
          const tests = executePostResponseScript({
            script: postResponseScript,
            envInterface,
            response: {
              body: res.body,
              headers: res.headers || {},
              status: res.status,
              time: res.time,
            },
          });
          setScriptTests(tests);
          if (tests.length > 0) {
            setResponseTab('test_results');
          }
        } catch (error) {
          const msg = await translateError(error);
          onLog(`Post-response script failed: ${msg}`);
        }
      }

      if (activeEnv) {
        setSavedEnvs((prev) => prev.map((item) => (item.id === activeEnv.id ? { ...item, variables: getRuntimeVariables() } : item)));
      }

      onLog(`Request succeeded: ${res.status} (${res.time}s)`);
    } catch (error) {
      const msg = await translateError(error);
      onLog(`Request failed: ${msg}`);
      setResponseStatus(0);
      setResponseBody(msg);
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
    getEnvBaseUrlValue,
    graphqlQuery,
    graphqlVariables,
    headers,
    method,
    onLog,
    postResponseScript,
    preRequestScript,
    queryParams,
    requestSettings.httpVersion,
    requestSettings.maxRedirects,
    requestSettings.timeout,
    requestSettings.verifySSL,
    requestSettings.followRedirects,
    savedEnvs,
    setCookieJar,
    setResponseTab,
    setSavedEnvs,
    translateError,
    xWwwFormUrlencodedParams,
    formDataParams,
  ]);

  const handleRun = useCallback(async () => {
    if (!projectId) return alert('Please select a project first');
    if (!apiPath) return alert('Please enter a request URL');
    if (!requirement) return alert('Please enter an AI testing requirement or API definition');

    setLoading(true);
    setTestResult(null);
    setResponseTab('report');
    onLog(`Generating API test script (${mode === 'natural' ? 'natural language' : 'structured'} mode)...`);

    try {
      const activeTypes = (Object.keys(testTypes) as Array<keyof typeof testTypes>)
        .filter((key) => testTypes[key])
        .map((key) => key.charAt(0).toUpperCase() + key.slice(1));

      let richRequirement = `Method: ${method}\nURL: ${apiPath}\nContext/Requirement: ${requirement}`.trim();

      const validParams = queryParams.filter((item) => item.key);
      if (validParams.length > 0) {
        richRequirement += `\n\nQuery Params:\n${validParams.map((item) => `${item.key}: ${item.value} (${item.desc})`).join('\n')}`;
      }

      const validHeaders = headers.filter((item) => item.key);
      if (validHeaders.length > 0) {
        richRequirement += `\n\nHeaders:\n${validHeaders.map((item) => `${item.key}: ${item.value} (${item.desc})`).join('\n')}`;
      }

      if (bodyMode !== 'none' && bodyContent.trim()) {
        richRequirement += `\n\nRequest Body (${bodyMode}):\n${bodyContent}`;
      }

      const data = await api.post<TestResult>('/api/api-testing', {
        requirement: richRequirement,
        project_id: projectId,
        base_url: '',
        test_types: activeTypes,
        mode,
      });

      setTestResult(data);
      onLog('API test execution complete');

      if (data.structured_report && data.structured_report.failed > 0) {
        onLog(`Test found ${data.structured_report.failed} issue(s)`);
      }
    } catch (error) {
      const msg = await translateError(error);
      onLog(`API test failed: ${msg}`);
    } finally {
      setLoading(false);
    }
  }, [apiPath, bodyContent, bodyMode, headers, method, mode, onLog, projectId, queryParams, requirement, testTypes, translateError]);

  const handleAnalyzeResponse = useCallback(async () => {
    if (!responseBody) return;

    setIsAnalyzing(true);
    try {
      const res = await api.post<any>('/standard/analyze_response', {
        method,
        url: apiPath,
        headers: sentHeaders,
        body: sentBody,
        response_status: responseStatus,
        response_headers: responseHeaders,
        response_body: typeof responseBody === 'string' ? responseBody : JSON.stringify(responseBody),
        error: null,
      });
      setAiAnalysis(res.analysis);
    } catch (error) {
      const msg = await translateError(error);
      onLog(`AI analysis failed: ${msg}`);
      setAiAnalysis(`Analysis failed: ${msg}`);
    } finally {
      setIsAnalyzing(false);
    }
  }, [apiPath, method, onLog, responseBody, responseHeaders, responseStatus, sentBody, sentHeaders, translateError]);

  return {
    mode,
    setMode,
    requirement,
    setRequirement,
    method,
    setMethod,
    testTypes,
    setTestTypes,
    loading,
    testResult,
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
    setTestResult,
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
    handleRun,
    handleAnalyzeResponse,
  };
}
