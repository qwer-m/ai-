import type { ResponsePanelProps } from "./ResponsePanel.types";
import { ResponsePanelContent } from "./ResponsePanelContent";
import { ResponsePanelHeader } from "./ResponsePanelHeader";

export function ResponsePanelShell({
  loading,
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
  handleAnalyzeResponse,
  isAnalyzing,
  scriptTests,
}: ResponsePanelProps) {
  return (
    <div className="d-flex flex-column overflow-hidden standard-api-response-panel standard-api-response-shell">
      <ResponsePanelHeader
        responseTab={responseTab}
        setResponseTab={setResponseTab}
        responseDetailedCookies={responseDetailedCookies}
        responseCookies={responseCookies}
        responseHeaders={responseHeaders}
        responseStatus={responseStatus}
        responseTime={responseTime}
        responseBody={responseBody}
      />
      <div className="flex-grow-1 overflow-hidden p-0 position-relative d-flex flex-column standard-api-response-content">
        {loading && (
          <div className="position-absolute top-0 start-0 w-100 h-100 d-flex align-items-center justify-content-center z-1 standard-api-response-loading">
            <span className="spinner-border text-primary" role="status" aria-hidden="true" />
          </div>
        )}
        <ResponsePanelContent
          responseTab={responseTab}
          responseBody={responseBody}
          responseFormat={responseFormat}
          setResponseFormat={setResponseFormat}
          responseViewMode={responseViewMode}
          setResponseViewMode={setResponseViewMode}
          responseDetailedCookies={responseDetailedCookies}
          responseCookies={responseCookies}
          responseHeaders={responseHeaders}
          sentHeaders={sentHeaders}
          sentCookies={sentCookies}
          aiAnalysis={aiAnalysis}
          handleAnalyzeResponse={handleAnalyzeResponse}
          isAnalyzing={isAnalyzing}
          scriptTests={scriptTests}
        />
      </div>
    </div>
  );
}
