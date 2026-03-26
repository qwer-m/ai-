import { api } from '../../utils/api';

const containsChinese = (text: string) => /[\u4e00-\u9fff]/.test(text);
const containsEnglish = (text: string) => /[A-Za-z]/.test(text);

export const extractErrorText = (error: unknown): string => {
  const err = error as any;
  if (!err) return '';
  if (typeof err === 'string') return err;
  if (err?.data?.error) return String(err.data.error);
  if (err?.data?.detail) return String(err.data.detail);
  if (err?.data?.message) return String(err.data.message);
  if (err?.message) return String(err.message);
  return String(err);
};

export const localizeConfigError = (raw: string): string => {
  if (!raw) return '发生未知错误，请稍后重试。';
  if (containsChinese(raw) && !containsEnglish(raw)) return raw;

  const directReplacements: Array<[RegExp, string]> = [
    [/OCR Error:/gi, 'OCR错误：'],
    [/OCR Exception:/gi, 'OCR异常：'],
    [/InvalidParameter/gi, '参数错误'],
    [/InternalError\.Algo\.InvalidParameter/gi, '内部算法参数错误'],
    [/The provided URL does not appear to be valid\./gi, '提供的URL看起来无效。'],
    [/Ensure it is correctly formatted\./gi, '请确认URL格式正确。'],
    [/url error,\s*please check url!?/gi, 'URL地址错误，请检查链接是否可访问。'],
    [/For details,\s*see:\s*https?:\/\/\S+/gi, '详情请参考阿里云错误码文档。'],
    [/content parameter's length invalid, please check the request parameters\./gi, 'content参数长度不合法，请检查请求参数。'],
    [/Requests rate limit exceeded, please try again later\./gi, '请求频率超限，请稍后重试。'],
    [/\<\s*400\s*\>/gi, '(HTTP 400)'],
  ];
  let normalized = raw;
  for (const [pattern, value] of directReplacements) {
    normalized = normalized.replace(pattern, value);
  }

  if (containsChinese(normalized) && !containsEnglish(normalized)) {
    return normalized;
  }

  const lower = normalized.toLowerCase();
  const mapping: Array<[string, string]> = [
    ['ssl: unexpected_eof_while_reading', '与云端服务的 SSL 握手异常，请检查网络、代理或系统时间。'],
    ['certificate verify failed', 'SSL 证书校验失败，请检查系统时间、证书链或代理设置。'],
    ['httpsconnectionpool', '连接云端服务失败，请检查网络或代理配置。'],
    ['max retries exceeded', '连接云端服务失败（多次重试未成功），请检查网络后重试。'],
    ['failed to establish a new connection', '无法建立网络连接，请检查网络或防火墙设置。'],
    ['name or service not known', '域名解析失败，请检查 DNS 或网络配置。'],
    ['temporary failure in name resolution', '域名解析失败，请稍后重试。'],
    ['connection refused', '连接被拒绝，请确认服务地址和端口是否可用。'],
    ['econnrefused', '连接被拒绝，请确认服务地址和端口是否可用。'],
    ['timed out', '请求超时，请检查网络后重试。'],
    ['timeout', '请求超时，请检查网络后重试。'],
    ['unauthorized', '鉴权失败，请检查 API Key 是否正确。'],
    ['invalid api key', 'API Key 无效，请重新填写。'],
    ['insufficient_quota', '账户余额不足，请检查余额或配额限制。'],
    ['quota', '账户余额不足或已达到上限，请检查配置。'],
    ['429', '请求过于频繁，请稍后重试。'],
    ['500', '服务端异常，请稍后重试。'],
    ['502', '网关异常，请稍后重试。'],
    ['503', '服务暂不可用，请稍后重试。'],
    ['504', '网关超时，请稍后重试。'],
  ];

  for (const [key, value] of mapping) {
    if (lower.includes(key)) return value;
  }
  return normalized !== raw ? normalized : '连接校验失败，请稍后重试。';
};

export const translateConfigError = async (error: unknown): Promise<string> => {
  const raw = extractErrorText(error).trim();
  if (!raw) return '发生未知错误，请稍后重试。';
  if (containsChinese(raw) && !containsEnglish(raw)) return raw;

  try {
    const translated = await api.post<{ message?: string }>('/api/error/translate', { error: raw });
    const msg = String(translated?.message || '').trim();
    if (msg && containsChinese(msg)) {
      return msg;
    }
  } catch {
    // 翻译接口失败时走本地兜底，保证前端提示仍为中文。
  }

  return localizeConfigError(raw);
};

