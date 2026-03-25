import type { Dispatch, MutableRefObject, ReactNode, SetStateAction } from 'react';
import { Button, Form, Spinner } from 'react-bootstrap';
import { FaBars, FaCog, FaRobot, FaSave } from 'react-icons/fa';
import { ResponsePanel } from './ResponsePanel';
import { StandardApiTestingRequestTabBar } from './StandardApiTestingRequestTabBar';
import { StandardApiTestingRequestBodyTab } from './StandardApiTestingRequestBodyTab';
import { StandardApiTestingRequestSettingsTab } from './StandardApiTestingRequestSettingsTab';
import { KvEditor, parseBulkText, stringifyBulkItems } from './RequestEditors';
import { getMethodColor } from './utils/requestUi';
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
} from './utils/types';

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
  mode: 'natural' | 'structured';
  setMode: Dispatch<SetStateAction<'natural' | 'structured'>>;
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
  activeScriptTab: 'pre' | 'post';
  setActiveScriptTab: Dispatch<SetStateAction<'pre' | 'post'>>;
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
  responseFormat: 'JSON' | 'XML' | 'HTML' | 'JavaScript' | 'Raw' | 'Hex' | 'Base64';
  setResponseFormat: Dispatch<SetStateAction<'JSON' | 'XML' | 'HTML' | 'JavaScript' | 'Raw' | 'Hex' | 'Base64'>>;
  responseViewMode: 'json' | 'html' | 'headers';
  setResponseViewMode: Dispatch<SetStateAction<'json' | 'html' | 'headers'>>;
  aiAnalysis: string | null;
  testResult: TestResult | null;
  handleAnalyzeResponse: () => void;
  isAnalyzing: boolean;
  scriptTests: { name: string; passed: boolean; error?: string }[];
  renderDashboard: (report: NonNullable<TestResult['structured_report']>) => ReactNode;
  onOpenCookieManager: () => void;
};

function RequestAuthTab({
  authType,
  setAuthType,
  authToken,
  setAuthToken,
  authBasic,
  setAuthBasic,
  authApiKey,
  setAuthApiKey,
}: Pick<StandardApiTestingRequestWorkspaceProps, 'authType' | 'setAuthType' | 'authToken' | 'setAuthToken' | 'authBasic' | 'setAuthBasic' | 'authApiKey' | 'setAuthApiKey'>) {
  return (
    <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white" style={{ visibility: 'visible', zIndex: 10, overflowX: 'hidden', overflowY: 'scroll' }}>
      <div className="d-flex h-100">
        <div className="border-end bg-light p-2" style={{ width: '200px', minWidth: '200px' }}>
          <div className="small text-muted mb-2 ps-2">类型</div>
          <div className="d-flex flex-column gap-1">
            {(['none', 'bearer', 'basic', 'apikey'] as const).map((type) => (
              <div
                key={type}
                className={`px-3 py-2 small rounded cursor-pointer ${authType === type ? 'bg-primary text-white' : 'text-secondary hover-bg-gray'}`}
                onClick={() => setAuthType(type)}
                style={{ cursor: 'pointer' }}
              >
                {type === 'none' ? '无认证(No Auth)' : type === 'bearer' ? 'Bearer 令牌' : type === 'basic' ? '基础认证 (Basic Auth)' : 'API 密钥 (API Key)'}
              </div>
            ))}
          </div>
        </div>
        <div className="flex-grow-1 p-3">
          {authType === 'none' && <div className="text-muted small">此请求不使用任何认证。</div>}
          {authType === 'bearer' && (
            <div className="d-flex flex-column gap-2" style={{ maxWidth: '500px' }}>
              <Form.Label className="small mb-0">Token</Form.Label>
              <Form.Control size="sm" placeholder="输入 Token" value={authToken} onChange={(e) => setAuthToken(e.target.value)} />
            </div>
          )}
          {authType === 'basic' && (
            <div className="d-flex flex-column gap-2" style={{ maxWidth: '500px' }}>
              <div className="d-flex gap-3">
                <div className="flex-grow-1">
                  <Form.Label className="small mb-0">用户名</Form.Label>
                  <Form.Control size="sm" placeholder="用户名" value={authBasic.username} onChange={(e) => setAuthBasic({ ...authBasic, username: e.target.value })} />
                </div>
                <div className="flex-grow-1">
                  <Form.Label className="small mb-0">密码</Form.Label>
                  <Form.Control size="sm" type="password" placeholder="密码" value={authBasic.password} onChange={(e) => setAuthBasic({ ...authBasic, password: e.target.value })} />
                </div>
              </div>
            </div>
          )}
          {authType === 'apikey' && (
            <div className="d-flex flex-column gap-3" style={{ maxWidth: '500px' }}>
              <div className="d-flex gap-3">
                <div className="flex-grow-1">
                  <Form.Label className="small mb-0">Key</Form.Label>
                  <Form.Control size="sm" placeholder="Key" value={authApiKey.key} onChange={(e) => setAuthApiKey({ ...authApiKey, key: e.target.value })} />
                </div>
                <div className="flex-grow-1">
                  <Form.Label className="small mb-0">Value</Form.Label>
                  <Form.Control size="sm" placeholder="Value" value={authApiKey.value} onChange={(e) => setAuthApiKey({ ...authApiKey, value: e.target.value })} />
                </div>
              </div>
              <div>
                <Form.Label className="small mb-0">添加到</Form.Label>
                <Form.Select size="sm" value={authApiKey.addTo} onChange={(e) => setAuthApiKey({ ...authApiKey, addTo: e.target.value as 'header' | 'query' })}>
                  <option value="header">Header</option>
                  <option value="query">Query Params</option>
                </Form.Select>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function RequestScriptsTab({
  activeScriptTab,
  setActiveScriptTab,
  preRequestScript,
  setPreRequestScript,
  postResponseScript,
  setPostResponseScript,
}: Pick<StandardApiTestingRequestWorkspaceProps, 'activeScriptTab' | 'setActiveScriptTab' | 'preRequestScript' | 'setPreRequestScript' | 'postResponseScript' | 'setPostResponseScript'>) {
  return (
    <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white" style={{ visibility: 'visible', zIndex: 10, overflowX: 'hidden', overflowY: 'scroll' }}>
      <div className="d-flex h-100">
        <div className="border-end bg-light p-2" style={{ width: '200px', minWidth: '200px' }}>
          <div className="d-flex flex-column gap-1">
            <div className={`px-3 py-2 small rounded cursor-pointer ${activeScriptTab === 'pre' ? 'bg-primary text-white' : 'text-secondary hover-bg-gray'}`} onClick={() => setActiveScriptTab('pre')} style={{ cursor: 'pointer' }}>
              Pre-request (前置脚本)
            </div>
            <div className={`px-3 py-2 small rounded cursor-pointer ${activeScriptTab === 'post' ? 'bg-primary text-white' : 'text-secondary hover-bg-gray'}`} onClick={() => setActiveScriptTab('post')} style={{ cursor: 'pointer' }}>
              Post-response (后置脚本)
            </div>
          </div>
        </div>
        <div className="flex-grow-1 p-0 d-flex flex-column">
          {activeScriptTab === 'pre' ? (
            <Form.Control as="textarea" className="flex-grow-1 border-0 p-3 font-monospace small bg-transparent" style={{ resize: 'none', outline: 'none' }} placeholder="// 在此编写前置脚本 (Pre-request scripts)..." value={preRequestScript} onChange={(e) => setPreRequestScript(e.target.value)} spellCheck={false} />
          ) : (
            <Form.Control as="textarea" className="flex-grow-1 border-0 p-3 font-monospace small bg-transparent" style={{ resize: 'none', outline: 'none' }} placeholder="// 在此编写后置脚本 (Post-response scripts)..." value={postResponseScript} onChange={(e) => setPostResponseScript(e.target.value)} spellCheck={false} />
          )}
        </div>
      </div>
    </div>
  );
}

export function StandardApiTestingRequestWorkspace({
  showSidebar,
  setShowSidebar,
  isDragging,
  requestHeight,
  handleRequestBarMouseDown,
  mainContentRef,
  method,
  setMethod,
  apiPath,
  setApiPath,
  inputRef,
  highlighterRef,
  bodyHighlighterRef,
  activeEnvTag,
  showPopup,
  setShowPopup,
  handleInputMouseMove,
  handleInputMouseLeave,
  handlePopupMouseEnter,
  handlePopupMouseLeave,
  getEnvBaseUrlValue,
  setEnvBaseUrlValue,
  handleApiPathBlur,
  loading,
  handleSendRequest,
  handleSaveInterfaceClick,
  handleSaveEnv,
  mode,
  setMode,
  requirement,
  setRequirement,
  handleRun,
  runSubTab,
  setRunSubTab,
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
  bodyMode,
  setBodyMode,
  rawType,
  setRawType,
  bodyContent,
  setBodyContent,
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
  isBulkEditFormData,
  setIsBulkEditFormData,
  formDataBulkText,
  setFormDataBulkText,
  isBulkEditBody,
  setIsBulkEditBody,
  bodyBulkText,
  setBodyBulkText,
  isBulkEditParams,
  setIsBulkEditParams,
  paramsBulkText,
  setParamsBulkText,
  isBulkEditHeaders,
  setIsBulkEditHeaders,
  headersBulkText,
  setHeadersBulkText,
  handleBodyScroll,
  responseTab,
  setResponseTab,
  responseDetailedCookies,
  responseCookies,
  responseHeaders,
  sentHeaders,
  sentCookies,
  responseStatus,
  responseTime,
  responseBody,
  responseFormat,
  setResponseFormat,
  responseViewMode,
  setResponseViewMode,
  aiAnalysis,
  testResult,
  handleAnalyzeResponse,
  isAnalyzing,
  scriptTests,
  renderDashboard,
  onOpenCookieManager,
}: StandardApiTestingRequestWorkspaceProps) {
  const renderRequestTabContent = () => {
    if (runSubTab === 'params') {
      return (
        <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white" style={{ visibility: 'visible', zIndex: 10, overflowX: 'hidden', overflowY: 'scroll' }}>
          <KvEditor
            items={queryParams}
            onChange={setQueryParams}
            isBulk={isBulkEditParams}
            onToggleBulk={() => {
              if (!isBulkEditParams) {
                setParamsBulkText(stringifyBulkItems(queryParams));
              } else {
                setQueryParams(parseBulkText(paramsBulkText));
              }
              setIsBulkEditParams(!isBulkEditParams);
            }}
            bulkText={paramsBulkText}
            onBulkChange={setParamsBulkText}
          />
        </div>
      );
    }

    if (runSubTab === 'headers') {
      return (
        <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white" style={{ visibility: 'visible', zIndex: 10, overflowX: 'hidden', overflowY: 'scroll' }}>
          <KvEditor
            items={headers}
            onChange={setHeaders}
            isBulk={isBulkEditHeaders}
            onToggleBulk={() => {
              if (!isBulkEditHeaders) {
                setHeadersBulkText(stringifyBulkItems(headers));
              } else {
                setHeaders(parseBulkText(headersBulkText));
              }
              setIsBulkEditHeaders(!isBulkEditHeaders);
            }}
            bulkText={headersBulkText}
            onBulkChange={setHeadersBulkText}
          />
        </div>
      );
    }

    if (runSubTab === 'authorization') {
      return <RequestAuthTab authType={authType} setAuthType={setAuthType} authToken={authToken} setAuthToken={setAuthToken} authBasic={authBasic} setAuthBasic={setAuthBasic} authApiKey={authApiKey} setAuthApiKey={setAuthApiKey} />;
    }

    if (runSubTab === 'scripts') {
      return <RequestScriptsTab activeScriptTab={activeScriptTab} setActiveScriptTab={setActiveScriptTab} preRequestScript={preRequestScript} setPreRequestScript={setPreRequestScript} postResponseScript={postResponseScript} setPostResponseScript={setPostResponseScript} />;
    }

    if (runSubTab === 'settings') {
      return <StandardApiTestingRequestSettingsTab requestSettings={requestSettings} setRequestSettings={setRequestSettings} />;
    }

    if (runSubTab === 'body') {
      return (
        <StandardApiTestingRequestBodyTab
          bodyMode={bodyMode}
          setBodyMode={setBodyMode}
          rawType={rawType}
          setRawType={setRawType}
          bodyContent={bodyContent}
          setBodyContent={setBodyContent}
          formDataParams={formDataParams}
          setFormDataParams={setFormDataParams}
          xWwwFormUrlencodedParams={xWwwFormUrlencodedParams}
          setXWwwFormUrlencodedParams={setXWwwFormUrlencodedParams}
          binaryFile={binaryFile}
          setBinaryFile={setBinaryFile}
          graphqlQuery={graphqlQuery}
          setGraphqlQuery={setGraphqlQuery}
          graphqlVariables={graphqlVariables}
          setGraphqlVariables={setGraphqlVariables}
          isBulkEditFormData={isBulkEditFormData}
          setIsBulkEditFormData={setIsBulkEditFormData}
          formDataBulkText={formDataBulkText}
          setFormDataBulkText={setFormDataBulkText}
          isBulkEditBody={isBulkEditBody}
          setIsBulkEditBody={setIsBulkEditBody}
          bodyBulkText={bodyBulkText}
          setBodyBulkText={setBodyBulkText}
          bodyHighlighterRef={bodyHighlighterRef}
          handleBodyScroll={handleBodyScroll}
        />
      );
    }

    return (
      <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white" style={{ visibility: 'visible', zIndex: 10, overflowX: 'hidden', overflowY: 'scroll' }}>
        <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white d-flex flex-column p-3" style={{ overflowX: 'hidden', overflowY: 'scroll' }}>
          <div className="d-flex justify-content-between mb-2">
            <Form.Label className="small text-muted mb-0">AI 测试生成 (自然语言或 JSON 定义)</Form.Label>
            <div className="d-flex gap-2">
              <Form.Check type="radio" label="自然语言" checked={mode === 'natural'} onChange={() => setMode('natural')} inline className="small" />
              <Form.Check type="radio" label="结构化" checked={mode === 'structured'} onChange={() => setMode('structured')} inline className="small" />
            </div>
          </div>
          <Form.Control
            as="textarea"
            className="flex-grow-1 font-monospace small bg-light"
            style={{ border: '1px solid #dee2e6' }}
            value={requirement}
            onChange={(e) => setRequirement(e.target.value)}
            placeholder="描述您的测试场景..."
          />
          <div className="mt-2 d-flex justify-content-end">
            <Button variant="outline-primary" size="sm" onClick={handleRun} disabled={loading}>
              <FaRobot className="me-1" /> 生成并运行测试
            </Button>
          </div>
        </div>
      </div>
    );
  };

  return (
    <>
      <div className="flex-grow-1 d-flex flex-column h-100 overflow-hidden bg-white api-main-content" ref={mainContentRef}>
        <div className="d-flex align-items-center p-2 border-bottom bg-light gap-2 flex-shrink-0" style={{ height: '50px' }}>
          <Button variant="link" className="p-0 text-secondary me-2" onClick={() => setShowSidebar(!showSidebar)} title={showSidebar ? '收起列表' : '展开列表'}>
            <FaBars size={16} />
          </Button>

          <div className="d-flex flex-grow-1 bg-white border rounded">
            <Form.Select
              className="border-0 shadow-none"
              style={{ width: '110px', backgroundColor: '#f9f9f9', borderRight: '1px solid #dee2e6', fontWeight: 600, color: getMethodColor(method) }}
              value={method}
              onChange={(e) => setMethod(e.target.value)}
            >
              <option value="GET">GET</option>
              <option value="POST">POST</option>
              <option value="PUT">PUT</option>
              <option value="DELETE">DELETE</option>
            </Form.Select>
            <div className="d-flex flex-grow-1 align-items-center px-2 border-end bg-white position-relative">
              <div className="position-relative w-100 h-100 d-flex align-items-center">
                <div
                  ref={highlighterRef}
                  className="position-absolute top-0 start-0 w-100 h-100 d-flex align-items-center"
                  style={{
                    whiteSpace: 'pre',
                    overflow: 'hidden',
                    pointerEvents: 'none',
                    font: 'inherit',
                    color: 'black',
                    paddingLeft: '0px',
                    paddingRight: '0px',
                  }}
                >
                  {apiPath.split(/(\{\{.*?\}\})/).map((part, index) => {
                    if (part.startsWith('{{') && part.endsWith('}}')) {
                      const isEmpty = part.replace(/[\{\}\s]/g, '').length === 0;
                      const envValue = getEnvBaseUrlValue(part);
                      const isMissingBaseUrl = !isEmpty && (!envValue || !envValue.trim() || envValue === 'null' || envValue === 'undefined');
                      const chipStyle: React.CSSProperties = isEmpty
                        ? { background: 'transparent', border: '1px solid #ffecb5', borderRadius: '4px', color: '#856404', padding: '0 2px', margin: '0 1px', fontSize: '1em', lineHeight: 1.6 }
                        : isMissingBaseUrl
                          ? { background: 'rgba(220, 53, 69, 0.1)', border: '1px solid rgba(220, 53, 69, 0.3)', borderRadius: '4px', color: '#dc3545', fontWeight: 600, padding: '0 2px', margin: '0 1px', fontSize: '1em', lineHeight: 1.6 }
                          : { background: 'transparent', border: '1px solid #dee2e6', borderRadius: '4px', color: '#0d6efd', padding: '0 2px', margin: '0 1px', fontSize: '1em', lineHeight: 1.6 };
                      return (
                        <span key={index} style={chipStyle}>
                          {part}
                        </span>
                      );
                    }
                    return <span key={index} style={{ color: '#212529' }}>{part}</span>;
                  })}
                </div>

                <Form.Control
                  ref={inputRef}
                  className="border-0 shadow-none p-0 bg-transparent custom-api-input"
                  placeholder="Enter request URL"
                  value={apiPath}
                  onChange={(e) => setApiPath(e.target.value)}
                  onBlur={handleApiPathBlur}
                  onMouseMove={handleInputMouseMove}
                  onMouseLeave={handleInputMouseLeave}
                  onScroll={(e) => {
                    if (highlighterRef.current) {
                      highlighterRef.current.scrollLeft = e.currentTarget.scrollLeft;
                    }
                  }}
                  style={{
                    color: apiPath ? 'transparent' : undefined,
                    caretColor: 'black',
                    position: 'relative',
                    zIndex: 1,
                    fontFamily: 'inherit',
                  }}
                />
              </div>

              {activeEnvTag && showPopup && (
                <div className="position-absolute start-0 end-0 bg-white border rounded shadow-sm px-3 py-2" style={{ top: '100%', zIndex: 1050, marginTop: '4px' }} onMouseEnter={handlePopupMouseEnter} onMouseLeave={handlePopupMouseLeave}>
                  <div className="d-flex align-items-center bg-white rounded px-2 py-1 border">
                    <span className="small me-2 font-monospace text-primary" style={{ fontWeight: 500 }}>
                      {activeEnvTag}:
                    </span>
                    <Form.Control
                      size="sm"
                      className="border-0 bg-transparent shadow-none p-0 text-muted"
                      placeholder="Enter Base URL value..."
                      value={getEnvBaseUrlValue(activeEnvTag)}
                      onChange={(e) => setEnvBaseUrlValue(activeEnvTag, e.target.value)}
                      onFocus={() => {
                        setShowPopup(true);
                      }}
                      onBlur={() => {
                        setTimeout(() => setShowPopup(false), 300);
                      }}
                    />
                  </div>
                </div>
              )}
            </div>
          </div>

          <Button variant="primary" onClick={handleSendRequest} disabled={loading} className="px-4 text-white rounded-0" style={{ fontWeight: 500, backgroundColor: '#0d6efd', borderColor: '#0d6efd' }}>
            {loading ? <Spinner size="sm" animation="border" /> : '发送'}
          </Button>
          <Button variant="outline-secondary" className="px-3" style={{ fontWeight: 500 }} onClick={handleSaveInterfaceClick} title="保存接口">
            <FaSave className="me-2" /> 保存
          </Button>
          <Button variant="light" className="border text-secondary" onClick={handleSaveEnv} title="环境管理">
            <FaCog className="me-2" /> 环境管理
          </Button>
        </div>

        <StandardApiTestingRequestTabBar runSubTab={runSubTab} setRunSubTab={setRunSubTab} headerCount={headers.filter((h) => h.key).length} onOpenCookieManager={onOpenCookieManager} />

        <div className="bg-white d-flex flex-column flex-shrink-0 api-request-config-content position-relative" style={{ height: `${requestHeight}px`, minHeight: '100px', overflow: 'hidden' }}>
          {renderRequestTabContent()}
        </div>

        <div
          className="border-top bg-light d-flex align-items-center justify-content-center text-muted flex-shrink-0"
          style={{ height: '6px', cursor: 'row-resize', backgroundColor: isDragging ? '#e9ecef' : '#f8f9fa', userSelect: 'none' }}
          onMouseDown={handleRequestBarMouseDown}
        />

        <ResponsePanel
          loading={loading}
          responseTab={responseTab}
          setResponseTab={setResponseTab}
          responseDetailedCookies={responseDetailedCookies}
          responseCookies={responseCookies}
          responseHeaders={responseHeaders}
          sentHeaders={sentHeaders}
          sentCookies={sentCookies}
          responseStatus={responseStatus}
          responseTime={responseTime}
          responseBody={responseBody}
          responseFormat={responseFormat}
          setResponseFormat={setResponseFormat}
          responseViewMode={responseViewMode}
          setResponseViewMode={setResponseViewMode}
          aiAnalysis={aiAnalysis}
          testResult={testResult}
          renderDashboard={renderDashboard}
          handleAnalyzeResponse={handleAnalyzeResponse}
          isAnalyzing={isAnalyzing}
          scriptTests={scriptTests}
        />
      </div>
    </>
  );
}
