import { Alert } from 'react-bootstrap';
import type { DragEvent, RefObject } from 'react';
import { useState } from 'react';
import { FaExclamationCircle } from 'react-icons/fa';
import { InlineStatusBanner } from '../shared/InlineStatusBanner';
import { TestGenerationConfigSection } from './TestGenerationConfigSection';
import { RagDebugPanel } from './debug/RagDebugPanel';
import { TestGenerationFeedback } from './TestGenerationFeedback';
import { TestGenerationInputSection } from './TestGenerationInputSection';
import { TestGenerationResultSection } from './TestGenerationResultSection';
import type { TestGenerationMode } from './types';
import './test-generation-refresh.css';

type TestGenerationPageViewProps = {
  mode: TestGenerationMode;
  setMode: (mode: TestGenerationMode) => void;
  requirement: string;
  setRequirement: (value: string) => void;
  file: File | null;
  protoFile: File | null;
  loading: boolean;
  showHint: boolean;
  onCloseHint: () => void;
  fileInputRef: RefObject<HTMLInputElement | null>;
  protoInputRef: RefObject<HTMLInputElement | null>;
  uploadZoneRef: RefObject<HTMLDivElement | null>;
  isDragActive: boolean;
  handleFileChange: (file: File | null) => void;
  handleProtoFileChange: (file: File | null) => void;
  handleDragOver: (e: DragEvent<HTMLDivElement>) => void;
  handleDragLeave: () => void;
  handleDrop: (e: DragEvent<HTMLDivElement>) => void;
  docType: string;
  setDocType: (value: string) => void;
  compress: boolean;
  setCompress: (value: boolean) => void;
  expectedCount: number;
  setExpectedCount: (value: number) => void;
  isEstimating: boolean;
  appendCount: number;
  setAppendCount: (value: number) => void;
  force: boolean;
  setForce: (value: boolean) => void;
  projectId: number | null;
  hasJsonInResultBox: boolean;
  isLimitReached: boolean;
  handleGenerateStream: (isText: boolean, forceOverride?: boolean, appendMode?: boolean) => Promise<void>;
  handleExportExcel: () => Promise<void>;
  handleClearCurrent: () => void;
  result: any;
  streamingContent: string;
  statsCount: number;
  handleCopyCurrent: () => void;
  toastMsg: string | null;
  toastType: 'success' | 'error';
  setToastMsg: (msg: string | null) => void;
  loadingStatus: string;
  error: string | null;
  setError: (msg: string | null) => void;
  showDuplicateModal: boolean;
  handleDuplicateCancel: () => void | Promise<void>;
  handleDuplicateConfirm: () => void;
};

export function TestGenerationPageView({
  mode,
  setMode,
  requirement,
  setRequirement,
  file,
  protoFile,
  loading,
  showHint,
  onCloseHint,
  fileInputRef,
  protoInputRef,
  uploadZoneRef,
  isDragActive,
  handleFileChange,
  handleProtoFileChange,
  handleDragOver,
  handleDragLeave,
  handleDrop,
  docType,
  setDocType,
  compress,
  setCompress,
  expectedCount,
  setExpectedCount,
  isEstimating,
  appendCount,
  setAppendCount,
  force,
  setForce,
  projectId,
  hasJsonInResultBox,
  isLimitReached,
  handleGenerateStream,
  handleExportExcel,
  handleClearCurrent,
  result,
  streamingContent,
  statsCount,
  handleCopyCurrent,
  toastMsg,
  toastType,
  setToastMsg,
  loadingStatus,
  error,
  setError,
  showDuplicateModal,
  handleDuplicateCancel,
  handleDuplicateConfirm,
}: TestGenerationPageViewProps) {
  const [activeRuleId, setActiveRuleId] = useState<string | null>(null);

  return (
    <div className="test-generation-shell workbench-shell bento-grid h-100 align-content-start position-relative postman-theme">
      <TestGenerationFeedback
        toastMsg={toastMsg}
        toastType={toastType}
        onToastClose={() => setToastMsg(null)}
        loading={loading}
        pollStatus={loadingStatus}
        error={error}
        onErrorClose={() => setError(null)}
        showDuplicateModal={showDuplicateModal}
        onDuplicateCancel={handleDuplicateCancel}
        onDuplicateConfirm={handleDuplicateConfirm}
        hideErrorAlert
        hideLoadingProgress
      />

      <TestGenerationInputSection
        mode={mode}
        file={file}
        loading={loading}
        showHint={showHint}
        onCloseHint={onCloseHint}
        requirement={requirement}
        onRequirementChange={setRequirement}
        fileInputRef={fileInputRef}
        uploadZoneRef={uploadZoneRef}
        isDragActive={isDragActive}
        onFileChange={handleFileChange}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onModeChange={setMode}
      />

      <TestGenerationConfigSection
        mode={mode}
        projectId={projectId}
        docType={docType}
        onDocTypeChange={setDocType}
        protoFile={protoFile}
        protoInputRef={protoInputRef}
        onProtoFileChange={handleProtoFileChange}
        compress={compress}
        onCompressChange={setCompress}
        expectedCount={expectedCount}
        onExpectedCountChange={setExpectedCount}
        isEstimating={isEstimating}
        appendCount={appendCount}
        onAppendCountChange={setAppendCount}
        force={force}
        onForceChange={setForce}
        hasJsonInResultBox={hasJsonInResultBox}
        isLimitReached={isLimitReached}
        loading={loading}
        onGenerate={() => handleGenerateStream(mode === 'text', undefined, hasJsonInResultBox)}
        onExport={handleExportExcel}
        onClear={handleClearCurrent}
        hasOutput={Boolean(result || streamingContent)}
      />
      {loading ? (
        <InlineStatusBanner
          className="col-span-12"
          type="loading"
          text={loadingStatus || 'AI 姝ｅ湪鍒嗘瀽闇€姹傛枃妗ｏ紝璇风◢鍊?..'}
        />
      ) : null}
      {!loading && !error && loadingStatus ? (
        <InlineStatusBanner className="col-span-12" type="info" text={loadingStatus} />
      ) : null}
      {error ? (
        <div className="col-span-12 d-flex flex-column gap-2">
          <InlineStatusBanner type="error" text={error} />
          <Alert variant="danger" dismissible onClose={() => setError(null)} className="shadow-sm border-0 mb-0 py-2">
            <FaExclamationCircle className="me-2" /> 浣犲彲浠ユ鏌ユā鍨嬮厤缃€佺綉缁滀笌鏃ュ織鍚庨噸璇曘€?          </Alert>
        </div>
      ) : null}

      <TestGenerationResultSection
        mode={mode}
        result={result}
        streamingContent={streamingContent}
        loading={loading}
        statsCount={statsCount}
        onCopy={handleCopyCurrent}
        highlightRuleId={activeRuleId}
        onClearHighlight={() => setActiveRuleId(null)}
      />

      <RagDebugPanel className="col-span-12" activeRuleId={activeRuleId} onRuleClick={setActiveRuleId} />
    </div>
  );
}




