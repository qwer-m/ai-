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

      </div>
    </div>
  );
}
