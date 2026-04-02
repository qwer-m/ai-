import classNames from 'classnames';
import { Button, Form, Nav } from 'react-bootstrap';
import { FaFileAlt, FaFileUpload, FaTrash } from 'react-icons/fa';
import type { DragEvent, RefObject } from 'react';
import { AIHintBubble } from './AIHintBubble';
import type { TestGenerationMode } from './types';

type TestGenerationInputSectionProps = {
  mode: TestGenerationMode;
  file: File | null;
  loading: boolean;
  showHint: boolean;
  onCloseHint: () => void;
  requirement: string;
  onRequirementChange: (value: string) => void;
  fileInputRef: RefObject<HTMLInputElement | null>;
  uploadZoneRef: RefObject<HTMLDivElement | null>;
  isDragActive: boolean;
  onFileChange: (file: File | null) => void;
  onDragOver: (e: DragEvent<HTMLDivElement>) => void;
  onDragLeave: () => void;
  onDrop: (e: DragEvent<HTMLDivElement>) => void;
  onModeChange: (mode: TestGenerationMode) => void;
};

export function TestGenerationInputSection({
  mode,
  file,
  loading,
  showHint,
  onCloseHint,
  requirement,
  onRequirementChange,
  fileInputRef,
  uploadZoneRef,
  isDragActive,
  onFileChange,
  onDragOver,
  onDragLeave,
  onDrop,
  onModeChange,
}: TestGenerationInputSectionProps) {
  return (
    <div className="test-generation-input bento-card col-span-6 p-4 d-flex flex-column position-relative panel-card panel-card-input">
      {showHint && mode === 'file' && !file ? <AIHintBubble onClose={onCloseHint} /> : null}

      <div className="d-flex justify-content-between align-items-center mb-3 panel-card-head">
        <div className="panel-card-title-row">
          <span className="panel-card-title">输入源</span>
        </div>
        {mode === 'file' && file ? (
          <Button variant="link" className="text-danger p-0 text-decoration-none small panel-card-action-link" onClick={() => onFileChange(null)}>
            <FaTrash className="me-1" /> 移除文件
          </Button>
        ) : null}
      </div>

      <div className="d-flex justify-content-between align-items-center mb-4 panel-card-toolbar">
        <Nav
          variant="pills"
          className="bg-light p-1 rounded-pill test-generation-mode-switch"
          activeKey={mode}
          onSelect={(key) => onModeChange(key as TestGenerationMode)}
        >
          <Nav.Item>
            <Nav.Link eventKey="text" className="rounded-pill px-3 py-1 small fw-bold test-generation-mode-link">
              <FaFileAlt className="me-2" /> 文本
            </Nav.Link>
          </Nav.Item>
          <Nav.Item>
            <Nav.Link eventKey="file" className="rounded-pill px-3 py-1 small fw-bold test-generation-mode-link">
              <FaFileUpload className="me-2" /> 文件
            </Nav.Link>
          </Nav.Item>
        </Nav>
      </div>

      <div className="flex-grow-1 test-generation-input-body">
        {mode === 'text' ? (
          <Form.Control
            as="textarea"
            className="input-pro h-100 border-0 bg-light test-generation-requirement test-generation-no-resize"
            placeholder="请输入详细需求描述，例如：登录功能中，用户输入账号密码后应跳转首页，并校验欢迎语。"
            value={requirement}
            onChange={(e) => onRequirementChange(e.target.value)}
          />
        ) : !file ? (
          <div
            ref={uploadZoneRef}
            className={classNames('test-generation-upload-zone h-100 rounded-3 d-flex flex-column align-items-center justify-content-center text-center transition-all p-5', {
              'bg-primary-subtle border-primary': isDragActive,
              'bg-light border-secondary-subtle': !isDragActive,
              'opacity-50': loading,
              'test-generation-upload-zone-disabled': loading,
            })}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            onClick={() => {
              if (!loading) {
                fileInputRef.current?.click();
              }
            }}
          >
            <input
              type="file"
              ref={fileInputRef}
              onChange={(e) => onFileChange(e.target.files?.[0] || null)}
              className="test-generation-hidden-input"
              accept="application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown,image/png,image/jpeg,image/gif"
            />
            <div className="mb-3 text-primary">
              <FaFileUpload size={48} />
            </div>
            <h6 className="fw-bold mb-2">点击或拖拽上传文档</h6>
            <div className="text-muted small">支持 PDF、Word、TXT、Markdown、图片（最大 50MB）</div>
          </div>
        ) : (
          <div className="h-100 d-flex flex-column align-items-center justify-content-center bg-light rounded-3 p-5">
            <FaFileAlt size={64} className="text-primary mb-3" />
            <h5 className="fw-bold text-dark text-truncate w-100 text-center">{file.name}</h5>
            <p className="text-secondary mb-0">{(file.size / 1024).toFixed(1)} KB</p>
          </div>
        )}
      </div>
    </div>
  );
}
