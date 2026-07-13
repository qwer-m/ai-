import type { SavedInterface } from './types';

type ExternalHeader = {
  key?: string;
  value?: string;
  description?: string;
  disabled?: boolean;
};

type ExternalQueryParam = {
  key?: string;
  value?: string;
  description?: string;
};

type ExternalRawOptions = {
  raw?: {
    language?: string;
  };
};

type ExternalBody = {
  mode?: string;
  raw?: string;
  options?: ExternalRawOptions;
  urlencoded?: Array<{ key?: string; value?: string }>;
};

type ExternalUrlObject = {
  raw?: string;
  query?: ExternalQueryParam[];
};

type ExternalRequest = {
  method?: string;
  url?: string | ExternalUrlObject;
  header?: ExternalHeader[];
  body?: ExternalBody;
};

type ExternalCollectionItem = {
  name?: string;
  item?: ExternalCollectionItem[];
  request?: ExternalRequest;
};

type CreateInterfaceFn = (payload: Record<string, unknown>) => Promise<SavedInterface>;

type ImportInterfaceItemsOptions = {
  items: SavedInterface[];
  rootParentId: number | null;
  projectId: number | null;
  createInterface: CreateInterfaceFn;
};

type ImportFilesOptions = {
  files: File[];
  rootParentId: number | null;
  importParsedItems: (items: SavedInterface[], rootParentId: number | null) => Promise<number>;
  onUnsupportedFormat: (fileName: string) => void;
  onParseError: (fileName: string, message: string) => void;
};

function randomNodeId() {
  // 导入阶段使用前端临时 ID 建树，落库后再由后端分配真实 ID。
  return Date.now() + Math.floor(Math.random() * 100000);
}

function parseRequestBody(
  req: ExternalRequest,
  headers: Array<{ key: string; value: string; desc: string }>,
) {
  // 统一把外部集合里的 body 结构映射到平台内部结构。
  let bodyMode = 'none';
  let bodyContent = '';
  let rawType = 'JSON';

  if (!req.body) {
    return { bodyMode, bodyContent, rawType };
  }

  if (req.body.mode === 'raw') {
    bodyMode = 'raw';
    bodyContent = req.body.raw || '';

    const lang = req.body.options?.raw?.language;
    if (lang === 'json') rawType = 'JSON';
    else if (lang === 'javascript') rawType = 'JavaScript';
    else if (lang === 'html') rawType = 'HTML';
    else if (lang === 'xml') rawType = 'XML';
    else rawType = 'Text';

    if (bodyContent.trim() && !headers.some((h) => h.key.toLowerCase() === 'content-type')) {
      if (lang === 'json') {
        headers.push({ key: 'Content-Type', value: 'application/json', desc: 'Auto-generated' });
      } else if (lang === 'html') {
        headers.push({ key: 'Content-Type', value: 'text/html', desc: 'Auto-generated' });
      } else if (lang === 'xml') {
        headers.push({ key: 'Content-Type', value: 'application/xml', desc: 'Auto-generated' });
      }
    }
  } else if (req.body.mode === 'urlencoded') {
    bodyMode = 'raw';
    bodyContent = (req.body.urlencoded || [])
      .map((p) => `${p.key || ''}=${p.value || ''}`)
      .join('&');

    if (bodyContent.trim() && !headers.some((h) => h.key.toLowerCase() === 'content-type')) {
      headers.push({
        key: 'Content-Type',
        value: 'application/x-www-form-urlencoded',
        desc: 'Auto-generated',
      });
    }
  }

  return { bodyMode, bodyContent, rawType };
}

export function parsePostmanItems(items: ExternalCollectionItem[], parentId: number | null): SavedInterface[] {
  // 递归解析 Postman/Apifox 节点，输出前端统一树结构。
  const result: SavedInterface[] = [];

  items.forEach((item) => {
    const id = randomNodeId();

    if (item.item) {
      const folder: SavedInterface = {
        id,
        type: 'folder',
        name: item.name || 'Untitled Folder',
        parentId,
        isOpen: false,
      };
      result.push(folder);
      result.push(...parsePostmanItems(item.item, id));
      return;
    }

    if (!item.request) return;
    const req = item.request;
    const rawUrl =
      typeof req.url === 'string'
        ? req.url
        : typeof req.url === 'object' && req.url
          ? req.url.raw || ''
          : '';

    const headers = (req.header || [])
      .filter((h) => !h.disabled)
      .map((h) => ({
        key: h.key || '',
        value: h.value || '',
        desc: h.description || '',
      }));

    const body = parseRequestBody(req, headers);
    const params =
      typeof req.url === 'object' && req.url?.query
        ? req.url.query.map((q) => ({
            key: q.key || '',
            value: q.value || '',
            desc: q.description || '',
          }))
        : [];

    result.push({
      id,
      type: 'request',
      name: item.name || 'Untitled Request',
      parentId,
      baseUrl: '',
      apiPath: rawUrl,
      method: req.method || 'GET',
      headers,
      params,
      bodyMode: body.bodyMode,
      rawType: body.rawType,
      bodyContent: body.bodyContent,
      testTypes: { functional: true, boundary: false, security: false },
    });
  });

  return result;
}

export async function importInterfaceItemsToBackend({
  items,
  rootParentId,
  projectId,
  createInterface,
}: ImportInterfaceItemsOptions): Promise<number> {
  // 保持现有行为：当前统计口径只计入 request 节点。
  // 说明：这里沿用历史行为，folder 节点不计入 created 计数。
  const idMap = new Map<number, number>();
  let created = 0;

  for (const item of items) {
    const parentId =
      item.parentId === null ? rootParentId : (idMap.get(item.parentId) ?? item.parentId ?? rootParentId);

    const payload: Record<string, unknown> = {
      name: item.name,
      description: item.description || undefined,
      project_id: projectId ?? undefined,
      parent_id: parentId ?? undefined,
      type: item.type,
    };

    if (item.type === 'request') {
      payload.method = item.method || 'GET';
      payload.base_url = item.baseUrl ?? '';
      payload.api_path = item.apiPath ?? '';
      payload.headers = (item.headers || []).filter((h) => h.key || h.value);
      payload.params = (item.params || []).filter((p) => p.key || p.value);
      payload.body_mode = item.bodyMode ?? 'none';
      payload.raw_type = item.rawType ?? 'JSON';
      payload.body_content = item.bodyContent ?? '';
      payload.test_config = {
        requirement: item.requirement ?? '',
        mode: item.mode ?? 'natural',
        testTypes: item.testTypes ?? { functional: true, boundary: false, security: false },
        pre_script: item.preScript ?? '',
        post_script: item.postScript ?? '',
      };

      const createdItem = await createInterface(payload);
      idMap.set(item.id, createdItem.id);
      created += 1;
    }
  }

  return created;
}

export async function importFilesFromCollections({
  files,
  rootParentId,
  importParsedItems,
  onUnsupportedFormat,
  onParseError,
}: ImportFilesOptions): Promise<number> {
  // 多文件导入入口：逐个文件解析、校验并写库，最终返回成功导入数量。
  let importCount = 0;

  for (const file of files) {
    try {
      const text = await file.text();
      const data: unknown = JSON.parse(text);

      // Postman Collection v2.1（包含 info 与 item）
      if (
        typeof data === 'object' &&
        data !== null &&
        'info' in data &&
        'item' in data &&
        Array.isArray((data as { item?: unknown[] }).item)
      ) {
        const parsed = parsePostmanItems(
          (data as { item: ExternalCollectionItem[] }).item,
          null,
        );
        importCount += await importParsedItems(parsed, rootParentId);
      } else if (Array.isArray(data)) {
        // 兼容：直接传 items 数组
        const parsed = parsePostmanItems(data as ExternalCollectionItem[], null);
        if (parsed.length > 0) {
          importCount += await importParsedItems(parsed, rootParentId);
        }
      } else {
        onUnsupportedFormat(file.name);
      }
    } catch (error) {
      onParseError(file.name, error instanceof Error ? error.message : String(error));
    }
  }

  return importCount;
}

export function buildPostmanFolderItems(savedInterfaces: SavedInterface[], parentId: number): unknown[] {
  // 从当前接口树递归构建 Postman Collection 的 item 数组。
  const children = savedInterfaces.filter((item) => item.parentId === parentId);
  return children.map((child) => {
    if (child.type === 'folder') {
      return {
        name: child.name,
        item: buildPostmanFolderItems(savedInterfaces, child.id),
      };
    }

    const headers = (child.headers || [])
      .filter((h) => h.key)
      .map((h) => ({ key: h.key, value: h.value, description: h.desc || '' }));

    const bodyLang = (child.rawType || 'JSON').toLowerCase();
    const hasBody = child.bodyMode === 'raw' && !!child.bodyContent && child.bodyContent.trim().length > 0;
    const body = hasBody
      ? {
          mode: 'raw',
          raw: child.bodyContent,
          options: {
            raw: {
              language:
                bodyLang === 'javascript'
                  ? 'javascript'
                  : bodyLang === 'html'
                    ? 'html'
                    : bodyLang === 'xml'
                      ? 'xml'
                      : 'json',
            },
          },
        }
      : undefined;

    return {
      name: child.name,
      request: {
        method: child.method || 'GET',
        header: headers,
        body,
        url: child.apiPath || '',
      },
    };
  });
}
