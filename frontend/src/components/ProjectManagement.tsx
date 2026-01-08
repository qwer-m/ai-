import { useState, useEffect } from 'react';
import { Button, Form, Modal, Badge, Table, Pagination } from 'react-bootstrap';
import { FaFolder, FaPlus, FaEdit, FaTrash, FaSearch } from 'react-icons/fa';
import { api } from '../utils/api';

export type Project = {
  id: number;
  name: string;
  description?: string | null;
  parent_id?: number | null;
  created_at?: string;
  level?: number;
};

type Props = {
  projects: Project[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  onSelectProject: (id: number) => void;
  onLog: (msg: string) => void;
};

export function ProjectManagement({ projects, loading, error, onRefresh, onLog }: Props) {
  const [showCreate, setShowCreate] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [searchTerm, setSearchTerm] = useState('');
  
  // Form states
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [parentId, setParentId] = useState<number | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  // Pagination
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 10;

  // Reset form
  const resetForm = () => {
    setName('');
    setDesc('');
    setParentId(null);
    setEditId(null);
    setActionLoading(false);
  };

  const openCreate = () => {
    resetForm();
    setShowCreate(true);
  };

  const openEdit = (p: Project) => {
    resetForm();
    setEditId(p.id);
    setName(p.name);
    setDesc(p.description || '');
    setParentId(p.parent_id || null);
    setShowEdit(true);
  };

  const handleCreate = async () => {
    if (!name) return alert('请输入项目名称');
    setActionLoading(true);
    try {
      await api.post('/api/projects', { name, description: desc, parent_id: parentId });
      onLog(`创建项目成功: ${name}`);
      setShowCreate(false);
      onRefresh();
    } catch (e) {
      const msg = String(e);
      onLog(`创建项目失败: ${msg}`);
      alert(msg);
    } finally {
      setActionLoading(false);
    }
  };

  const handleUpdate = async () => {
    if (!editId || !name) return;
    setActionLoading(true);
    try {
      const data = await api.put<any>(`/api/projects/${editId}`, { name, description: desc, parent_id: parentId });
      if (data.error) throw new Error(data.error);
      onLog(`更新项目成功: ${name}`);
      setShowEdit(false);
      onRefresh();
    } catch (e) {
      const msg = String(e);
      onLog(`更新项目失败: ${msg}`);
      alert(msg);
    } finally {
      setActionLoading(false);
    }
  };

  const handleDelete = async (id: number, name: string) => {
    if (!confirm(`确定要删除项目 "${name}" 吗？此操作不可恢复，且会删除所有关联数据！`)) return;
    try {
      const data = await api.delete<any>(`/api/projects/${id}`);
      if (data.error) throw new Error(data.error);
      onLog(`删除项目成功: ${name}`);
      onRefresh();
    } catch (e) {
      const msg = String(e);
      onLog(`删除项目失败: ${msg}`);
      alert(msg);
    }
  };

  const renderModalContent = () => (
    <Form>
      <Form.Group className="mb-3">
        <Form.Label>项目名称</Form.Label>
        <Form.Control 
            className="input-pro"
            value={name} 
            onChange={e => setName(e.target.value)} 
            autoFocus 
            placeholder="例如：电商平台二期"
        />
      </Form.Group>
      <Form.Group className="mb-3">
        <Form.Label>项目描述</Form.Label>
        <Form.Control 
            as="textarea" 
            className="input-pro"
            rows={3} 
            value={desc} 
            onChange={e => setDesc(e.target.value)}
            placeholder="简要描述项目的目标和范围..."
        />
      </Form.Group>
      <Form.Group className="mb-3">
        <Form.Label>父项目</Form.Label>
        <Form.Select 
            className="input-pro"
            value={parentId ?? ''} 
            onChange={e => setParentId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">无 (作为顶级项目)</option>
          {projects.filter(p => p.id !== editId).map(p => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </Form.Select>
      </Form.Group>
    </Form>
  );

  const filteredProjects = projects.filter(p => 
    p.name.toLowerCase().includes(searchTerm.toLowerCase()) || 
    (p.description && p.description.toLowerCase().includes(searchTerm.toLowerCase()))
  );

  // Reset pagination when search changes
  useEffect(() => {
    setCurrentPage(1);
  }, [searchTerm]);

  const totalPages = Math.ceil(filteredProjects.length / itemsPerPage);
  const currentProjects = filteredProjects.slice(
    (currentPage - 1) * itemsPerPage,
    currentPage * itemsPerPage
  );

  return (
    <div className="bento-grid h-100 align-content-start">
        {/* Header Section */}
        <div className="bento-card col-span-12 p-4 d-flex align-items-center justify-content-between glass-panel">
            <div className="d-flex align-items-center gap-3">
                <div className="bg-primary bg-opacity-10 p-3 rounded-circle text-primary">
                    <FaFolder size={24} />
                </div>
                <div>
                    <h4 className="mb-1 fw-bold text-gradient">项目管理</h4>
                    <p className="mb-0 text-secondary small">
                        {loading ? '加载中...' : error ? `错误: ${error}` : `共 ${projects.length} 个项目`}
                    </p>
                </div>
            </div>
            <div className="d-flex gap-3">
                 <div className="position-relative">
                    <FaSearch className="position-absolute top-50 start-0 translate-middle-y ms-3 text-secondary" />
                    <Form.Control 
                        type="text" 
                        placeholder="搜索项目..." 
                        className="input-pro ps-5" 
                        style={{ width: '250px' }}
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                    />
                 </div>
                 <Button className="btn-pro-primary d-flex align-items-center gap-2" onClick={openCreate}>
                    <FaPlus /> 新建项目
                 </Button>
            </div>
        </div>

        {/* Project List */}
        {loading ? (
             <div className="col-span-12 text-center py-5 text-muted">
                <div className="spinner-border text-primary mb-3" role="status"></div>
                <div>加载项目数据中...</div>
             </div>
        ) : filteredProjects.length === 0 ? (
            <div className="col-span-12 bento-card p-5 text-center d-flex flex-column align-items-center justify-content-center" style={{ minHeight: '300px' }}>
                <div className="bg-light p-4 rounded-circle mb-3 text-secondary">
                    <FaFolder size={40} className="opacity-50" />
                </div>
                <h5 className="text-secondary mb-3">暂无项目</h5>
                <p className="text-muted small mb-4">创建一个新项目开始您的测试工作</p>
                <Button className="btn-pro-primary" onClick={openCreate}>
                    <FaPlus className="me-2" /> 创建第一个项目
                </Button>
            </div>
        ) : (
            <div className="col-span-12 bento-card p-0 overflow-hidden bg-white">
                <Table hover responsive className="mb-0 align-middle">
                    <thead className="bg-light text-secondary small">
                        <tr>
                            <th className="ps-4 border-0">项目名称</th>
                            <th className="border-0">描述</th>
                            <th className="border-0">层级</th>
                            <th className="border-0">创建时间</th>
                            <th className="border-0">父项目ID</th>
                            <th className="text-end pe-4 border-0">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        {currentProjects.map(p => (
                            <tr key={p.id} className="transition-all hover-bg-light">
                                <td className="ps-4 border-light">
                                    <div className="fw-bold text-dark">{p.name}</div>
                                </td>
                                <td className="text-secondary small border-light" style={{maxWidth: '300px'}}>
                                    <div className="text-truncate" title={p.description || ''}>{p.description || '-'}</div>
                                </td>
                                <td className="border-light">
                                    <Badge bg={p.level === 0 ? 'primary' : 'secondary'} className="bg-opacity-10 text-reset fw-normal">
                                        {p.level === 0 ? '顶级项目' : '子项目'}
                                    </Badge>
                                </td>
                                <td className="text-secondary small border-light">
                                     {p.created_at ? new Date(p.created_at).toLocaleDateString() : '-'}
                                </td>
                                <td className="text-secondary small border-light">
                                    {p.parent_id || '-'}
                                </td>
                                <td className="text-end pe-4 border-light">
                                     <div className="d-flex justify-content-end gap-3">
                                         <Button variant="link" className="p-0 text-primary opacity-75 hover-opacity-100" onClick={() => openEdit(p)} title="编辑">
                                             <FaEdit />
                                         </Button>
                                         <Button variant="link" className="p-0 text-danger opacity-75 hover-opacity-100" onClick={() => handleDelete(p.id, p.name)} title="删除">
                                             <FaTrash />
                                         </Button>
                                     </div>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </Table>
                {totalPages > 1 && (
                    <div className="d-flex justify-content-center py-3 border-top border-light">
                        <Pagination className="mb-0">
                            <Pagination.Prev 
                                onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                                disabled={currentPage === 1}
                            />
                            {[...Array(totalPages)].map((_, idx) => (
                                <Pagination.Item 
                                    key={idx + 1} 
                                    active={idx + 1 === currentPage}
                                    onClick={() => setCurrentPage(idx + 1)}
                                >
                                    {idx + 1}
                                </Pagination.Item>
                            ))}
                            <Pagination.Next 
                                onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                                disabled={currentPage === totalPages}
                            />
                        </Pagination>
                    </div>
                )}
            </div>
        )}

      <Modal show={showCreate} onHide={() => setShowCreate(false)} centered backdrop="static">
        <Modal.Header closeButton className="border-0 pb-0">
            <Modal.Title className="fw-bold">新建项目</Modal.Title>
        </Modal.Header>
        <Modal.Body className="pt-2">{renderModalContent()}</Modal.Body>
        <Modal.Footer className="border-0 pt-0">
          <Button variant="light" onClick={() => setShowCreate(false)}>取消</Button>
          <Button className="btn-pro-primary" onClick={handleCreate} disabled={actionLoading}>
             {actionLoading ? '创建中...' : '立即创建'}
          </Button>
        </Modal.Footer>
      </Modal>

      <Modal show={showEdit} onHide={() => setShowEdit(false)} centered backdrop="static">
        <Modal.Header closeButton className="border-0 pb-0">
            <Modal.Title className="fw-bold">编辑项目</Modal.Title>
        </Modal.Header>
        <Modal.Body className="pt-2">{renderModalContent()}</Modal.Body>
        <Modal.Footer className="border-0 pt-0">
          <Button variant="light" onClick={() => setShowEdit(false)}>取消</Button>
          <Button className="btn-pro-primary" onClick={handleUpdate} disabled={actionLoading}>
             {actionLoading ? '保存中...' : '保存更改'}
          </Button>
        </Modal.Footer>
      </Modal>
    </div>
  );
}
