import { useState, useEffect, useMemo } from 'react';
import { Alert, Badge, Button, Form, InputGroup, Modal, Pagination, Table } from 'react-bootstrap';
import { FaEdit, FaFolderOpen, FaPlus, FaSearch, FaTrash } from 'react-icons/fa';
import { api } from '../../utils/api';

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

export function ProjectManagement({ projects, loading, error, onRefresh, onSelectProject, onLog }: Props) {
  const [showCreate, setShowCreate] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [searchTerm, setSearchTerm] = useState('');

  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [parentId, setParentId] = useState<number | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 10;

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
    if (!name.trim()) {
      window.alert('请输入项目名称');
      return;
    }
    setActionLoading(true);
    try {
      await api.post('/api/projects', { name: name.trim(), description: desc.trim() || null, parent_id: parentId });
      onLog(`创建项目成功: ${name.trim()}`);
      setShowCreate(false);
      onRefresh();
    } catch (e) {
      const msg = String(e);
      onLog(`创建项目失败: ${msg}`);
      window.alert(msg);
    } finally {
      setActionLoading(false);
    }
  };

  const handleUpdate = async () => {
    if (!editId || !name.trim()) return;
    setActionLoading(true);
    try {
      const data = await api.put<any>(`/api/projects/${editId}`, {
        name: name.trim(),
        description: desc.trim() || null,
        parent_id: parentId,
      });
      if (data?.error) throw new Error(data.error);
      onLog(`更新项目成功: ${name.trim()}`);
      setShowEdit(false);
      onRefresh();
    } catch (e) {
      const msg = String(e);
      onLog(`更新项目失败: ${msg}`);
      window.alert(msg);
    } finally {
      setActionLoading(false);
    }
  };

  const handleDelete = async (id: number, projectName: string) => {
    if (!window.confirm(`确定删除项目 “${projectName}”？此操作不可恢复，并会删除关联数据。`)) return;
    try {
      const data = await api.delete<any>(`/api/projects/${id}`);
      if (data?.error) throw new Error(data.error);
      onLog(`删除项目成功: ${projectName}`);
      onRefresh();
    } catch (e) {
      const msg = String(e);
      onLog(`删除项目失败: ${msg}`);
      window.alert(msg);
    }
  };

  const filteredProjects = useMemo(() => {
    const keyword = searchTerm.trim().toLowerCase();
    if (!keyword) return projects;
    return projects.filter((p) => {
      const nameHit = p.name.toLowerCase().includes(keyword);
      const descHit = (p.description || '').toLowerCase().includes(keyword);
      return nameHit || descHit;
    });
  }, [projects, searchTerm]);

  useEffect(() => {
    setCurrentPage(1);
  }, [searchTerm]);

  const totalPages = Math.max(1, Math.ceil(filteredProjects.length / itemsPerPage));
  const currentProjects = filteredProjects.slice((currentPage - 1) * itemsPerPage, currentPage * itemsPerPage);

  const renderModalForm = () => (
    <Form>
      <Form.Group className="mb-3">
        <Form.Label>项目名称</Form.Label>
        <Form.Control
          className="input-pro"
          value={name}
          onChange={(e) => setName(e.target.value)}
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
          onChange={(e) => setDesc(e.target.value)}
          placeholder="描述项目目标与范围（可选）"
        />
      </Form.Group>
      <Form.Group className="mb-1">
        <Form.Label>父项目</Form.Label>
        <Form.Select className="input-pro" value={parentId ?? ''} onChange={(e) => setParentId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">无（作为顶级项目）</option>
          {projects.filter((p) => p.id !== editId).map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </Form.Select>
      </Form.Group>
    </Form>
  );

  return (
    <div className="bento-grid h-100 align-content-start project-page-shell">
      <div className="bento-card col-span-12 p-4 d-flex flex-wrap align-items-center justify-content-between gap-3 project-page-header">
        <div className="d-flex align-items-center gap-3">
          <div className="project-page-icon">
            <FaFolderOpen size={18} />
          </div>
          <div>
            <h4 className="mb-1 fw-bold">项目管理</h4>
            <div className="small text-muted">
              {loading ? '项目数据加载中...' : `共 ${projects.length} 个项目`}
            </div>
          </div>
        </div>

        <div className="d-flex flex-wrap align-items-center gap-2 project-page-actions">
          <InputGroup className="project-search-box">
            <InputGroup.Text><FaSearch /></InputGroup.Text>
            <Form.Control value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)} placeholder="搜索项目名称或描述" />
          </InputGroup>
          <Button className="btn-pro-primary d-flex align-items-center gap-2" onClick={openCreate}>
            <FaPlus /> 新建项目
          </Button>
        </div>
      </div>

      {error ? (
        <div className="col-span-12">
          <Alert variant="danger" className="mb-0">{error}</Alert>
        </div>
      ) : null}

      {loading ? (
        <div className="col-span-12 ui-section-card text-center py-5 text-muted">加载项目数据中...</div>
      ) : filteredProjects.length === 0 ? (
        <div className="col-span-12 ui-section-card text-center py-5">
          <div className="text-muted mb-2">暂无匹配项目</div>
          <Button variant="outline-primary" onClick={openCreate}>创建第一个项目</Button>
        </div>
      ) : (
        <div className="col-span-12 ui-section-card p-0 overflow-hidden">
          <div className="table-responsive project-page-table-wrap">
            <Table hover className="mb-0 align-middle project-page-table">
              <thead>
                <tr>
                  <th className="ps-4">项目名称</th>
                  <th>描述</th>
                  <th>层级</th>
                  <th>创建时间</th>
                  <th>父项目</th>
                  <th className="text-end pe-4">操作</th>
                </tr>
              </thead>
              <tbody>
                {currentProjects.map((p) => (
                  <tr key={p.id}>
                    <td className="ps-4 fw-semibold">{p.name}</td>
                    <td className="text-muted project-page-desc-cell">
                      <div className="text-truncate" title={p.description || ''}>{p.description || '-'}</div>
                    </td>
                    <td>
                      <Badge bg={p.level === 0 ? 'primary' : 'secondary'}>{p.level === 0 ? '顶级项目' : '子项目'}</Badge>
                    </td>
                    <td>{p.created_at ? new Date(p.created_at).toLocaleDateString() : '-'}</td>
                    <td>{p.parent_id || '-'}</td>
                    <td className="text-end pe-4">
                      <div className="d-inline-flex gap-2">
                        <Button size="sm" variant="outline-secondary" onClick={() => onSelectProject(p.id)}>切换</Button>
                        <Button size="sm" variant="outline-primary" onClick={() => openEdit(p)} title="编辑"><FaEdit /></Button>
                        <Button size="sm" variant="outline-danger" onClick={() => void handleDelete(p.id, p.name)} title="删除"><FaTrash /></Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>

          {totalPages > 1 ? (
            <div className="d-flex justify-content-center py-3 border-top">
              <Pagination className="mb-0">
                <Pagination.Prev onClick={() => setCurrentPage((prev) => Math.max(1, prev - 1))} disabled={currentPage === 1} />
                {Array.from({ length: totalPages }).map((_, idx) => {
                  const page = idx + 1;
                  return (
                    <Pagination.Item key={page} active={page === currentPage} onClick={() => setCurrentPage(page)}>
                      {page}
                    </Pagination.Item>
                  );
                })}
                <Pagination.Next onClick={() => setCurrentPage((prev) => Math.min(totalPages, prev + 1))} disabled={currentPage === totalPages} />
              </Pagination>
            </div>
          ) : null}
        </div>
      )}

      <Modal show={showCreate} onHide={() => setShowCreate(false)} centered backdrop="static" dialogClassName="project-page-modal">
        <Modal.Header closeButton>
          <Modal.Title>新建项目</Modal.Title>
        </Modal.Header>
        <Modal.Body>{renderModalForm()}</Modal.Body>
        <Modal.Footer>
          <Button variant="outline-secondary" onClick={() => setShowCreate(false)}>取消</Button>
          <Button className="btn-pro-primary" onClick={handleCreate} disabled={actionLoading}>{actionLoading ? '创建中...' : '创建'}</Button>
        </Modal.Footer>
      </Modal>

      <Modal show={showEdit} onHide={() => setShowEdit(false)} centered backdrop="static" dialogClassName="project-page-modal">
        <Modal.Header closeButton>
          <Modal.Title>编辑项目</Modal.Title>
        </Modal.Header>
        <Modal.Body>{renderModalForm()}</Modal.Body>
        <Modal.Footer>
          <Button variant="outline-secondary" onClick={() => setShowEdit(false)}>取消</Button>
          <Button className="btn-pro-primary" onClick={handleUpdate} disabled={actionLoading}>{actionLoading ? '保存中...' : '保存'}</Button>
        </Modal.Footer>
      </Modal>
    </div>
  );
}
