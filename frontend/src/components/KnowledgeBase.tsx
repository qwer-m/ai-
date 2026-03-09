import { Toast, ToastContainer } from 'react-bootstrap';
import { PreviewModal } from './PreviewModal';
import { KnowledgeBaseToolbar, OfflineBanner } from './knowledge-base/KnowledgeBaseToolbar';
import { KnowledgeBaseContent } from './knowledge-base/KnowledgeBaseContent';
import { KnowledgeBasePaginationBar } from './knowledge-base/KnowledgeBasePaginationBar';
import { DeleteConfirmModal, ManageRelationModal } from './knowledge-base/KnowledgeBaseModals';
import { useKnowledgeBase } from './knowledge-base/useKnowledgeBase';

type Props = {
  projectId: number | null;
  onLog: (msg: string) => void;
};

export function KnowledgeBase({ projectId, onLog }: Props) {
  const kb = useKnowledgeBase({ projectId, onLog });

  return (
    <div className="h-100 d-flex flex-column gap-3 position-relative">
      <ToastContainer position="top-end" className="p-3" style={{ zIndex: 1100 }}>
        {kb.toastMsg && (
          <Toast
            onClose={() => kb.setToastMsg(null)}
            show={!!kb.toastMsg}
            delay={3000}
            autohide
            bg={kb.toastMsg.type === 'success' ? 'success' : 'danger'}
          >
            <Toast.Header>
              <strong className="me-auto">{kb.toastMsg.type === 'success' ? '成功' : '错误'}</strong>
            </Toast.Header>
            <Toast.Body className="text-white">{kb.toastMsg.msg}</Toast.Body>
          </Toast>
        )}
      </ToastContainer>

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
