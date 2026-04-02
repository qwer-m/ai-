import { Button, Form, InputGroup, Spinner } from 'react-bootstrap';
import { FaCog, FaDownload, FaFileImage, FaPlay, FaTrash } from 'react-icons/fa';
import type { RefObject } from 'react';
import type { TestGenerationMode } from './types';

type TestGenerationConfigSectionProps = {
  mode: TestGenerationMode;
  projectId: number | null;
  docType: string;
  onDocTypeChange: (value: string) => void;
  protoFile: File | null;
  protoInputRef: RefObject<HTMLInputElement | null>;
  onProtoFileChange: (file: File | null) => void;
  compress: boolean;
  onCompressChange: (checked: boolean) => void;
  expectedCount: number;
  onExpectedCountChange: (value: number) => void;
  isEstimating: boolean;
  appendCount: number;
  onAppendCountChange: (value: number) => void;
  force: boolean;
  onForceChange: (checked: boolean) => void;
  hasJsonInResultBox: boolean;
  isLimitReached: boolean;
  loading: boolean;
  onGenerate: () => void;
  onExport: () => void;
  onClear: () => void;
  hasOutput: boolean;
};

export function TestGenerationConfigSection({
  mode,
  projectId,
  docType,
  onDocTypeChange,
  protoFile,
  protoInputRef,
  onProtoFileChange,
  compress,
  onCompressChange,
  expectedCount,
  onExpectedCountChange,
  isEstimating,
  appendCount,
  onAppendCountChange,
  force,
  onForceChange,
  hasJsonInResultBox,
  isLimitReached,
  loading,
  onGenerate,
  onExport,
  onClear,
  hasOutput,
}: TestGenerationConfigSectionProps) {
  return (
    <div className="test-generation-config bento-card col-span-6 p-4 d-flex flex-column gap-3 bg-body panel-card panel-card-config">
      <div className="panel-card-head">
        <div className="panel-card-title-row">
          <h6 className="fw-bold d-flex align-items-center gap-2 mb-0 panel-card-title">
            <FaCog className="text-primary" /> 配置面板
          </h6>
        </div>
      </div>

      {mode === 'file' ? (
        <div className="p-3 bg-body-tertiary rounded-3 mb-2">
          <Form.Group className="mb-3">
            <Form.Label className="small fw-bold text-secondary">文档类型</Form.Label>
            <Form.Select className="input-pro form-select-sm" value={docType} onChange={(e) => onDocTypeChange(e.target.value)}>
              <option value="requirement">需求文档</option>
              <option value="incomplete">残缺文档（需补充原型图）</option>
              <option value="product_requirement">产品需求</option>
            </Form.Select>
          </Form.Group>

          {docType === 'incomplete' ? (
            <Form.Group>
              <Form.Label className="small fw-bold text-secondary">补充原型图</Form.Label>
              <div className="d-flex gap-2">
                <Button
                  variant="outline-secondary"
                  size="sm"
                  className="w-100 text-start d-flex align-items-center justify-content-between input-pro"
                  onClick={() => protoInputRef.current?.click()}
                >
                  <span className="text-truncate">{protoFile ? protoFile.name : '选择图片...'}</span>
                  <FaFileImage />
                </Button>
                {protoFile ? (
                  <Button variant="outline-danger" size="sm" onClick={() => onProtoFileChange(null)}>
                    <FaTrash />
                  </Button>
                ) : null}
              </div>
            </Form.Group>
          ) : null}
        </div>
      ) : null}

      <div className="p-3 bg-body-tertiary rounded-3 flex-grow-1">
        <Form.Check
          type="switch"
          id="compress-switch"
          label="启用上下文压缩"
          checked={compress}
          onChange={(e) => onCompressChange(e.target.checked)}
          className="fw-medium mb-3"
        />

        <Form.Group className="mb-3">
          <div className="d-flex gap-2">
            <div className="flex-grow-1">
              <Form.Label className="small fw-bold text-secondary">推荐生成用例数</Form.Label>
              <InputGroup>
                <Form.Control
                  type="number"
                  className={`input-pro ${isEstimating ? 'test-generation-estimating-input' : ''}`}
                  value={expectedCount}
                  min={1}
                  step={1}
                  onChange={(e) => onExpectedCountChange(Math.max(1, Math.floor(Number(e.target.value) || 1)))}
                />
                {isEstimating ? (
                  <InputGroup.Text className="bg-white border-start-0 ps-0">
                    <Spinner animation="border" size="sm" variant="primary" />
                  </InputGroup.Text>
                ) : null}
              </InputGroup>
            </div>
            <div className="flex-grow-1">
              <Form.Label className="small fw-bold text-secondary">追加用例数</Form.Label>
              <Form.Control
                type="number"
                className="input-pro"
                value={appendCount}
                min={1}
                step={1}
                onChange={(e) => onAppendCountChange(Math.max(1, Math.floor(Number(e.target.value) || 1)))}
              />
            </div>
          </div>
        </Form.Group>

        {mode === 'file' ? (
          <Form.Check
            type="checkbox"
            id="force-gen"
            label="强制重新生成"
            checked={force}
            onChange={(e) => onForceChange(e.target.checked)}
            className="text-secondary small mt-3"
          />
        ) : null}
      </div>

      <div className="mt-auto d-flex flex-column gap-2 panel-card-actions">
        <Button
          variant="primary"
          className="w-100 text-white d-flex align-items-center justify-content-center test-generation-action-btn"
          disabled={loading || !projectId || isLimitReached}
          onClick={onGenerate}
        >
          {loading ? (
            <>
              <Spinner size="sm" animation="border" className="me-2" /> 生成中...
            </>
          ) : isLimitReached ? (
            <>
              <FaPlay className="me-2" /> 开始生成
            </>
          ) : (
            <>
              <FaPlay className="me-2" /> {hasJsonInResultBox ? '继续生成' : '开始生成'}
            </>
          )}
        </Button>

        {hasOutput ? (
          <div className="d-flex gap-2">
            <Button variant="outline-secondary" className="flex-grow-1 border d-flex align-items-center justify-content-center test-generation-action-btn" onClick={onExport}>
              <FaDownload className="me-2" /> 导出
            </Button>
            <Button variant="outline-danger" className="flex-grow-1 border d-flex align-items-center justify-content-center test-generation-action-btn" onClick={onClear}>
              <FaTrash className="me-2" /> 清除
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
