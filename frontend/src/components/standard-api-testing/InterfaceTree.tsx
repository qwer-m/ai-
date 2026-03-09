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
import type { DragOverPosition } from './dragTree';
import type { SavedInterface } from './types';

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
  // 递归渲染接口树：folder 节点继续向下展开，request 节点作为叶子节点展示。
  const renderTree = (parentId: number | null, depth = 0): React.ReactNode => {
    const items = savedInterfaces.filter((item) => item.parentId === parentId);
    if (items.length === 0) return null;

    return items.map((item) => {
      const isFolder = item.type === 'folder';
      const isSelected = item.id === selectedId;
      const isOver = dragOverId === item.id;
      const isHovered = hoverId === item.id;
      const isBulkSelected = !!bulkSelected[item.id];

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
            className={`
              rounded
              ${isOver && dragOverPosition === 'middle' ? 'bg-primary-subtle border border-primary' : ''}
            `}
            style={{
              // 拖拽反馈：上/中/下三种投放位置用不同边框提示。
              transition: 'all 0.2s',
              borderTop:
                isOver && dragOverPosition === 'top'
                  ? '2px solid #dc3545'
                  : isOver && dragOverPosition === 'middle'
                    ? undefined
                    : '1px solid transparent',
              borderBottom:
                isOver && dragOverPosition === 'bottom'
                  ? '2px solid #dc3545'
                  : isOver && dragOverPosition === 'middle'
                    ? undefined
                    : '1px solid transparent',
              borderLeft: isOver && dragOverPosition === 'middle' ? undefined : '1px solid transparent',
              borderRight: isOver && dragOverPosition === 'middle' ? undefined : '1px solid transparent',
            }}
          >
            <ListGroup.Item
              action
              onClick={() => {
                // 批量删除模式下点击用于勾选；普通模式下点击加载接口详情。
                if (bulkDeleteMode) {
                  onToggleBulkSelected(item.id);
                } else {
                  onLoadInterface(item);
                }
              }}
              className={`api-tree-item ${isSelected ? 'api-tree-item-selected' : ''} border-0 py-1 px-2 d-flex align-items-center`}
              style={{
                paddingLeft: '0px',
                backgroundColor: 'transparent',
                borderLeft: isSelected ? '3px solid #0d6efd' : '3px solid transparent',
              }}
            >
              {bulkDeleteMode && (
                <div
                  className="me-1 d-flex align-items-center justify-content-center"
                  style={{ width: '18px', height: '18px' }}
                  onClick={(e) => e.stopPropagation()}
                >
                  <Form.Check
                    type="checkbox"
                    className="mb-0"
                    checked={isBulkSelected}
                    onChange={() => onToggleBulkSelected(item.id)}
                  />
                </div>
              )}

              <div style={{ width: `${depth * 6}px`, flexShrink: 0 }} />

              {!isFolder && (
                <div
                  className="api-tree-method-col d-flex align-items-center justify-content-start flex-shrink-0"
                  style={{ width: 'auto', marginRight: '0px' }}
                >
                  <span
                    className="small"
                    style={{
                      fontSize: '0.75rem',
                      fontWeight: 700,
                      color: getMethodColor(item.method || 'GET'),
                      display: 'block',
                      textAlign: 'left',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {item.method}
                  </span>
                </div>
              )}

              {isFolder && (
                <div
                  className="me-1 d-flex align-items-center justify-content-center text-secondary flex-shrink-0"
                  style={{ width: '19px', height: '18px', cursor: 'pointer', marginLeft: '-7px' }}
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
                    className="p-0 px-1 py-0 h-auto"
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
                <div
                  className="ms-2 d-flex gap-2 align-items-center flex-shrink-0"
                  style={{ opacity: isHovered || isSelected ? 1 : 0, transition: 'opacity 0.2s' }}
                >
                  <Dropdown onClick={(e) => e.stopPropagation()}>
                    {/* 右侧三点菜单承载节点级操作，避免挤占主信息区 */}
                    <Dropdown.Toggle as="div" className="cursor-pointer text-secondary px-1 no-caret">
                      <FaEllipsisH size={12} />
                    </Dropdown.Toggle>

                    <Dropdown.Menu align="end" style={{ zIndex: 1050 }} popperConfig={{ strategy: 'fixed' }}>
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
            <div className="border-start" style={{ marginLeft: `${depth * 6 + 12}px`, paddingLeft: '0px' }}>
              {renderTree(item.id, depth + 1)}
            </div>
          )}
        </div>
      );
    });
  };

  return <>{renderTree(null)}</>;
}
