import { Alert, Button, Card, Spinner } from 'react-bootstrap';
import { FaPlay, FaRedo } from 'react-icons/fa';
import { PipelineOrchestrationInputPanel } from '../pipeline-orchestration/PipelineOrchestrationInputPanel';
import { PipelineOrchestrationMonitorPanel } from '../pipeline-orchestration/PipelineOrchestrationMonitorPanel';
import { usePipelineOrchestrationController } from '../pipeline-orchestration/usePipelineOrchestrationController';

type Props = {
  projectId: number | null;
  onLog: (msg: string) => void;
};

/**
 * 页面容器仅负责组织布局与状态编排。
 * 具体的业务状态、副作用和网络请求下沉到 hook，表单与监控区域下沉到展示组件，
 * 这样可以在不改变现有行为的前提下，降低单文件复杂度并便于后续局部迭代。
 */
export function PipelineOrchestration({ projectId, onLog }: Props) {
  const controller = usePipelineOrchestrationController({ projectId, onLog });

  return (
    <div className="d-flex flex-column gap-3 p-3 h-100 overflow-auto pipeline-workbench">
      {controller.errorMsg && <Alert variant="danger" className="mb-0">{controller.errorMsg}</Alert>}
      {!projectId && <Alert variant="warning" className="mb-0">请先选择项目。</Alert>}

      <Card className="border-0 shadow-sm panel-card pipeline-hero-card">
        <Card.Body className="d-flex justify-content-between align-items-center pipeline-hero-body">
          <div>
            <h5 className="mb-1">全局编排</h5>
            <div className="text-muted small">支持运行持久化、历史记录、恢复执行和分阶段重试。</div>
          </div>
          <div className="d-flex gap-2">
            <Button variant="outline-secondary" onClick={controller.resetView} disabled={controller.isRunning}>
              <FaRedo className="me-1" />
              重置视图
            </Button>
            <Button variant="primary" onClick={controller.runPipeline} disabled={!controller.canRun}>
              {controller.isRunning ? <Spinner size="sm" className="me-2" /> : <FaPlay className="me-2" />}
              运行流水线
            </Button>
          </div>
        </Card.Body>
      </Card>

      <div className="row g-3">
        <div className="col-lg-7">
          <PipelineOrchestrationInputPanel controller={controller} />
        </div>
        <div className="col-lg-5">
          <PipelineOrchestrationMonitorPanel controller={controller} projectId={projectId} />
        </div>
      </div>
    </div>
  );
}
