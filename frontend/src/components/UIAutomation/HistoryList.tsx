import React, { useEffect, useImperativeHandle, useState, forwardRef } from 'react';
import { Alert, Spinner, Button, Modal, Form } from 'react-bootstrap';
import { FaFolder, FaFileAlt, FaFolderPlus, FaChevronRight, FaChevronDown, FaTrash } from 'react-icons/fa';
import { api } from '../../utils/api';

interface UITestCase {
    id: number;
    name: string;
    type: 'folder' | 'file';
    parent_id: number | null;
    children?: UITestCase[];
    script_content?: string;
    requirements?: string;
    automation_type?: string;
    description?: string;
    hierarchy?: string[];
    code_path?: string;
}

export interface HistoryListHandle {
    openCreateFolder: (parentId?: number | null) => void;
    refresh: () => Promise<void>;
}

interface HistoryListProps {
    projectId: number | null;
    onSelect: (item: UITestCase) => void;
    onFolderSelect?: (item: UITestCase | null) => void;
    onHierarchyChange?: (message: string) => void;
    onNodeMoved?: (item: UITestCase, parent: UITestCase | null) => void;
    filterType?: 'web' | 'app';
    selectedId?: number | null;
    selectedFolderId?: number | null;
}

export const HistoryList = forwardRef<HistoryListHandle, HistoryListProps>(({
    projectId,
    onSelect,
    onFolderSelect,
    onHierarchyChange,
    onNodeMoved,
    filterType,
    selectedId,
    selectedFolderId,
}, ref) => {
    const [treeData, setTreeData] = useState<UITestCase[]>([]);
    const [loading, setLoading] = useState(false);
    const [expandedFolders, setExpandedFolders] = useState<Record<number, boolean>>({});

    const [showModal, setShowModal] = useState(false);
    const [newItemName, setNewItemName] = useState('');
    const [targetParentId, setTargetParentId] = useState<number | null>(null);
    const [createError, setCreateError] = useState('');
    const [creating, setCreating] = useState(false);
    const [draggedNode, setDraggedNode] = useState<UITestCase | null>(null);
    const [dropTargetId, setDropTargetId] = useState<number | null | 'root'>(null);
    const [moveError, setMoveError] = useState('');
    const [moving, setMoving] = useState(false);

    const openCreateFolder = (parentId: number | null = null) => {
        setTargetParentId(parentId);
        setNewItemName('');
        setCreateError('');
        setShowModal(true);
    };

    const fetchTree = async () => {
        if (!projectId) {
            return;
        }
        setLoading(true);
        try {
            const data = await api.get<UITestCase[]>(`/api/ui-test-cases?project_id=${projectId}`);
            const buildTree = (items: UITestCase[], parentId: number | null = null): UITestCase[] => {
                return items
                    .filter((item) => item.parent_id === parentId)
                    .sort((left, right) => {
                        if (left.type !== right.type) return left.type === 'folder' ? -1 : 1;
                        return left.name.localeCompare(right.name, 'zh-CN');
                    })
                    .map((item) => ({
                        ...item,
                        children: buildTree(items, item.id),
                    }));
            };
            setTreeData(buildTree(data));
        } catch (error) {
            console.error('Failed to fetch test cases:', error);
        } finally {
            setLoading(false);
        }
    };

    useImperativeHandle(ref, () => ({
        openCreateFolder,
        refresh: fetchTree,
    }));

    useEffect(() => {
        void fetchTree();
    }, [projectId]);

    const toggleFolder = (id: number) => {
        setExpandedFolders((prev) => ({ ...prev, [id]: !prev[id] }));
    };

    const handleCreate = async () => {
        const name = newItemName.trim();
        if (!projectId) {
            setCreateError('请先选择项目后再创建分组。');
            return;
        }
        if (!name) {
            setCreateError('请输入分组名称。');
            return;
        }
        setCreating(true);
        setCreateError('');
        try {
            await api.post('/api/ui-test-cases', {
                project_id: projectId,
                name,
                type: 'folder',
                parent_id: targetParentId,
                automation_type: filterType || 'web',
            });
            setShowModal(false);
            setNewItemName('');
            await fetchTree();
            if (targetParentId) {
                setExpandedFolders((prev) => ({ ...prev, [targetParentId]: true }));
            }
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            setCreateError(`创建失败：${message}`);
        } finally {
            setCreating(false);
        }
    };

    const handleDelete = async (e: React.MouseEvent, id: number) => {
        e.stopPropagation();
        if (!window.confirm('确定要删除吗？')) {
            return;
        }
        try {
            await api.delete(`/api/ui-test-cases/${id}`);
            await fetchTree();
        } catch (error) {
            console.error(error);
        }
    };

    const moveNode = async (targetParentId: number | null) => {
        if (!draggedNode || moving || draggedNode.parent_id === targetParentId) {
            setDraggedNode(null);
            setDropTargetId(null);
            return;
        }
        setMoving(true);
        setMoveError('');
        try {
            await api.put(`/api/ui-test-cases/${draggedNode.id}`, { parent_id: targetParentId });
            await fetchTree();
            if (targetParentId) {
                setExpandedFolders((prev) => ({ ...prev, [targetParentId]: true }));
            }
            const findNode = (nodes: UITestCase[]): UITestCase | null => {
                for (const node of nodes) {
                    if (node.id === targetParentId) return node;
                    const child = node.children ? findNode(node.children) : null;
                    if (child) return child;
                }
                return null;
            };
            onNodeMoved?.(draggedNode, targetParentId ? findNode(treeData) : null);
            onHierarchyChange?.(`已调整“${draggedNode.name}”的层级，桌面脚本目录已同步。`);
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            setMoveError(`移动失败：${message}`);
        } finally {
            setMoving(false);
            setDraggedNode(null);
            setDropTargetId(null);
        }
    };

    const renderTree = (nodes: UITestCase[], depth = 0, ancestors: string[] = []) => {
        return nodes.map((node) => {
            if (node.type === 'file' && filterType && node.automation_type !== filterType) {
                return null;
            }

            const isExpanded = expandedFolders[node.id];
            const isSelected = selectedId === node.id;
            const isFolderSelected = node.type === 'folder' && selectedFolderId === node.id;
            const codeSegments = [...ancestors, node.name];
            const fallbackCodeLocation = node.type === 'folder'
                ? `scripts/${codeSegments.join('/')}/`
                : `scripts/${ancestors.length > 0 ? `${ancestors.join('/')}/` : ''}${node.name}.py`;
            const codeLocation = node.code_path || fallbackCodeLocation;
            const normalizedCodeLocation = codeLocation.replace(/\\/g, '/');
            const scriptsMarker = normalizedCodeLocation.toLowerCase().lastIndexOf('/scripts/');
            const displayedCodeLocation = scriptsMarker >= 0
                ? normalizedCodeLocation.slice(scriptsMarker + 1)
                : normalizedCodeLocation;

            return (
                <div key={node.id}>
                    <div
                        className={`ui-automation-history-item ui-automation-history-row d-flex align-items-center py-1 px-2 ${isSelected ? 'is-selected' : ''} ${isFolderSelected ? 'is-folder-selected' : ''} ${dropTargetId === node.id ? 'is-drop-target' : ''}`}
                        style={{ '--ui-depth': depth } as React.CSSProperties}
                        draggable={!moving}
                        title={codeLocation}
                        onDragStart={(event) => {
                            event.dataTransfer.effectAllowed = 'move';
                            event.dataTransfer.setData('text/plain', String(node.id));
                            setDraggedNode(node);
                            setMoveError('');
                        }}
                        onDragEnd={() => {
                            setDraggedNode(null);
                            setDropTargetId(null);
                        }}
                        onDragOver={(event) => {
                            if (node.type !== 'folder') {
                                event.stopPropagation();
                                return;
                            }
                            if (!draggedNode || draggedNode.id === node.id) return;
                            event.preventDefault();
                            event.stopPropagation();
                            event.dataTransfer.dropEffect = 'move';
                            setDropTargetId(node.id);
                        }}
                        onDrop={(event) => {
                            if (node.type !== 'folder') {
                                event.stopPropagation();
                                return;
                            }
                            event.preventDefault();
                            event.stopPropagation();
                            void moveNode(node.id);
                        }}
                        onClick={() => {
                            if (node.type === 'folder') {
                                toggleFolder(node.id);
                                onFolderSelect?.(node);
                            } else {
                                onSelect({ ...node, hierarchy: ancestors });
                            }
                        }}
                    >
                        <div className="me-2 ui-automation-history-fold-toggle">
                            {node.type === 'folder' ? (isExpanded ? <FaChevronDown size={10} /> : <FaChevronRight size={10} />) : null}
                        </div>
                        <div className="me-2 text-warning">{node.type === 'folder' ? <FaFolder /> : <FaFileAlt className="text-info" />}</div>
                        <div className="flex-grow-1 ui-automation-history-label">
                            <div className="text-truncate">{node.name}</div>
                            <div className="ui-automation-history-code-path text-truncate">{displayedCodeLocation}</div>
                        </div>
                        <div className="ui-automation-history-actions">
                            {node.type === 'folder' && depth < 1 ? (
                                <>
                                    <FaFolderPlus
                                        className="me-2 text-muted"
                                        size={12}
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            openCreateFolder(node.id);
                                        }}
                                        title="新建子目录"
                                    />
                                </>
                            ) : null}
                            <FaTrash className="text-danger" size={12} onClick={(e) => void handleDelete(e, node.id)} title="删除" />
                        </div>
                    </div>
                    {node.type === 'folder' && isExpanded && node.children ? <div>{renderTree(node.children, depth + 1, codeSegments)}</div> : null}
                </div>
            );
        });
    };

    return (
        <div className="ui-automation-history h-100 d-flex flex-column">
            {moveError ? <Alert variant="danger" className="small py-2 mx-2 mt-2 mb-0">{moveError}</Alert> : null}
            <div
                className={`flex-grow-1 overflow-auto custom-scrollbar ui-automation-history-scroll ${dropTargetId === 'root' ? 'is-root-drop-target' : ''}`}
                onDragOver={(event) => {
                    if (!draggedNode) return;
                    event.preventDefault();
                    event.dataTransfer.dropEffect = 'move';
                    setDropTargetId('root');
                }}
                onDrop={(event) => {
                    event.preventDefault();
                    void moveNode(null);
                    onFolderSelect?.(null);
                }}
            >
                {draggedNode ? <div className="ui-automation-root-drop-hint">拖到此处移至根目录</div> : null}
                {loading ? (
                    <div className="text-center p-3 text-muted">
                        <Spinner animation="border" size="sm" /> 加载中...
                    </div>
                ) : (
                    <>
                        {renderTree(treeData)}
                        {treeData.length === 0 ? <div className="text-center p-4 text-muted small">暂无自动化操作，请点击上方按钮创建。</div> : null}
                    </>
                )}
            </div>

            <Modal show={showModal} onHide={() => !creating && setShowModal(false)} size="sm" centered>
                <Modal.Header closeButton>
                    <Modal.Title className="h6">新建分组</Modal.Title>
                </Modal.Header>
                <Modal.Body>
                    {createError ? <Alert variant="danger" className="small py-2">{createError}</Alert> : null}
                    <Form.Control
                        autoFocus
                        placeholder="请输入名称"
                        value={newItemName}
                        disabled={creating}
                        onChange={(e) => {
                            setNewItemName(e.target.value);
                            setCreateError('');
                        }}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                                void handleCreate();
                            }
                        }}
                    />
                </Modal.Body>
                <Modal.Footer className="p-2">
                    <Button size="sm" variant="secondary" disabled={creating} onClick={() => setShowModal(false)}>
                        取消
                    </Button>
                    <Button size="sm" variant="primary" disabled={creating || !newItemName.trim()} onClick={() => void handleCreate()}>
                        {creating ? <Spinner animation="border" size="sm" className="me-1" /> : null}
                        {creating ? '创建中' : '确定'}
                    </Button>
                </Modal.Footer>
            </Modal>
        </div>
    );
});
