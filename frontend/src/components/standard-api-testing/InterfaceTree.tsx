import type { CSSProperties } from 'react';
import { Dropdown, Form, ListGroup } from 'react-bootstrap';
import {
  FaChevronDown,
  FaChevronRight,
  FaDownload,
  FaEdit,
  FaEllipsisH,
  FaLayerGroup,
  FaPlus,
  FaTrash,
  FaUpload,
} from 'react-icons/fa';
import type { DragOverPosition } from './utils/dragTree';
import type { SavedInterface } from './utils/types';

type InterfaceTreeProps = {
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
  onCreateInterface: (targetParentId?: number | null) => void;
  onEditFolder: (item: SavedInterface) => void;
  onFolderImport: (folderId: number) => void;
  onFolderExport: (folderId: number) => void;
  onDeleteInterface: (id: number, e: React.MouseEvent) => void;
};

export function InterfaceTree({
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
  onCreateInterface,
  onEditFolder,
  onFolderImport,
  onFolderExport,
  onDeleteInterface,
}: InterfaceTreeProps) {
  const renderTree = (parentId: number | null, depth = 0): React.ReactNode => {
    const items = savedInterfaces.filter((item) => item.parentId === parentId);
    if (items.length === 0) return null;

    return items.map((item) => {
      const isFolder = item.type === 'folder';
      const isSelected = item.id === selectedId;
      const isOver = dragOverId === item.id;
      const isHovered = hoverId === item.id;
      const isBulkSelected = !!bulkSelected[item.id];

      const dropClass =
        isOver && dragOverPosition === 'middle'
          ? 'api-tree-drop-middle'
          : isOver && dragOverPosition === 'top'
            ? 'api-tree-drop-top'
            : isOver && dragOverPosition === 'bottom'
              ? 'api-tree-drop-bottom'
              : '';

      return (
        <div key={item.id}>
          <div
            draggable={renamingId !== item.id && !bulkDeleteMode}
            onMouseEnter={() => setHoverId(item.id)}
            onMouseLeave={() => setHoverId(null)}
            onDragStart={(e) => onDragStart(e, item.id)}
            onDragOver={(e) => onDragOver(e, item.id, isFolder)}
            onDragLeave={onDragLeave}
            onDrop={(e) => onDrop(e, item.id)}
            className={`api-tree-drop-wrap rounded ${dropClass}`}
          >
            <ListGroup.Item
              action
              onClick={() => {
                if (bulkDeleteMode) {
                  onToggleBulkSelected(item.id);
                } else {
                  onLoadInterface(item);
                }
              }}
              className={`api-tree-item ${isSelected ? 'api-tree-item-selected' : ''} border-0 py-1 px-2 d-flex align-items-center`}
            >
              {bulkDeleteMode && (
                <div className="me-1 d-flex align-items-center justify-content-center api-tree-bulk-check" onClick={(e) => e.stopPropagation()}>
                  <Form.Check
                    type="checkbox"
                    className="mb-0"
                    checked={isBulkSelected}
                    onChange={() => onToggleBulkSelected(item.id)}
                  />
                </div>
              )}

              <div className="api-tree-indent" style={{ '--api-tree-depth': depth } as CSSProperties} />

              {!isFolder && (
                <div className="api-tree-method-col d-flex align-items-center justify-content-start flex-shrink-0">
                  <span
                    className="small api-tree-method-text"
                    style={{ '--api-method-color': getMethodColor(item.method || 'GET') } as CSSProperties}
                  >
                    {item.method}
                  </span>
                </div>
              )}

              {isFolder && (
                <div
                  className="me-1 d-flex align-items-center justify-content-center text-secondary flex-shrink-0 api-tree-folder-toggle"
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggleFolder(item.id);
                  }}
                >
                  {item.isOpen ? <FaChevronDown size={10} /> : <FaChevronRight size={10} />}
                </div>
              )}

              <div className="d-flex align-items-center flex-grow-1 overflow-hidden">
                {isFolder && (
                  <span className="api-tree-icon-slot text-warning me-2">
                    <FaLayerGroup size={14} />
                  </span>
                )}

                {renamingId === item.id ? (
                  <Form.Control
                    size="sm"
                    value={renamingName}
                    onChange={(e) => onSetRenamingName(e.target.value)}
                    onBlur={onRenameConfirm}
                    onKeyDown={(e) => e.key === 'Enter' && onRenameConfirm()}
                    autoFocus
                    onClick={(e) => e.stopPropagation()}
                    onMouseDown={(e) => e.stopPropagation()}
                    onDragStart={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                    }}
                    className="api-tree-rename-input"
                  />
                ) : (
                  <span
                    className={`text-truncate small flex-grow-1 ${isFolder ? 'fw-semibold' : 'fw-medium'}`}
                    title={item.name}
                    onDoubleClick={(e) => {
                      e.stopPropagation();
                      onSetRenamingId(item.id);
                      onSetRenamingName(item.name);
                    }}
                  >
                    {item.name}
                  </span>
                )}
              </div>

              {!bulkDeleteMode && (
                <div className={`ms-2 d-flex gap-2 align-items-center flex-shrink-0 api-tree-actions ${isHovered || isSelected ? 'is-visible' : ''}`}>
                  <Dropdown onClick={(e) => e.stopPropagation()}>
                    <Dropdown.Toggle as="div" className="cursor-pointer text-secondary px-1 no-caret">
                      <FaEllipsisH size={12} />
                    </Dropdown.Toggle>

                    <Dropdown.Menu align="end" className="api-tree-menu" popperConfig={{ strategy: 'fixed' }}>
                      <Dropdown.Item onClick={() => (isFolder ? onCreateInterface(item.id) : onCreateInterface(item.parentId))}>
                        <FaPlus className="me-2" /> 新增接口
                      </Dropdown.Item>
                      {isFolder && (
                        <>
                          <Dropdown.Item onClick={() => onEditFolder(item)}>
                            <FaEdit className="me-2" /> 编辑详情
                          </Dropdown.Item>
                          <Dropdown.Item onClick={() => onFolderImport(item.id)}>
                            <FaUpload className="me-2" /> 导入 JSON
                          </Dropdown.Item>
                          <Dropdown.Item onClick={() => onFolderExport(item.id)}>
                            <FaDownload className="me-2" /> 导出 JSON
                          </Dropdown.Item>
                        </>
                      )}
                      <Dropdown.Item
                        onClick={() => {
                          onSetRenamingId(item.id);
                          onSetRenamingName(item.name);
                        }}
                      >
                        <FaEdit className="me-2" /> 重命名
                      </Dropdown.Item>
                      <Dropdown.Item onClick={(e) => onDeleteInterface(item.id, e)} className="text-danger">
                        <FaTrash className="me-2" /> 删除
                      </Dropdown.Item>
                    </Dropdown.Menu>
                  </Dropdown>
                </div>
              )}
            </ListGroup.Item>
          </div>

          {isFolder && item.isOpen && (
            <div className="border-start api-tree-children" style={{ '--api-tree-depth': depth } as CSSProperties}>
              {renderTree(item.id, depth + 1)}
            </div>
          )}
        </div>
      );
    });
  };

  return <>{renderTree(null)}</>;
}
