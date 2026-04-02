import { useEffect } from 'react';
import { PreviewModal } from '../shared/PreviewModal';
import { KnowledgeBaseToolbar, OfflineBanner } from '../knowledge-base/KnowledgeBaseToolbar';
import { KnowledgeBaseContent } from '../knowledge-base/KnowledgeBaseContent';
import { KnowledgeBasePaginationBar } from '../knowledge-base/KnowledgeBasePaginationBar';
import { DeleteConfirmModal, ManageRelationModal } from '../knowledge-base/KnowledgeBaseModals';
import { useKnowledgeBase } from '../knowledge-base/useKnowledgeBase';
import { emitFeedback } from '../../utils/feedback';

type Props = {
  projectId: number | null;
  onLog: (msg: string) => void;
};

export function KnowledgeBase({ projectId, onLog }: Props) {
  const kb = useKnowledgeBase({ projectId, onLog });

  useEffect(() => {
    if (!kb.toastMsg) return;

    emitFeedback({
      title: kb.toastMsg.type === 'success' ? '知识库操作成功' : '知识库操作失败',
      level: kb.toastMsg.type === 'success' ? 'success' : 'error',
      message: kb.toastMsg.msg,
    });

    kb.setToastMsg(null);
  }, [kb.toastMsg, kb.setToastMsg]);

  return (
    <div className="h-100 d-flex flex-column gap-3 position-relative knowledge-base-shell workbench-shell">
      <KnowledgeBaseToolbar
        isOnline={kb.isOnline}
        docType={kb.docType}
        setDocType={kb.setDocType}
        uploading={kb.uploading}
        projectId={projectId}
        onUpload={kb.handleUpload}
        onFileChange={kb.setFile}
        searchTerm={kb.searchTerm}
        setSearchTerm={kb.setSearchTerm}
        filterDocType={kb.filterDocType}
        setFilterDocType={kb.setFilterDocType}
        startDate={kb.startDate}
        setStartDate={kb.setStartDate}
        endDate={kb.endDate}
        setEndDate={kb.setEndDate}
        onSearch={() => kb.fetchList(1)}
      />

      <OfflineBanner isOnline={kb.isOnline} />

      <KnowledgeBaseContent
        loading={kb.loading}
        docs={kb.docs}
        dragTarget={kb.dragTarget}
        onDragStart={kb.handleDragStart}
        onItemDragOver={kb.handleItemDragOver}
        onDragLeave={kb.handleDragLeave}
        onDrop={kb.handleDrop}
        onMouseEnter={kb.handleMouseEnter}
        onPreview={kb.handlePreview}
        onDelete={kb.confirmDelete}
        onOpenManage={kb.openManage}
        onUnlink={kb.handleUnlink}
      />

      <KnowledgeBasePaginationBar
        totalItems={kb.totalItems}
        docsLength={kb.docs.length}
        page={kb.page}
        totalPages={kb.totalPages}
        onFetchPage={kb.fetchList}
        onPageDragEnter={kb.handlePageDragEnter}
        onPageDragLeave={kb.handlePageDragLeave}
        onPageDrop={kb.handlePageDrop}
        pageSwitchTimerActive={!!kb.pageSwitchTimer.current}
      />

      <DeleteConfirmModal
        show={kb.showDeleteModal}
        filename={kb.deleteTarget?.filename}
        onCancel={kb.closeDeleteModal}
        onConfirm={kb.handleDelete}
      />

      <PreviewModal
        show={kb.showPreview}
        onHide={() => kb.setShowPreview(false)}
        title={kb.previewDoc?.title || ''}
        content={kb.previewDoc?.content || ''}
        loading={kb.previewLoading}
      />

      <ManageRelationModal
        show={kb.showManage}
        manageTarget={kb.manageTarget}
        manageLoading={kb.manageLoading}
        candidates={kb.candidates}
        onClose={kb.closeManageModal}
        onToggleRelation={kb.toggleRelation}
      />
    </div>
  );
}
