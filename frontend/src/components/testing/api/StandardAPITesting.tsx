import { StandardApiTestingSidebar } from '../../standard-api-testing/StandardApiTestingSidebar';
import { StandardApiTestingRequestWorkspace } from '../../standard-api-testing/StandardApiTestingRequestWorkspace';
import { StandardApiTestingModalLayer } from '../../standard-api-testing/StandardApiTestingModalLayer';
import { StandardApiTestingPageStyles } from '../../standard-api-testing/styles/StandardApiTestingPageStyles';
import { getMethodColor } from '../../standard-api-testing/utils/requestUi';
import type { StandardAPITestingProps } from '../../standard-api-testing/utils/types';
import { useStandardApiTestingController } from './hooks/useStandardApiTestingController';

export function StandardAPITesting(props: StandardAPITestingProps) {
  const controller = useStandardApiTestingController(props);

  return (
    <div className="d-flex h-100 w-100 overflow-hidden postman-theme standard-api-shell standard-api-layout">
      <StandardApiTestingPageStyles />

      <StandardApiTestingSidebar
        showSidebar={controller.showSidebar}
        sidebarWidth={controller.sidebarWidth}
        isResizingSidebar={controller.isResizingSidebar}
        savedInterfaces={controller.savedInterfaces}
        selectedId={controller.selectedId}
        dragOverId={controller.dragOverId}
        dragOverPosition={controller.dragOverPosition}
        hoverId={controller.hoverId}
        bulkDeleteMode={controller.bulkDeleteMode}
        bulkSelected={controller.bulkSelected}
        renamingId={controller.renamingId}
        renamingName={controller.renamingName}
        setHoverId={controller.setHoverId}
        onDragStart={controller.handleDragStart}
        onDragOver={controller.handleDragOver}
        onDragLeave={controller.handleDragLeave}
        onDrop={controller.handleDrop}
        onToggleBulkSelected={controller.handleToggleBulkSelected}
        onLoadInterface={controller.handleLoadInterface}
        getMethodColor={getMethodColor}
        onToggleFolder={controller.toggleFolder}
        onSetRenamingId={controller.setRenamingId}
        onSetRenamingName={controller.setRenamingName}
        onRenameConfirm={controller.handleRenameConfirm}
        onCreateFolder={controller.handleCreateFolder}
        onCreateInterface={controller.handleCreateInterface}
        onEditFolder={controller.handleEditFolder}
        onDeleteInterface={controller.handleDeleteInterface}
        onBulkDeleteToggleOrConfirm={controller.handleBulkDeleteToggleOrConfirm}
        onLog={props.onLog}
        onRefreshInterfaces={controller.fetchInterfaces}
        onImportFiles={controller.importFiles}
        onOpenFolderAfterImport={controller.handleOpenFolderAfterImport}
      />

      {controller.showSidebar && (
        <div className="api-sidebar-resizer" onMouseDown={controller.handleSidebarResizerMouseDown} />
      )}

      <StandardApiTestingRequestWorkspace
        showSidebar={controller.showSidebar}
        setShowSidebar={controller.setShowSidebar}
        isDragging={controller.isDragging}
        requestHeight={controller.requestHeight}
        handleRequestBarMouseDown={controller.handleRequestBarMouseDown}
        mainContentRef={controller.mainContentRef}
        method={controller.method}
        setMethod={controller.setMethod}
        apiPath={controller.apiPath}
        setApiPath={controller.setApiPath}
        inputRef={controller.inputRef}
        highlighterRef={controller.highlighterRef}
        bodyHighlighterRef={controller.bodyHighlighterRef}
        activeEnvTag={controller.activeEnvTag}
        showPopup={controller.showPopup}
        setShowPopup={controller.setShowPopup}
        handleInputMouseMove={controller.handleInputMouseMove}
        handleInputMouseLeave={controller.handleInputMouseLeave}
        handlePopupMouseEnter={controller.handlePopupMouseEnter}
        handlePopupMouseLeave={controller.handlePopupMouseLeave}
        getEnvBaseUrlValue={controller.getEnvBaseUrlValue}
        setEnvBaseUrlValue={controller.setEnvBaseUrlValue}
        handleApiPathBlur={controller.handleApiPathBlur}
        loading={controller.loading}
        handleSendRequest={controller.handleSendRequest}
        handleSaveInterfaceClick={controller.handleSaveInterfaceClick}
        handleSaveEnv={controller.handleSaveEnv}
        runSubTab={controller.runSubTab}
        setRunSubTab={controller.setRunSubTab}
        queryParams={controller.queryParams}
        setQueryParams={controller.setQueryParams}
        headers={controller.headers}
        setHeaders={controller.setHeaders}
        authType={controller.authType}
        setAuthType={controller.setAuthType}
        authToken={controller.authToken}
        setAuthToken={controller.setAuthToken}
        authBasic={controller.authBasic}
        setAuthBasic={controller.setAuthBasic}
        authApiKey={controller.authApiKey}
        setAuthApiKey={controller.setAuthApiKey}
        activeScriptTab={controller.activeScriptTab}
        setActiveScriptTab={controller.setActiveScriptTab}
        preRequestScript={controller.preRequestScript}
        setPreRequestScript={controller.setPreRequestScript}
        postResponseScript={controller.postResponseScript}
        setPostResponseScript={controller.setPostResponseScript}
        requestSettings={controller.requestSettings}
        setRequestSettings={controller.setRequestSettings}
        bodyMode={controller.bodyMode}
        setBodyMode={controller.setBodyMode}
        rawType={controller.rawType}
        setRawType={controller.setRawType}
        bodyContent={controller.bodyContent}
        setBodyContent={controller.setBodyContent}
        formDataParams={controller.formDataParams}
        setFormDataParams={controller.setFormDataParams}
        xWwwFormUrlencodedParams={controller.xWwwFormUrlencodedParams}
        setXWwwFormUrlencodedParams={controller.setXWwwFormUrlencodedParams}
        binaryFile={controller.binaryFile}
        setBinaryFile={controller.setBinaryFile}
        graphqlQuery={controller.graphqlQuery}
        setGraphqlQuery={controller.setGraphqlQuery}
        graphqlVariables={controller.graphqlVariables}
        setGraphqlVariables={controller.setGraphqlVariables}
        isBulkEditFormData={controller.isBulkEditFormData}
        setIsBulkEditFormData={controller.setIsBulkEditFormData}
        formDataBulkText={controller.formDataBulkText}
        setFormDataBulkText={controller.setFormDataBulkText}
        isBulkEditBody={controller.isBulkEditBody}
        setIsBulkEditBody={controller.setIsBulkEditBody}
        bodyBulkText={controller.bodyBulkText}
        setBodyBulkText={controller.setBodyBulkText}
        isBulkEditParams={controller.isBulkEditParams}
        setIsBulkEditParams={controller.setIsBulkEditParams}
        paramsBulkText={controller.paramsBulkText}
        setParamsBulkText={controller.setParamsBulkText}
        isBulkEditHeaders={controller.isBulkEditHeaders}
        setIsBulkEditHeaders={controller.setIsBulkEditHeaders}
        headersBulkText={controller.headersBulkText}
        setHeadersBulkText={controller.setHeadersBulkText}
        handleBodyScroll={controller.handleBodyScroll}
        responseTab={controller.responseTab}
        setResponseTab={controller.setResponseTab}
        responseDetailedCookies={controller.responseDetailedCookies}
        responseCookies={controller.responseCookies}
        responseHeaders={controller.responseHeaders}
        sentHeaders={controller.sentHeaders}
        sentCookies={controller.sentCookies}
        responseStatus={controller.responseStatus}
        responseTime={controller.responseTime}
        responseBody={controller.responseBody}
        responseFormat={controller.responseFormat}
        setResponseFormat={controller.setResponseFormat}
        responseViewMode={controller.responseViewMode}
        setResponseViewMode={controller.setResponseViewMode}
        aiAnalysis={controller.aiAnalysis}
        handleAnalyzeResponse={controller.handleAnalyzeResponse}
        isAnalyzing={controller.isAnalyzing}
        scriptTests={controller.scriptTests}
        onOpenCookieManager={controller.onOpenCookieManager}
      />

      <StandardApiTestingModalLayer
        showSaveModal={controller.showSaveModal}
        setShowSaveModal={controller.setShowSaveModal}
        saveForm={controller.saveForm}
        setSaveForm={controller.setSaveForm}
        renderFolderOptions={controller.renderFolderOptions}
        handleConfirmSave={controller.handleConfirmSave}
        showCookieModal={controller.showCookieModal}
        setShowCookieModal={controller.setShowCookieModal}
        cookieJar={controller.cookieJar}
        setCookieJar={controller.setCookieJar}
        showEnvModal={controller.showEnvModal}
        setShowEnvModal={controller.setShowEnvModal}
        editingEnv={controller.editingEnv}
        setEditingEnv={controller.setEditingEnv}
        savedEnvs={controller.savedEnvs}
        handleDeleteEnv={controller.handleDeleteEnv}
        handleUpdateEnv={controller.handleUpdateEnv}
      />
    </div>
  );
}
