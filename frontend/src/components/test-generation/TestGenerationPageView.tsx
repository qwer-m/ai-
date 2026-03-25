import { Badge } from 'react-bootstrap';
import type { DragEvent, RefObject } from 'react';
import { FaChartBar, FaPlay } from 'react-icons/fa';
import { TestGenerationConfigSection } from './TestGenerationConfigSection';
import { TestGenerationFeedback } from './TestGenerationFeedback';
import { TestGenerationInputSection } from './TestGenerationInputSection';
import { TestGenerationResultSection } from './TestGenerationResultSection';
import type { TestGenerationMode } from './types';

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
  return (
    <div className="bento-grid h-100 align-content-start position-relative postman-theme">
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
      />

      <div className="bento-card col-span-12 p-4 d-flex align-items-center justify-content-between glass-panel">
        <div>
          <h4 className="text-gradient mb-1 d-flex align-items-center gap-2">
            <FaPlay className="text-primary" size={20} />
            测试用例生成中心
          </h4>
          <p className="text-secondary small mb-0">AI 驱动的智能测试设计引擎，支持文本描述与文件分析</p>
        </div>
        <div className="d-flex gap-3">
          <Badge bg="white" text="primary" className="border shadow-sm p-2 px-3 d-flex align-items-center gap-2">
            <FaChartBar />
            已生成 <span className="fw-bold">{statsCount}</span>
          </Badge>
        </div>
      </div>

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

      <TestGenerationResultSection
        mode={mode}
        result={result}
        streamingContent={streamingContent}
        loading={loading}
        statsCount={statsCount}
        onCopy={handleCopyCurrent}
      />
    </div>
  );
}
