import React, { useEffect, useImperativeHandle, useState, forwardRef } from 'react';
import { Spinner, Button, Modal, Form } from 'react-bootstrap';
import { FaFolder, FaFileAlt, FaFolderPlus, FaFile, FaChevronRight, FaChevronDown, FaTrash } from 'react-icons/fa';
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
}

export interface HistoryListHandle {
    openCreateModal: (type: 'folder' | 'file', parentId?: number | null) => void;
}

interface HistoryListProps {
    projectId: number | null;
    onSelect: (item: UITestCase) => void;
    filterType?: 'web' | 'app';
    selectedId?: number | null;
}

export const HistoryList = forwardRef<HistoryListHandle, HistoryListProps>(({ projectId, onSelect, filterType, selectedId }, ref) => {
    const [treeData, setTreeData] = useState<UITestCase[]>([]);
    const [loading, setLoading] = useState(false);
    const [expandedFolders, setExpandedFolders] = useState<Record<number, boolean>>({});

    const [showModal, setShowModal] = useState(false);
    const [modalType, setModalType] = useState<'folder' | 'file'>('folder');
    const [newItemName, setNewItemName] = useState('');
    const [targetParentId, setTargetParentId] = useState<number | null>(null);

    const openCreateModal = (type: 'folder' | 'file', parentId: number | null = null) => {
        setModalType(type);
        setTargetParentId(parentId);
        setNewItemName('');
        setShowModal(true);
    };

    useImperativeHandle(ref, () => ({
        openCreateModal,
    }));

    const fetchTree = async () => {
        if (!projectId) {
            return;
        }
        setLoading(true);
        try {
            const data = await api.get<UITestCase[]>(`/api/ui-test-cases/?project_id=${projectId}`);
            const buildTree = (items: UITestCase[], parentId: number | null = null): UITestCase[] => {
                return items
                    .filter((item) => item.parent_id === parentId)
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

    useEffect(() => {
        void fetchTree();
    }, [projectId]);

    const toggleFolder = (id: number) => {
        setExpandedFolders((prev) => ({ ...prev, [id]: !prev[id] }));
    };

    const handleCreate = async () => {
        if (!newItemName) {
            return;
        }
        try {
            await api.post('/api/ui-test-cases/', {
                project_id: projectId,
                name: newItemName,
                type: modalType,
                parent_id: targetParentId,
                automation_type: filterType || 'web',
            });
            setShowModal(false);
            setNewItemName('');
            await fetchTree();
            if (targetParentId) {
                setExpandedFolders((prev) => ({ ...prev, [targetParentId]: true }));
            }
        } catch (e) {
            alert('创建失败');
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

    const renderTree = (nodes: UITestCase[], depth = 0) => {
        return nodes.map((node) => {
            if (node.type === 'file' && filterType && node.automation_type !== filterType) {
                return null;
            }

            const isExpanded = expandedFolders[node.id];
            const isSelected = selectedId === node.id;

            return (
                <div key={node.id}>
                    <div
                        className={`ui-automation-history-item ui-automation-history-row d-flex align-items-center py-1 px-2 border-bottom ${isSelected ? 'is-selected' : ''}`}
                        style={{ '--ui-depth': depth } as React.CSSProperties}
                        onClick={() => {
                            if (node.type === 'folder') {
                                toggleFolder(node.id);
                            } else {
                                onSelect(node);
                            }
                        }}
                    >
                        <div className="me-2 ui-automation-history-fold-toggle">
                            {node.type === 'folder' ? (isExpanded ? <FaChevronDown size={10} /> : <FaChevronRight size={10} />) : null}
                        </div>
                        <div className="me-2 text-warning">{node.type === 'folder' ? <FaFolder /> : <FaFileAlt className="text-info" />}</div>
                        <div className="flex-grow-1 text-truncate">{node.name}</div>
                        <div className="ui-automation-history-actions">
                            {node.type === 'folder' ? (
                                <>
                                    <FaFolderPlus
                                        className="me-2 text-muted"
                                        size={12}
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            openCreateModal('folder', node.id);
                                        }}
                                        title="新建子目录"
                                    />
                                    <FaFile
                                        className="me-2 text-muted"
                                        size={12}
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            openCreateModal('file', node.id);
                                        }}
                                        title="新建脚本"
                                    />
                                </>
                            ) : null}
                            <FaTrash className="text-danger" size={12} onClick={(e) => void handleDelete(e, node.id)} title="删除" />
                        </div>
                    </div>
                    {node.type === 'folder' && isExpanded && node.children ? <div>{renderTree(node.children, depth + 1)}</div> : null}
                </div>
            );
        });
    };

    return (
        <div className="ui-automation-history h-100 d-flex flex-column">
            <div className="flex-grow-1 overflow-auto custom-scrollbar ui-automation-history-scroll">
                {loading ? (
                    <div className="text-center p-3 text-muted">
                        <Spinner animation="border" size="sm" /> 加载中...
                    </div>
                ) : (
                    <>
                        {renderTree(treeData)}
                        {treeData.length === 0 ? <div className="text-center p-4 text-muted small">暂无脚本，请点击上方按钮创建。</div> : null}
                    </>
                )}
            </div>

            <Modal show={showModal} onHide={() => setShowModal(false)} size="sm" centered>
                <Modal.Header closeButton>
                    <Modal.Title className="h6">{modalType === 'folder' ? '新建文件夹' : '新建脚本'}</Modal.Title>
                </Modal.Header>
                <Modal.Body>
                    <Form.Control
                        autoFocus
                        placeholder="请输入名称"
                        value={newItemName}
                        onChange={(e) => setNewItemName(e.target.value)}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                                void handleCreate();
                            }
                        }}
                    />
                </Modal.Body>
                <Modal.Footer className="p-2">
                    <Button size="sm" variant="secondary" onClick={() => setShowModal(false)}>
                        取消
                    </Button>
                    <Button size="sm" variant="primary" onClick={() => void handleCreate()}>
                        确定
                    </Button>
                </Modal.Footer>
            </Modal>
        </div>
    );
});
