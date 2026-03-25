import { useCallback, type Dispatch, type DragEvent, type MouseEvent, type SetStateAction } from 'react';
import { api } from '../../../../utils/api';
import { computeDragOverPosition, planInterfaceDrop, type DragOverPosition } from '../../../standard-api-testing/utils/dragTree';
import { importFilesFromCollections, importInterfaceItemsToBackend } from '../../../standard-api-testing/utils/importExport';
import type { SavedInterface } from '../../../standard-api-testing/utils/types';

type UseApiTestingInterfaceTreeParams = {
  savedInterfaces: SavedInterface[];
  setSavedInterfaces: Dispatch<SetStateAction<SavedInterface[]>>;
  selectedId: number | null;
  setSelectedId: Dispatch<SetStateAction<number | null>>;
  draggedId: number | null;
  setDraggedId: Dispatch<SetStateAction<number | null>>;
  dragOverId: number | null;
  setDragOverId: Dispatch<SetStateAction<number | null>>;
  dragOverPosition: DragOverPosition | null;
  setDragOverPosition: Dispatch<SetStateAction<DragOverPosition | null>>;
  hoverId: number | null;
  setHoverId: Dispatch<SetStateAction<number | null>>;
  bulkDeleteMode: boolean;
  setBulkDeleteMode: Dispatch<SetStateAction<boolean>>;
  bulkSelected: Record<number, boolean>;
  setBulkSelected: Dispatch<SetStateAction<Record<number, boolean>>>;
  renamingId: number | null;
  setRenamingId: Dispatch<SetStateAction<number | null>>;
  renamingName: string;
  setRenamingName: Dispatch<SetStateAction<string>>;
  projectId: number | null;
  fetchInterfaces: () => Promise<void> | void;
  createFolder: (parentId?: number | null) => Promise<void>;
  createInterface: (targetParentId?: number | null) => Promise<SavedInterface | null>;
  updateInterface: (id: number, updates: Record<string, unknown>) => Promise<void>;
  handleLoadInterface: (item: SavedInterface) => void;
  translateError: (error: any) => Promise<string>;
  onLog: (msg: string) => void;
};

export function useApiTestingInterfaceTree({
  savedInterfaces,
  setSavedInterfaces,
  selectedId,
  setSelectedId,
  draggedId,
  setDraggedId,
  dragOverId,
  setDragOverId,
  dragOverPosition,
  setDragOverPosition,
  hoverId,
  setHoverId,
  bulkDeleteMode,
  setBulkDeleteMode,
  bulkSelected,
  setBulkSelected,
  renamingId,
  setRenamingId,
  renamingName,
  setRenamingName,
  projectId,
  fetchInterfaces,
  createFolder,
  createInterface,
  updateInterface,
  handleLoadInterface,
  translateError,
  onLog,
}: UseApiTestingInterfaceTreeParams) {
  const handleCreateFolder = useCallback(
    async (parentId: number | null = null) => {
      await createFolder(parentId);
    },
    [createFolder],
  );

  const handleCreateInterface = useCallback(
    async (targetParentId?: number | null) => {
      const created = await createInterface(targetParentId);
      if (created) handleLoadInterface(created);
    },
    [createInterface, handleLoadInterface],
  );

  const handleDeleteInterface = useCallback(
    async (id: number, event: MouseEvent) => {
      event.stopPropagation();
      if (!window.confirm('Delete this interface?')) return;

      try {
        await api.delete(`/api/standard/interfaces/${id}`);
        if (selectedId === id) setSelectedId(null);
        await fetchInterfaces();
      } catch (error) {
        const msg = await translateError(error);
        alert(`Delete failed: ${msg}`);
      }
    },
    [fetchInterfaces, selectedId, setSelectedId, translateError],
  );

  const handleToggleBulkSelected = useCallback((id: number) => {
    setBulkSelected((prev) => {
      const next = { ...prev };
      if (next[id]) delete next[id];
      else next[id] = true;
      return next;
    });
  }, []);

  const buildRemoveSet = useCallback(
    (rootIds: number[]) => {
      const childrenByParent = new Map<number | null, number[]>();
      for (const item of savedInterfaces) {
        const parentId = item.parentId ?? null;
        const current = childrenByParent.get(parentId);
        if (current) current.push(item.id);
        else childrenByParent.set(parentId, [item.id]);
      }

      const remove = new Set<number>();
      const stack = [...rootIds];
      while (stack.length > 0) {
        const id = stack.pop() as number;
        if (remove.has(id)) continue;
        remove.add(id);
        const children = childrenByParent.get(id);
        if (!children) continue;
        for (const childId of children) stack.push(childId);
      }

      return remove;
    },
    [savedInterfaces],
  );

  const handleBulkDeleteToggleOrConfirm = useCallback(async () => {
    if (!bulkDeleteMode) {
      setBulkDeleteMode(true);
      setBulkSelected({});
      return;
    }

    const selectedIds = Object.keys(bulkSelected).map(Number);
    if (selectedIds.length === 0) {
      setBulkDeleteMode(false);
      return;
    }

    const removeSet = buildRemoveSet(selectedIds);
    const hint = removeSet.size !== selectedIds.length ? ` (including ${removeSet.size - selectedIds.length} child items)` : '';
    if (!window.confirm(`Delete ${selectedIds.length} selected item(s)?${hint}`)) return;

    try {
      for (const id of selectedIds) {
        await api.delete(`/api/standard/interfaces/${id}`);
      }

      if (selectedId !== null && removeSet.has(selectedId)) {
        setSelectedId(null);
      }

      setBulkDeleteMode(false);
      setBulkSelected({});
      await fetchInterfaces();
    } catch (error) {
      alert('Bulk delete failed');
    }
  }, [buildRemoveSet, bulkDeleteMode, bulkSelected, fetchInterfaces, selectedId, setSelectedId]);

  const handleDragStart = useCallback((event: DragEvent, id: number) => {
    event.stopPropagation();
    event.dataTransfer.setData('text/plain', String(id));
    setDraggedId(id);
  }, []);

  const handleDragOver = useCallback(
    (event: DragEvent, targetId: number, isFolder: boolean) => {
      event.preventDefault();
      event.stopPropagation();
      if (draggedId === targetId) return;

      const rect = event.currentTarget.getBoundingClientRect();
      const position = computeDragOverPosition(isFolder, event.clientY - rect.top, rect.height);

      setDragOverId(targetId);
      setDragOverPosition(position);
    },
    [draggedId],
  );

  const handleDragLeave = useCallback(() => {
    setDragOverId(null);
    setDragOverPosition(null);
  }, []);

  const handleDrop = useCallback(
    async (event: DragEvent, targetId: number | null) => {
      event.preventDefault();
      event.stopPropagation();
      setDragOverId(null);
      setDragOverPosition(null);

      const idStr = event.dataTransfer.getData('text/plain');
      if (!idStr) return;

      const id = Number(idStr);
      if (id === targetId) return;

      const position = dragOverPosition || 'middle';
      const dropPlan = planInterfaceDrop(savedInterfaces, id, targetId, position);
      if (!dropPlan) return;

      setSavedInterfaces(dropPlan.nextItems);

      const updates: Record<string, unknown> = { parent_id: dropPlan.newParentId };
      if (dropPlan.newParentId) {
        const parentFolder = savedInterfaces.find((item) => item.id === dropPlan.newParentId);
        if (parentFolder && parentFolder.baseUrl) {
          updates.base_url = parentFolder.baseUrl;
        }
      }

      await updateInterface(dropPlan.draggedId, updates);
      setDraggedId(null);
    },
    [dragOverPosition, savedInterfaces, setSavedInterfaces, updateInterface],
  );

  const toggleFolder = useCallback((id: number) => {
    setSavedInterfaces((prev) => prev.map((item) => (item.id === id ? { ...item, isOpen: !item.isOpen } : item)));
  }, [setSavedInterfaces]);

  const handleRenameConfirm = useCallback(async () => {
    if (renamingId === null) return;
    if (!renamingName.trim()) {
      setRenamingId(null);
      return;
    }

    setSavedInterfaces((prev) => prev.map((item) => (item.id === renamingId ? { ...item, name: renamingName } : item)));

    try {
      await api.put(`/api/standard/interfaces/${renamingId}`, { name: renamingName });
    } catch (error) {
      console.error('Rename failed', error);
      await fetchInterfaces();
    } finally {
      setRenamingId(null);
    }
  }, [fetchInterfaces, renamingId, renamingName, setSavedInterfaces]);

  const handleOpenFolderAfterImport = useCallback((folderId: number) => {
    setSavedInterfaces((prev) => prev.map((item) => (item.id === folderId ? { ...item, isOpen: true } : item)));
  }, [setSavedInterfaces]);

  const importInterfaceItems = useCallback(
    async (items: SavedInterface[], rootParentId: number | null) => {
      return importInterfaceItemsToBackend({
        items,
        rootParentId,
        projectId,
        createInterface: (payload) => api.post<SavedInterface>('/api/standard/interfaces', payload),
      });
    },
    [projectId],
  );

  const importFiles = useCallback(
    async (files: File[], rootParentId: number | null) => {
      return importFilesFromCollections({
        files,
        rootParentId,
        importParsedItems: importInterfaceItems,
        onUnsupportedFormat: (fileName) => {
          onLog(`File ${fileName} does not match a supported import format (Postman v2.1 / Apifox export).`);
        },
        onParseError: (fileName, message) => {
          onLog(`File ${fileName} parse failed: ${message}`);
        },
      });
    },
    [importInterfaceItems, onLog],
  );

  return {
    renamingId,
    setRenamingId,
    renamingName,
    setRenamingName,
    draggedId,
    dragOverId,
    dragOverPosition,
    hoverId,
    setHoverId,
    bulkDeleteMode,
    bulkSelected,
    handleDragStart,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    handleToggleBulkSelected,
    toggleFolder,
    handleCreateFolder,
    handleCreateInterface,
    handleDeleteInterface,
    handleBulkDeleteToggleOrConfirm,
    fetchInterfaces,
    importFiles,
    handleOpenFolderAfterImport,
    handleRenameConfirm,
  };
}
