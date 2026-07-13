import { Nav } from 'react-bootstrap';
import { FaRobot } from 'react-icons/fa';
import type { ResponsePanelProps } from './ResponsePanel.types';
import { getRawBodyText, ResponseStatusBadge } from './ResponsePanel.utils';

type Props = Pick<
  ResponsePanelProps,
  | 'responseTab'
  | 'setResponseTab'
  | 'responseDetailedCookies'
  | 'responseCookies'
  | 'responseHeaders'
  | 'responseStatus'
  | 'responseTime'
  | 'responseBody'
>;

export function ResponsePanelHeader({
  responseTab,
  setResponseTab,
  responseDetailedCookies,
  responseCookies,
  responseHeaders,
  responseStatus,
  responseTime,
  responseBody,
}: Props) {
  const cookieCount = Object.keys(responseDetailedCookies).length || Object.keys(responseCookies).length;

  return (
    <div className="px-3 py-1 border-bottom d-flex justify-content-between align-items-center flex-shrink-0 standard-api-response-head">
      <Nav
        variant="underline"
        activeKey={responseTab}
        onSelect={(key) => setResponseTab((key as Props['responseTab']) || 'body')}
        className="small custom-nav-tabs standard-api-response-tabs"
      >
        <Nav.Item>
          <Nav.Link eventKey="body" className={responseTab === 'body' ? 'active' : ''}>
            响应体 (Body)
          </Nav.Link>
        </Nav.Item>
        <Nav.Item>
          <Nav.Link eventKey="cookies" className={responseTab === 'cookies' ? 'active' : ''}>
            Cookies <span className="text-muted">({cookieCount})</span>
          </Nav.Link>
        </Nav.Item>
        <Nav.Item>
          <Nav.Link eventKey="headers" className={responseTab === 'headers' ? 'active' : ''}>
            响应头 (Headers) <span className="text-muted">({Object.keys(responseHeaders).length})</span>
          </Nav.Link>
        </Nav.Item>
        <Nav.Item>
          <Nav.Link eventKey="test_results" className={responseTab === 'test_results' ? 'active' : ''}>
            测试结果
          </Nav.Link>
        </Nav.Item>
        <Nav.Item>
          <Nav.Link eventKey="report" className={responseTab === 'report' ? 'text-primary active' : 'text-primary'}>
            <FaRobot className="me-1" />
            AI 分析报告
          </Nav.Link>
        </Nav.Item>
      </Nav>

      <div className="d-flex gap-3 align-items-center small text-secondary standard-api-response-metrics">
        <span>
          状态: <ResponseStatusBadge status={responseStatus} />
        </span>
        <span>
          耗时: <span className="text-dark standard-api-metric-value">{responseTime ? `${responseTime} ms` : '---'}</span>
        </span>
        <span>
          大小:{' '}
          <span className="text-dark standard-api-metric-value">
            {responseBody ? `${getRawBodyText(responseBody).length} B` : '---'}
          </span>
        </span>
      </div>
    </div>
  );
}
