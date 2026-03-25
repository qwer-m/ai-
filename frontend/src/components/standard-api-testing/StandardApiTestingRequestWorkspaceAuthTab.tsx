import { Form } from "react-bootstrap";
import type { StandardApiTestingRequestWorkspaceProps } from "./StandardApiTestingRequestWorkspace.types";

type Props = Pick<
  StandardApiTestingRequestWorkspaceProps,
  "authType" | "setAuthType" | "authToken" | "setAuthToken" | "authBasic" | "setAuthBasic" | "authApiKey" | "setAuthApiKey"
>;

export function StandardApiTestingRequestWorkspaceAuthTab({
  authType,
  setAuthType,
  authToken,
  setAuthToken,
  authBasic,
  setAuthBasic,
  authApiKey,
  setAuthApiKey,
}: Props) {
  const authTypes = [
    { key: "none", label: "无认证(No Auth)" },
    { key: "bearer", label: "Bearer 令牌" },
    { key: "basic", label: "基础认证 (Basic Auth)" },
    { key: "apikey", label: "API 密钥 (API Key)" },
  ] as const;

  return (
    <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white" style={{ visibility: "visible", zIndex: 10, overflowX: "hidden", overflowY: "scroll" }}>
      <div className="d-flex h-100">
        <div className="border-end bg-light p-2" style={{ width: "200px", minWidth: "200px" }}>
          <div className="small text-muted mb-2 ps-2">类型</div>
          <div className="d-flex flex-column gap-1">
            {authTypes.map((type) => (
              <div
                key={type.key}
                className={`px-3 py-2 small rounded cursor-pointer ${authType === type.key ? "bg-primary text-white" : "text-secondary hover-bg-gray"}`}
                onClick={() => setAuthType(type.key)}
                style={{ cursor: "pointer" }}
              >
                {type.label}
              </div>
            ))}
          </div>
        </div>
        <div className="flex-grow-1 p-3">
          {authType === "none" && <div className="text-muted small">此请求不使用任何认证。</div>}
          {authType === "bearer" && (
            <div className="d-flex flex-column gap-2" style={{ maxWidth: "500px" }}>
              <Form.Label className="small mb-0">Token</Form.Label>
              <Form.Control size="sm" placeholder="输入 Token" value={authToken} onChange={(e) => setAuthToken(e.target.value)} />
            </div>
          )}
          {authType === "basic" && (
            <div className="d-flex flex-column gap-2" style={{ maxWidth: "500px" }}>
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
          {authType === "apikey" && (
            <div className="d-flex flex-column gap-3" style={{ maxWidth: "500px" }}>
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
                <Form.Select size="sm" value={authApiKey.addTo} onChange={(e) => setAuthApiKey({ ...authApiKey, addTo: e.target.value as "header" | "query" })}>
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
