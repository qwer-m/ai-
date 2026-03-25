import type { CSSProperties } from "react";
import { Button, Form, Spinner } from "react-bootstrap";
import { FaBars, FaCog, FaSave } from "react-icons/fa";
import type { StandardApiTestingRequestWorkspaceProps } from "./StandardApiTestingRequestWorkspace.types";
import { getMethodColor } from "./utils/requestUi";

type Props = Pick<
  StandardApiTestingRequestWorkspaceProps,
  | "showSidebar"
  | "setShowSidebar"
  | "isDragging"
  | "handleRequestBarMouseDown"
  | "method"
  | "setMethod"
  | "apiPath"
  | "setApiPath"
  | "inputRef"
  | "highlighterRef"
  | "activeEnvTag"
  | "showPopup"
  | "setShowPopup"
  | "handleInputMouseMove"
  | "handleInputMouseLeave"
  | "handlePopupMouseEnter"
  | "handlePopupMouseLeave"
  | "getEnvBaseUrlValue"
  | "setEnvBaseUrlValue"
  | "handleApiPathBlur"
  | "loading"
  | "handleSendRequest"
  | "handleSaveInterfaceClick"
  | "handleSaveEnv"
>;

export function StandardApiTestingRequestWorkspaceToolbar({
  showSidebar,
  setShowSidebar,
  isDragging,
  handleRequestBarMouseDown,
  method,
  setMethod,
  apiPath,
  setApiPath,
  inputRef,
  highlighterRef,
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
}: Props) {
  return (
    <>
      <div className="d-flex align-items-center p-2 border-bottom bg-light gap-2 flex-shrink-0" style={{ height: "50px" }}>
        <Button
          variant="link"
          className="p-0 text-secondary me-2"
          onClick={() => setShowSidebar(!showSidebar)}
          title={showSidebar ? "收起列表" : "展开列表"}
        >
          <FaBars size={16} />
        </Button>

        <div className="d-flex flex-grow-1 bg-white border rounded">
          <Form.Select
            className="border-0 shadow-none"
            style={{
              width: "110px",
              backgroundColor: "#f9f9f9",
              borderRight: "1px solid #dee2e6",
              fontWeight: 600,
              color: getMethodColor(method),
            }}
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
                  whiteSpace: "pre",
                  overflow: "hidden",
                  pointerEvents: "none",
                  font: "inherit",
                  color: "black",
                  paddingLeft: "0px",
                  paddingRight: "0px",
                }}
              >
                {apiPath.split(/(\{\{.*?\}\})/).map((part, index) => {
                  if (part.startsWith("{{") && part.endsWith("}}")) {
                    const isEmpty = part.replace(/[\{\}\s]/g, "").length === 0;
                    const envValue = getEnvBaseUrlValue(part);
                    const isMissingBaseUrl =
                      !isEmpty && (!envValue || !envValue.trim() || envValue === "null" || envValue === "undefined");
                    const chipStyle: CSSProperties = isEmpty
                      ? {
                          background: "transparent",
                          border: "1px solid #ffecb5",
                          borderRadius: "4px",
                          color: "#856404",
                          padding: "0 2px",
                          margin: "0 1px",
                          fontSize: "1em",
                          lineHeight: 1.6,
                        }
                      : isMissingBaseUrl
                        ? {
                            background: "rgba(220, 53, 69, 0.1)",
                            border: "1px solid rgba(220, 53, 69, 0.3)",
                            borderRadius: "4px",
                            color: "#dc3545",
                            fontWeight: 600,
                            padding: "0 2px",
                            margin: "0 1px",
                            fontSize: "1em",
                            lineHeight: 1.6,
                          }
                        : {
                            background: "transparent",
                            border: "1px solid #dee2e6",
                            borderRadius: "4px",
                            color: "#0d6efd",
                            padding: "0 2px",
                            margin: "0 1px",
                            fontSize: "1em",
                            lineHeight: 1.6,
                          };

                    return (
                      <span key={index} style={chipStyle}>
                        {part}
                      </span>
                    );
                  }

                  return (
                    <span key={index} style={{ color: "#212529" }}>
                      {part}
                    </span>
                  );
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
                  color: apiPath ? "transparent" : undefined,
                  caretColor: "black",
                  position: "relative",
                  zIndex: 1,
                  fontFamily: "inherit",
                }}
              />
            </div>

            {activeEnvTag && showPopup && (
              <div
                className="position-absolute start-0 end-0 bg-white border rounded shadow-sm px-3 py-2"
                style={{ top: "100%", zIndex: 1050, marginTop: "4px" }}
                onMouseEnter={handlePopupMouseEnter}
                onMouseLeave={handlePopupMouseLeave}
              >
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

        <Button
          variant="primary"
          onClick={handleSendRequest}
          disabled={loading}
          className="px-4 text-white rounded-0"
          style={{ fontWeight: 500, backgroundColor: "#0d6efd", borderColor: "#0d6efd" }}
        >
          {loading ? <Spinner size="sm" animation="border" /> : "发送"}
        </Button>
        <Button
          variant="outline-secondary"
          className="px-3"
          style={{ fontWeight: 500 }}
          onClick={handleSaveInterfaceClick}
          title="保存接口"
        >
          <FaSave className="me-2" /> 保存
        </Button>
        <Button variant="light" className="border text-secondary" onClick={handleSaveEnv} title="环境管理">
          <FaCog className="me-2" /> 环境管理
        </Button>
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
    </>
  );
}
