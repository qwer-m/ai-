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
  return token ? { 'Authorization': `Bearer ${token}` } : {};
}

async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const defaultHeaders: Record<string, string> = {
    'Content-Type': 'application/json',
    ...getAuthHeaders(),
  };

  const config = {
    ...options,
    headers: {
      ...defaultHeaders,
      ...options.headers,
    },
  };

  // If uploading file (FormData), remove Content-Type to let browser set it
  if (options.body instanceof FormData) {
      delete (config.headers as any)['Content-Type'];
  }

  try {
    const response = await fetch(url, config);
    
    if (response.status === 401) {
        localStorage.removeItem('token');
        // Optional: Redirect to login or dispatch event
        // window.location.href = '/login'; 
        // We'll let the UI handle the error or redirect
    }

    const data = await response.json();

    if (!response.ok) {
      throw new APIError(data.error || data.detail || data.message || 'Request failed', response.status, data);
    }
    
    // Check for application-level error in 200 OK response (common in some backends)
    if (data.error) {
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
  }
};
