import type { EnvConfig } from './types';

type KeyValueRow = {
  key: string;
  value: string;
  desc?: string;
};

type FormDataRow = {
  key: string;
  value: string;
  desc?: string;
  type: 'text' | 'file';
  src?: string;
};

type AuthType = 'none' | 'bearer' | 'basic' | 'apikey';

export type ScriptTest = {
  name: string;
  passed: boolean;
  error?: string;
};

export type RuntimeEnvInterface = {
  get: (key: string) => string;
  set: (key: string, value: string) => void;
  unset: (key: string) => void;
};

type BuildDebugRequestPayloadOptions = {
  apiPath: string;
  headers: KeyValueRow[];
  queryParams: KeyValueRow[];
  authType: AuthType;
  authToken: string;
  authBasic: { username: string; password: string };
  authApiKey: { key: string; value: string; addTo: 'header' | 'query' };
  bodyMode: 'none' | 'form-data' | 'x-www-form-urlencoded' | 'raw' | 'binary' | 'graphql';
  bodyContent: string;
  xWwwFormUrlencodedParams: KeyValueRow[];
  formDataParams: FormDataRow[];
  binaryFile: { name: string; data: string } | null;
  graphqlQuery: string;
  graphqlVariables: string;
  substitute: (str: string) => string;
};

type ExecutePostResponseScriptOptions = {
  script: string;
  envInterface: RuntimeEnvInterface;
  response: {
    body: unknown;
    headers: Record<string, unknown>;
    status: number;
    time: number;
  };
};

export function resolveActiveEnv(savedEnvs: EnvConfig[], apiPath: string): EnvConfig | undefined {
  // 优先按 baseUrl 前缀匹配；若 URL 以 {{变量}} 开头，再按变量标签匹配。
  const directMatch = savedEnvs.find((env) => env.baseUrl && apiPath.startsWith(env.baseUrl));
  if (directMatch) return directMatch;

  const tagMatch = apiPath.match(/^(\{\{\s*.+?\s*\}\})/);
  if (!tagMatch) return undefined;
  return savedEnvs.find((env) => env.baseUrl === tagMatch[1]);
}

export function createRuntimeContext(
  activeEnv: EnvConfig | undefined,
  getEnvBaseUrlValue: (key: string) => string,
) {
  // 运行时变量副本：允许 pre/post 脚本读写，不直接污染源状态。
  let runtimeVariables = activeEnv ? [...(activeEnv.variables || [])] : [];

  const envInterface: RuntimeEnvInterface = {
    get: (key: string) => {
      const found = runtimeVariables.find((item) => item.key === key && item.enabled);
      return found ? found.value : getEnvBaseUrlValue(`{{${key}}}`);
    },
    set: (key: string, value: string) => {
      const idx = runtimeVariables.findIndex((item) => item.key === key);
      if (idx >= 0) {
        runtimeVariables[idx] = { ...runtimeVariables[idx], value };
      } else {
        runtimeVariables.push({ key, value, enabled: true });
      }
    },
    unset: (key: string) => {
      runtimeVariables = runtimeVariables.filter((item) => item.key !== key);
    },
  };

  const substitute = (str: string) => {
    if (!str) return str;
    let processed = str;
    runtimeVariables.forEach((item) => {
      if (item.enabled && item.key) {
        processed = processed.replaceAll(`{{${item.key}}}`, item.value);
      }
    });
    return processed;
  };

  return {
    envInterface,
    substitute,
    getRuntimeVariables: () => runtimeVariables,
  };
}

export function executePreRequestScript(script: string, envInterface: RuntimeEnvInterface) {
  // 以 Postman 风格 pm 对象执行前置脚本。
  const pm = {
    environment: envInterface,
    variables: envInterface,
    globals: { get: envInterface.get, set: envInterface.set },
    info: { requestName: 'Current Request' },
  };
  new Function('pm', 'console', script)(pm, console);
}

export function buildDebugRequestPayload(options: BuildDebugRequestPayloadOptions) {
  // 统一组装调试请求负载，保持组件内只有编排逻辑。
  const {
    apiPath,
    headers,
    queryParams,
    authType,
    authToken,
    authBasic,
    authApiKey,
    bodyMode,
    bodyContent,
    xWwwFormUrlencodedParams,
    formDataParams,
    binaryFile,
    graphqlQuery,
    graphqlVariables,
    substitute,
  } = options;

  let fullUrl = substitute(apiPath);
  if (!fullUrl.startsWith('http') && !fullUrl.startsWith('{{')) {
    fullUrl = `http://${fullUrl}`;
  }

  const reqHeaders = headers.reduce((acc, row) => {
    if (row.key) acc[substitute(row.key)] = substitute(row.value);
    return acc;
  }, {} as Record<string, string>);

  if (authType === 'bearer' && authToken) {
    reqHeaders.Authorization = `Bearer ${substitute(authToken)}`;
  } else if (authType === 'basic' && (authBasic.username || authBasic.password)) {
    const user = substitute(authBasic.username);
    const pass = substitute(authBasic.password);
    reqHeaders.Authorization = `Basic ${btoa(`${user}:${pass}`)}`;
  } else if (
    authType === 'apikey' &&
    authApiKey.key &&
    authApiKey.value &&
    authApiKey.addTo === 'header'
  ) {
    reqHeaders[substitute(authApiKey.key)] = substitute(authApiKey.value);
  }

  const reqParams = queryParams.reduce((acc, row) => {
    if (row.key) acc[substitute(row.key)] = substitute(row.value);
    return acc;
  }, {} as Record<string, string>);

  if (
    authType === 'apikey' &&
    authApiKey.key &&
    authApiKey.value &&
    authApiKey.addTo === 'query'
  ) {
    reqParams[substitute(authApiKey.key)] = substitute(authApiKey.value);
  }

  let finalBody: string | undefined;
  let isBase64 = false;

  if (bodyMode === 'raw') {
    finalBody = substitute(bodyContent);
  } else if (bodyMode === 'x-www-form-urlencoded') {
    const params = new URLSearchParams();
    xWwwFormUrlencodedParams.forEach((item) => {
      if (item.key) params.append(substitute(item.key), substitute(item.value));
    });
    finalBody = params.toString();
  } else if (bodyMode === 'form-data') {
    // form-data 使用随机 boundary，与后端调试接口保持兼容。
    const boundary = `----WebKitFormBoundary${Math.random().toString(36).substring(2)}`;
    if (!reqHeaders['content-type'] && !reqHeaders['Content-Type']) {
      reqHeaders['Content-Type'] = `multipart/form-data; boundary=${boundary}`;
    }

    const bodyParts: string[] = [];
    formDataParams.forEach((item) => {
      if (item.key && item.type === 'text') {
        bodyParts.push(
          `--${boundary}\r\nContent-Disposition: form-data; name="${substitute(item.key)}"\r\n\r\n${substitute(item.value)}`,
        );
      } else if (item.key && item.type === 'file' && item.value) {
        const matches = item.value.match(/^data:(.+);base64,(.+)$/);
        if (!matches) return;
        const mime = matches[1];
        const b64Data = matches[2];
        const filename = item.src || 'file';
        bodyParts.push(
          `--${boundary}\r\nContent-Disposition: form-data; name="${substitute(item.key)}"; filename="${filename}"\r\nContent-Type: ${mime}\r\nContent-Transfer-Encoding: base64\r\n\r\n${b64Data}`,
        );
      }
    });

    if (bodyParts.length > 0) {
      bodyParts.push(`--${boundary}--`);
      finalBody = bodyParts.join('\r\n');
    }
  } else if (bodyMode === 'binary') {
    if (binaryFile && binaryFile.data) {
      const matches = binaryFile.data.match(/^data:(.+);base64,(.+)$/);
      finalBody = matches ? matches[2] : binaryFile.data;
      isBase64 = true;
    }
  } else if (bodyMode === 'graphql') {
    const payload = {
      query: substitute(graphqlQuery),
      variables: graphqlVariables ? JSON.parse(substitute(graphqlVariables)) : {},
    };
    finalBody = JSON.stringify(payload);
    if (!reqHeaders['content-type'] && !reqHeaders['Content-Type']) {
      reqHeaders['Content-Type'] = 'application/json';
    }
  }

  return {
    fullUrl,
    reqHeaders,
    reqParams,
    finalBody,
    isBase64,
  };
}

export function executePostResponseScript(options: ExecutePostResponseScriptOptions): ScriptTest[] {
  // 以最小可用的 Postman 运行时（pm/test/expect）执行后置脚本并收集断言结果。
  const { script, envInterface, response } = options;
  const tests: ScriptTest[] = [];

  const expect = (actual: unknown) => ({
    to: {
      eql: (expected: unknown) => {
        if (JSON.stringify(actual) !== JSON.stringify(expected)) {
          throw new Error(`Expected ${JSON.stringify(expected)} but got ${JSON.stringify(actual)}`);
        }
      },
      equal: (expected: unknown) => {
        if (actual != expected) {
          throw new Error(`Expected ${String(expected)} but got ${String(actual)}`);
        }
      },
      include: (expected: unknown) => {
        if (!String(actual).includes(String(expected))) {
          throw new Error(`Expected ${String(actual)} to include ${String(expected)}`);
        }
      },
      be: {
        get ok() {
          if (!actual) throw new Error('Expected truthy');
          return this;
        },
        get true() {
          if (actual !== true) throw new Error('Expected true');
          return this;
        },
        get false() {
          if (actual !== false) throw new Error('Expected false');
          return this;
        },
      },
      have: {
        property: (prop: string) => {
          if ((actual as Record<string, unknown>)?.[prop] === undefined) {
            throw new Error(`Expected property ${prop}`);
          }
        },
      },
    },
  });

  const pm = {
    environment: envInterface,
    variables: envInterface,
    globals: { get: envInterface.get, set: envInterface.set },
    response: {
      json: () => {
        try {
          return typeof response.body === 'string' ? JSON.parse(response.body) : response.body;
        } catch {
          return {};
        }
      },
      text: () => (typeof response.body === 'object' ? JSON.stringify(response.body) : String(response.body)),
      headers: response.headers,
      code: response.status,
      responseTime: response.time,
      to: {
        have: {
          status: (code: number) => {
            if (response.status !== code) {
              throw new Error(`Expected status ${code} but got ${response.status}`);
            }
          },
          header: (key: string) => {
            const hasHeader = Object.keys(response.headers).some(
              (header) => header.toLowerCase() === key.toLowerCase(),
            );
            if (!hasHeader) throw new Error(`Expected header ${key}`);
          },
          jsonBody: () => {
            try {
              if (typeof response.body === 'string') JSON.parse(response.body);
            } catch {
              throw new Error('Expected JSON body');
            }
          },
        },
      },
    },
    test: (name: string, fn: () => void) => {
      try {
        fn();
        tests.push({ name, passed: true });
      } catch (error) {
        tests.push({
          name,
          passed: false,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    },
    expect,
    info: { requestName: 'Current Request' },
  };

  new Function('pm', 'console', script)(pm, console);
  return tests;
}
