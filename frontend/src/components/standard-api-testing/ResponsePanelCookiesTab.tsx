import type { ResponsePanelProps } from './ResponsePanel.types';
import { CookieTable } from './ResponsePanel.utils';

type Props = Pick<ResponsePanelProps, 'responseDetailedCookies' | 'responseCookies' | 'sentCookies'>;

export function ResponsePanelCookiesTab({ responseDetailedCookies, responseCookies, sentCookies }: Props) {
  return (
    <div className="flex-grow-1 overflow-auto custom-scrollbar p-3 standard-api-panel-scroll">
      <h6 className="text-secondary border-bottom pb-2 mb-3">响应 Cookies</h6>
      {Object.keys(responseDetailedCookies).length > 0 ? (
        <CookieTable rows={Object.entries(responseDetailedCookies)} detailed />
      ) : Object.keys(responseCookies).length > 0 ? (
        <CookieTable rows={Object.entries(responseCookies)} />
      ) : (
        <div className="text-muted small mb-3">无响应 Cookies</div>
      )}

      <h6 className="text-secondary border-bottom pb-2 mb-3 mt-4">请求 Cookies (已发送)</h6>
      {Object.keys(sentCookies).length > 0 ? (
        <CookieTable rows={Object.entries(sentCookies)} />
      ) : (
        <div className="text-muted small">无请求 Cookies</div>
      )}
    </div>
  );
}
