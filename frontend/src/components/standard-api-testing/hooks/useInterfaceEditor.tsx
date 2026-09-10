import { useState, type Dispatch, type ReactElement, type SetStateAction } from 'react';
import { api } from '../../../utils/api';
import { parseSavedInterface } from '../utils/interfaceContract';
import type { SavedInterface, StandardInterfaceUpdate } from '../utils/types';

type KVRow = { key: string; value: string; desc: string };
type BodyMode = 'none' | 'form-data' | 'x-www-form-urlencoded' | 'raw' | 'binary' | 'graphql';
type RawType = 'Text' | 'JavaScript' | 'JSON' | 'HTML' | 'XML';

type SaveForm = {
  name: string;
  description: string;
  parentId: number | null;
};

type InterfaceDraft = {
  apiPath: string;
  method: string;
  headers: KVRow[];
  params: KVRow[];
  bodyMode: BodyMode;
  rawType: RawType;
  bodyContent: string;
  preRequestScript: string;
  postResponseScript: string;
  baseUrl?: string;
};

type UseInterfaceEditorParams = {
  projectId: number | null;
  savedInterfaces: SavedInterface[];
  setSavedInterfaces: Dispatch<SetStateAction<SavedInterface[]>>;
  selectedId: number | null;
  setSelectedId: Dispatch<SetStateAction<number | null>>;
  apiPath: string;
  setApiPath: Dispatch<SetStateAction<string>>;
  method: string;
  setMethod: Dispatch<SetStateAction<string>>;
  headers: KVRow[];
  setHeaders: Dispatch<SetStateAction<KVRow[]>>;
  queryParams: KVRow[];
  setQueryParams: Dispatch<SetStateAction<KVRow[]>>;
  bodyMode: BodyMode;
  setBodyMode: Dispatch<SetStateAction<BodyMode>>;
  rawType: RawType;
  setRawType: Dispatch<SetStateAction<RawType>>;
  bodyContent: string;
  setBodyContent: Dispatch<SetStateAction<string>>;
  preRequestScript: string;
  setPreRequestScript: Dispatch<SetStateAction<string>>;
  postResponseScript: string;
  setPostResponseScript: Dispatch<SetStateAction<string>>;
  setResponseStatus: Dispatch<SetStateAction<number | null>>;
  setResponseTime: Dispatch<SetStateAction<number | null>>;
  setResponseBody: Dispatch<SetStateAction<string | null>>;
  setResponseHeaders: Dispatch<SetStateAction<Record<string, string>>>;
  setResponseCookies: Dispatch<SetStateAction<Record<string, string>>>;
  updateInterface: (id: number, updates: StandardInterfaceUpdate) => Promise<void>;
  fetchInterfaces: () => Promise<void> | void;
  translateError: (error: unknown) => Promise<string>;
  onLog: (msg: string) => void;
};

export function useInterfaceEditor(params: UseInterfaceEditorParams) {
  const {
    projectId,
    savedInterfaces,
    setSavedInterfaces,
    selectedId,
    setSelectedId,
    apiPath,
    setApiPath,
    method,
    setMethod,
    headers,
    setHeaders,
    queryParams,
    setQueryParams,
    bodyMode,
    setBodyMode,
    rawType,
    setRawType,
    bodyContent,
    setBodyContent,
    preRequestScript,
    setPreRequestScript,
    postResponseScript,
    setPostResponseScript,
    setResponseStatus,
    setResponseTime,
    setResponseBody,
    setResponseHeaders,
    setResponseCookies,
    updateInterface,
    fetchInterfaces,
    translateError,
    onLog,
  } = params;

  const [drafts, setDrafts] = useState<Record<number, InterfaceDraft>>({});
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [saveForm, setSaveForm] = useState<SaveForm>({
    name: '',
    description: '',
    parentId: null,
  });
  const [editingTargetId, setEditingTargetId] = useState<number | null>(null);

  const renderFolderOptions = (parentId: number | null = null, depth = 0): ReactElement[] => {
    const items = savedInterfaces.filter((item) => item.type === 'folder' && item.parentId === parentId);
    let options: ReactElement[] = [];

    items.forEach((item) => {
      options.push(
        <option key={item.id} value={item.id}>
          {'\u00A0'.repeat(depth * 4)}
          {item.name}
        </option>,
      );
      options = [...options, ...renderFolderOptions(item.id, depth + 1)];
    });

    return options;
  };

  const handleEditFolder = (item: SavedInterface) => {
    setEditingTargetId(item.id);
    setSaveForm({
      name: item.name,
      description: item.description || '',
      parentId: item.parentId,
    });
    setShowSaveModal(true);
  };

  const handleSaveInterfaceClick = () => {
    setEditingTargetId(null);
    if (!apiPath) {
      alert('请至少填写接口路径');
      return;
    }

    let defaultParentId: number | null = null;
    if (selectedId) {
      const existing = savedInterfaces.find((item) => item.id === selectedId);
      if (existing) {
        defaultParentId = existing.type === 'folder' ? existing.id : existing.parentId;
      }
    }

    if (selectedId) {
      const existing = savedInterfaces.find((item) => item.id === selectedId);
      if (existing && existing.type === 'request') {
        setSaveForm({
          name: existing.name,
          description: existing.description || '',
          parentId: existing.parentId,
        });
      } else {
        setSaveForm({ name: apiPath, description: '', parentId: defaultParentId });
      }
    } else {
      setSaveForm({ name: apiPath, description: '', parentId: defaultParentId });
    }

    setShowSaveModal(true);
  };

  const handleConfirmSave = async () => {
    if (!saveForm.name) {
      alert('请输入名称');
      return;
    }

    if (editingTargetId) {
      const target = savedInterfaces.find((item) => item.id === editingTargetId);
      if (target) {
        const updates: StandardInterfaceUpdate = {
          name: saveForm.name,
          description: saveForm.description,
          parent_id: saveForm.parentId,
          project_id: projectId,
        };
        await updateInterface(editingTargetId, updates);
        setShowSaveModal(false);
        setEditingTargetId(null);
        fetchInterfaces();
        return;
      }
    }

    const payload: StandardInterfaceUpdate = {
      name: saveForm.name,
      description: saveForm.description,
      project_id: projectId,
      type: 'request',
      method,
      base_url: '',
      api_path: apiPath,
      headers,
      params: queryParams,
      body_mode: bodyMode,
      raw_type: rawType,
      body_content: bodyContent,
      test_config: {
        pre_script: preRequestScript,
        post_script: postResponseScript,
      },
      parent_id: saveForm.parentId,
    };

    try {
      if (selectedId) {
        await updateInterface(selectedId, payload);
        onLog('接口已更新');
      } else {
        const res = await api.post<unknown>('/api/standard/interfaces', payload);
        setSelectedId(parseSavedInterface(res).id);
        onLog('接口已保存');
      }
      setShowSaveModal(false);
      fetchInterfaces();
    } catch (error) {
      const msg = await translateError(error);
      alert(`保存失败: ${msg}`);
    }
  };

  const handleLoadInterface = (item: SavedInterface) => {
    if (item.type === 'folder') {
      setSavedInterfaces((prev) =>
        prev.map((curr) => (curr.id === item.id ? { ...curr, isOpen: !curr.isOpen } : curr)),
      );
      return;
    }

    if (selectedId) {
      setDrafts((prev) => ({
        ...prev,
        [selectedId]: {
          apiPath,
          method,
          headers,
          params: queryParams,
          bodyMode,
          rawType,
          bodyContent,
          preRequestScript,
          postResponseScript,
        },
      }));
    }

    // 切换接口时清空响应区，避免旧数据残留。
    setResponseStatus(null);
    setResponseTime(null);
    setResponseBody(null);
    setResponseHeaders({});
    setResponseCookies({});
    setSelectedId(item.id);

    const draft = drafts[item.id];
    if (draft) {
      setApiPath((draft.baseUrl || '') + (draft.apiPath || ''));
      setMethod(draft.method ?? item.method ?? 'POST');
      setPreRequestScript(
        draft.preRequestScript ?? item.preScript ?? item.testConfig?.pre_script ?? '',
      );
      setPostResponseScript(
        draft.postResponseScript ?? item.postScript ?? item.testConfig?.post_script ?? '',
      );
      setHeaders(draft.headers ?? item.headers ?? [{ key: '', value: '', desc: '' }]);
      setQueryParams(draft.params ?? item.params ?? [{ key: '', value: '', desc: '' }]);
      setBodyMode(draft.bodyMode ?? ((item.bodyMode as BodyMode) || 'raw'));
      setRawType(draft.rawType ?? ((item.rawType as RawType) || 'JSON'));
      setBodyContent(draft.bodyContent ?? item.bodyContent ?? '');
      return;
    }

    setApiPath((item.baseUrl || '') + (item.apiPath || ''));
    setMethod(item.method || 'POST');
    setPreRequestScript(item.preScript || item.testConfig?.pre_script || '');
    setPostResponseScript(item.postScript || item.testConfig?.post_script || '');
    setHeaders(item.headers || [{ key: '', value: '', desc: '' }]);
    setQueryParams(item.params || [{ key: '', value: '', desc: '' }]);
    setBodyMode((item.bodyMode as BodyMode) || 'raw');
    setRawType((item.rawType as RawType) || 'JSON');
    setBodyContent(item.bodyContent || '');
  };

  return {
    showSaveModal,
    setShowSaveModal,
    saveForm,
    setSaveForm,
    handleEditFolder,
    handleSaveInterfaceClick,
    handleConfirmSave,
    handleLoadInterface,
    renderFolderOptions,
  };
}
