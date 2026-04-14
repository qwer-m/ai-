export class APIError extends Error {
  status: number;
  data: any;

  constructor(message: string, status: number, data: any) {
    super(message);
    this.status = status;
    this.data = data;
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
        localStorage.removeItem('token');
        // Optional: Redirect to login or dispatch event
        // window.location.href = '/login'; 
        // We'll let the UI handle the error or redirect
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
      const message =
        data && typeof data === 'object'
          ? (data.error || data.detail || data.message || response.statusText || 'Request failed')
          : (typeof data === 'string' && data.trim()
              ? data
              : (response.statusText || 'Request failed'));
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
    localStorage.removeItem('token');
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
      contentType.includes('application/json') && data && typeof data === 'object'
        ? (data.error || data.detail || data.message || response.statusText || 'Request failed')
        : (typeof data === 'string' && data.trim()
            ? data
            : (response.statusText || 'Request failed'));
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
