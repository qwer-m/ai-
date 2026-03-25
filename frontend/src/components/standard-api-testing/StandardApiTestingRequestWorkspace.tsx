import { ResponsePanel } from "./ResponsePanel";
import { StandardApiTestingRequestTabBar } from "./StandardApiTestingRequestTabBar";
import { StandardApiTestingRequestWorkspaceContent } from "./StandardApiTestingRequestWorkspaceContent";
import { StandardApiTestingRequestWorkspaceToolbar } from "./StandardApiTestingRequestWorkspaceToolbar";
import type { StandardApiTestingRequestWorkspaceProps } from "./StandardApiTestingRequestWorkspace.types";

export type { StandardApiTestingRequestWorkspaceProps } from "./StandardApiTestingRequestWorkspace.types";

export function StandardApiTestingRequestWorkspace(props: StandardApiTestingRequestWorkspaceProps) {
  const {
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
  } = props;

  return (
    <>
      <div className="flex-grow-1 d-flex flex-column h-100 overflow-hidden bg-white api-main-content" ref={mainContentRef}>
        <StandardApiTestingRequestWorkspaceToolbar
          showSidebar={showSidebar}
          setShowSidebar={setShowSidebar}
          isDragging={isDragging}
          handleRequestBarMouseDown={handleRequestBarMouseDown}
          method={method}
          setMethod={setMethod}
          apiPath={apiPath}
          setApiPath={setApiPath}
          inputRef={inputRef}
          highlighterRef={highlighterRef}
          activeEnvTag={activeEnvTag}
          showPopup={showPopup}
          setShowPopup={setShowPopup}
          handleInputMouseMove={handleInputMouseMove}
          handleInputMouseLeave={handleInputMouseLeave}
          handlePopupMouseEnter={handlePopupMouseEnter}
          handlePopupMouseLeave={handlePopupMouseLeave}
          getEnvBaseUrlValue={getEnvBaseUrlValue}
          setEnvBaseUrlValue={setEnvBaseUrlValue}
          handleApiPathBlur={handleApiPathBlur}
          loading={loading}
          handleSendRequest={handleSendRequest}
          handleSaveInterfaceClick={handleSaveInterfaceClick}
          handleSaveEnv={handleSaveEnv}
        />

        <StandardApiTestingRequestTabBar
          runSubTab={runSubTab}
          setRunSubTab={setRunSubTab}
          headerCount={headers.filter((header) => header.key).length}
          onOpenCookieManager={onOpenCookieManager}
        />

        <div
          className="bg-white d-flex flex-column flex-shrink-0 api-request-config-content position-relative"
          style={{ height: `${requestHeight}px`, minHeight: "100px", overflow: "hidden" }}
        >
          <StandardApiTestingRequestWorkspaceContent
            runSubTab={runSubTab}
            queryParams={queryParams}
            setQueryParams={setQueryParams}
            headers={headers}
            setHeaders={setHeaders}
            authType={authType}
            setAuthType={setAuthType}
            authToken={authToken}
            setAuthToken={setAuthToken}
            authBasic={authBasic}
            setAuthBasic={setAuthBasic}
            authApiKey={authApiKey}
            setAuthApiKey={setAuthApiKey}
            activeScriptTab={activeScriptTab}
            setActiveScriptTab={setActiveScriptTab}
            preRequestScript={preRequestScript}
            setPreRequestScript={setPreRequestScript}
            postResponseScript={postResponseScript}
            setPostResponseScript={setPostResponseScript}
            requestSettings={requestSettings}
            setRequestSettings={setRequestSettings}
            bodyMode={bodyMode}
            setBodyMode={setBodyMode}
            rawType={rawType}
            setRawType={setRawType}
            bodyContent={bodyContent}
            setBodyContent={setBodyContent}
            bodyHighlighterRef={bodyHighlighterRef}
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
            isBulkEditParams={isBulkEditParams}
            setIsBulkEditParams={setIsBulkEditParams}
            paramsBulkText={paramsBulkText}
            setParamsBulkText={setParamsBulkText}
            isBulkEditHeaders={isBulkEditHeaders}
            setIsBulkEditHeaders={setIsBulkEditHeaders}
            headersBulkText={headersBulkText}
            setHeadersBulkText={setHeadersBulkText}
            handleBodyScroll={handleBodyScroll}
            mode={mode}
            setMode={setMode}
            requirement={requirement}
            setRequirement={setRequirement}
            handleRun={handleRun}
            loading={loading}
          />
        </div>

        <div
          className="border-top bg-light d-flex align-items-center justify-content-center text-muted flex-shrink-0"
          style={{
            height: "6px",
            cursor: "row-resize",
            backgroundColor: isDragging ? "#e9ecef" : "#f8f9fa",
            userSelect: "none",
          }}
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
