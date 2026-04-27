import { api } from '../../../utils/api';

export const getErrorText = (error: unknown) => {
  if (!error) return '';
  if (typeof error === 'string') return error;

  const err = error as {
    data?: { error?: unknown; detail?: unknown; message?: unknown };
    message?: unknown;
  };

  if (err?.data?.error) return String(err.data.error);
  if (err?.data?.detail) return String(err.data.detail);
  if (err?.data?.message) return String(err.data.message);
  if (err?.message) return String(err.message);

  try {
    return JSON.stringify(error);
  } catch {
    return String(error);
  }
};

export const translateError = async (error: unknown) => {
  const raw = getErrorText(error);
  try {
    const res = await api.post<{ message?: string }>('/api/error/translate', { error: raw });
    return res?.message ? String(res.message) : raw;
  } catch {
    return raw;
  }
};

export const getMethodColor = (method: string) => {
  switch (method) {
    case 'GET':
      return '#198754';
    case 'POST':
      return '#8B4513';
    case 'PUT':
      return '#6f42c1';
    case 'DELETE':
      return '#b02a37';
    default:
      return '#6c757d';
  }
};
