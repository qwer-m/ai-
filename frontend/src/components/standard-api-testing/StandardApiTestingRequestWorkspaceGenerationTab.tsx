import { Button, Form } from 'react-bootstrap';
import { FaRobot } from 'react-icons/fa';
import type { StandardApiTestingRequestWorkspaceProps } from './StandardApiTestingRequestWorkspace.types';

type Props = Pick<
  StandardApiTestingRequestWorkspaceProps,
  'mode' | 'setMode' | 'requirement' | 'setRequirement' | 'handleRun' | 'loading'
>;

export function StandardApiTestingRequestWorkspaceGenerationTab({
  mode,
  setMode,
  requirement,
  setRequirement,
  handleRun,
  loading,
}: Props) {
  return (
    <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 standard-api-scroll-pane standard-api-pane-gen">
      <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 d-flex flex-column p-3 standard-api-pane-gen-inner">
        <div className="d-flex justify-content-between mb-2">
          <Form.Label className="small text-muted mb-0">AI 测试生成（自然语言或 JSON 结构化定义）</Form.Label>
          <div className="d-flex gap-2">
            <Form.Check type="radio" label="自然语言" checked={mode === 'natural'} onChange={() => setMode('natural')} inline className="small" />
            <Form.Check type="radio" label="结构化" checked={mode === 'structured'} onChange={() => setMode('structured')} inline className="small" />
          </div>
        </div>
        <Form.Control
          as="textarea"
          className="flex-grow-1 font-monospace small standard-api-gen-textarea"
          value={requirement}
          onChange={(e) => setRequirement(e.target.value)}
          placeholder="描述您的测试场景..."
        />
        <div className="mt-2 d-flex justify-content-end">
          <Button variant="outline-primary" size="sm" onClick={handleRun} disabled={loading}>
            <FaRobot className="me-1" /> 生成并运行测试
          </Button>
        </div>
      </div>
    </div>
  );
}
