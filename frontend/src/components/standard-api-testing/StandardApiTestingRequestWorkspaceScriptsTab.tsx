import { Form } from "react-bootstrap";
import type { StandardApiTestingRequestWorkspaceProps } from "./StandardApiTestingRequestWorkspace.types";

type Props = Pick<
  StandardApiTestingRequestWorkspaceProps,
  "activeScriptTab" | "setActiveScriptTab" | "preRequestScript" | "setPreRequestScript" | "postResponseScript" | "setPostResponseScript"
>;

export function StandardApiTestingRequestWorkspaceScriptsTab({
  activeScriptTab,
  setActiveScriptTab,
  preRequestScript,
  setPreRequestScript,
  postResponseScript,
  setPostResponseScript,
}: Props) {
  return (
    <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white" style={{ visibility: "visible", zIndex: 10, overflowX: "hidden", overflowY: "scroll" }}>
      <div className="d-flex h-100">
        <div className="border-end bg-light p-2" style={{ width: "200px", minWidth: "200px" }}>
          <div className="d-flex flex-column gap-1">
            <div
              className={`px-3 py-2 small rounded cursor-pointer ${activeScriptTab === "pre" ? "bg-primary text-white" : "text-secondary hover-bg-gray"}`}
              onClick={() => setActiveScriptTab("pre")}
              style={{ cursor: "pointer" }}
            >
              Pre-request (前置脚本)
            </div>
            <div
              className={`px-3 py-2 small rounded cursor-pointer ${activeScriptTab === "post" ? "bg-primary text-white" : "text-secondary hover-bg-gray"}`}
              onClick={() => setActiveScriptTab("post")}
              style={{ cursor: "pointer" }}
            >
              Post-response (后置脚本)
            </div>
          </div>
        </div>
        <div className="flex-grow-1 p-0 d-flex flex-column">
          <Form.Control
            as="textarea"
            className="flex-grow-1 border-0 p-3 font-monospace small bg-transparent"
            style={{ resize: "none", outline: "none" }}
            placeholder={activeScriptTab === "pre" ? "// 在此编写前置脚本 (Pre-request scripts)..." : "// 在此编写后置脚本 (Post-response scripts)..."}
            value={activeScriptTab === "pre" ? preRequestScript : postResponseScript}
            onChange={(e) => {
              if (activeScriptTab === "pre") {
                setPreRequestScript(e.target.value);
              } else {
                setPostResponseScript(e.target.value);
              }
            }}
            spellCheck={false}
          />
        </div>
      </div>
    </div>
  );
}
