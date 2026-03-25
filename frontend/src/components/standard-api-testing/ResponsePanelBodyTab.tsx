import { Button, Dropdown } from "react-bootstrap";
import { FaGlobe } from "react-icons/fa";
import { highlightJson } from "./utils/jsonHighlight";
import type { ResponsePanelProps } from "./ResponsePanel.types";
import {
  getDisplayBodyText,
  getPrettyJsonText,
  responseFormatOptions,
} from "./ResponsePanel.utils";

type Props = Pick<
  ResponsePanelProps,
  "responseBody" | "responseFormat" | "setResponseFormat" | "responseViewMode" | "setResponseViewMode"
>;

function ResponseFormatDropdown({
  responseFormat,
  setResponseFormat,
  setResponseViewMode,
}: Pick<Props, "responseFormat" | "setResponseFormat" | "setResponseViewMode">) {
  const currentOption = responseFormatOptions.find((option) => option.value === responseFormat);

  return (
    <Dropdown>
      <Dropdown.Toggle
        variant="light"
        size="sm"
        className="border-0 bg-transparent text-dark d-flex align-items-center gap-3 p-0 px-2"
        style={{ fontWeight: 600 }}
        id="response-format-dropdown"
      >
        <span
          className="text-secondary small d-inline-flex align-items-center justify-content-center"
          style={{ width: "34px" }}
        >
          {currentOption?.glyph || ""}
        </span>
        {responseFormat}
      </Dropdown.Toggle>
      <Dropdown.Menu style={{ minWidth: "200px" }}>
        {responseFormatOptions.map((option) => (
          <div key={option.value}>
            {option.dividerBefore && <Dropdown.Divider />}
            <Dropdown.Item
              onClick={() => {
                setResponseFormat(option.value);
                setResponseViewMode("json");
              }}
              active={responseFormat === option.value}
            >
              <div className="d-flex align-items-center justify-content-between w-100">
                <span className="d-flex align-items-center">
                  <span
                    className="me-4 text-muted fw-normal d-inline-flex align-items-center justify-content-center"
                    style={{ width: "34px" }}
                  >
                    {option.glyph}
                  </span>
                  <span>{option.label}</span>
                </span>
                {responseFormat === option.value && <span>✓</span>}
              </div>
            </Dropdown.Item>
          </div>
        ))}
      </Dropdown.Menu>
    </Dropdown>
  );
}

export function ResponsePanelBodyTab({
  responseBody,
  responseFormat,
  setResponseFormat,
  responseViewMode,
  setResponseViewMode,
}: Props) {
  return (
    <div className="flex-grow-1 d-flex flex-column" style={{ minHeight: 0 }}>
      {responseBody ? (
        <>
          <div className="bg-light border-bottom px-2 py-1 d-flex justify-content-between align-items-center">
            <div className="d-flex align-items-center gap-2">
              <ResponseFormatDropdown
                responseFormat={responseFormat}
                setResponseFormat={setResponseFormat}
                setResponseViewMode={setResponseViewMode}
              />
              <div className="vr h-50 my-auto text-secondary opacity-25" style={{ height: "16px" }} />
              <Button
                variant="link"
                size="sm"
                className={`p-0 text-decoration-none d-flex align-items-center gap-1 ${
                  responseViewMode === "html" ? "text-primary" : "text-secondary"
                }`}
                style={{ fontWeight: 600 }}
                onClick={() => setResponseViewMode("html")}
              >
                <FaGlobe size={12} />
                预览
              </Button>
            </div>
          </div>

          <div className="flex-grow-1 bg-white position-relative" style={{ minHeight: 0 }}>
            {responseViewMode === "html" ? (
              <iframe
                srcDoc={typeof responseBody === "string" ? responseBody : JSON.stringify(responseBody)}
                style={{ width: "100%", height: "100%", border: "none" }}
                title="Response Preview"
                sandbox="allow-same-origin"
              />
            ) : responseFormat === "JSON" ? (
              <div
                className="w-100 h-100 border-0 p-3 font-monospace small custom-scrollbar bg-white"
                style={{
                  whiteSpace: "pre-wrap",
                  wordWrap: "break-word",
                  overflow: "auto",
                  userSelect: "text",
                }}
                dangerouslySetInnerHTML={highlightJson(getPrettyJsonText(responseBody))}
              />
            ) : (
              <textarea
                className="w-100 h-100 border-0 p-3 font-monospace small custom-scrollbar"
                style={{
                  resize: "none",
                  outline: "none",
                  color: "black",
                  opacity: 1,
                  backgroundColor: "white",
                }}
                value={getDisplayBodyText(responseBody, responseFormat)}
                readOnly
              />
            )}
          </div>
        </>
      ) : (
        <div className="d-flex flex-column align-items-center justify-content-center h-100 text-muted opacity-50">
          <FaGlobe size={48} className="mb-3" />
          <div>输入 URL 并点击发送以获取响应</div>
        </div>
      )}
    </div>
  );
}
