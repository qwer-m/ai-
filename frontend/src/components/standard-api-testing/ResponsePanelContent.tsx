import type { ResponsePanelProps } from "./ResponsePanel.types";
import { ResponsePanelBodyTab } from "./ResponsePanelBodyTab";
import { ResponsePanelCookiesTab } from "./ResponsePanelCookiesTab";
import { ResponsePanelHeadersTab } from "./ResponsePanelHeadersTab";
import { ResponsePanelReportTab } from "./ResponsePanelReportTab";
import { ResponsePanelTestResultsTab } from "./ResponsePanelTestResultsTab";

type Props = Pick<
  ResponsePanelProps,
  | "responseTab"
  | "responseBody"
  | "responseFormat"
  | "setResponseFormat"
  | "responseViewMode"
  | "setResponseViewMode"
  | "responseDetailedCookies"
  | "responseCookies"
  | "responseHeaders"
  | "sentHeaders"
  | "sentCookies"
  | "aiAnalysis"
  | "handleAnalyzeResponse"
  | "isAnalyzing"
  | "scriptTests"
>;

export function ResponsePanelContent(props: Props) {
  const {
    responseTab,
    responseBody,
    responseFormat,
    setResponseFormat,
    responseViewMode,
    setResponseViewMode,
    responseDetailedCookies,
    responseCookies,
    responseHeaders,
    sentHeaders,
    sentCookies,
    aiAnalysis,
    handleAnalyzeResponse,
    isAnalyzing,
    scriptTests,
  } = props;

  if (responseTab === "body") {
    return (
      <ResponsePanelBodyTab
        responseBody={responseBody}
        responseFormat={responseFormat}
        setResponseFormat={setResponseFormat}
        responseViewMode={responseViewMode}
        setResponseViewMode={setResponseViewMode}
      />
    );
  }

  if (responseTab === "headers") {
    return <ResponsePanelHeadersTab responseHeaders={responseHeaders} sentHeaders={sentHeaders} />;
  }

  if (responseTab === "report") {
    return (
      <ResponsePanelReportTab
        aiAnalysis={aiAnalysis}
        handleAnalyzeResponse={handleAnalyzeResponse}
        isAnalyzing={isAnalyzing}
      />
    );
  }

  if (responseTab === "test_results") {
    return <ResponsePanelTestResultsTab scriptTests={scriptTests} />;
  }

  if (responseTab === "cookies") {
    return (
      <ResponsePanelCookiesTab
        responseDetailedCookies={responseDetailedCookies}
        responseCookies={responseCookies}
        sentCookies={sentCookies}
      />
    );
  }

  return null;
}
