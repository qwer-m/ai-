import type { CSSProperties } from 'react';
import { useRef, useState } from 'react';
import { Button, ListGroup } from 'react-bootstrap';
import { FaFolderPlus, FaMinus, FaPlus } from 'react-icons/fa';
import { InterfaceTree } from './InterfaceTree';
import { buildPostmanFolderItems } from './utils/importExport';
import type { DragOverPosition } from './utils/dragTree';
import type { SavedInterface } from './utils/types';

export type StandardApiTestingSidebarProps = {
  showSidebar: boolean;
  sidebarWidth: number;
  isResizingSidebar: boolean;
  savedInterfaces: SavedInterface[];
  selectedId: number | null;
  dragOverId: number | null;
  dragOverPosition: DragOverPosition | null;
  hoverId: number | null;
  bulkDeleteMode: boolean;
  bulkSelected: Record<number, boolean>;
  renamingId: number | null;
  renamingName: string;
  setHoverId: (id: number | null) => void;
  onDragStart: (e: React.DragEvent, id: number) => void;
  onDragOver: (e: React.DragEvent, targetId: number, isFolder: boolean) => void;
  onDragLeave: () => void;
  onDrop: (e: React.DragEvent, targetId: number | null) => void;
  onToggleBulkSelected: (id: number) => void;
  onLoadInterface: (item: SavedInterface) => void;
  getMethodColor: (method: string) => string;
  onToggleFolder: (id: number) => void;
  onSetRenamingId: (id: number | null) => void;
  onSetRenamingName: (name: string) => void;
  onRenameConfirm: () => void;
  onCreateFolder: (parentId: number | null) => Promise<void>;
  onCreateInterface: (targetParentId?: number | null) => Promise<void>;
  onEditFolder: (item: SavedInterface) => void;
  onDeleteInterface: (id: number, e: React.MouseEvent) => Promise<void>;
  onBulkDeleteToggleOrConfirm: () => Promise<void>;
  onLog: (msg: string) => void;
  onRefreshInterfaces: () => void;
  onImportFiles: (files: File[], rootParentId: number | null) => Promise<number>;
  onOpenFolderAfterImport: (folderId: number) => void;
};

export function StandardApiTestingSidebar({
  showSidebar,
  sidebarWidth,
  isResizingSidebar,
  savedInterfaces,
  selectedId,
  dragOverId,
  dragOverPosition,
  hoverId,
  bulkDeleteMode,
  bulkSelected,
  renamingId,
  renamingName,
  setHoverId,
  onDragStart,
  onDragOver,
  onDragLeave,
  onDrop,
  onToggleBulkSelected,
  onLoadInterface,
  getMethodColor,
  onToggleFolder,
  onSetRenamingId,
  onSetRenamingName,
  onRenameConfirm,
  onCreateFolder,
  onCreateInterface,
  onEditFolder,
  onDeleteInterface,
  onBulkDeleteToggleOrConfirm,
  onLog,
  onRefreshInterfaces,
  onImportFiles,
  onOpenFolderAfterImport,
}: StandardApiTestingSidebarProps) {
  const [isDragOver, setIsDragOver] = useState(false);
  const [folderImportTargetId, setFolderImportTargetId] = useState<number | null>(null);
  const folderImportInputRef = useRef<HTMLInputElement>(null);

  const handleFolderImport = (folderId: number) => {
    setFolderImportTargetId(folderId);
    folderImportInputRef.current?.click();
  };

  const handleFolderImportFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    if (files.length === 0) return;

    const count = await onImportFiles(files, folderImportTargetId);
    if (count > 0) {
      onLog(`成功导入 ${count} 个项目（已写入数据库）`);
      onRefreshInterfaces();
      if (folderImportTargetId) {
        onOpenFolderAfterImport(folderImportTargetId);
      }
    } else {
      onLog('未找到可导入的接口数据（支持 Postman v2.1 / Apifox 导出）');
    }

    setFolderImportTargetId(null);
  };

  const handleImportDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);

    const files = Array.from(e.dataTransfer.files);
    if (files.length === 0) return;

    const importCount = await onImportFiles(files, null);
    if (importCount > 0) {
      onLog(`成功导入 ${importCount} 个项目（已写入数据库）`);
      onRefreshInterfaces();
    } else {
      onLog('未找到可导入的接口数据（支持 Postman v2.1 / Apifox 导出）');
    }
  };

  const handleFolderExport = (folderId: number) => {
    const folder = savedInterfaces.find((item) => item.id === folderId && item.type === 'folder');
    if (!folder) return;

    const collection = {
      info: {
        name: folder.name,
        schema: 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json',
      },
      item: buildPostmanFolderItems(savedInterfaces, folderId),
    };

    const blob = new Blob([JSON.stringify(collection, null, 2)], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${folder.name || 'collection'}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div
      className={`border-end bg-light d-flex flex-column position-relative standard-api-sidebar standard-api-sidebar-resizable ${showSidebar ? 'is-open' : 'is-closed'} ${isResizingSidebar ? 'is-resizing' : ''}`}
      style={{ '--sat-sidebar-width': `${sidebarWidth}px` } as CSSProperties}
      onDragOver={(e) => {
        e.preventDefault();
        setIsDragOver(true);
      }}
      onDragLeave={() => setIsDragOver(false)}
      onDrop={handleImportDrop}
    >
      <input
        ref={folderImportInputRef}
        type="file"
        accept=".json,application/json"
        multiple
        onChange={handleFolderImportFiles}
        className="standard-api-hidden-input"
      />
      {isDragOver && (
        <div className="position-absolute top-0 start-0 w-100 h-100 d-flex align-items-center justify-content-center bg-primary bg-opacity-10 standard-api-import-overlay">
          <div className="text-primary px-3 py-2 rounded shadow-sm standard-api-import-overlay-card">
            <FaFolderPlus className="me-2" /> 拖拽以导入
          </div>
        </div>
      )}
      <div className="d-flex justify-content-between align-items-center mb-2 px-3 pt-3 standard-api-sidebar-head">
        <h6 className="mb-0 text-secondary standard-api-sidebar-title">接口列表</h6>
        <div className="d-flex gap-2">
          <Button variant="link" className="p-0 text-secondary" onClick={() => onCreateFolder(null)} title="新建文件夹">
            <FaFolderPlus size={16} />
          </Button>
          <Button variant="link" className="p-0 text-secondary" onClick={() => onCreateInterface(null)} title="新建接口">
            <FaPlus size={16} />
          </Button>
          <Button
            variant="link"
            className={`p-0 ${bulkDeleteMode ? 'text-danger' : 'text-secondary'}`}
            onClick={onBulkDeleteToggleOrConfirm}
            title={bulkDeleteMode ? '删除选中（再次点击执行；不选则退出）' : '批量删除'}
          >
            <FaMinus size={16} />
          </Button>
        </div>
      </div>
      <div className="flex-grow-1 overflow-auto border-top position-relative standard-api-sidebar-body standard-api-sidebar-scroll">
        <ListGroup variant="flush">
          <InterfaceTree
            savedInterfaces={savedInterfaces}
            selectedId={selectedId}
            dragOverId={dragOverId}
            dragOverPosition={dragOverPosition}
            hoverId={hoverId}
            bulkDeleteMode={bulkDeleteMode}
            bulkSelected={bulkSelected}
            renamingId={renamingId}
            renamingName={renamingName}
            setHoverId={setHoverId}
            onDragStart={onDragStart}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            onToggleBulkSelected={onToggleBulkSelected}
            onLoadInterface={onLoadInterface}
            getMethodColor={getMethodColor}
            onToggleFolder={onToggleFolder}
            onSetRenamingId={onSetRenamingId}
            onSetRenamingName={onSetRenamingName}
            onRenameConfirm={onRenameConfirm}
            onCreateInterface={onCreateInterface}
            onEditFolder={onEditFolder}
            onFolderImport={handleFolderImport}
            onFolderExport={handleFolderExport}
            onDeleteInterface={onDeleteInterface}
          />
        </ListGroup>

        {savedInterfaces.length === 0 && (
          <div className="text-center text-muted mt-5 small position-absolute w-100 standard-api-sidebar-empty">
            暂无接口，点击右上角 + 新建
          </div>
        )}
      </div>
    </div>
  );
}
