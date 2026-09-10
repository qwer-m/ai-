import { useEffect, useRef, useState } from "react";
import type { Dispatch, DragEvent, RefObject, SetStateAction } from "react";
import {
  deleteKnowledgeDocument,
  fetchAllTestCaseCandidates,
  fetchKnowledgeDoc,
  moveKnowledgeDocument,
  updateKnowledgeRelation,
  uploadKnowledgeDocument,
} from "./knowledgeBaseApi";
import type { KnowledgeDocumentDetailResponse, KnowledgeListResponse } from "./knowledgeBaseApi";
import { normalizeDoc } from "./types";
import type { Doc, DragTarget, LinkedDoc } from "./types";

export type KnowledgeBaseToast = {
  type: "success" | "error";
  msg: string;
};

type PreviewDocument = {
  title: string;
  content: string;
};

type UseKnowledgeBaseActionsParams = {
  projectId: number | null;
  page: number;
  docs: Doc[];
  searchTerm: string;
  filterDocType: string;
  startDate: string;
  endDate: string;
  setLoading: Dispatch<SetStateAction<boolean>>;
  fetchList: (page?: number) => void;
  doFetchList: (
    projectId: number,
    page: number,
    search: string,
    type: string,
    start: string,
    end: string,
  ) => Promise<KnowledgeListResponse | null>;
};

export type UseKnowledgeBaseActionsResult = {
  file: File | null;
  setFile: Dispatch<SetStateAction<File | null>>;
  docType: string;
  setDocType: Dispatch<SetStateAction<string>>;
  uploading: boolean;
  showPreview: boolean;
  setShowPreview: Dispatch<SetStateAction<boolean>>;
  previewDoc: PreviewDocument | null;
  previewLoading: boolean;
  showManage: boolean;
  manageTarget: Doc | null;
  candidates: Doc[];
  manageLoading: boolean;
  showDeleteModal: boolean;
  deleteTarget: Doc | null;
  toastMsg: KnowledgeBaseToast | null;
  setToastMsg: Dispatch<SetStateAction<KnowledgeBaseToast | null>>;
  isOnline: boolean;
  dragTarget: DragTarget | null;
  pageSwitchTimer: RefObject<ReturnType<typeof setTimeout> | null>;
  handleUpload: () => Promise<void>;
  confirmDelete: (document: Doc) => void;
  handleDelete: () => Promise<void>;
  handleMouseEnter: (document: Doc) => Promise<void>;
  handlePreview: (document: Doc) => Promise<void>;
  handleUnlink: (sourceDocument: Doc, linkedDocument: LinkedDoc) => Promise<void>;
  handleDragStart: (event: DragEvent, index: number, document: Doc) => void;
  handleItemDragOver: (event: DragEvent, index: number) => void;
  handleDragLeave: () => void;
  handleDrop: (event: DragEvent) => Promise<void>;
  handlePageDragEnter: (targetPage: number) => void;
  handlePageDragLeave: () => void;
  handlePageDrop: (event: DragEvent, targetPage: number) => Promise<void>;
  openManage: (document: Doc) => Promise<void>;
  toggleRelation: (candidate: Doc, isLinked: boolean) => Promise<void>;
  closeDeleteModal: () => void;
  closeManageModal: () => void;
};

const formatError = (error: unknown) => (error instanceof Error ? error.message : String(error));

export function useKnowledgeBaseActions({
  projectId,
  page,
  docs,
  searchTerm,
  filterDocType,
  startDate,
  endDate,
  setLoading,
  fetchList,
  doFetchList,
}: UseKnowledgeBaseActionsParams): UseKnowledgeBaseActionsResult {
  const [file, setFile] = useState<File | null>(null);
  const [docType, setDocType] = useState("requirement");
  const [uploading, setUploading] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const [previewDoc, setPreviewDoc] = useState<PreviewDocument | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [showManage, setShowManage] = useState(false);
  const [manageTarget, setManageTarget] = useState<Doc | null>(null);
  const [candidates, setCandidates] = useState<Doc[]>([]);
  const [manageLoading, setManageLoading] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Doc | null>(null);
  const [toastMsg, setToastMsg] = useState<KnowledgeBaseToast | null>(null);
  const [isOnline, setIsOnline] = useState(navigator.onLine);
  const [dragTarget, setDragTarget] = useState<DragTarget | null>(null);

  const previewCache = useRef(new Map<number, KnowledgeDocumentDetailResponse>());
  const draggedIndex = useRef<number | null>(null);
  const draggedDocument = useRef<Doc | null>(null);
  const pageSwitchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const handleOnline = () => setIsOnline(true);
    const handleOffline = () => setIsOnline(false);
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      if (pageSwitchTimer.current) clearTimeout(pageSwitchTimer.current);
    };
  }, []);

  const clearPageSwitchTimer = () => {
    if (pageSwitchTimer.current) clearTimeout(pageSwitchTimer.current);
    pageSwitchTimer.current = null;
  };

  const resetDragState = () => {
    draggedIndex.current = null;
    draggedDocument.current = null;
    setDragTarget(null);
    clearPageSwitchTimer();
  };

  const handleUpload = async () => {
    if (!projectId) {
      setToastMsg({ type: "error", msg: "请先选择项目后再上传文档。" });
      return;
    }
    if (!file) {
      setToastMsg({ type: "error", msg: "请先选择要上传的文件。" });
      return;
    }

    setUploading(true);
    const uploadData = new FormData();
    uploadData.append("file", file);
    uploadData.append("project_id", String(projectId));
    uploadData.append("doc_type", docType);

    try {
      const response = await uploadKnowledgeDocument(uploadData);
      if (!response.success) throw new Error("上传请求未成功入队");
      setToastMsg({ type: "success", msg: `上传成功：${response.filename}` });
      setFile(null);
      previewCache.current.clear();
      fetchList(page);
    } catch (error) {
      setToastMsg({ type: "error", msg: `上传失败：${formatError(error)}` });
    } finally {
      setUploading(false);
    }
  };

  const confirmDelete = (document: Doc) => {
    setDeleteTarget(document);
    setShowDeleteModal(true);
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;

    try {
      const response = await deleteKnowledgeDocument(deleteTarget.id);
      if (!response.success) throw new Error(response.error ?? "删除操作未成功");
      previewCache.current.delete(deleteTarget.id);
      setToastMsg({ type: "success", msg: `已删除：${deleteTarget.filename}` });
      fetchList(page);
    } catch (error) {
      setToastMsg({ type: "error", msg: `删除失败：${formatError(error)}` });
    } finally {
      setShowDeleteModal(false);
      setDeleteTarget(null);
    }
  };

  const handleMouseEnter = async (document: Doc) => {
    if (previewCache.current.has(document.id)) return;
    try {
      const detail = await fetchKnowledgeDoc(document.id);
      previewCache.current.set(document.id, detail);
    } catch {
      // 悬停预取失败不打断用户当前操作，点击预览时会展示明确错误。
    }
  };

  const handlePreview = async (document: Doc) => {
    setPreviewLoading(true);
    setShowPreview(true);

    const cachedDetail = previewCache.current.get(document.id);
    if (cachedDetail) {
      setPreviewDoc({ title: cachedDetail.filename, content: cachedDetail.content });
      setPreviewLoading(false);
      return;
    }

    setPreviewDoc({ title: document.filename, content: "正在加载..." });
    try {
      const detail = await fetchKnowledgeDoc(document.id);
      previewCache.current.set(document.id, detail);
      setPreviewDoc({ title: detail.filename, content: detail.content });
    } catch (error) {
      setPreviewDoc({ title: document.filename, content: `加载失败：${formatError(error)}` });
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleUnlink = async (sourceDocument: Doc, linkedDocument: LinkedDoc) => {
    const confirmed = window.confirm(
      `确认解除“${linkedDocument.filename}”与“${sourceDocument.filename}”的关联吗？`,
    );
    if (!confirmed) return;

    try {
      const response = await updateKnowledgeRelation({
        doc_id: linkedDocument.id,
        source_doc_id: -1,
      });
      if (!response.success) throw new Error(response.error ?? "解除关联未成功");
      setToastMsg({ type: "success", msg: "关联已移除。" });
      fetchList(page);
    } catch (error) {
      setToastMsg({ type: "error", msg: `移除关联失败：${formatError(error)}` });
    }
  };

  const handleDragStart = (event: DragEvent, index: number, document: Doc) => {
    draggedIndex.current = index;
    draggedDocument.current = document;
    event.dataTransfer.effectAllowed = "move";
  };

  const handleItemDragOver = (event: DragEvent, index: number) => {
    event.preventDefault();
    event.stopPropagation();
    const bounds = event.currentTarget.getBoundingClientRect();
    const position = event.clientY < bounds.top + bounds.height / 2 ? "before" : "after";
    if (dragTarget?.index !== index || dragTarget.position !== position) {
      setDragTarget({ index, position });
    }
  };

  const handleDragLeave = () => setDragTarget(null);

  const handleDrop = async (event: DragEvent) => {
    event.preventDefault();
    clearPageSwitchTimer();

    const document = draggedDocument.current;
    if (!projectId || !document || !dragTarget) {
      resetDragState();
      return;
    }

    const anchorDocument = docs[dragTarget.index];
    if (!anchorDocument || anchorDocument.id === document.id) {
      resetDragState();
      return;
    }

    if (docs.some((item) => item.id === document.id)) {
      const sourceIndex = draggedIndex.current;
      const isSamePosition =
        sourceIndex === dragTarget.index ||
        (sourceIndex !== null &&
          ((dragTarget.position === "before" && dragTarget.index === sourceIndex + 1) ||
            (dragTarget.position === "after" && dragTarget.index === sourceIndex - 1)));
      if (isSamePosition) {
        resetDragState();
        return;
      }
    }

    setLoading(true);
    try {
      const response = await moveKnowledgeDocument({
        project_id: projectId,
        doc_id: document.id,
        anchor_doc_id: anchorDocument.id,
        position: dragTarget.position,
      });
      if (!response.success) throw new Error(response.error ?? "文档移动未成功");
      fetchList(page);
    } catch (error) {
      setToastMsg({ type: "error", msg: `移动失败：${formatError(error)}` });
      fetchList(page);
    } finally {
      resetDragState();
      setLoading(false);
    }
  };

  const handlePageDragEnter = (targetPage: number) => {
    clearPageSwitchTimer();
    pageSwitchTimer.current = setTimeout(() => {
      pageSwitchTimer.current = null;
      if (targetPage !== page) fetchList(targetPage);
    }, 600);
  };

  const handlePageDragLeave = () => clearPageSwitchTimer();

  const handlePageDrop = async (event: DragEvent, targetPage: number) => {
    event.preventDefault();
    event.stopPropagation();
    clearPageSwitchTimer();

    const document = draggedDocument.current;
    if (!document || !projectId) {
      resetDragState();
      return;
    }

    setLoading(true);
    try {
      const targetPageData = await doFetchList(
        projectId,
        targetPage,
        searchTerm,
        filterDocType,
        startDate,
        endDate,
      );
      const lastRecord = targetPageData?.documents.at(-1);
      if (!lastRecord) throw new Error("目标页没有可用的定位文档");

      const anchorDocument = normalizeDoc(lastRecord);
      if (anchorDocument.id !== document.id) {
        const response = await moveKnowledgeDocument({
          project_id: projectId,
          doc_id: document.id,
          anchor_doc_id: anchorDocument.id,
          position: "after",
        });
        if (!response.success) throw new Error(response.error ?? "跨页移动未成功");
        fetchList(targetPage);
        setToastMsg({ type: "success", msg: `已移动到第 ${targetPage} 页末尾。` });
      }
    } catch (error) {
      setToastMsg({ type: "error", msg: `移动失败：${formatError(error)}` });
    } finally {
      resetDragState();
      setLoading(false);
    }
  };

  const openManage = async (document: Doc) => {
    setManageTarget(document);
    setShowManage(true);
    setManageLoading(true);

    try {
      if (!projectId) {
        setCandidates([]);
        return;
      }

      const allCandidates = await fetchAllTestCaseCandidates(projectId);
      setCandidates(
        allCandidates
          .filter(
            (candidate) =>
              candidate.source_doc_id === null || candidate.source_doc_id === document.id,
          )
          .map((candidate) => ({
            ...candidate,
            _isLinked: candidate.source_doc_id === document.id,
          })),
      );
    } catch (error) {
      setCandidates([]);
      setToastMsg({ type: "error", msg: `加载候选用例失败：${formatError(error)}` });
    } finally {
      setManageLoading(false);
    }
  };

  const toggleRelation = async (candidate: Doc, isLinked: boolean) => {
    if (!manageTarget) return;

    try {
      const response = await updateKnowledgeRelation({
        doc_id: candidate.id,
        source_doc_id: isLinked ? -1 : manageTarget.id,
      });
      if (!response.success) throw new Error(response.error ?? "关联操作未成功");

      setCandidates((currentCandidates) =>
        currentCandidates.map((current) =>
          current.id === candidate.id ? { ...current, _isLinked: !isLinked } : current,
        ),
      );
      setManageTarget((currentTarget) => {
        if (!currentTarget) return null;
        const linkedDocuments = currentTarget.linked_test_cases;
        if (isLinked) {
          return {
            ...currentTarget,
            linked_test_cases: linkedDocuments.filter((linked) => linked.id !== candidate.id),
          };
        }
        if (linkedDocuments.some((linked) => linked.id === candidate.id)) return currentTarget;
        return {
          ...currentTarget,
          linked_test_cases: [
            ...linkedDocuments,
            {
              id: candidate.id,
              display_id: candidate.display_id,
              filename: candidate.filename,
              content_preview: candidate.content_preview,
            },
          ],
        };
      });
      fetchList(page);
    } catch (error) {
      setToastMsg({ type: "error", msg: `关联操作失败：${formatError(error)}` });
    }
  };

  const closeDeleteModal = () => {
    setShowDeleteModal(false);
    setDeleteTarget(null);
  };

  const closeManageModal = () => {
    setShowManage(false);
    setManageTarget(null);
    setCandidates([]);
  };

  return {
    file,
    setFile,
    docType,
    setDocType,
    uploading,
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
    closeDeleteModal,
    closeManageModal,
  };
}
