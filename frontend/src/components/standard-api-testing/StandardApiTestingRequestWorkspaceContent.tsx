import { KvEditor, parseBulkText, stringifyBulkItems } from "./RequestEditors";
import { StandardApiTestingRequestBodyTab } from "./StandardApiTestingRequestBodyTab";
import { StandardApiTestingRequestSettingsTab } from "./StandardApiTestingRequestSettingsTab";
import { StandardApiTestingRequestWorkspaceAuthTab } from "./StandardApiTestingRequestWorkspaceAuthTab";
import { StandardApiTestingRequestWorkspaceGenerationTab } from "./StandardApiTestingRequestWorkspaceGenerationTab";
import { StandardApiTestingRequestWorkspaceScriptsTab } from "./StandardApiTestingRequestWorkspaceScriptsTab";
import type { StandardApiTestingRequestWorkspaceProps } from "./StandardApiTestingRequestWorkspace.types";

type Props = Pick<
  StandardApiTestingRequestWorkspaceProps,
  | "runSubTab"
  | "queryParams"
  | "setQueryParams"
  | "headers"
  | "setHeaders"
  | "authType"
  | "setAuthType"
  | "authToken"
  | "setAuthToken"
  | "authBasic"
  | "setAuthBasic"
  | "authApiKey"
  | "setAuthApiKey"
  | "activeScriptTab"
  | "setActiveScriptTab"
  | "preRequestScript"
  | "setPreRequestScript"
  | "postResponseScript"
  | "setPostResponseScript"
  | "requestSettings"
  | "setRequestSettings"
  | "bodyMode"
  | "setBodyMode"
  | "rawType"
  | "setRawType"
  | "bodyContent"
  | "setBodyContent"
  | "bodyHighlighterRef"
  | "formDataParams"
  | "setFormDataParams"
  | "xWwwFormUrlencodedParams"
  | "setXWwwFormUrlencodedParams"
  | "binaryFile"
  | "setBinaryFile"
  | "graphqlQuery"
  | "setGraphqlQuery"
  | "graphqlVariables"
  | "setGraphqlVariables"
  | "isBulkEditFormData"
  | "setIsBulkEditFormData"
  | "formDataBulkText"
  | "setFormDataBulkText"
  | "isBulkEditBody"
  | "setIsBulkEditBody"
  | "bodyBulkText"
  | "setBodyBulkText"
  | "isBulkEditParams"
  | "setIsBulkEditParams"
  | "paramsBulkText"
  | "setParamsBulkText"
  | "isBulkEditHeaders"
  | "setIsBulkEditHeaders"
  | "headersBulkText"
  | "setHeadersBulkText"
  | "handleBodyScroll"
  | "mode"
  | "setMode"
  | "requirement"
  | "setRequirement"
  | "handleRun"
  | "loading"
>;

export function StandardApiTestingRequestWorkspaceContent(props: Props) {
  const {
    runSubTab,
    queryParams,
    setQueryParams,
    headers,
    setHeaders,
    isBulkEditParams,
    setIsBulkEditParams,
    paramsBulkText,
    setParamsBulkText,
    isBulkEditHeaders,
    setIsBulkEditHeaders,
    headersBulkText,
    setHeadersBulkText,
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
    bodyHighlighterRef,
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
    handleBodyScroll,
    mode,
    setMode,
    requirement,
    setRequirement,
    handleRun,
    loading,
  } = props;

  if (runSubTab === "params") {
    return (
      <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white" style={{ visibility: "visible", zIndex: 10, overflowX: "hidden", overflowY: "scroll" }}>
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

  if (runSubTab === "headers") {
    return (
      <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white" style={{ visibility: "visible", zIndex: 10, overflowX: "hidden", overflowY: "scroll" }}>
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

  if (runSubTab === "authorization") {
    return (
      <StandardApiTestingRequestWorkspaceAuthTab
        authType={authType}
        setAuthType={setAuthType}
        authToken={authToken}
        setAuthToken={setAuthToken}
        authBasic={authBasic}
        setAuthBasic={setAuthBasic}
        authApiKey={authApiKey}
        setAuthApiKey={setAuthApiKey}
      />
    );
  }

  if (runSubTab === "scripts") {
    return (
      <StandardApiTestingRequestWorkspaceScriptsTab
        activeScriptTab={activeScriptTab}
        setActiveScriptTab={setActiveScriptTab}
        preRequestScript={preRequestScript}
        setPreRequestScript={setPreRequestScript}
        postResponseScript={postResponseScript}
        setPostResponseScript={setPostResponseScript}
      />
    );
  }

  if (runSubTab === "settings") {
    return <StandardApiTestingRequestSettingsTab requestSettings={requestSettings} setRequestSettings={setRequestSettings} />;
  }

  if (runSubTab === "body") {
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
    <StandardApiTestingRequestWorkspaceGenerationTab
      mode={mode}
      setMode={setMode}
      requirement={requirement}
      setRequirement={setRequirement}
      handleRun={handleRun}
      loading={loading}
    />
  );
}
