import { useKnowledgeBaseActions } from "./useKnowledgeBaseActions";
import { useKnowledgeBaseState } from "./useKnowledgeBaseState";

type UseKnowledgeBaseParams = {
  projectId: number | null;
  onLog: (msg: string) => void;
};

export function useKnowledgeBase({ projectId, onLog }: UseKnowledgeBaseParams) {
  const state = useKnowledgeBaseState({ projectId, onLog });
  const actions = useKnowledgeBaseActions({
    projectId,
    page: state.page,
    docs: state.docs,
    searchTerm: state.searchTerm,
    filterDocType: state.filterDocType,
    startDate: state.startDate,
    endDate: state.endDate,
    setLoading: state.setLoading,
    fetchList: state.fetchList,
    doFetchList: state.doFetchList,
  });

  return {
    file: actions.file,
    setFile: actions.setFile,
    docType: actions.docType,
    setDocType: actions.setDocType,
    uploading: actions.uploading,
    docs: state.docs,
    loading: state.loading,
    page: state.page,
    totalPages: state.totalPages,
    totalItems: state.totalItems,
    searchTerm: state.searchTerm,
    setSearchTerm: state.setSearchTerm,
    filterDocType: state.filterDocType,
    setFilterDocType: state.setFilterDocType,
    startDate: state.startDate,
    setStartDate: state.setStartDate,
    endDate: state.endDate,
    setEndDate: state.setEndDate,
    showPreview: actions.showPreview,
    setShowPreview: actions.setShowPreview,
    previewDoc: actions.previewDoc,
    previewLoading: actions.previewLoading,
    showManage: actions.showManage,
    manageTarget: actions.manageTarget,
    candidates: actions.candidates,
    manageLoading: actions.manageLoading,
    showDeleteModal: actions.showDeleteModal,
    deleteTarget: actions.deleteTarget,
    toastMsg: actions.toastMsg,
    setToastMsg: actions.setToastMsg,
    isOnline: actions.isOnline,
    dragTarget: actions.dragTarget,
    pageSwitchTimer: actions.pageSwitchTimer,
    fetchList: state.fetchList,
    handleUpload: actions.handleUpload,
    confirmDelete: actions.confirmDelete,
    handleDelete: actions.handleDelete,
    handleMouseEnter: actions.handleMouseEnter,
    handlePreview: actions.handlePreview,
    handleUnlink: actions.handleUnlink,
    handleDragStart: actions.handleDragStart,
    handleItemDragOver: actions.handleItemDragOver,
    handleDragLeave: actions.handleDragLeave,
    handleDrop: actions.handleDrop,
    handlePageDragEnter: actions.handlePageDragEnter,
    handlePageDragLeave: actions.handlePageDragLeave,
    handlePageDrop: actions.handlePageDrop,
    openManage: actions.openManage,
    toggleRelation: actions.toggleRelation,
    closeDeleteModal: actions.closeDeleteModal,
    closeManageModal: actions.closeManageModal,
  };
}
