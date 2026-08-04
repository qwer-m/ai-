import type {
  BodyMode,
  KeyValueItem,
  RawType,
  SavedInterface,
} from './types';

type ApiInterfaceRecord = {
  id: number;
  type: 'request' | 'folder';
  name: string;
  description: string | null;
  parent_id: number | null;
  base_url: string | null;
  api_path: string | null;
  method: string | null;
  headers: KeyValueItem[] | null;
  params: KeyValueItem[] | null;
  body_mode: BodyMode | null;
  raw_type: RawType | null;
  body_content: string | null;
  test_config: { pre_script?: string; post_script?: string } | null;
};

const BODY_MODES = new Set<BodyMode>([
  'none',
  'form-data',
  'x-www-form-urlencoded',
  'raw',
  'binary',
  'graphql',
]);
const RAW_TYPES = new Set<RawType>(['Text', 'JavaScript', 'JSON', 'HTML', 'XML']);

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function readNullableString(value: unknown, field: string): string | null {
  if (value === null) return null;
  if (typeof value !== 'string') throw new Error(`接口数据字段 ${field} 必须是字符串或 null`);
  return value;
}

function readNullableInteger(value: unknown, field: string): number | null {
  if (value === null) return null;
  if (!Number.isInteger(value)) throw new Error(`接口数据字段 ${field} 必须是整数或 null`);
  return value as number;
}

function readKeyValueRows(value: unknown, field: string): KeyValueItem[] | null {
  if (value === null) return null;
  if (!Array.isArray(value)) throw new Error(`接口数据字段 ${field} 必须是数组或 null`);
  return value.map((row, index) => {
    if (!isRecord(row)) throw new Error(`接口数据字段 ${field}[${index}] 必须是对象`);
    if (typeof row.key !== 'string' || typeof row.value !== 'string' || typeof row.desc !== 'string') {
      throw new Error(`接口数据字段 ${field}[${index}] 缺少 key、value 或 desc`);
    }
    return { key: row.key, value: row.value, desc: row.desc };
  });
}

function readTestConfig(value: unknown): ApiInterfaceRecord['test_config'] {
  if (value === null) return null;
  if (!isRecord(value)) throw new Error('接口数据字段 test_config 必须是对象或 null');
  const config: NonNullable<ApiInterfaceRecord['test_config']> = {};
  if (value.pre_script !== undefined) {
    if (typeof value.pre_script !== 'string') throw new Error('test_config.pre_script 必须是字符串');
    config.pre_script = value.pre_script;
  }
  if (value.post_script !== undefined) {
    if (typeof value.post_script !== 'string') throw new Error('test_config.post_script 必须是字符串');
    config.post_script = value.post_script;
  }
  return config;
}

function readBodyMode(value: unknown): BodyMode | null {
  const mode = readNullableString(value, 'body_mode');
  if (mode === null) return null;
  if (!BODY_MODES.has(mode as BodyMode)) throw new Error(`不支持的 body_mode: ${mode}`);
  return mode as BodyMode;
}

function readRawType(value: unknown): RawType | null {
  const rawType = readNullableString(value, 'raw_type');
  if (rawType === null) return null;
  if (!RAW_TYPES.has(rawType as RawType)) throw new Error(`不支持的 raw_type: ${rawType}`);
  return rawType as RawType;
}

function parseApiInterface(value: unknown): ApiInterfaceRecord {
  if (!isRecord(value)) throw new Error('接口数据必须是对象');
  if (!Number.isInteger(value.id)) throw new Error('接口数据字段 id 必须是整数');
  if (value.type !== 'request' && value.type !== 'folder') throw new Error('接口数据字段 type 无效');
  if (typeof value.name !== 'string' || !value.name.trim()) throw new Error('接口数据字段 name 不能为空');

  return {
    id: value.id as number,
    type: value.type,
    name: value.name,
    description: readNullableString(value.description, 'description'),
    parent_id: readNullableInteger(value.parent_id, 'parent_id'),
    base_url: readNullableString(value.base_url, 'base_url'),
    api_path: readNullableString(value.api_path, 'api_path'),
    method: readNullableString(value.method, 'method'),
    headers: readKeyValueRows(value.headers, 'headers'),
    params: readKeyValueRows(value.params, 'params'),
    body_mode: readBodyMode(value.body_mode),
    raw_type: readRawType(value.raw_type),
    body_content: readNullableString(value.body_content, 'body_content'),
    test_config: readTestConfig(value.test_config),
  };
}

export function parseSavedInterface(value: unknown): SavedInterface {
  const item = parseApiInterface(value);
  return {
    id: item.id,
    type: item.type,
    name: item.name,
    description: item.description ?? undefined,
    parentId: item.parent_id,
    isOpen: false,
    baseUrl: item.base_url ?? undefined,
    apiPath: item.api_path ?? undefined,
    method: item.method ?? undefined,
    headers: item.headers ?? undefined,
    params: item.params ?? undefined,
    bodyMode: item.body_mode ?? undefined,
    rawType: item.raw_type ?? undefined,
    bodyContent: item.body_content ?? undefined,
    testConfig: item.test_config ?? undefined,
    preScript: item.test_config?.pre_script,
    postScript: item.test_config?.post_script,
  };
}

export function parseSavedInterfaceList(value: unknown): SavedInterface[] {
  if (!Array.isArray(value)) throw new Error('接口列表响应必须是数组');
  return value.map(parseSavedInterface);
}
