import type { Dispatch, ReactNode, SetStateAction } from 'react';
import { Form } from 'react-bootstrap';
import type { RequestSettings } from './utils/types';

export type StandardApiTestingRequestSettingsTabProps = {
  requestSettings: RequestSettings;
  setRequestSettings: Dispatch<SetStateAction<RequestSettings>>;
};

function SettingRow({
  title,
  description,
  children,
  bordered = true,
}: {
  title: string;
  description: string;
  children: ReactNode;
  bordered?: boolean;
}) {
  return (
    <div className={`d-flex align-items-center justify-content-between mb-3 pb-3 standard-api-setting-row ${bordered ? 'border-bottom' : ''}`}>
      <div>
        <div className="small fw-bold">{title}</div>
        <div className="text-muted standard-api-setting-desc">{description}</div>
      </div>
      {children}
    </div>
  );
}

export function StandardApiTestingRequestSettingsTab({
  requestSettings,
  setRequestSettings,
}: StandardApiTestingRequestSettingsTabProps) {
  return (
    <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 standard-api-scroll-pane standard-api-pane-settings">
      <div className="p-4 standard-api-setting-inner">
        <h6 className="mb-3 text-secondary">General (常规)</h6>

        <SettingRow title="HTTP Version (HTTP 版本)" description="选择发送请求时使用的 HTTP 版本。">
          <div className="standard-api-setting-select-wide">
            <Form.Select
              size="sm"
              value={requestSettings.httpVersion}
              onChange={(e) => setRequestSettings({ ...requestSettings, httpVersion: e.target.value })}
            >
              <option value="HTTP/1.x">HTTP/1.x</option>
              <option value="HTTP/2">HTTP/2</option>
            </Form.Select>
          </div>
        </SettingRow>

        <SettingRow title="Enable SSL certificate verification (启用 SSL 证书验证)" description="发送请求时验证 SSL 证书。验证失败将导致请求中止。">
          <Form.Check type="switch" checked={requestSettings.verifySSL} onChange={(e) => setRequestSettings({ ...requestSettings, verifySSL: e.target.checked })} />
        </SettingRow>

        <SettingRow title="Automatically follow redirects (自动跟随重定向)" description="将 HTTP 3xx 响应作为重定向处理。">
          <Form.Check type="switch" checked={requestSettings.followRedirects} onChange={(e) => setRequestSettings({ ...requestSettings, followRedirects: e.target.checked })} />
        </SettingRow>

        {requestSettings.followRedirects && (
          <div className="d-flex align-items-center justify-content-between mb-3 pb-3 border-bottom ps-4 standard-api-setting-row">
            <div>
              <div className="small fw-bold">Maximum number of redirects (最大重定向次数)</div>
              <div className="text-muted standard-api-setting-desc">设置跟随重定向的最大次数限制。</div>
            </div>
            <div className="standard-api-setting-input-narrow">
              <Form.Control
                size="sm"
                type="number"
                value={requestSettings.maxRedirects}
                onChange={(e) => setRequestSettings({ ...requestSettings, maxRedirects: parseInt(e.target.value) || 0 })}
              />
            </div>
          </div>
        )}

        <SettingRow title="Follow original HTTP Method (保持原 HTTP 方法)" description="重定向时保持原 HTTP 方法，而不是默认的 GET 方法。">
          <Form.Check type="switch" checked={requestSettings.followOriginalHttpMethod} onChange={(e) => setRequestSettings({ ...requestSettings, followOriginalHttpMethod: e.target.checked })} />
        </SettingRow>

        <SettingRow title="Follow Authorization header (保持 Authorization 头)" description="重定向时保留 Authorization 头。">
          <Form.Check type="switch" checked={requestSettings.followAuthorizationHeader} onChange={(e) => setRequestSettings({ ...requestSettings, followAuthorizationHeader: e.target.checked })} />
        </SettingRow>

        <SettingRow title="Remove referer header on redirect (重定向时移除 Referer 头)" description="发生重定向时移除 Referer 头。">
          <Form.Check type="switch" checked={requestSettings.removeRefererHeader} onChange={(e) => setRequestSettings({ ...requestSettings, removeRefererHeader: e.target.checked })} />
        </SettingRow>

        <SettingRow title="Enable strict HTTP parser (启用严格 HTTP 解析)" description="限制包含无效 HTTP 头的响应。">
          <Form.Check type="switch" checked={requestSettings.strictHttpParser} onChange={(e) => setRequestSettings({ ...requestSettings, strictHttpParser: e.target.checked })} />
        </SettingRow>

        <SettingRow title="Encode URL automatically (自动编码 URL)" description="自动编码 URL 路径、查询参数和认证字段。">
          <Form.Check type="switch" checked={requestSettings.encodeUrl} onChange={(e) => setRequestSettings({ ...requestSettings, encodeUrl: e.target.checked })} />
        </SettingRow>

        <SettingRow title="Disable cookie jar (禁用 Cookie Jar)" description="防止此请求使用的 Cookie 被存储到 Cookie Jar 中。">
          <Form.Check type="switch" checked={requestSettings.disableCookieJar} onChange={(e) => setRequestSettings({ ...requestSettings, disableCookieJar: e.target.checked })} />
        </SettingRow>

        <SettingRow title="Request Timeout (请求超时)" description="设置请求超时时间（毫秒，0 表示无限）。">
          <div className="standard-api-setting-input-narrow">
            <Form.Control
              size="sm"
              type="number"
              value={requestSettings.timeout}
              onChange={(e) => setRequestSettings({ ...requestSettings, timeout: parseInt(e.target.value) || 0 })}
            />
          </div>
        </SettingRow>

        <h6 className="mb-3 mt-4 text-secondary">Advanced (高级)</h6>

        <SettingRow title="Use server cipher suite during handshake (握手时使用服务器加密套件)" description="在握手过程中使用服务器的加密套件顺序，而不是客户端的。">
          <Form.Check type="switch" checked={requestSettings.useServerCipherSuite} onChange={(e) => setRequestSettings({ ...requestSettings, useServerCipherSuite: e.target.checked })} />
        </SettingRow>

        <div className="mb-3 pb-3 border-bottom">
          <div className="mb-2">
            <div className="small fw-bold">TLS/SSL protocols disabled during handshake (握手期间禁用的 TLS/SSL 协议)</div>
            <div className="text-muted standard-api-setting-desc">
              指定在握手期间禁用的 SSL 和 TLS 协议版本。所有其他协议将被启用。
            </div>
          </div>
          <Form.Control
            size="sm"
            value={requestSettings.disabledSSLProtocols}
            onChange={(e) => setRequestSettings({ ...requestSettings, disabledSSLProtocols: e.target.value })}
          />
        </div>

        <div className="mb-3 pb-3 border-bottom">
          <div className="mb-2">
            <div className="small fw-bold">Cipher suite selection (加密套件选择)</div>
            <div className="text-muted standard-api-setting-desc">
              SSL 服务器配置文件用于建立安全连接的加密套件顺序。
            </div>
          </div>
          <Form.Control
            as="textarea"
            size="sm"
            placeholder="Enter cipher suites"
            value={requestSettings.cipherSuites}
            onChange={(e) => setRequestSettings({ ...requestSettings, cipherSuites: e.target.value })}
          />
        </div>
      </div>
    </div>
  );
}
