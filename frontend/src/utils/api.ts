class APIError extends Error {
  status: number;
  data: unknown;

  constructor(message: string, status: number, data: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export const AUTH_UNAUTHORIZED_EVENT = 'app:auth-unauthorized';

function handleUnauthorized() {
  localStorage.removeItem('token');
  window.dispatchEvent(new Event(AUTH_UNAUTHORIZED_EVENT));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function extractErrorMessage(data: unknown, statusText?: string): string {
  const fallback = statusText || 'Request failed';
  if (data === undefined || data === null) return fallback;
  if (typeof data === 'string') return data.trim() || fallback;
  if (!isRecord(data)) return String(data);

  const directCandidates = [data.error_message, data.error, data.message, data.msg];
  for (const candidate of directCandidates) {
    if (typeof candidate === 'string' && candidate.trim()) return candidate.trim();
  }

  const detail = data.detail;
  if (typeof detail === 'string' && detail.trim()) return detail.trim();
  if (isRecord(detail)) {
    const detailCandidates = [
      detail.error_message,
      detail.error,
      detail.message,
      detail.msg,
      detail.detail,
    ];
    for (const candidate of detailCandidates) {
      if (typeof candidate === 'string' && candidate.trim()) {
        const code = typeof detail.error_code === 'string' ? detail.error_code.trim() : '';
        return code ? `${candidate.trim()} (${code})` : candidate.trim();
      }
    }
    if (typeof detail.error_code === 'string' && detail.error_code.trim()) {
      return detail.error_code.trim();
    }
  }

  try {
    return JSON.stringify(data);
  } catch {
    return fallback;
  }
}

function getAuthToken(): string | null {
  const token = localStorage.getItem('token');
  return token?.trim() || null;
}

function buildRequestConfig(options: RequestInit = {}, includeJsonContentType = true): RequestInit {
  const headers = new Headers(options.headers);
  const token = getAuthToken();
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  if (options.body instanceof FormData) {
    headers.delete('Content-Type');
  } else if (includeJsonContentType && options.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  return { ...options, headers };
}

function parseResponseText(rawText: string): unknown {
  if (!rawText) return null;
  try {
    const parsed: unknown = JSON.parse(rawText);
    return parsed;
  } catch {
    return rawText;
  }
}

async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const config = buildRequestConfig(options, true);

  try {
    const response = await fetch(url, config);
    
    if (response.status === 401) {
        handleUnauthorized();
    }

    const rawText = await response.text();
    const data = parseResponseText(rawText);

    if (!response.ok) {
      const message = extractErrorMessage(data, response.statusText);
      throw new APIError(message, response.status, data);
    }
    
    if (isRecord(data) && data.error) {
      throw new APIError(extractErrorMessage(data), 200, data);
    }

    return data as T;
  } catch (error) {
    if (error instanceof APIError) {
      throw error;
    }
    throw new Error(error instanceof Error ? error.message : String(error));
  }
}

async function requestRaw(url: string, options: RequestInit = {}): Promise<Response> {
  const config = buildRequestConfig(options, false);
  const response = await fetch(url, config);

  if (response.status === 401) {
    handleUnauthorized();
  }
  if (!response.ok) {
    const contentType = response.headers.get('content-type') || '';
    const rawText = await response.text();
    const data = parseResponseText(rawText);
    const message =
      contentType.includes('application/json')
        ? extractErrorMessage(data, response.statusText)
        : (typeof data === 'string' && data.trim()
            ? data
            : extractErrorMessage(data, response.statusText));
    throw new APIError(message, response.status, data);
  }
  return response;
}

export const api = {
  get: <T>(url: string) => request<T>(url, { method: 'GET' }),
  post: <T>(url: string, body: unknown) => request<T>(url, { method: 'POST', body: JSON.stringify(body) }),
  put: <T>(url: string, body: unknown) => request<T>(url, { method: 'PUT', body: JSON.stringify(body) }),
  delete: <T>(url: string) => request<T>(url, { method: 'DELETE' }),
  upload: <T>(url: string, formData: FormData) => {
      return request<T>(url, {
          method: 'POST',
          body: formData
      });
  },
  raw: (url: string, options: RequestInit = {}) => requestRaw(url, options),
  getBlob: (url: string, options: RequestInit = {}) => requestRaw(url, { ...options, method: options.method || 'GET' }).then((r) => r.blob()),
  postBlob: (url: string, body: unknown, options: RequestInit = {}) => {
    const headers = new Headers(options.headers);
    if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    return requestRaw(url, {
      ...options,
      method: 'POST',
      body: JSON.stringify(body),
      headers,
    }).then((response) => response.blob());
  },
};
