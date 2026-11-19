type JsonObject = Record<string, unknown>;

export function readObject(value: unknown, path: string): JsonObject {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error(`${path} 应为对象`);
  }
  return value as JsonObject;
}

export function readArray(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${path} 应为数组`);
  return value;
}

export function readString(value: unknown, path: string): string {
  if (typeof value !== 'string') throw new Error(`${path} 应为字符串`);
  return value;
}

export function readNullableString(value: unknown, path: string): string | null {
  if (value === null || value === undefined) return null;
  return readString(value, path);
}

export function readNumber(value: unknown, path: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`${path} 应为有效数字`);
  }
  return value;
}

export function readNullableNumber(value: unknown, path: string): number | null {
  if (value === null || value === undefined) return null;
  return readNumber(value, path);
}

export function readBoolean(value: unknown, path: string): boolean {
  if (typeof value !== 'boolean') throw new Error(`${path} 应为布尔值`);
  return value;
}

export function readStringArray(value: unknown, path: string): string[] {
  return readArray(value, path).map((item, index) => readString(item, `${path}[${index}]`));
}

export function readStringMap(value: unknown, path: string): Record<string, string> {
  const object = readObject(value, path);
  return Object.fromEntries(
    Object.entries(object).map(([key, item]) => [key, readString(item, `${path}.${key}`)]),
  );
}

