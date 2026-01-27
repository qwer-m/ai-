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
  const [page, setPage] = useState(() => {
      if (!projectId) return 1;
      const saved = sessionStorage.getItem(`kb_page_${projectId}`);
      return saved ? parseInt(saved) : 1;
  });
  const [totalPages, setTotalPages] = useState(1);
  const [totalItems, setTotalItems] = useState(0);
  
  // Filters
  const [searchTerm, setSearchTerm] = useState(() => sessionStorage.getItem(`kb_search_${projectId}`) || '');
  const [filterDocType, setFilterDocType] = useState(() => sessionStorage.getItem(`kb_type_${projectId}`) || '');
  const [startDate, setStartDate] = useState(() => sessionStorage.getItem(`kb_start_${projectId}`) || '');
  const [endDate, setEndDate] = useState(() => sessionStorage.getItem(`kb_end_${projectId}`) || '');

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
  
  // Refs for tracking changes
  const prevProjectId = useRef(projectId);
  const prevFilters = useRef({ searchTerm, filterDocType, startDate, endDate });

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

  // Persist Filters & Page
  useEffect(() => {
    if (!projectId) return;
    sessionStorage.setItem(`kb_search_${projectId}`, searchTerm);
    sessionStorage.setItem(`kb_type_${projectId}`, filterDocType);
    sessionStorage.setItem(`kb_start_${projectId}`, startDate);
    sessionStorage.setItem(`kb_end_${projectId}`, endDate);
  }, [projectId, searchTerm, filterDocType, startDate, endDate]);

  // Restore Page on Mount/Project Change
  useEffect(() => {
      if (!projectId) return;
      const savedPage = sessionStorage.getItem(`kb_page_${projectId}`);
      if (savedPage) {
          const p = parseInt(savedPage);
          if (p !== page) setPage(p);
      }
  }, [projectId]);

  // Old fetchList removed in favor of doFetchList and wrapper below


  useEffect(() => {
    if (!projectId) {
        setDocs([]);
        return;
    }

    const filtersChanged = 
        searchTerm !== prevFilters.current.searchTerm ||
        filterDocType !== prevFilters.current.filterDocType ||
        startDate !== prevFilters.current.startDate ||
        endDate !== prevFilters.current.endDate;
    
    // Check if project just changed (or first mount with project)
    const projectChanged = projectId !== prevProjectId.current;

    // If filters changed, reset to page 1
    if (filtersChanged) {
        fetchList(1);
    } else if (projectChanged) {
        // If project changed, we need to reload filters for the NEW project from session
        // However, useState only runs once. We need to manually update state to match session.
        const pId = projectId!; // Safe because we returned if !projectId
        const savedSearch = sessionStorage.getItem(`kb_search_${pId}`) || '';
        const savedType = sessionStorage.getItem(`kb_type_${pId}`) || '';
        const savedStart = sessionStorage.getItem(`kb_start_${pId}`) || '';
        const savedEnd = sessionStorage.getItem(`kb_end_${pId}`) || '';
        const savedPage = sessionStorage.getItem(`kb_page_${pId}`);
        
        // Batch updates
        setSearchTerm(savedSearch);
        setFilterDocType(savedType);
        setStartDate(savedStart);
        setEndDate(savedEnd);
        
        // Use saved page or default to 1
        const targetPage = savedPage ? parseInt(savedPage) : 1;
        
        // We need to fetch with the *new* values, not the current state (which is old)
        // fetchList uses state, so we can't call it immediately if state update is async.
        // But we can call the API directly or pass params to fetchList if we refactor it.
        // Better: Refactor fetchList to accept overrides.
        // For now, let's just update state. The next effect cycle might not catch it if we don't include them in deps?
        // Actually, if we set state, it triggers re-render.
        // We should *not* call fetchList here if we want the re-render to handle it.
        // But the re-render will trigger this effect again.
        // We need to avoid infinite loop.
        
        // Strategy: 
        // 1. Update state.
        // 2. The state change will trigger this effect again.
        // 3. In the next run, `projectChanged` will be false (because we update prevProjectId now).
        // 4. `filtersChanged` might be true if we updated them?
        //    If we update `searchTerm`, `prevFilters` (ref) is still old.
        //    So `filtersChanged` will be true.
        //    Then it calls `fetchList(1)`.
        //    Wait, if we load saved page, we don't want page 1.
        
        // Let's modify logic:
        // If project changed:
        //   Load saved filters & page.
        //   Set states.
        //   Set `prevProjectId` to new.
        //   Set `prevFilters` to NEW values.
        //   Call fetchList(savedPage) with NEW values.
        
        // But fetchList uses `searchTerm` state. We can't guarantee state is updated instantly.
        // So we need to pass params to fetchList.
        
        // Let's change fetchList signature or usage.
        doFetchList(pId, targetPage, savedSearch, savedType, savedStart, savedEnd);
        
        // Update refs manually to prevent double-fetch in next render
        prevFilters.current = { 
            searchTerm: savedSearch, 
            filterDocType: savedType, 
            startDate: savedStart, 
            endDate: savedEnd 
        };
    } else {
        // If only project changed (or mount), try to restore page
        const savedPage = sessionStorage.getItem(`kb_page_${projectId}`);
        const targetPage = savedPage ? parseInt(savedPage) : 1;
        fetchList(targetPage);
    }

    // Update refs
    prevProjectId.current = projectId;
    if (!projectChanged) {
       prevFilters.current = { searchTerm, filterDocType, startDate, endDate };
    }
  }, [projectId, searchTerm, filterDocType, startDate, endDate]);

  const doFetchList = async (pid: number, p: number, search: string, type: string, start: string, end: string) => {
      setLoading(true);
      try {
        let url = `/api/knowledge-list?project_id=${pid}&page=${p}&page_size=8`;
        if (search) url += `&search=${encodeURIComponent(search)}`;
        if (type) url += `&doc_type=${encodeURIComponent(type)}`;
        if (start) url += `&start_date=${encodeURIComponent(start)}`;
        if (end) url += `&end_date=${encodeURIComponent(end)}`;

        const data = await api.get<any>(url);
        
        if (data.documents) {
          setDocs(data.documents);
          setPage(data.pagination.page); // Should be p
          setTotalPages(data.pagination.total_pages);
          setTotalItems(data.pagination.total || data.documents.length);
          sessionStorage.setItem(`kb_page_${pid}`, String(data.pagination.page));
        } else {
          setDocs([]);
          setTotalItems(0);
        }
        return data;
      } catch (e) {
        onLog(`获取列表失败: ${e}`);
        return null;
      } finally {
        setLoading(false);
      }
  };
  
  // Wrapper for existing calls that use state
  const fetchList = (p = 1) => {
      if (!projectId) return;
      doFetchList(projectId, p, searchTerm, filterDocType, startDate, endDate);
  };

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
        fetchList(page);
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

  // DnD
  const dragItem = useRef<number | null>(null);
  const draggedDocRef = useRef<Doc | null>(null);
  const pageSwitchTimer = useRef<any>(null);
  const [dragTarget, setDragTarget] = useState<{index: number, position: 'before' | 'after'} | null>(null);

  const handleDragStart = (e: React.DragEvent, index: number, doc: Doc) => {
      dragItem.current = index;
      draggedDocRef.current = doc;
      e.dataTransfer.effectAllowed = "move";
      // 设置透明图像以避免默认的拖拽残影遮挡视线（可选）
      // const img = new Image();
      // img.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
      // e.dataTransfer.setDragImage(img, 0, 0);
  };

  const handleItemDragOver = (e: React.DragEvent, index: number) => {
      e.preventDefault();
      e.stopPropagation();

      const rect = e.currentTarget.getBoundingClientRect();
      const midY = rect.top + rect.height / 2;
      const position = e.clientY < midY ? 'before' : 'after';

      if (!dragTarget || dragTarget.index !== index || dragTarget.position !== position) {
          setDragTarget({ index, position });
      }
  };
  
  const handleDragLeave = () => {
      // 可以在这里做一些清理，但由于 bubble 机制，可能需要谨慎
  };

  const handlePageDragEnter = (targetPage: number) => {
      if (pageSwitchTimer.current) clearTimeout(pageSwitchTimer.current);
      pageSwitchTimer.current = setTimeout(() => {
          if (targetPage !== page) {
              fetchList(targetPage);
          }
      }, 600);
  };

  const handlePageDragLeave = () => {
       if (pageSwitchTimer.current) clearTimeout(pageSwitchTimer.current);
  };

  const handlePageDrop = async (e: React.DragEvent, targetPage: number) => {
      e.preventDefault();
      e.stopPropagation();
      
      if (pageSwitchTimer.current) clearTimeout(pageSwitchTimer.current);

      const draggedDoc = draggedDocRef.current;
      if (!draggedDoc || !projectId) {
          resetDrag();
          return;
      }

      // If we are already on this page, and no specific item target, 
      // maybe we just want to move to end?
      // But if we are on same page, handleDrop usually handles sorting within list.
      // If user drops on page number of CURRENT page, let's treat it as "Move to End of Page".
      
      setLoading(true);
      try {
          // Switch to target page and get its content
          const data = await doFetchList(projectId, targetPage, searchTerm, filterDocType, startDate, endDate);
          
          if (data && data.documents && data.documents.length > 0) {
             // To append to this page, we place it AFTER the last item
             const anchor = data.documents[data.documents.length - 1];
             
             // Avoid self-referencing if somehow it's the same
             if (anchor.global_id !== draggedDoc.global_id) {
                 await api.post('/api/knowledge/move', {
                    project_id: projectId,
                    doc_id: draggedDoc.global_id,
                    anchor_doc_id: anchor.global_id,
                    position: 'after'
                 });
                 // Fetch again to show updated order
                 fetchList(targetPage);
                 setToastMsg({ type: 'success', msg: `已移动到第 ${targetPage} 页末尾` });
             }
          }
      } catch (e) {
          setToastMsg({ type: 'error', msg: `移动失败: ${e}` });
      } finally {
          resetDrag();
          setLoading(false);
      }
  };

  const resetDrag = () => {
      dragItem.current = null;
      draggedDocRef.current = null;
      setDragTarget(null);
      if (pageSwitchTimer.current) clearTimeout(pageSwitchTimer.current);
  };

  const handleDrop = async (e: React.DragEvent) => {
      e.preventDefault();
      
      // Reset timer
      if (pageSwitchTimer.current) clearTimeout(pageSwitchTimer.current);

      const draggedDoc = draggedDocRef.current;
      if (!draggedDoc) {
          resetDrag();
          return;
      }

      if (!dragTarget) {
          resetDrag();
          return;
      }

      const { index: dropIndex, position } = dragTarget;
      const anchorDoc = docs[dropIndex];

      if (!anchorDoc) {
          resetDrag();
          return;
      }

      // Check if same doc
      if (anchorDoc.global_id === draggedDoc.global_id) {
          resetDrag();
          return;
      }

      // 检查是否是同页面的相邻位置移动（无效移动）
      // 如果在同一页，且移动到自己前面或后面，或者移动到相邻元素导致顺序不变
      // 这里的逻辑稍微复杂，简化处理：只要不是自己，就提交给后端，后端会判断顺序
      // 但为了减少请求，可以做简单判断
      // 比如：index 5, drop on 4 (after) -> index 5 (no change)
      // index 5, drop on 6 (before) -> index 5 (no change)
      
      const isSamePage = docs.some(d => d.global_id === draggedDoc.global_id);
      if (isSamePage) {
           const dragIndex = dragItem.current; // 只有同页时 dragIndex 才可靠
           if (dragIndex === dropIndex) {
               // 自己拖到自己身上，但在不同半区？
               // before: no change. after: no change.
               resetDrag();
               return;
           }
           if (dragIndex !== null) {
               if (position === 'before' && dropIndex === dragIndex + 1) {
                   // 拖到下一个元素的上半部分 -> 还是原来的位置
                   resetDrag();
                   return;
               }
               if (position === 'after' && dropIndex === dragIndex - 1) {
                   // 拖到上一个元素的下半部分 -> 还是原来的位置
                   resetDrag();
                   return;
               }
           }
      }

      setLoading(true);
      try {
          await api.post('/api/knowledge/move', {
              project_id: projectId,
              doc_id: draggedDoc.global_id,
              anchor_doc_id: anchorDoc.global_id,
              position: position
          });
          // Refresh list to show new order
          fetchList(page);
      } catch (e) {
          setToastMsg({ type: 'error', msg: `移动失败: ${e}` });
          fetchList(page); // Revert
      } finally {
          resetDrag();
          setLoading(false);
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
                        <Col md={2} className="d-flex align-items-end">
                            <Form.Select size="sm" value={filterDocType} onChange={e => setFilterDocType(e.target.value)} aria-label="文档类型过滤">
                                <option value="">所有类型</option>
                                <option value="requirement">需求文档</option>
                                <option value="test_case">测试用例</option>
                                <option value="prototype">原型图</option>
                                <option value="product_requirement">产品需求</option>
                                <option value="incomplete">残缺文档</option>
                            </Form.Select>
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
                  {docs.map((doc, index) => (
                      <Col 
                        key={doc.global_id} 
                        md={6} lg={4} xl={3}
                        draggable
                        onDragStart={(e) => handleDragStart(e, index, doc)}
                        onDragOver={(e) => handleItemDragOver(e, index)}
                        onDrop={handleDrop}
                        style={{ 
                            cursor: 'move',
                            transition: 'all 0.1s',
                            transform: dragTarget?.index === index 
                                ? (dragTarget.position === 'before' ? 'translateY(2px)' : 'translateY(-2px)') 
                                : 'none',
                            boxShadow: dragTarget?.index === index 
                                ? (dragTarget.position === 'before' ? '0 -4px 0 0 #0d6efd' : '0 4px 0 0 #0d6efd') 
                                : 'none'
                        }}
                      >
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
                                  
                                  <Card.Title className="h6 mb-3 flex-grow-1" style={{ lineHeight: '1.4', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden', textOverflow: 'ellipsis', wordBreak: 'break-all' }} title={doc.filename}>
                                      {doc.filename}
                                  </Card.Title>

                                  {/* Associated Cases (Highlighted Area) */}
                                  <div className="bg-white bg-opacity-50 rounded p-2 mb-3 border border-light" style={{ maxHeight: '150px', overflowY: 'auto' }}>
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
                <Pagination.First 
                    onClick={() => fetchList(1)} 
                    disabled={page === 1} 
                    onDragEnter={() => !pageSwitchTimer.current && page > 1 && handlePageDragEnter(1)}
                    onDragOver={(e) => e.preventDefault()}
                    onDragLeave={handlePageDragLeave}
                    onDrop={(e) => handlePageDrop(e, 1)}
                />
                <Pagination.Prev 
                    onClick={() => fetchList(page - 1)} 
                    disabled={page === 1} 
                    onDragEnter={() => !pageSwitchTimer.current && page > 1 && handlePageDragEnter(page - 1)}
                    onDragOver={(e) => e.preventDefault()}
                    onDragLeave={handlePageDragLeave}
                    onDrop={(e) => page > 1 && handlePageDrop(e, page - 1)}
                />
                
                {[...Array(totalPages)].map((_, i) => {
                    const p = i + 1;
                    // Show first, last, and window around current
                    if (p === 1 || p === totalPages || Math.abs(page - p) <= 2) {
                        return (
                             <Pagination.Item 
                                key={p} 
                                active={p === page} 
                                onClick={() => fetchList(p)}
                                onDragEnter={() => handlePageDragEnter(p)}
                                onDragOver={(e) => e.preventDefault()}
                                onDragLeave={handlePageDragLeave}
                                onDrop={(e) => handlePageDrop(e, p)}
                             >
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

                <Pagination.Next 
                    onClick={() => fetchList(page + 1)} 
                    disabled={page === totalPages}
                    onDragEnter={() => !pageSwitchTimer.current && page < totalPages && handlePageDragEnter(page + 1)}
                    onDragOver={(e) => e.preventDefault()}
                    onDragLeave={handlePageDragLeave}
                    onDrop={(e) => page < totalPages && handlePageDrop(e, page + 1)}
                />
                <Pagination.Last 
                    onClick={() => fetchList(totalPages)} 
                    disabled={page === totalPages}
                    onDragEnter={() => !pageSwitchTimer.current && page < totalPages && handlePageDragEnter(totalPages)}
                    onDragOver={(e) => e.preventDefault()}
                    onDragLeave={handlePageDragLeave}
                    onDrop={(e) => handlePageDrop(e, totalPages)}
                />
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
