import { Badge } from 'react-bootstrap';
import type { ResponseFormat } from './ResponsePanel.types';

export const responseFormatOptions: Array<{
  value: ResponseFormat;
  label: string;
  glyph: string;
  dividerBefore?: boolean;
}> = [
  { value: 'JSON', label: 'JSON', glyph: '{}' },
  { value: 'XML', label: 'XML', glyph: '</>' },
  { value: 'HTML', label: 'HTML', glyph: 'HTML' },
  { value: 'JavaScript', label: 'JavaScript', glyph: 'JS' },
  { value: 'Raw', label: 'Raw', glyph: 'T', dividerBefore: true },
  { value: 'Hex', label: 'Hex', glyph: '0x' },
  { value: 'Base64', label: 'Base64', glyph: '64' },
];

export function getRawBodyText(responseBody: unknown) {
  if (!responseBody) return '';
  return typeof responseBody === 'object' ? JSON.stringify(responseBody) : String(responseBody);
}

export function getPrettyJsonText(responseBody: unknown) {
  if (!responseBody) return '';
  if (typeof responseBody === 'object') return JSON.stringify(responseBody, null, 2);

  try {
    return JSON.stringify(JSON.parse(String(responseBody)), null, 2);
  } catch {
    return String(responseBody);
  }
}

export function getDisplayBodyText(responseBody: unknown, responseFormat: ResponseFormat) {
  const raw = getRawBodyText(responseBody);
  if (!raw) return '';

  if (responseFormat === 'Base64') {
    try {
      return btoa(unescape(encodeURIComponent(raw)));
    } catch {
      return 'Base64 编码失败';
    }
  }

  if (responseFormat === 'Hex') {
    let hex = '';
    for (let index = 0; index < raw.length; index += 1) {
      hex += raw.charCodeAt(index).toString(16).padStart(2, '0');
    }
    return hex;
  }

  return raw;
}

export function KeyValueTable({
  entries,
  columns,
  emptyText,
}: {
  entries: Array<[string, unknown]>;
  columns: [string, string];
  emptyText: string;
}) {
  if (entries.length === 0) {
    return <div className="text-muted small mb-3">{emptyText}</div>;
  }

  return (
    <table className="table table-sm table-hover table-bordered mb-0 small">
      <thead className="bg-light">
        <tr>
          <th className="ps-3 border-0">{columns[0]}</th>
          <th className="border-0">{columns[1]}</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([key, value]) => (
          <tr key={key}>
            <td className="ps-3 text-secondary standard-api-table-key">
              {key}
            </td>
            <td className="font-monospace text-break text-dark">{String(value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function CookieTable({
  rows,
  detailed = false,
}: {
  rows: Array<[string, any]>;
  detailed?: boolean;
}) {
  return detailed ? (
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
        {rows.map(([key, value]) => (
          <tr key={key}>
            <td className="ps-3 text-secondary standard-api-table-key">
              {key}
            </td>
            <td className="font-monospace text-break text-dark standard-api-cookie-value" title={value.value}>
              {value.value}
            </td>
            <td className="text-dark">{value.domain}</td>
            <td className="text-dark">{value.path}</td>
            <td className="text-dark">{value.expires ? new Date(value.expires * 1000).toLocaleString() : 'Session'}</td>
            <td className="text-dark">{value.secure ? 'Yes' : 'No'}</td>
            <td className="text-dark">{value.httpOnly ? 'Yes' : 'No'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  ) : (
    <table className="table table-sm table-hover table-bordered mb-0 small">
      <thead className="bg-light">
        <tr>
          <th className="ps-3 border-0">Name</th>
          <th className="border-0">Value</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([key, value]) => (
          <tr key={key}>
            <td className="ps-3 text-secondary standard-api-table-key">
              {key}
            </td>
            <td className="font-monospace text-break text-dark">{String(value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ResponseStatusBadge({ status }: { status: number | null }) {
  return (
    <span className={status === 200 ? 'text-success standard-api-fw-600' : status ? 'text-danger standard-api-fw-600' : 'standard-api-fw-600'}>
      {status || '---'}
    </span>
  );
}

export function ResponseCountBadge({ count }: { count: number }) {
  return <Badge bg="secondary">{count}</Badge>;
}
