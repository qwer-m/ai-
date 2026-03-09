import type { ReactNode } from "react";
import { Badge, Button, Dropdown, Nav, Spinner } from "react-bootstrap";
import {
  FaCheck,
  FaCheckCircle,
  FaExclamationCircle,
  FaGlobe,
  FaRobot,
} from "react-icons/fa";
import { highlightJson } from "./jsonHighlight";
import type { ResponseTab, TestResult } from "./types";

type ResponseViewMode = "json" | "html" | "headers";
type ResponseFormat =
  | "JSON"
  | "XML"
  | "HTML"
  | "JavaScript"
  | "Raw"
  | "Hex"
  | "Base64";

type ResponsePanelProps = {
  loading: boolean;
  responseTab: ResponseTab;
  setResponseTab: (tab: ResponseTab) => void;
  responseDetailedCookies: any;
  responseCookies: any;
  responseHeaders: any;
  sentHeaders: any;
  sentCookies: any;
  responseStatus: number | null;
  responseTime: number | null;
  responseBody: any;
  responseFormat: ResponseFormat;
  setResponseFormat: (value: ResponseFormat) => void;
  responseViewMode: ResponseViewMode;
  setResponseViewMode: (value: ResponseViewMode) => void;
  aiAnalysis: string | null;
  testResult: TestResult | null;
  renderDashboard: (report: NonNullable<TestResult["structured_report"]>) => ReactNode;
  handleAnalyzeResponse: () => void;
  isAnalyzing: boolean;
  scriptTests: { name: string; passed: boolean; error?: string }[];
};

export function ResponsePanel({
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
  testResult,
  renderDashboard,
  handleAnalyzeResponse,
  isAnalyzing,
  scriptTests,
}: ResponsePanelProps) {
  // 响应面板聚合了 5 个标签页：响应体、Headers、Cookies、测试结果、AI 报告。
  // 这里保持“只展示，不做业务请求”，业务逻辑仍在 StandardAPITesting 容器中。

  // 统一把响应体转成字符串，便于不同格式复用。
  const getRawBodyText = () => {
    if (!responseBody) return "";
    return typeof responseBody === "object" ? JSON.stringify(responseBody) : String(responseBody);
  };

  const getPrettyJsonText = () => {
    if (!responseBody) return "";
    if (typeof responseBody === "object") return JSON.stringify(responseBody, null, 2);
    try {
      const parsed = JSON.parse(responseBody);
      return JSON.stringify(parsed, null, 2);
    } catch {
      return String(responseBody);
    }
  };

  return (
    <div className="d-flex flex-column bg-white overflow-hidden" style={{ flex: 1, minHeight: 0 }}>
      <div className="px-3 py-1 border-bottom bg-white d-flex justify-content-between align-items-center flex-shrink-0">
        <Nav
          variant="underline"
          activeKey={responseTab}
          onSelect={(k) => setResponseTab((k as ResponseTab) || "body")}
          className="small custom-nav-tabs"
        >
          <Nav.Item>
            <Nav.Link eventKey="body" className={responseTab === "body" ? "active" : ""}>
              响应体 (Body)
            </Nav.Link>
          </Nav.Item>
          <Nav.Item>
            <Nav.Link eventKey="cookies" className={responseTab === "cookies" ? "active" : ""}>
              Cookies{" "}
              <span className="text-muted">
                ({Object.keys(responseDetailedCookies).length || Object.keys(responseCookies).length})
              </span>
            </Nav.Link>
          </Nav.Item>
          <Nav.Item>
            <Nav.Link eventKey="headers" className={responseTab === "headers" ? "active" : ""}>
              响应头 (Headers) <span className="text-muted">({Object.keys(responseHeaders).length})</span>
            </Nav.Link>
          </Nav.Item>
          <Nav.Item>
            <Nav.Link
              eventKey="test_results"
              className={responseTab === "test_results" ? "active" : ""}
            >
              测试结果
            </Nav.Link>
          </Nav.Item>
          <Nav.Item>
            <Nav.Link
              eventKey="report"
              className={responseTab === "report" ? "text-primary active" : "text-primary"}
            >
              <FaRobot className="me-1" />
              AI 分析报告
            </Nav.Link>
          </Nav.Item>
        </Nav>

        <div className="d-flex gap-3 align-items-center small text-secondary">
          <span>
            状态:{" "}
            <span
              className={responseStatus === 200 ? "text-success" : responseStatus ? "text-danger" : ""}
              style={{ fontWeight: 600 }}
            >
              {responseStatus || "---"}
            </span>
          </span>
          <span>
            耗时:{" "}
            <span className="text-dark" style={{ fontWeight: 600 }}>
              {responseTime ? `${responseTime} ms` : "---"}
            </span>
          </span>
          <span>
            大小:{" "}
            <span className="text-dark" style={{ fontWeight: 600 }}>
              {responseBody ? `${getRawBodyText().length} B` : "---"}
            </span>
          </span>
        </div>
      </div>

      <div className="flex-grow-1 overflow-hidden p-0 position-relative d-flex flex-column">
        {loading && (
          <div className="position-absolute top-0 start-0 w-100 h-100 bg-white bg-opacity-75 d-flex align-items-center justify-content-center z-1">
            <Spinner animation="border" variant="primary" />
          </div>
        )}

        {/* 响应体标签：支持格式切换和 HTML 预览 */}
        {responseTab === "body" && (
          <div className="flex-grow-1 d-flex flex-column" style={{ minHeight: 0 }}>
            {responseBody ? (
              <>
                <div className="bg-light border-bottom px-2 py-1 d-flex justify-content-between align-items-center">
                  <div className="d-flex align-items-center gap-2">
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
                          {responseFormat === "JSON"
                            ? "{}"
                            : responseFormat === "XML"
                              ? "</>"
                              : responseFormat === "HTML"
                                ? "HTML"
                                : responseFormat === "JavaScript"
                                  ? "JS"
                                  : ""}
                        </span>
                        {responseFormat}
                      </Dropdown.Toggle>
                      <Dropdown.Menu style={{ minWidth: "200px" }}>
                        <Dropdown.Item
                          onClick={() => {
                            setResponseFormat("JSON");
                            setResponseViewMode("json");
                          }}
                          active={responseFormat === "JSON"}
                        >
                          <div className="d-flex align-items-center justify-content-between w-100">
                            <span className="d-flex align-items-center">
                              <span
                                className="me-4 text-muted fw-normal d-inline-flex align-items-center justify-content-center"
                                style={{ width: "34px" }}
                              >
                                {"{}"}
                              </span>
                              <span>JSON</span>
                            </span>
                            {responseFormat === "JSON" && <FaCheck size={12} />}
                          </div>
                        </Dropdown.Item>
                        <Dropdown.Item
                          onClick={() => {
                            setResponseFormat("XML");
                            setResponseViewMode("json");
                          }}
                          active={responseFormat === "XML"}
                        >
                          <div className="d-flex align-items-center justify-content-between w-100">
                            <span className="d-flex align-items-center">
                              <span
                                className="me-4 text-muted fw-normal d-inline-flex align-items-center justify-content-center"
                                style={{ width: "34px" }}
                              >
                                {"</>"}
                              </span>
                              <span>XML</span>
                            </span>
                            {responseFormat === "XML" && <FaCheck size={12} />}
                          </div>
                        </Dropdown.Item>
                        <Dropdown.Item
                          onClick={() => {
                            setResponseFormat("HTML");
                            setResponseViewMode("json");
                          }}
                          active={responseFormat === "HTML"}
                        >
                          <div className="d-flex align-items-center justify-content-between w-100">
                            <span className="d-flex align-items-center">
                              <span
                                className="me-4 text-muted fw-normal d-inline-flex align-items-center justify-content-center"
                                style={{ width: "34px" }}
                              >
                                HTML
                              </span>
                              <span>HTML</span>
                            </span>
                            {responseFormat === "HTML" && <FaCheck size={12} />}
                          </div>
                        </Dropdown.Item>
                        <Dropdown.Item
                          onClick={() => {
                            setResponseFormat("JavaScript");
                            setResponseViewMode("json");
                          }}
                          active={responseFormat === "JavaScript"}
                        >
                          <div className="d-flex align-items-center justify-content-between w-100">
                            <span className="d-flex align-items-center">
                              <span
                                className="me-4 text-muted fw-normal d-inline-flex align-items-center justify-content-center"
                                style={{ width: "34px" }}
                              >
                                JS
                              </span>
                              <span>JavaScript</span>
                            </span>
                            {responseFormat === "JavaScript" && <FaCheck size={12} />}
                          </div>
                        </Dropdown.Item>
                        <Dropdown.Divider />
                        <Dropdown.Item
                          onClick={() => {
                            setResponseFormat("Raw");
                            setResponseViewMode("json");
                          }}
                          active={responseFormat === "Raw"}
                        >
                          <div className="d-flex align-items-center justify-content-between w-100">
                            <span className="d-flex align-items-center">
                              <span
                                className="me-4 text-muted fw-normal d-inline-flex align-items-center justify-content-center"
                                style={{ width: "34px" }}
                              >
                                T
                              </span>
                              <span>Raw</span>
                            </span>
                            {responseFormat === "Raw" && <FaCheck size={12} />}
                          </div>
                        </Dropdown.Item>
                        <Dropdown.Item
                          onClick={() => {
                            setResponseFormat("Hex");
                            setResponseViewMode("json");
                          }}
                          active={responseFormat === "Hex"}
                        >
                          <div className="d-flex align-items-center justify-content-between w-100">
                            <span className="d-flex align-items-center">
                              <span
                                className="me-4 text-muted fw-normal d-inline-flex align-items-center justify-content-center"
                                style={{ width: "34px" }}
                              >
                                0x
                              </span>
                              <span>Hex</span>
                            </span>
                            {responseFormat === "Hex" && <FaCheck size={12} />}
                          </div>
                        </Dropdown.Item>
                        <Dropdown.Item
                          onClick={() => {
                            setResponseFormat("Base64");
                            setResponseViewMode("json");
                          }}
                          active={responseFormat === "Base64"}
                        >
                          <div className="d-flex align-items-center justify-content-between w-100">
                            <span className="d-flex align-items-center">
                              <span
                                className="me-4 text-muted fw-normal d-inline-flex align-items-center justify-content-center"
                                style={{ width: "34px" }}
                              >
                                64
                              </span>
                              <span>Base64</span>
                            </span>
                            {responseFormat === "Base64" && <FaCheck size={12} />}
                          </div>
                        </Dropdown.Item>
                      </Dropdown.Menu>
                    </Dropdown>
                    <div className="vr h-50 my-auto text-secondary opacity-25" style={{ height: "16px" }} />
                    <Button
                      variant="link"
                      size="sm"
                      className={`p-0 text-decoration-none d-flex align-items-center gap-1 ${responseViewMode === "html" ? "text-primary" : "text-secondary"}`}
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
                      dangerouslySetInnerHTML={highlightJson(getPrettyJsonText())}
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
                      value={(() => {
                        const raw = getRawBodyText();
                        if (!raw) return "";
                        if (responseFormat === "Base64") {
                          try {
                            return btoa(unescape(encodeURIComponent(raw)));
                          } catch {
                            return "Base64 编码失败";
                          }
                        }
                        if (responseFormat === "Hex") {
                          let hex = "";
                          for (let i = 0; i < raw.length; i++) {
                            hex += raw.charCodeAt(i).toString(16).padStart(2, "0");
                          }
                          return hex;
                        }
                        return raw;
                      })()}
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
        )}

        {/* Headers 标签：同时展示响应头和已发送请求头，方便对比排查 */}
        {responseTab === "headers" && (
          <div className="flex-grow-1 overflow-auto p-3" style={{ minHeight: 0 }}>
            <h6 className="text-secondary border-bottom pb-2 mb-3">响应头 (Response Headers)</h6>
            {Object.keys(responseHeaders).length > 0 ? (
              <table className="table table-sm table-hover table-bordered mb-0 small">
                <thead className="bg-light">
                  <tr>
                    <th className="ps-3 border-0">键 (Key)</th>
                    <th className="border-0">值 (Value)</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(responseHeaders).map(([k, v]) => (
                    <tr key={k}>
                      <td className="ps-3 text-secondary" style={{ fontWeight: 600 }}>
                        {k}
                      </td>
                      <td className="font-monospace text-break text-dark">{String(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="text-muted small mb-3">无响应头</div>
            )}

            <h6 className="text-secondary border-bottom pb-2 mb-3 mt-4">
              请求头 (Request Headers - 已发送)
            </h6>
            {Object.keys(sentHeaders).length > 0 ? (
              <table className="table table-sm table-hover table-bordered mb-0 small">
                <thead className="bg-light">
                  <tr>
                    <th className="ps-3 border-0">键 (Key)</th>
                    <th className="border-0">值 (Value)</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(sentHeaders).map(([k, v]) => (
                    <tr key={k}>
                      <td className="ps-3 text-secondary" style={{ fontWeight: 600 }}>
                        {k}
                      </td>
                      <td className="font-monospace text-break text-dark">{String(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="text-muted small">无请求头</div>
            )}
          </div>
        )}

        {/* 报告标签：优先显示 AI 分析，其次显示结构化报告 */}
        {responseTab === "report" && (
          <div className="flex-grow-1 overflow-auto bg-light p-3" style={{ minHeight: 0 }}>
            {aiAnalysis ? (
              <div className="bg-white p-3 border rounded">
                <h6 className="border-bottom pb-2 mb-3">AI 分析报告</h6>
                <pre
                  className="mb-0 font-monospace small text-dark"
                  style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", fontFamily: "inherit" }}
                >
                  {aiAnalysis}
                </pre>
              </div>
            ) : testResult ? (
              testResult.structured_report ? (
                renderDashboard(testResult.structured_report)
              ) : (
                <div>
                  <h6 className="text-secondary border-bottom pb-2 mb-3">执行结果 (Raw)</h6>
                  <pre
                    className="mb-0 font-monospace small text-dark"
                    style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}
                  >
                    {testResult.result || testResult.script || "未生成输出。"}
                  </pre>
                </div>
              )
            ) : responseBody ? (
              <div className="d-flex flex-column align-items-center justify-content-center h-100">
                <FaRobot size={48} className="mb-3 text-primary opacity-50" />
                <h5 className="mb-3">AI 智能分析</h5>
                <p className="text-muted mb-4 text-center" style={{ maxWidth: "400px" }}>
                  使用 AI 分析当前响应数据，识别潜在问题与安全风险，并给出优化建议。
                </p>
                <Button variant="primary" onClick={handleAnalyzeResponse} disabled={isAnalyzing}>
                  {isAnalyzing ? (
                    <>
                      <Spinner size="sm" animation="border" className="me-2" />
                      分析中...
                    </>
                  ) : (
                    "开始智能分析"
                  )}
                </Button>
              </div>
            ) : (
              <div className="d-flex flex-column align-items-center justify-content-center h-100 text-muted opacity-50">
                <FaRobot size={48} className="mb-3" />
                <div>发送请求后可生成 AI 分析报告</div>
              </div>
            )}
          </div>
        )}

        {/* 测试结果标签：展示脚本断言通过/失败详情 */}
        {responseTab === "test_results" && (
          <div className="flex-grow-1 overflow-auto p-3 custom-scrollbar" style={{ minHeight: 0 }}>
            {scriptTests.length > 0 ? (
              <div className="d-flex flex-column gap-2">
                <div className="d-flex justify-content-between align-items-center mb-2 pb-2 border-bottom">
                  <h6 className="text-secondary mb-0">
                    测试结果（{scriptTests.filter((t) => t.passed).length}/{scriptTests.length} 通过）
                  </h6>
                  <Badge bg={scriptTests.every((t) => t.passed) ? "success" : "danger"}>
                    {scriptTests.every((t) => t.passed) ? "PASS" : "FAIL"}
                  </Badge>
                </div>
                {scriptTests.map((test, idx) => (
                  <div
                    key={idx}
                    className={`p-2 border rounded d-flex align-items-start gap-2 ${test.passed ? "bg-success bg-opacity-10 border-success border-opacity-25" : "bg-danger bg-opacity-10 border-danger border-opacity-25"}`}
                  >
                    <div className={`mt-1 ${test.passed ? "text-success" : "text-danger"}`}>
                      {test.passed ? <FaCheckCircle size={14} /> : <FaExclamationCircle size={14} />}
                    </div>
                    <div className="flex-grow-1">
                      <div className={`fw-bold small ${test.passed ? "text-success" : "text-danger"}`}>
                        {test.name}
                      </div>
                      {!test.passed && test.error && (
                        <div className="text-danger small font-monospace mt-1">{test.error}</div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="d-flex flex-column align-items-center justify-content-center h-100 text-muted opacity-50">
                <FaCheckCircle size={48} className="mb-3" />
                <div>暂无测试结果</div>
                <div className="small mt-2">可在 Scripts 标签页编写测试脚本</div>
              </div>
            )}
          </div>
        )}

        {/* Cookies 标签：展示响应 Cookie 与发送 Cookie */}
        {responseTab === "cookies" && (
          <div className="flex-grow-1 overflow-auto custom-scrollbar p-3" style={{ minHeight: 0 }}>
            <h6 className="text-secondary border-bottom pb-2 mb-3">响应 Cookies</h6>
            {Object.keys(responseDetailedCookies).length > 0 ? (
              <table className="table table-sm table-hover table-bordered mb-0 small">
                <thead className="bg-light">
                  <tr>
                    <th className="ps-3 border-0">名称 (Name)</th>
                    <th className="border-0">值 (Value)</th>
                    <th className="border-0">域 (Domain)</th>
                    <th className="border-0">路径 (Path)</th>
                    <th className="border-0">过期时间 (Expires)</th>
                    <th className="border-0">Secure</th>
                    <th className="border-0">HttpOnly</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(responseDetailedCookies).map(([k, v]: [string, any]) => (
                    <tr key={k}>
                      <td className="ps-3 text-secondary" style={{ fontWeight: 600 }}>
                        {k}
                      </td>
                      <td
                        className="font-monospace text-break text-dark"
                        style={{ maxWidth: "200px" }}
                        title={v.value}
                      >
                        {v.value}
                      </td>
                      <td className="text-dark">{v.domain}</td>
                      <td className="text-dark">{v.path}</td>
                      <td className="text-dark">
                        {v.expires ? new Date(v.expires * 1000).toLocaleString() : "Session"}
                      </td>
                      <td className="text-dark">{v.secure ? "Yes" : "No"}</td>
                      <td className="text-dark">{v.httpOnly ? "Yes" : "No"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : Object.keys(responseCookies).length > 0 ? (
              <table className="table table-sm table-hover table-bordered mb-0 small">
                <thead className="bg-light">
                  <tr>
                    <th className="ps-3 border-0">Name</th>
                    <th className="border-0">Value</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(responseCookies).map(([k, v]) => (
                    <tr key={k}>
                      <td className="ps-3 text-secondary" style={{ fontWeight: 600 }}>
                        {k}
                      </td>
                      <td className="font-monospace text-break text-dark">{String(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="text-muted small mb-3">无响应 Cookies</div>
            )}

            <h6 className="text-secondary border-bottom pb-2 mb-3 mt-4">请求 Cookies (已发送)</h6>
            {Object.keys(sentCookies).length > 0 ? (
              <table className="table table-sm table-hover table-bordered mb-0 small">
                <thead className="bg-light">
                  <tr>
                    <th className="ps-3 border-0">名称 (Name)</th>
                    <th className="border-0">值 (Value)</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(sentCookies).map(([k, v]) => (
                    <tr key={k}>
                      <td className="ps-3 text-secondary" style={{ fontWeight: 600 }}>
                        {k}
                      </td>
                      <td className="font-monospace text-break text-dark">{String(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="text-muted small">无请求 Cookies</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
