import React, { useEffect, useState, useImperativeHandle, forwardRef } from 'react';
import { Spinner, Button, Modal, Form } from 'react-bootstrap';
import { FaFolder, FaFileAlt, FaFolderPlus, FaFile, FaChevronRight, FaChevronDown, FaTrash } from 'react-icons/fa';
import { api } from '../../utils/api';

// 树形结构节点类型
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
    
    // 新建弹窗状态
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
        openCreateModal
    }));

    const fetchTree = async () => {
        if (!projectId) return;
        setLoading(true);
        try {
            const data = await api.get<UITestCase[]>(`/api/ui-test-cases/?project_id=${projectId}`);
            // 把后端返回的扁平列表转成树（parent_id -> children）
            const buildTree = (items: UITestCase[], parentId: number | null = null): UITestCase[] => {
                return items
                    .filter(item => item.parent_id === parentId)
                    .map(item => ({
                        ...item,
                        children: buildTree(items, item.id)
                    }));
            };
            const tree = buildTree(data);
            setTreeData(tree);
        } catch (error) {
            console.error('Failed to fetch test cases:', error);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchTree();
    }, [projectId]);

    const toggleFolder = (id: number) => {
        setExpandedFolders(prev => ({ ...prev, [id]: !prev[id] }));
    };

    const handleCreate = async () => {
        if (!newItemName) return;
        try {
            await api.post('/api/ui-test-cases/', {
                project_id: projectId,
                name: newItemName,
                type: modalType,
                parent_id: targetParentId,
                automation_type: filterType || 'web'
            });
            setShowModal(false);
            setNewItemName('');
            fetchTree();
            // 新建子节点后自动展开父目录，提升可见性
            if (targetParentId) {
                setExpandedFolders(prev => ({ ...prev, [targetParentId]: true }));
            }
        } catch (e) {
            alert('创建失败');
        }
    };

    const handleDelete = async (e: React.MouseEvent, id: number) => {
        e.stopPropagation();
        if (!window.confirm('确定要删除吗？')) return;
        try {
            await api.delete(`/api/ui-test-cases/${id}`);
            fetchTree();
        } catch (error) {
            console.error(error);
        }
    };

    const renderTree = (nodes: UITestCase[], depth = 0) => {
        return nodes.map(node => {
            // 如果指定了过滤类型，只过滤文件节点；目录节点始终保留
            if (node.type === 'file' && filterType && node.automation_type !== filterType) return null;

            const isExpanded = expandedFolders[node.id];
            const isSelected = selectedId === node.id;
            
            return (
                <div key={node.id}>
                    <div 
                        className={`d-flex align-items-center py-1 px-2 border-bottom ${isSelected ? 'bg-primary text-white' : 'hover-bg-light'}`}
                        style={{ paddingLeft: `${depth * 16 + 8}px`, cursor: 'pointer', fontSize: '0.9em' }}
                        onClick={() => {
                            if (node.type === 'folder') toggleFolder(node.id);
                            else onSelect(node);
                        }}
                    >
                        <div className="me-2" style={{width: '16px'}}>
                            {node.type === 'folder' && (
                                isExpanded ? <FaChevronDown size={10} /> : <FaChevronRight size={10} />
                            )}
                        </div>
                        <div className="me-2 text-warning">
                            {node.type === 'folder' ? <FaFolder /> : <FaFileAlt className="text-info" />}
                        </div>
                        <div className="flex-grow-1 text-truncate">
                            {node.name}
                        </div>
                        <div className="actions opacity-0 hover-opacity-100">
                            {node.type === 'folder' && (
                                <>
                                    <FaFolderPlus className="me-2 text-muted" size={12} onClick={(e) => { e.stopPropagation(); openCreateModal('folder', node.id); }} title="新建子文件夹"/>
                                    <FaFile className="me-2 text-muted" size={12} onClick={(e) => { e.stopPropagation(); openCreateModal('file', node.id); }} title="新建脚本"/>
                                </>
                            )}
                            <FaTrash className="text-danger" size={12} onClick={(e) => handleDelete(e, node.id)} title="删除"/>
                        </div>
                    </div>
                    {node.type === 'folder' && isExpanded && node.children && (
                        <div>{renderTree(node.children, depth + 1)}</div>
                    )}
                </div>
            );
        });
    };

    return (
        <div className="h-100 d-flex flex-column">
            {/* 按需求移除顶部标题区域 */}
            <div className="flex-grow-1 overflow-auto custom-scrollbar bg-white">
                {loading ? (
                    <div className="text-center p-3 text-muted">
                        <Spinner animation="border" size="sm" /> 加载中...
                    </div>
                ) : (
                    <div>
                        {renderTree(treeData)}
                        {treeData.length === 0 && (
                            <div className="text-center p-4 text-muted small">
                                暂无脚本，请点击上方按钮创建。
                            </div>
                        )}
                    </div>
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
                        onChange={e => setNewItemName(e.target.value)}
                        onKeyPress={e => e.key === 'Enter' && handleCreate()}
                    />
                </Modal.Body>
                <Modal.Footer className="p-1">
                    <Button size="sm" variant="secondary" onClick={() => setShowModal(false)}>取消</Button>
                    <Button size="sm" variant="primary" onClick={handleCreate}>确定</Button>
                </Modal.Footer>
            </Modal>

            <style>{`
                .hover-bg-light:hover { background-color: #f8f9fa; }
                .hover-opacity-100:hover { opacity: 1 !important; }
                .hover-bg-light:hover .actions { opacity: 1 !important; }
            `}</style>
        </div>
    );
});
