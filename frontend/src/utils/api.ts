export class APIError extends Error {
  status: number;
  data: any;

  constructor(message: string, status: number, data: any) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export const AUTH_EXPIRED_EVENT = 'auth:expired';

function notifyAuthExpired(): void {
  localStorage.removeItem('token');
  window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
}

function extractErrorMessage(data: any, statusText?: string): string {
  const fallback = statusText || 'Request failed';
  if (data === undefined || data === null) return fallback;
  if (typeof data === 'string') return data.trim() || fallback;
  if (typeof data !== 'object') return String(data);

  const directCandidates = [data.error_message, data.error, data.message, data.msg];
  for (const candidate of directCandidates) {
    if (typeof candidate === 'string' && candidate.trim()) return candidate.trim();
  }

  const detail = data.detail;
  if (typeof detail === 'string' && detail.trim()) return detail.trim();
  if (detail && typeof detail === 'object') {
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

export function getAuthHeaders(): Record<string, string> {
  const token = localStorage.getItem('token');
  // Check if token is valid (simple check)
  if (token && token.trim().length > 0) {
      return { 'Authorization': `Bearer ${token}` };
  }
  return {};
}

function buildRequestConfig(options: RequestInit = {}, includeJsonContentType = true): RequestInit {
  const authHeaders = getAuthHeaders();
  const defaultHeaders: Record<string, string> = includeJsonContentType
    ? {
      'Content-Type': 'application/json',
      ...authHeaders,
    }
    : {
      ...authHeaders,
    };

  const config: RequestInit = {
    ...options,
    headers: {
      ...defaultHeaders,
      ...options.headers,
    },
  };

  if (options.body instanceof FormData) {
    delete (config.headers as any)['Content-Type'];
  }
  return config;
}

async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const config = buildRequestConfig(options, true);

  try {
    const response = await fetch(url, config);
    
    if (response.status === 401) {
      notifyAuthExpired();
    }

    const contentType = response.headers.get('content-type') || '';
    const rawText = await response.text();
    let data: any = null;
    if (rawText) {
      const shouldTryJson = contentType.includes('application/json') || contentType.includes('+json');
      if (shouldTryJson) {
        try {
          data = JSON.parse(rawText);
        } catch {
          data = rawText;
        }
      } else {
        try {
          data = JSON.parse(rawText);
        } catch {
          data = rawText;
        }
      }
    }

    if (!response.ok) {
      const message = extractErrorMessage(data, response.statusText);
      throw new APIError(message, response.status, data);
    }
    
    // Check for application-level error in 200 OK response (common in some backends)
    if (data && typeof data === 'object' && data.error) {
        throw new APIError(data.error, 200, data);
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
    notifyAuthExpired();
  }
  if (!response.ok) {
    const contentType = response.headers.get('content-type') || '';
    const rawText = await response.text();
    let data: any = null;
    if (rawText) {
      try {
        data = JSON.parse(rawText);
      } catch {
        data = rawText;
      }
    }
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
  post: <T>(url: string, body: any) => request<T>(url, { method: 'POST', body: JSON.stringify(body) }),
  put: <T>(url: string, body: any) => request<T>(url, { method: 'PUT', body: JSON.stringify(body) }),
  delete: <T>(url: string) => request<T>(url, { method: 'DELETE' }),
  upload: <T>(url: string, formData: FormData) => {
      return request<T>(url, {
          method: 'POST',
          body: formData
      });
  },
  raw: (url: string, options: RequestInit = {}) => requestRaw(url, options),
  getBlob: (url: string, options: RequestInit = {}) => requestRaw(url, { ...options, method: options.method || 'GET' }).then((r) => r.blob()),
  postBlob: (url: string, body: any, options: RequestInit = {}) => requestRaw(url, {
    ...options,
    method: 'POST',
    body: JSON.stringify(body),
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  }).then((r) => r.blob()),
};
