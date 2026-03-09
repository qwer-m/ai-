import { useCallback, type Dispatch, type SetStateAction } from 'react';
import { api } from '../../utils/api';
import type { SavedInterface } from './types';

type KVRow = { key: string; value: string; desc: string };
type BodyMode = 'none' | 'form-data' | 'x-www-form-urlencoded' | 'raw' | 'binary' | 'graphql';
type RawType = 'Text' | 'JavaScript' | 'JSON' | 'HTML' | 'XML';

type UseInterfaceCrudParams = {
  projectId: number | null;
  selectedId: number | null;
  savedInterfaces: SavedInterface[];
  setSavedInterfaces: Dispatch<SetStateAction<SavedInterface[]>>;
  apiPath: string;
  method: string;
  queryParams: KVRow[];
  headers: KVRow[];
  bodyMode: BodyMode;
  rawType: RawType;
  bodyContent: string;
  translateError: (error: any) => Promise<string>;
};

function mapApiInterfaceToSaved(i: any): SavedInterface {
  return {
    id: i.id,
    type: i.type,
    name: i.name,
    description: i.description,
    parentId: i.parent_id,
    isOpen: false,
    baseUrl: i.base_url,
    apiPath: i.api_path,
    method: i.method,
    headers: i.headers,
    params: i.params,
    bodyMode: i.body_mode,
    rawType: i.raw_type,
    bodyContent: i.body_content,
    testConfig: i.test_config,
    requirement: i.test_config?.requirement,
    mode: i.test_config?.mode,
    testTypes: i.test_config?.testTypes,
    preScript: i.test_config?.pre_script,
    postScript: i.test_config?.post_script,
  };
}

export function useInterfaceCrud({
  projectId,
  selectedId,
  savedInterfaces,
  setSavedInterfaces,
  apiPath,
  method,
  queryParams,
  headers,
  bodyMode,
  rawType,
  bodyContent,
  translateError,
}: UseInterfaceCrudParams) {
  // 读取后端接口树并映射为前端统一结构。
  const fetchInterfaces = useCallback(async () => {
    try {
      const url = projectId
        ? `/api/standard/interfaces?project_id=${projectId}`
        : '/api/standard/interfaces';
      const res = await api.get<any[]>(url);
      if (res) {
        setSavedInterfaces(res.map(mapApiInterfaceToSaved));
      }
    } catch (error) {
      console.error('Failed to fetch interfaces:', error);
    }
  }, [projectId, setSavedInterfaces]);

  // 创建目录节点，成功后刷新接口树。
  const createFolder = useCallback(
    async (parentId: number | null = null) => {
      const name = prompt('请输入文件夹名称:', '新建文件夹');
      if (!name) return;

      try {
        await api.post('/api/standard/interfaces', {
          name,
          type: 'folder',
          project_id: projectId,
          parent_id: parentId,
        });
        await fetchInterfaces();
      } catch (error) {
        const msg = await translateError(error);
        alert(`创建文件夹失败: ${msg}`);
      }
    },
    [fetchInterfaces, projectId, translateError],
  );

  // 创建请求节点；返回创建后的节点，调用方可决定是否立即加载该节点。
  const createInterface = useCallback(
    async (targetParentId?: number | null) => {
      let parentId = targetParentId;
      if (parentId === undefined) {
        parentId = null;
        if (selectedId) {
          const existing = savedInterfaces.find((item) => item.id === selectedId);
          if (existing) {
            parentId = existing.type === 'folder' ? existing.id : existing.parentId;
          }
        }
      }

      try {
        const res = await api.post<any>('/api/standard/interfaces', {
          name: selectedId === null && apiPath ? apiPath.split('/').pop() || 'New Request' : 'New Request',
          type: 'request',
          project_id: projectId,
          parent_id: parentId,
          method: selectedId === null && method ? method : 'GET',
          api_path: selectedId === null && apiPath ? apiPath : '',
          params: selectedId === null && queryParams ? queryParams : [],
          headers: selectedId === null && headers ? headers : [],
          body_mode: selectedId === null && bodyMode ? bodyMode : 'raw',
          raw_type: selectedId === null && rawType ? rawType : 'JSON',
          body_content: selectedId === null && bodyContent ? bodyContent : '',
        });

        if (!res) return null;
        const newItem = mapApiInterfaceToSaved(res);
        setSavedInterfaces((prev) => [...prev, newItem]);

        if (parentId) {
          setSavedInterfaces((prev) =>
            prev.map((item) => (item.id === parentId ? { ...item, isOpen: true } : item)),
          );
        }

        return newItem;
      } catch (error) {
        const msg = await translateError(error);
        alert(`创建接口失败: ${msg}`);
        return null;
      }
    },
    [
      apiPath,
      bodyContent,
      bodyMode,
      headers,
      method,
      projectId,
      queryParams,
      rawType,
      savedInterfaces,
      selectedId,
      setSavedInterfaces,
      translateError,
    ],
  );

  // 通用更新器：先乐观更新，再写后端，失败时回滚。
  const updateInterface = useCallback(
    async (id: number, updates: any) => {
      setSavedInterfaces((prev) =>
        prev.map((item) => {
          if (item.id !== id) return item;
          const next = { ...item };
          if (updates.parent_id !== undefined) next.parentId = updates.parent_id;
          if (updates.name !== undefined) next.name = updates.name;
          if (updates.base_url !== undefined) next.baseUrl = updates.base_url;
          if (updates.method !== undefined) next.method = updates.method;
          if (updates.api_path !== undefined) next.apiPath = updates.api_path;
          if (updates.description !== undefined) next.description = updates.description;
          return next;
        }),
      );

      try {
        await api.put(`/api/standard/interfaces/${id}`, updates);
      } catch (error) {
        console.error('Update failed', error);
        await fetchInterfaces();
      }
    },
    [fetchInterfaces, setSavedInterfaces],
  );

  return {
    fetchInterfaces,
    createFolder,
    createInterface,
    updateInterface,
  };
}
