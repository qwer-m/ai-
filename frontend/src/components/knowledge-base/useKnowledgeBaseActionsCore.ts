import { useEffect, useRef, useState } from "react";
import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import type { DragEvent } from "react";
import { normalizeDoc, type Doc, type DragTarget, type LinkedDoc } from "./types";
import {
  deleteKnowledgeDocument,
  fetchAllTestCaseCandidates,
  fetchKnowledgeDoc,
  moveKnowledgeDocument,
  trackOperation,
  updateKnowledgeRelation,
  uploadKnowledgeDocument,
} from "./knowledgeBaseApi";
type UseKnowledgeBaseActionsParams = {
  projectId: number | null;
  page: number;
  docs: Doc[];
  searchTerm: string;
  filterDocType: string;
  startDate: string;
  endDate: string;
  setLoading: Dispatch<SetStateAction<boolean>>;
  fetchList: (p?: number) => void;
  doFetchList: (
    pid: number,
    p: number,
    search: string,
    type: string,
    start: string,
    end: string,
  ) => Promise<any>;
};
export type UseKnowledgeBaseActionsResult = {
  file: File | null;
  setFile: Dispatch<SetStateAction<File | null>>;
  docType: string;
  setDocType: Dispatch<SetStateAction<string>>;
  force: boolean;
  setForce: Dispatch<SetStateAction<boolean>>;
  uploading: boolean;
  showPreview: boolean;
  setShowPreview: Dispatch<SetStateAction<boolean>>;
  previewDoc: { title: string; content: string; linkedDocs?: any[] } | null;
  previewLoading: boolean;
  showManage: boolean;
  manageTarget: Doc | null;
  candidates: Doc[];
  manageLoading: boolean;
  showDeleteModal: boolean;
  deleteTarget: Doc | null;
  toastMsg: { type: "success" | "error"; msg: string } | null;
  setToastMsg: Dispatch<SetStateAction<{ type: "success" | "error"; msg: string } | null>>;
  isOnline: boolean;
  dragTarget: DragTarget | null;
  pageSwitchTimer: MutableRefObject<ReturnType<typeof setTimeout> | null>;
  handleUpload: () => Promise<void>;
  confirmDelete: (doc: Doc) => void;
  handleDelete: () => Promise<void>;
  handleMouseEnter: (doc: Doc) => Promise<void>;
  handlePreview: (doc: Doc) => Promise<void>;
  handleUnlink: (_parentDoc: Doc, linkedDoc: LinkedDoc) => Promise<void>;
  handleDragStart: (e: DragEvent, index: number, doc: Doc) => void;
  handleItemDragOver: (e: DragEvent, index: number) => void;
  handleDragLeave: () => void;
  handleDrop: (e: DragEvent) => Promise<void>;
  handlePageDragEnter: (targetPage: number) => void;
  handlePageDragLeave: () => void;
  handlePageDrop: (e: DragEvent, targetPage: number) => Promise<void>;
  openManage: (doc: Doc) => Promise<void>;
  toggleRelation: (testCase: Doc, isLinked: boolean) => Promise<void>;
  closeDeleteModal: () => void;
  closeManageModal: () => void;
};
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
  const [force, setForce] = useState(false);
  const [uploading, setUploading] = useState(false);

  const [showPreview, setShowPreview] = useState(false);
  const [previewDoc, setPreviewDoc] = useState<{
    title: string;
    content: string;
    linkedDocs?: any[];
  } | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const [showManage, setShowManage] = useState(false);
  const [manageTarget, setManageTarget] = useState<Doc | null>(null);
  const [candidates, setCandidates] = useState<Doc[]>([]);
  const [manageLoading, setManageLoading] = useState(false);

  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Doc | null>(null);
  const [toastMsg, setToastMsg] = useState<{
    type: "success" | "error";
    msg: string;
  } | null>(null);
  const [isOnline, setIsOnline] = useState(navigator.onLine);
  const [dragTarget, setDragTarget] = useState<DragTarget | null>(null);

  const prefetchCache = useRef<Map<number, any>>(new Map());
  const dragItem = useRef<number | null>(null);
  const draggedDocRef = useRef<Doc | null>(null);
  const pageSwitchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  const resetDrag = () => {
    dragItem.current = null;
    draggedDocRef.current = null;
    setDragTarget(null);
    if (pageSwitchTimer.current) clearTimeout(pageSwitchTimer.current);
  };

  const handleUpload = async () => {
    if (!projectId) return alert("Select a project first");
    if (!file) return alert("Select a file first");

    setUploading(true);
    const uploadData = new FormData();
    uploadData.append("file", file);
    uploadData.append("project_id", String(projectId));
    uploadData.append("doc_type", docType);
    uploadData.append("force", String(force));

    try {
      const data = await uploadKnowledgeDocument(uploadData);

      if (data.status === "duplicate") {
        setToastMsg({
          type: "error",
          msg: `File "${data.existing_filename || file.name}" already exists in the knowledge base.`,
        });
        setFile(null);
      } else if (data.error) {
        throw new Error(data.error);
      } else {
        setToastMsg({ type: "success", msg: `Upload succeeded: ${data.filename}` });
        setFile(null);
        fetchList(page);
        trackOperation("upload_document", {
          filename: data.filename,
          project_id: projectId,
        });
      }
    } catch (e) {
      setToastMsg({ type: "error", msg: `Upload failed: ${e}` });
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
      const data = await deleteKnowledgeDocument(deleteTarget.global_id);
      if (data.error) throw new Error(data.error);

      setToastMsg({ type: "success", msg: `Deleted: ${deleteTarget.filename}` });
      trackOperation("delete_document", {
        document_id: deleteTarget.global_id,
        file_name: deleteTarget.filename,
      });
      fetchList(page);
    } catch (e) {
      setToastMsg({ type: "error", msg: `Delete failed: ${e}` });
    } finally {
      setShowDeleteModal(false);
      setDeleteTarget(null);
    }
  };

  const handleMouseEnter = async (doc: Doc) => {
    if (!doc || !doc.global_id || prefetchCache.current.has(doc.global_id)) return;

    try {
      const data = await fetchKnowledgeDoc(doc.global_id);
      if (!data.error) {
        prefetchCache.current.set(doc.global_id, data);
      }
    } catch {
      // Ignore prefetch failures.
    }
  };

  const handlePreview = async (doc: Doc) => {
    setPreviewLoading(true);
    setShowPreview(true);

    if (prefetchCache.current.has(doc.global_id)) {
      const cached = prefetchCache.current.get(doc.global_id);
      setPreviewDoc({ title: cached.filename, content: cached.content });
      setPreviewLoading(false);
      trackOperation("preview_document_cache_hit", { document_id: doc.global_id });
      return;
    }

    setPreviewDoc({ title: doc.filename, content: "Loading..." });

    try {
      const data = await fetchKnowledgeDoc(doc.global_id);
      if (data.error) {
        setPreviewDoc({ title: doc.filename, content: `Load failed: ${data.error}` });
      } else {
        setPreviewDoc({ title: data.filename, content: data.content });
      }
    } catch (e) {
      setPreviewDoc({ title: doc.filename, content: `Request failed: ${e}` });
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleUnlink = async (_parentDoc: Doc, linkedDoc: LinkedDoc) => {
    if (!confirm(`Remove relation for "${linkedDoc.filename}"?`)) return;
    try {
      const data = await updateKnowledgeRelation({
        doc_id: linkedDoc.global_id,
        source_doc_id: -1,
      });
      if (data.success) {
        setToastMsg({ type: "success", msg: "Relation removed." });
        fetchList(page);
      } else {
        throw new Error("Update failed");
      }
    } catch (e) {
      setToastMsg({ type: "error", msg: `Unlink failed: ${e}` });
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
        const anchorDoc = normalizeDoc(data.documents[data.documents.length - 1]);
        if (anchorDoc.global_id !== draggedDoc.global_id) {
          await moveKnowledgeDocument({
            project_id: projectId,
            doc_id: draggedDoc.global_id,
            anchor_doc_id: anchorDoc.global_id,
            position: "after",
          });
          fetchList(targetPage);
          setToastMsg({ type: "success", msg: `Moved to the end of page ${targetPage}.` });
        }
      }
    } catch (e) {
      setToastMsg({ type: "error", msg: `Move failed: ${e}` });
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
      await moveKnowledgeDocument({
        project_id: projectId ?? 0,
        doc_id: draggedDoc.global_id,
        anchor_doc_id: anchorDoc.global_id,
        position,
      });
      fetchList(page);
    } catch (e) {
      setToastMsg({ type: "error", msg: `Move failed: ${e}` });
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
      const data = await updateKnowledgeRelation({
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
      setToastMsg({ type: "error", msg: `Operation failed: ${e}` });
    }
  };

  const openManage = async (doc: Doc) => {
    setManageTarget(doc);
    setShowManage(true);
    setManageLoading(true);
    try {
      if (!projectId) {
        setCandidates([]);
        return;
      }
      const allCases = await fetchAllTestCaseCandidates(projectId);
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
      setToastMsg({ type: "error", msg: `Failed to load candidates: ${e}` });
    } finally {
      setManageLoading(false);
    }
  };

  return {
    file,
    setFile,
    docType,
    setDocType,
    force,
    setForce,
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
    closeDeleteModal: () => setShowDeleteModal(false),
    closeManageModal: () => setShowManage(false),
  };
}
