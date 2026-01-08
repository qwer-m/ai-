import { useState, useEffect, useRef } from 'react';
import { Button, Form, Pagination, Badge, Spinner, Card, Row, Col, Modal, Toast, ToastContainer, InputGroup, Placeholder, OverlayTrigger, Popover } from 'react-bootstrap';
import { FaSearch, FaTrash, FaEye, FaFileAlt, FaCalendarAlt, FaExclamationTriangle, FaWifi, FaTimes, FaLink, FaPlus } from 'react-icons/fa';
import { api } from '../utils/api';
import { PreviewModal } from './PreviewModal';

type Props = {
  projectId: number | null;
  onLog: (msg: string) => void;
};

type LinkedDoc = {
    id: number;
    global_id: number;
    filename: string;
    content_preview: string;
};

type Doc = {
  id: number; // project specific id
  global_id: number;
  filename: string;
  doc_type: string;
  created_at: string;
  file_size?: number;
  linked_test_cases?: LinkedDoc[];
  content_preview?: string;
  isNew?: boolean; // For focus management
  _isLinked?: boolean;
};

// --- God-tier Audit Stub ---
const trackOperation = (action: string, metadata: object) => {
    // In production, this would send data to SOC 2 compliant audit logs
    console.log(`[AUDIT] Action: ${action}`, metadata, new Date().toISOString());
};

export function KnowledgeBase({ projectId, onLog }: Props) {
  // Upload State
  const [file, setFile] = useState<File | null>(null);
  const [docType, setDocType] = useState('requirement');
  const [force, setForce] = useState(false);
  const [uploading, setUploading] = useState(false);

  // List State
  const [docs, setDocs] = useState<Doc[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalItems, setTotalItems] = useState(0);
  
  // Filters
  const [searchTerm, setSearchTerm] = useState('');
  const [filterDocType] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');

  // Preview Modal State
  const [showPreview, setShowPreview] = useState(false);
  const [previewDoc, setPreviewDoc] = useState<{title: string, content: string, linkedDocs?: any[]} | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // Manage Modal State
  const [showManage, setShowManage] = useState(false);
  const [manageTarget, setManageTarget] = useState<Doc | null>(null);
  const [candidates, setCandidates] = useState<Doc[]>([]);
  const [manageLoading, setManageLoading] = useState(false);

  // --- God-tier Safety & Feedback State ---
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Doc | null>(null);
  const [toastMsg, setToastMsg] = useState<{type: 'success' | 'error', msg: string} | null>(null);
  const [isOnline, setIsOnline] = useState(navigator.onLine);

  // --- Smart Prefetch Cache ---
  const prefetchCache = useRef<Map<number, any>>(new Map());

  // Offline Listener
  useEffect(() => {
      const handleOnline = () => setIsOnline(true);
      const handleOffline = () => setIsOnline(false);
      window.addEventListener('online', handleOnline);
      window.addEventListener('offline', handleOffline);
      return () => {
          window.removeEventListener('online', handleOnline);
          window.removeEventListener('offline', handleOffline);
      };
  }, []);

  const fetchList = async (p = 1) => {
    if (!projectId) return;
    setLoading(true);
    try {
      let url = `/api/knowledge-list?project_id=${projectId}&page=${p}&page_size=8`; // 8 per page as requested
      if (searchTerm) url += `&search=${encodeURIComponent(searchTerm)}`;
      if (filterDocType) url += `&doc_type=${encodeURIComponent(filterDocType)}`;
      if (startDate) url += `&start_date=${encodeURIComponent(startDate)}`;
      if (endDate) url += `&end_date=${encodeURIComponent(endDate)}`;

      const data = await api.get<any>(url);
      
      if (data.documents) {
        setDocs(data.documents);
        setPage(data.pagination.page);
        setTotalPages(data.pagination.total_pages);
        setTotalItems(data.pagination.total || data.documents.length); // Assuming API might add total
      } else {
        setDocs([]);
        setTotalItems(0);
      }
    } catch (e) {
      onLog(`获取列表失败: ${e}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (projectId) fetchList(1);
    else setDocs([]);
  }, [projectId, searchTerm, filterDocType, startDate, endDate]);

  const handleUpload = async () => {
    if (!projectId) return alert('请先选择项目');
    if (!file) return alert('请选择文件');
    
    setUploading(true);
    const uploadData = new FormData();
    uploadData.append('file', file);
    uploadData.append('project_id', String(projectId));
    uploadData.append('doc_type', docType);
    uploadData.append('force', String(force));

    try {
      const doUpload = async (formData: FormData) => {
          return await api.upload<any>('/api/upload-knowledge', formData);
      };

      let data = await doUpload(uploadData);
      
      if (data.status === 'duplicate') {
        setToastMsg({ type: 'error', msg: `文件 "${data.existing_filename || file.name}" 已存在于知识库中，不允许重复录入。` });
        setFile(null);
      } else if (data.error) {
          throw new Error(data.error);
      } else {
        setToastMsg({ type: 'success', msg: `上传成功: ${data.filename}` });
        setFile(null); 
        fetchList(1);
        trackOperation('upload_document', { filename: data.filename, project_id: projectId });
      }
    } catch (e) {
      setToastMsg({ type: 'error', msg: `上传失败: ${e}` });
    } finally {
      setUploading(false);
      setForce(false);
    }
  };

  const confirmDelete = (doc: Doc) => {
      setDeleteTarget(doc);
      setShowDeleteModal(true);
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      const data = await api.delete<any>(`/api/knowledge/${deleteTarget.global_id}`);
      if (data.error) throw new Error(data.error);
      
      setToastMsg({ type: 'success', msg: `已删除: ${deleteTarget.filename}` });
      trackOperation('delete_document', { 
          document_id: deleteTarget.global_id, 
          file_name: deleteTarget.filename 
      });
      fetchList(page);
    } catch (e) {
      setToastMsg({ type: 'error', msg: `删除失败: ${e}` });
    } finally {
      setShowDeleteModal(false);
      setDeleteTarget(null);
    }
  };

  // --- Smart Prefetch on Hover ---
  const handleMouseEnter = async (doc: Doc) => {
      if (prefetchCache.current.has(doc.global_id)) return;
      
      // Prefetch logic
    try {
        const data = await api.get<any>(`/api/knowledge/${doc.global_id}`);
        if (!data.error) {
            prefetchCache.current.set(doc.global_id, data);
        }
    } catch (e) {
        // Silent fail for prefetch
    }
  };

  const handlePreview = async (doc: Doc) => {
    setPreviewLoading(true);
    setShowPreview(true);
    
    // Check cache first
    if (prefetchCache.current.has(doc.global_id)) {
        const cached = prefetchCache.current.get(doc.global_id);
        setPreviewDoc({ title: cached.filename, content: cached.content });
        setPreviewLoading(false);
        trackOperation('preview_document_cache_hit', { document_id: doc.global_id });
        return;
    }

    setPreviewDoc({ title: doc.filename, content: '加载中...' });
    
    try {
      const data = await api.get<any>(`/api/knowledge/${doc.global_id}`);
      
      if (data.error) {
        setPreviewDoc({ title: doc.filename, content: `加载失败: ${data.error}` });
      } else {
        setPreviewDoc({ title: data.filename, content: data.content });
      }
    } catch (e) {
      setPreviewDoc({ title: doc.filename, content: `请求失败: ${e}` });
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleUnlink = async (_parentDoc: Doc, linkedDoc: LinkedDoc) => {
    if (!confirm(`确定要移除关联测试用例 "${linkedDoc.filename}" 吗？`)) return;
    
    try {
        const data = await api.post<any>('/api/knowledge/update-relation', {
            doc_id: linkedDoc.global_id,
            source_doc_id: -1
        });
        if (data.success) {
            setToastMsg({ type: 'success', msg: '已移除关联' });
            fetchList(page);
        } else {
            throw new Error('Update failed');
        }
    } catch (e) {
        setToastMsg({ type: 'error', msg: `移除失败: ${e}` });
    }
  };

  const handleClean = async () => {
    if (!confirm('确定要清理跨项目关联数据吗？')) return;
    try {
      const data = await api.post<any>('/api/knowledge/clean-cross-project', {});
      setToastMsg({ type: 'success', msg: data.message });
    } catch (e) {
      setToastMsg({ type: 'error', msg: `清理失败: ${e}` });
    }
  };

  const toggleRelation = async (testCase: Doc, isLinked: boolean) => {
    if (!manageTarget) return;
    const newSourceId = isLinked ? -1 : manageTarget.global_id;
    
    try {
         const data = await api.post<any>('/api/knowledge/update-relation', {
             doc_id: testCase.global_id,
             source_doc_id: newSourceId
         });
         if (data.success) {
             setManageTarget(prev => {
                 if (!prev) return null;
                 let newLinks = prev.linked_test_cases ? [...prev.linked_test_cases] : [];
                 if (isLinked) {
                     newLinks = newLinks.filter(d => d.global_id !== testCase.global_id);
                 } else {
                     newLinks.push({
                         id: testCase.id,
                         global_id: testCase.global_id,
                         filename: testCase.filename,
                         content_preview: testCase.content_preview || ''
                     });
                 }
                 return { ...prev, linked_test_cases: newLinks };
             });
             fetchList(page);
         }
    } catch (e) {
        setToastMsg({ type: 'error', msg: `操作失败: ${e}` });
    }
  };

  const openManage = async (doc: Doc) => {
    setManageTarget(doc);
    setShowManage(true);
    setManageLoading(true);
    try {
        const data = await api.get<any>(`/api/knowledge-list?project_id=${projectId}&doc_type=test_case`);
        if (data.documents) {
            // Filter: Only show test cases that are NOT linked to ANY document
            // OR are linked to THIS document
            const available = data.documents.filter((d: any) => {
                if (d.doc_type !== 'test_case') return false;
                // If source_doc_id matches current doc, it is ALREADY linked (and we might want to show it as "linked")
                // If source_doc_id exists and is NOT this doc, it is linked to someone else (hide it)
                if (d.source_doc_id && d.source_doc_id !== doc.global_id) return false; 
                return true;
            });
            // Mark those already linked
            const candidatesWithStatus = available.map((c: any) => ({
                 ...c,
                 _isLinked: c.source_doc_id === doc.global_id
            }));
            
            setCandidates(candidatesWithStatus);
        }
    } catch (e) {
        setToastMsg({ type: 'error', msg: `加载候选列表失败: ${e}` });
    } finally {
        setManageLoading(false);
    }
  };

  const docTypeMap: Record<string, string> = {
    'requirement': '需求文档',
    'test_case': '测试用例',
    'prototype': '原型图',
    'product_requirement': '产品需求',
    'incomplete': '残缺文档'
  };

  const docTypeColor: Record<string, string> = {
      'requirement': 'primary',
      'test_case': 'success',
      'prototype': 'info',
      'product_requirement': 'primary',
      'incomplete': 'warning'
  };

  return (
    <div className="h-100 d-flex flex-column gap-3 position-relative">
      {/* Toast Container */}
      <ToastContainer position="top-end" className="p-3" style={{ zIndex: 1100 }}>
        {toastMsg && (
            <Toast onClose={() => setToastMsg(null)} show={!!toastMsg} delay={3000} autohide bg={toastMsg.type === 'success' ? 'success' : 'danger'}>
                <Toast.Header>
                    <strong className="me-auto">{toastMsg.type === 'success' ? '成功' : '错误'}</strong>
                </Toast.Header>
                <Toast.Body className="text-white">{toastMsg.msg}</Toast.Body>
            </Toast>
        )}
      </ToastContainer>

      {/* Upload & Search Area (Card) */}
      <Card className="border-0 shadow-sm search-card">
        <Card.Body className="p-3">
            <Row className="g-3 align-items-end">
                {/* File Upload */}
                <Col md={3}>
                    <Form.Label className="small fw-bold text-secondary">上传文档 {isOnline ? '' : '(离线)'}</Form.Label>
                    <InputGroup size="sm">
                        <Form.Control type="file" onChange={(e: any) => setFile(e.target.files[0] || null)} disabled={!isOnline} />
                    </InputGroup>
                </Col>
                <Col md={2}>
                    <Form.Label className="small fw-bold text-secondary">类型</Form.Label>
                    <Form.Select size="sm" value={docType} onChange={e => setDocType(e.target.value)} disabled={!isOnline}>
                        <option value="requirement">需求文档</option>
                        <option value="test_case">测试用例</option>
                        <option value="prototype">原型图</option>
                        <option value="product_requirement">产品需求</option>
                        <option value="incomplete">残缺文档</option>
                    </Form.Select>
                </Col>
                <Col md={1}>
                    <Button variant="primary" size="sm" className="w-100" onClick={handleUpload} disabled={uploading || !projectId || !isOnline}>
                        {uploading ? <Spinner size="sm" animation="border" /> : '上传'}
                    </Button>
                </Col>
                
                {/* Filters */}
                <Col className="border-start ps-4">
                    <Row className="g-2">
                        <Col md={4}>
                            <Form.Label className="small fw-bold text-secondary">关键词</Form.Label>
                            <InputGroup size="sm">
                                <InputGroup.Text><FaSearch /></InputGroup.Text>
                                <Form.Control placeholder="搜索文件名..." value={searchTerm} onChange={e => setSearchTerm(e.target.value)} />
                            </InputGroup>
                        </Col>
                        <Col md={4}>
                            <Form.Label className="small fw-bold text-secondary">日期范围</Form.Label>
                            <InputGroup size="sm">
                                <Form.Control type="date" value={startDate} onChange={e => setStartDate(e.target.value)} aria-label="开始日期" />
                                <InputGroup.Text className="px-1">-</InputGroup.Text>
                                <Form.Control type="date" value={endDate} onChange={e => setEndDate(e.target.value)} aria-label="结束日期" />
                            </InputGroup>
                        </Col>
                        <Col md={2} className="d-flex align-items-end">
                            <Button variant="secondary" size="sm" className="w-100" onClick={() => fetchList(1)}>查询</Button>
                        </Col>
                        <Col md={2} className="d-flex align-items-end">
                             <Button variant="outline-warning" size="sm" className="w-100" onClick={handleClean} title="清理无效关联">清理</Button>
                        </Col>
                    </Row>
                </Col>
            </Row>
        </Card.Body>
      </Card>

      {/* Offline Indicator */}
      {!isOnline && (
          <div className="alert alert-warning d-flex align-items-center py-2 mb-0" role="alert">
              <FaWifi className="me-2 offline-badge" /> 
              <strong>离线模式</strong>: 您当前处于离线状态，部分功能不可用。
          </div>
      )}

      {/* Content Area (Responsive Grid) */}
      <div className="flex-grow-1 overflow-auto p-3">
          {loading ? (
              <Row className="g-3">
                  {[...Array(8)].map((_, i) => (
                      <Col key={i} md={6} lg={4} xl={3}>
                          <Card className="h-100 border-0 shadow-sm">
                              <Card.Body>
                                  <Placeholder as={Card.Title} animation="glow">
                                      <Placeholder xs={8} />
                                  </Placeholder>
                                  <Placeholder as={Card.Text} animation="glow">
                                      <Placeholder xs={4} /> <Placeholder xs={6} />
                                      <Placeholder xs={12} className="mt-3" style={{ height: '60px' }} />
                                  </Placeholder>
                              </Card.Body>
                          </Card>
                      </Col>
                  ))}
              </Row>
          ) : docs.length === 0 ? (
              <div className="text-center py-5 text-muted bg-light rounded border border-dashed">
                  <FaFileAlt size={48} className="mb-3 opacity-50" />
                  <h5>暂无文档</h5>
                  <p>上传您的首个测试文档开始构建知识库</p>
              </div>
          ) : (
              <Row className="g-3">
                  {docs.map(doc => (
                      <Col key={doc.global_id} md={6} lg={4} xl={3}>
                          <Card 
                            className={`h-100 doc-card ${['incomplete', 'prototype'].includes(doc.doc_type) ? 'doc-card-texture-warning' : 'doc-card-texture-success'}`}
                            onMouseEnter={() => handleMouseEnter(doc)}
                            tabIndex={0}
                            role="article"
                            aria-label={`${doc.filename}, 类型: ${docTypeMap[doc.doc_type]}`}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter') handlePreview(doc);
                                if (e.key === 'Delete') confirmDelete(doc);
                            }}
                          >
                              <Card.Body className="d-flex flex-column">
                                  {/* Header */}
                                  <div className="d-flex justify-content-between align-items-start mb-2">
                                      <Badge bg={docTypeColor[doc.doc_type] || 'secondary'} className="mb-2">
                                          {docTypeMap[doc.doc_type] || doc.doc_type}
                                      </Badge>
                                      <small className="text-muted d-flex align-items-center gap-1" style={{ fontSize: '0.75rem' }}>
                                          <FaCalendarAlt /> {new Date(doc.created_at).toLocaleDateString()}
                                      </small>
                                  </div>
                                  
                                  <Card.Title className="h6 text-break mb-3 flex-grow-1" style={{ lineHeight: '1.4' }}>
                                      {doc.filename}
                                  </Card.Title>

                                  {/* Associated Cases (Highlighted Area) */}
                                  <div className="bg-white bg-opacity-50 rounded p-2 mb-3 border border-light">
                                      <div className="d-flex justify-content-between align-items-center mb-2">
                                          <small className="text-muted fw-bold" style={{ fontSize: '0.7rem' }}>关联用例</small>
                                          {doc.doc_type === 'requirement' && (
                                              <Button variant="link" className="p-0 text-primary small" style={{ fontSize: '0.7rem', textDecoration: 'none' }} onClick={() => openManage(doc)}>
                                                  <FaLink className="me-1" />管理
                                              </Button>
                                          )}
                                      </div>
                                      {doc.linked_test_cases && doc.linked_test_cases.length > 0 ? (
                                          <div className="d-flex flex-wrap gap-1">
                                              {doc.linked_test_cases.map(ld => (
                                                  <OverlayTrigger
                                                    key={ld.global_id}
                                                    placement="top"
                                                    overlay={
                                                        <Popover id={`popover-${ld.global_id}`}>
                                                            <Popover.Header as="h3" className="fs-6">{ld.filename}</Popover.Header>
                                                            <Popover.Body className="small text-secondary py-2">
                                                                {ld.content_preview ? (
                                                                    <div style={{maxHeight: '150px', overflowY: 'auto'}}>{ld.content_preview}</div>
                                                                ) : '暂无预览'}
                                                            </Popover.Body>
                                                        </Popover>
                                                    }
                                                  >
                                                      <Badge bg="light" text="dark" className="border fw-normal text-truncate position-relative pe-3" style={{ maxWidth: '100%', cursor: 'pointer' }}>
                                                          {ld.filename}
                                                          <span 
                                                              className="position-absolute top-50 end-0 translate-middle-y me-1 text-danger p-0 d-flex align-items-center justify-content-center"
                                                              style={{ width: '12px', height: '12px', borderRadius: '50%' }}
                                                              onClick={(e) => {
                                                                  e.stopPropagation();
                                                                  handleUnlink(doc, ld);
                                                              }}
                                                              title="移除关联"
                                                          >
                                                              <FaTimes size={10} />
                                                          </span>
                                                      </Badge>
                                                  </OverlayTrigger>
                                              ))}
                                          </div>
                                      ) : (
                                          <span className="text-muted small fst-italic">暂无关联</span>
                                      )}
                                  </div>

                                  {/* Footer Actions */}
                                  <div className="d-flex justify-content-between align-items-center mt-auto pt-2 border-top">
                                      <Button variant="outline-primary" size="sm" className="border-0 px-2" onClick={() => handlePreview(doc)}>
                                          <FaEye className="me-1" /> 查看
                                      </Button>
                                      <Button variant="outline-danger" size="sm" className="border-0 px-2" onClick={() => confirmDelete(doc)}>
                                          <FaTrash className="me-1" /> 删除
                                      </Button>
                                  </div>
                              </Card.Body>
                          </Card>
                      </Col>
                  ))}
              </Row>
          )}
      </div>

      {/* Footer Status Bar */}
      <div className="bg-light border-top px-3 py-2 d-flex justify-content-between align-items-center small text-secondary">
          <div>
              共找到 <strong>{totalItems || docs.length}</strong> 个项目
              {page > 1 && ` (第 ${page}/${totalPages} 页)`}
          </div>
          {totalPages > 1 && (
            <Pagination size="sm" className="m-0">
                <Pagination.First onClick={() => fetchList(1)} disabled={page === 1} />
                <Pagination.Prev onClick={() => fetchList(page - 1)} disabled={page === 1} />
                
                {[...Array(totalPages)].map((_, i) => {
                    const p = i + 1;
                    // Show first, last, and window around current
                    if (p === 1 || p === totalPages || Math.abs(page - p) <= 2) {
                        return (
                             <Pagination.Item key={p} active={p === page} onClick={() => fetchList(p)}>
                                {p}
                             </Pagination.Item>
                        );
                    }
                    // Show ellipsis
                    if (p === page - 3 || p === page + 3) {
                        return <Pagination.Ellipsis key={p} disabled />;
                    }
                    return null;
                })}

                <Pagination.Next onClick={() => fetchList(page + 1)} disabled={page === totalPages} />
                <Pagination.Last onClick={() => fetchList(totalPages)} disabled={page === totalPages} />
            </Pagination>
          )}
      </div>

      {/* Delete Confirmation Modal */}
      <Modal show={showDeleteModal} onHide={() => setShowDeleteModal(false)} centered>
        <Modal.Header closeButton className="bg-danger text-white">
          <Modal.Title><FaExclamationTriangle /> 确认删除</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          <p>您确定要永久删除以下文档吗？此操作无法撤销。</p>
          <div className="alert alert-secondary p-2">
              <strong>{deleteTarget?.filename}</strong>
          </div>
          <p className="small text-muted mb-0">这也将解除其与所有测试用例的关联。</p>
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={() => setShowDeleteModal(false)}>取消</Button>
          <Button variant="danger" onClick={handleDelete}>确认删除</Button>
        </Modal.Footer>
      </Modal>

      {/* Document Preview Modal */}
      <PreviewModal 
        show={showPreview} 
        onHide={() => setShowPreview(false)} 
        title={previewDoc?.title || ''} 
        content={previewDoc?.content || ''} 
        loading={previewLoading}
      />

      <Modal show={showManage} onHide={() => setShowManage(false)} centered size="lg">
        <Modal.Header closeButton>
            <Modal.Title className="fw-bold">添加关联用例</Modal.Title>
        </Modal.Header>
        <Modal.Body className="p-0">
            <div className="p-3 bg-light border-bottom">
                <h6 className="mb-1 text-primary">{manageTarget?.filename}</h6>
                <p className="small text-secondary mb-0">点击下列未关联的测试用例以将其添加到当前需求文档。</p>
            </div>
            <div className="overflow-auto" style={{ maxHeight: '400px' }}>
                {manageLoading ? (
                    <div className="text-center py-5">
                        <Spinner animation="border" variant="primary" />
                        <div className="mt-2 text-muted small">加载候选列表...</div>
                    </div>
                ) : candidates.length === 0 ? (
                    <div className="text-center py-5 text-muted">
                        <FaExclamationTriangle className="mb-2" />
                        <p>暂无未关联的测试用例</p>
                    </div>
                ) : (
                    <div className="list-group list-group-flush">
                        {candidates.map(c => (
                            <div key={c.global_id} className={`list-group-item d-flex align-items-center justify-content-between action-hover-bg ${c._isLinked ? 'bg-light' : ''}`}>
                                <div className="d-flex align-items-center gap-3 flex-grow-1">
                                    <Button 
                                        variant={c._isLinked ? "outline-danger" : "outline-primary"}
                                        size="sm" 
                                        className="rounded-circle p-0 d-flex align-items-center justify-content-center"
                                        style={{ width: '24px', height: '24px' }}
                                        onClick={() => toggleRelation(c, !!c._isLinked)}
                                        title={c._isLinked ? "移除关联" : "添加关联"}
                                    >
                                        {c._isLinked ? <FaTimes size={10} /> : <FaPlus size={10} />}
                                    </Button>
                                    <div>
                                        <div className="fw-medium">{c.filename}</div>
                                        <div className="small text-muted">{new Date(c.created_at).toLocaleDateString()} · {(c.file_size || 0) / 1024 < 1 ? '<1KB' : `${((c.file_size || 0)/1024).toFixed(1)}KB`}</div>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </Modal.Body>
        <Modal.Footer>
            <Button variant="secondary" onClick={() => setShowManage(false)}>关闭</Button>
        </Modal.Footer>
      </Modal>
    </div>
  );
}
