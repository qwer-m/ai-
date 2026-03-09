import { useEffect, useRef, useState } from "react";
import { api } from "../../utils/api";
import { normalizeDoc } from "./types";
import type { Doc, DragTarget, LinkedDoc } from "./types";
import type { DragEvent } from "react";

type UseKnowledgeBaseParams = {
  projectId: number | null;
  onLog: (msg: string) => void;
};

// 审计埋点（当前为前端占位实现）
const trackOperation = (action: string, metadata: object) => {
  // 后续可替换为服务端审计日志接口
  console.log(`[AUDIT] Action: ${action}`, metadata, new Date().toISOString());
};

export function useKnowledgeBase({ projectId, onLog }: UseKnowledgeBaseParams) {
  // 上传状态
  const [file, setFile] = useState<File | null>(null);
  const [docType, setDocType] = useState("requirement");
  const [force, setForce] = useState(false);
  const [uploading, setUploading] = useState(false);

  // 列表状态
  const [docs, setDocs] = useState<Doc[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(() => {
    if (!projectId) return 1;
    const saved = sessionStorage.getItem(`kb_page_${projectId}`);
    return saved ? parseInt(saved) : 1;
  });
  const [totalPages, setTotalPages] = useState(1);
  const [totalItems, setTotalItems] = useState(0);

  // 搜索与筛选状态
  const [searchTerm, setSearchTerm] = useState(
    () => sessionStorage.getItem(`kb_search_${projectId}`) || "",
  );
  const [filterDocType, setFilterDocType] = useState(
    () => sessionStorage.getItem(`kb_type_${projectId}`) || "",
  );
  const [startDate, setStartDate] = useState(
    () => sessionStorage.getItem(`kb_start_${projectId}`) || "",
  );
  const [endDate, setEndDate] = useState(
    () => sessionStorage.getItem(`kb_end_${projectId}`) || "",
  );

  // 预览弹窗状态
  const [showPreview, setShowPreview] = useState(false);
  const [previewDoc, setPreviewDoc] = useState<{
    title: string;
    content: string;
    linkedDocs?: any[];
  } | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // 关联管理弹窗状态
  const [showManage, setShowManage] = useState(false);
  const [manageTarget, setManageTarget] = useState<Doc | null>(null);
  const [candidates, setCandidates] = useState<Doc[]>([]);
  const [manageLoading, setManageLoading] = useState(false);

  // 删除确认与提示反馈状态
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Doc | null>(null);
  const [toastMsg, setToastMsg] = useState<{
    type: "success" | "error";
    msg: string;
  } | null>(null);
  const [isOnline, setIsOnline] = useState(navigator.onLine);

  // 预取缓存（鼠标移入时提前拉取文档详情）
  const prefetchCache = useRef<Map<number, any>>(new Map());

  // 上一次项目与筛选状态（用于避免重复请求）
  const prevProjectId = useRef(projectId);
  const prevFilters = useRef({ searchTerm, filterDocType, startDate, endDate });

  // 拖拽排序状态
  const dragItem = useRef<number | null>(null);
  const draggedDocRef = useRef<Doc | null>(null);
  const pageSwitchTimer = useRef<any>(null);
  const [dragTarget, setDragTarget] = useState<DragTarget | null>(null);

  // 在线/离线监听：离线时禁用上传
  useEffect(() => {
    const handleOnline = () => setIsOnline(true);
    const handleOffline = () => setIsOnline(false);
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, []);

  // 持久化筛选条件（按项目隔离）
  useEffect(() => {
    if (!projectId) return;
    sessionStorage.setItem(`kb_search_${projectId}`, searchTerm);
    sessionStorage.setItem(`kb_type_${projectId}`, filterDocType);
    sessionStorage.setItem(`kb_start_${projectId}`, startDate);
    sessionStorage.setItem(`kb_end_${projectId}`, endDate);
  }, [projectId, searchTerm, filterDocType, startDate, endDate]);

  // 项目切换时恢复历史页码
  useEffect(() => {
    if (!projectId) return;
    const savedPage = sessionStorage.getItem(`kb_page_${projectId}`);
    if (savedPage) {
      const p = parseInt(savedPage);
      if (p !== page) setPage(p);
    }
  }, [projectId, page]);

  const doFetchList = async (
    pid: number,
    p: number,
    search: string,
    type: string,
    start: string,
    end: string,
  ) => {
    setLoading(true);
    try {
      let url = `/api/knowledge-list?project_id=${pid}&page=${p}&page_size=8`;
      if (search) url += `&search=${encodeURIComponent(search)}`;
      if (type) url += `&doc_type=${encodeURIComponent(type)}`;
      if (start) url += `&start_date=${encodeURIComponent(start)}`;
      if (end) url += `&end_date=${encodeURIComponent(end)}`;

      const data = await api.get<any>(url);

      if (Array.isArray(data.documents)) {
        const normalizedDocs = data.documents.map(normalizeDoc);
        setDocs(normalizedDocs);
        setPage(data.pagination.page); // 回写后端分页结果
        setTotalPages(data.pagination.total_pages);
        setTotalItems(data.pagination.total || normalizedDocs.length);
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

  // 兼容现有调用：使用当前筛选状态发起请求
  const fetchList = (p = 1) => {
    if (!projectId) return;
    doFetchList(projectId, p, searchTerm, filterDocType, startDate, endDate);
  };

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

    // 判断项目是否发生切换（含首次进入）
    const projectChanged = projectId !== prevProjectId.current;

    // 筛选条件变化时默认回到第一页
    if (filtersChanged) {
      fetchList(1);
    } else if (projectChanged) {
      // 项目切换后，从 sessionStorage 恢复该项目的筛选状态与页码
      const pId = projectId; // 前面已判空，这里可安全使用
      const savedSearch = sessionStorage.getItem(`kb_search_${pId}`) || "";
      const savedType = sessionStorage.getItem(`kb_type_${pId}`) || "";
      const savedStart = sessionStorage.getItem(`kb_start_${pId}`) || "";
      const savedEnd = sessionStorage.getItem(`kb_end_${pId}`) || "";
      const savedPage = sessionStorage.getItem(`kb_page_${pId}`);

      // 批量回填筛选状态
      setSearchTerm(savedSearch);
      setFilterDocType(savedType);
      setStartDate(savedStart);
      setEndDate(savedEnd);

      // 恢复历史页码，不存在则回到第一页
      const targetPage = savedPage ? parseInt(savedPage) : 1;

      // 直接带恢复参数请求，避免等待 setState 异步生效
      doFetchList(pId, targetPage, savedSearch, savedType, savedStart, savedEnd);

      // 手动更新对比基线，防止下一轮 effect 误判为筛选变化
      prevFilters.current = {
        searchTerm: savedSearch,
        filterDocType: savedType,
        startDate: savedStart,
        endDate: savedEnd,
      };
    } else {
      // 普通渲染时，按当前项目历史页码拉取
      const savedPage = sessionStorage.getItem(`kb_page_${projectId}`);
      const targetPage = savedPage ? parseInt(savedPage) : 1;
      fetchList(targetPage);
    }

    // 更新 refs，供下一轮 effect 比较
    prevProjectId.current = projectId;
    if (!projectChanged) {
      prevFilters.current = { searchTerm, filterDocType, startDate, endDate };
    }
  }, [projectId, searchTerm, filterDocType, startDate, endDate]);

  const handleUpload = async () => {
    if (!projectId) return alert("请先选择项目");
    if (!file) return alert("请选择文件");

    setUploading(true);
    const uploadData = new FormData();
    uploadData.append("file", file);
    uploadData.append("project_id", String(projectId));
    uploadData.append("doc_type", docType);
    uploadData.append("force", String(force));

    try {
      const data = await api.upload<any>("/api/upload-knowledge", uploadData);

      if (data.status === "duplicate") {
        setToastMsg({
          type: "error",
          msg: `文件 "${data.existing_filename || file.name}" 已存在于知识库中，不允许重复录入。`,
        });
        setFile(null);
      } else if (data.error) {
        throw new Error(data.error);
      } else {
        setToastMsg({ type: "success", msg: `上传成功: ${data.filename}` });
        setFile(null);
        fetchList(page);
        trackOperation("upload_document", {
          filename: data.filename,
          project_id: projectId,
        });
      }
    } catch (e) {
      setToastMsg({ type: "error", msg: `上传失败: ${e}` });
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

      setToastMsg({ type: "success", msg: `已删除: ${deleteTarget.filename}` });
      trackOperation("delete_document", {
        document_id: deleteTarget.global_id,
        file_name: deleteTarget.filename,
      });
      fetchList(page);
    } catch (e) {
      setToastMsg({ type: "error", msg: `删除失败: ${e}` });
    } finally {
      setShowDeleteModal(false);
      setDeleteTarget(null);
    }
  };

  // 鼠标悬停预取：减少点击预览时的等待
  const handleMouseEnter = async (doc: Doc) => {
    if (!doc || !doc.global_id || prefetchCache.current.has(doc.global_id)) return;

    try {
      const data = await api.get<any>(`/api/knowledge/${doc.global_id}`);
      if (!data.error) {
        prefetchCache.current.set(doc.global_id, data);
      }
    } catch {
      // 预取失败时静默处理
    }
  };

  const handlePreview = async (doc: Doc) => {
    setPreviewLoading(true);
    setShowPreview(true);

    // 优先命中预取缓存，减少重复请求
    if (prefetchCache.current.has(doc.global_id)) {
      const cached = prefetchCache.current.get(doc.global_id);
      setPreviewDoc({ title: cached.filename, content: cached.content });
      setPreviewLoading(false);
      trackOperation("preview_document_cache_hit", { document_id: doc.global_id });
      return;
    }

    setPreviewDoc({ title: doc.filename, content: "加载中..." });

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
      const data = await api.post<any>("/api/knowledge/update-relation", {
        doc_id: linkedDoc.global_id,
        source_doc_id: -1,
      });
      if (data.success) {
        setToastMsg({ type: "success", msg: "已移除关联" });
        fetchList(page);
      } else {
        throw new Error("Update failed");
      }
    } catch (e) {
      setToastMsg({ type: "error", msg: `移除失败: ${e}` });
    }
  };

  const handleDragStart = (e: DragEvent, index: number, doc: Doc) => {
    dragItem.current = index;
    draggedDocRef.current = doc;
    e.dataTransfer.effectAllowed = "move";
  };

  const handleItemDragOver = (e: DragEvent, index: number) => {
    e.preventDefault();
    e.stopPropagation();

    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const midY = rect.top + rect.height / 2;
    const position = e.clientY < midY ? "before" : "after";
    if (!dragTarget || dragTarget.index !== index || dragTarget.position !== position) {
      setDragTarget({ index, position });
    }
  };

  const handleDragLeave = () => {
    // 鼠标离开卡片时清理插入指示，避免残留高亮
    setDragTarget(null);
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

  const resetDrag = () => {
    dragItem.current = null;
    draggedDocRef.current = null;
    setDragTarget(null);
    if (pageSwitchTimer.current) clearTimeout(pageSwitchTimer.current);
  };

  const handlePageDrop = async (e: DragEvent, targetPage: number) => {
    e.preventDefault();
    e.stopPropagation();
    if (pageSwitchTimer.current) clearTimeout(pageSwitchTimer.current);

    const draggedDoc = draggedDocRef.current;
    if (!draggedDoc || !projectId) {
      resetDrag();
      return;
    }

    setLoading(true);
    try {
      const data = await doFetchList(
        projectId,
        targetPage,
        searchTerm,
        filterDocType,
        startDate,
        endDate,
      );
      if (data && data.documents && data.documents.length > 0) {
        const anchor = normalizeDoc(data.documents[data.documents.length - 1]);
        if (anchor.global_id !== draggedDoc.global_id) {
          await api.post("/api/knowledge/move", {
            project_id: projectId,
            doc_id: draggedDoc.global_id,
            anchor_doc_id: anchor.global_id,
            position: "after",
          });
          fetchList(targetPage);
          setToastMsg({ type: "success", msg: `已移动到第 ${targetPage} 页末尾` });
        }
      }
    } catch (e) {
      setToastMsg({ type: "error", msg: `移动失败: ${e}` });
    } finally {
      resetDrag();
      setLoading(false);
    }
  };

  const handleDrop = async (e: DragEvent) => {
    e.preventDefault();
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
    if (anchorDoc.global_id === draggedDoc.global_id) {
      resetDrag();
      return;
    }

    const isSamePage = docs.some((d) => d.global_id === draggedDoc.global_id);
    if (isSamePage) {
      const dragIndex = dragItem.current;
      if (dragIndex === dropIndex) {
        resetDrag();
        return;
      }
      if (dragIndex !== null) {
        if (position === "before" && dropIndex === dragIndex + 1) {
          resetDrag();
          return;
        }
        if (position === "after" && dropIndex === dragIndex - 1) {
          resetDrag();
          return;
        }
      }
    }

    setLoading(true);
    try {
      await api.post("/api/knowledge/move", {
        project_id: projectId,
        doc_id: draggedDoc.global_id,
        anchor_doc_id: anchorDoc.global_id,
        position,
      });
      fetchList(page);
    } catch (e) {
      setToastMsg({ type: "error", msg: `移动失败: ${e}` });
      fetchList(page);
    } finally {
      resetDrag();
      setLoading(false);
    }
  };

  const toggleRelation = async (testCase: Doc, isLinked: boolean) => {
    if (!manageTarget) return;
    const newSourceId = isLinked ? -1 : manageTarget.global_id;
    try {
      const data = await api.post<any>("/api/knowledge/update-relation", {
        doc_id: testCase.global_id,
        source_doc_id: newSourceId,
      });
      if (data.success) {
        setManageTarget((prev) => {
          if (!prev) return null;
          let newLinks = prev.linked_test_cases ? [...prev.linked_test_cases] : [];
          if (isLinked) {
            newLinks = newLinks.filter((d) => d.global_id !== testCase.global_id);
          } else {
            newLinks.push({
              id: testCase.id,
              global_id: testCase.global_id,
              filename: testCase.filename,
              content_preview: testCase.content_preview || "",
            });
          }
          return { ...prev, linked_test_cases: newLinks };
        });
        fetchList(page);
      }
    } catch (e) {
      setToastMsg({ type: "error", msg: `操作失败: ${e}` });
    }
  };

  const fetchAllTestCaseCandidates = async (): Promise<Doc[]> => {
    if (!projectId) return [];
    const pageSize = 200;
    let currentPage = 1;
    let totalPagesToFetch = 1;
    const allDocs: Doc[] = [];

    // 管理关联时需要完整候选集，循环拉取全部分页
    while (currentPage <= totalPagesToFetch) {
      const data = await api.get<any>(
        `/api/knowledge-list?project_id=${projectId}&doc_type=test_case&include_linked_test_cases=true&page=${currentPage}&page_size=${pageSize}`,
      );

      const pageDocs = Array.isArray(data?.documents)
        ? data.documents.map(normalizeDoc)
        : [];
      allDocs.push(...pageDocs);
      totalPagesToFetch = Math.max(1, Number(data?.pagination?.total_pages || 1));
      currentPage += 1;
      if (pageDocs.length === 0 && currentPage > totalPagesToFetch) break;
    }

    // 按 global_id 去重，避免跨页重复
    const dedup = new Map<number, Doc>();
    for (const d of allDocs) dedup.set(d.global_id, d);
    return Array.from(dedup.values());
  };

  const openManage = async (doc: Doc) => {
    setManageTarget(doc);
    setShowManage(true);
    setManageLoading(true);
    try {
      const allCases = await fetchAllTestCaseCandidates();
      // 候选区显示：未关联用例 + 已关联到当前需求文档的用例
      const available = allCases.filter((d) => {
        if (d.doc_type !== "test_case") return false;
        if (d.source_doc_id && d.source_doc_id !== doc.global_id) return false;
        return true;
      });

      const candidatesWithStatus = available.map((c) => ({
        ...c,
        _isLinked: c.source_doc_id === doc.global_id,
      }));
      setCandidates(candidatesWithStatus);
    } catch (e) {
      setToastMsg({ type: "error", msg: `加载候选列表失败: ${e}` });
    } finally {
      setManageLoading(false);
    }
  };

  return {
    file,
    setFile,
    docType,
    setDocType,
    uploading,
    docs,
    loading,
    page,
    totalPages,
    totalItems,
    searchTerm,
    setSearchTerm,
    filterDocType,
    setFilterDocType,
    startDate,
    setStartDate,
    endDate,
    setEndDate,
    showPreview,
    setShowPreview,
    previewDoc,
    previewLoading,
    showManage,
    manageTarget,
    candidates,
    manageLoading,
    showDeleteModal,
    deleteTarget,
    toastMsg,
    setToastMsg,
    isOnline,
    dragTarget,
    pageSwitchTimer,
    fetchList,
    handleUpload,
    confirmDelete,
    handleDelete,
    handleMouseEnter,
    handlePreview,
    handleUnlink,
    handleDragStart,
    handleItemDragOver,
    handleDragLeave,
    handleDrop,
    handlePageDragEnter,
    handlePageDragLeave,
    handlePageDrop,
    openManage,
    toggleRelation,
    closeDeleteModal: () => setShowDeleteModal(false),
    closeManageModal: () => setShowManage(false),
  };
}
