import { Badge, Button, Card, Form, Spinner, Table } from 'react-bootstrap';
import {
  runStatusLabel,
  stageLabel,
  stageOrder,
  stageStatusLabel,
  statusVariant,
  toText,
  traceKindLabel,
  type StageKey,
} from './model';
import type { PipelineOrchestrationController } from './usePipelineOrchestrationController';

type Props = {
  controller: PipelineOrchestrationController;
  projectId: number | null;
};

export function PipelineOrchestrationMonitorPanel({ controller, projectId }: Props) {
  return (
    <>
      <Card className="border-0 shadow-sm mb-3">
        <Card.Body>
          <div className="d-flex justify-content-between align-items-center mb-3">
            <h6 className="mb-0">阶段状态</h6>
            <div className="d-flex gap-2 align-items-center">
              {controller.activeRunId && <Badge bg="dark">运行 #{controller.activeRunId}</Badge>}
              <Badge
                bg={
                  controller.runStatus === 'idle'
                    ? 'secondary'
                    : controller.runStatus === 'success'
                      ? 'success'
                      : controller.runStatus === 'failed'
                        ? 'danger'
                        : 'primary'
                }
              >
                {runStatusLabel[controller.runStatus]}
              </Badge>
            </div>
          </div>

          <Table size="sm" className="mb-2 align-middle">
            <thead>
              <tr>
                <th>阶段</th>
                <th>状态</th>
                <th>消息</th>
              </tr>
            </thead>
            <tbody>
              {controller.stageRows.map((row) => (
                <tr key={row.key}>
                  <td>{stageLabel[row.key]}</td>
                  <td>
                    <Badge bg={statusVariant(row.status)}>{stageStatusLabel[row.status]}</Badge>
                  </td>
                  <td className="small text-muted">{row.message || '-'}</td>
                </tr>
              ))}
            </tbody>
          </Table>

          {controller.activeRunId && controller.runStatus !== 'running' && controller.runStatus !== 'pending' && (
            <div className="d-flex gap-2 mt-2">
              <Button variant="outline-primary" size="sm" onClick={controller.resumeRun}>
                恢复运行
              </Button>
              <Form.Select
                size="sm"
                value={controller.retryFromStage}
                onChange={(e) => controller.setRetryFromStage(e.target.value as StageKey)}
                className="pipeline-stage-select"
              >
                {stageOrder.map((stage) => (
                  <option key={stage} value={stage}>
                    {stageLabel[stage]}
                  </option>
                ))}
              </Form.Select>
              <Button variant="outline-secondary" size="sm" onClick={controller.retryRun}>
                从该阶段重试
              </Button>
            </div>
          )}
        </Card.Body>
      </Card>

      <Card className="border-0 shadow-sm mb-3">
        <Card.Body>
          <div className="d-flex justify-content-between align-items-center mb-2">
            <h6 className="mb-0">运行历史</h6>
            <Button
              variant="outline-secondary"
              size="sm"
              onClick={controller.refreshHistory}
              disabled={controller.historyLoading || !projectId}
            >
              {controller.historyLoading ? <Spinner size="sm" /> : '刷新'}
            </Button>
          </div>
          <div className="pipeline-history-scroll">
            <Table size="sm" hover className="mb-0">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>状态</th>
                  <th>创建时间</th>
                </tr>
              </thead>
              <tbody>
                {controller.history.map((item) => (
                  <tr key={item.id} onClick={() => controller.openHistoryRun(item.id)} className="pipeline-row-action">
                    <td>#{item.id}</td>
                    <td>
                      <Badge bg={item.status === 'success' ? 'success' : item.status === 'failed' ? 'danger' : 'primary'}>
                        {runStatusLabel[item.status]}
                      </Badge>
                    </td>
                    <td className="small text-muted">{item.created_at ? new Date(item.created_at).toLocaleString() : '-'}</td>
                  </tr>
                ))}
                {controller.history.length === 0 && (
                  <tr>
                    <td colSpan={3} className="small text-muted text-center py-3">
                      暂无运行记录。
                    </td>
                  </tr>
                )}
              </tbody>
            </Table>
          </div>
        </Card.Body>
      </Card>

      <Card className="border-0 shadow-sm mb-3">
        <Card.Body>
          <div className="d-flex justify-content-between align-items-center mb-2">
            <h6 className="mb-0">工作流追踪</h6>
            <Button
              variant="outline-secondary"
              size="sm"
              onClick={() => controller.refreshTraces()}
              disabled={controller.traceLoading || !controller.activeRunId}
            >
              {controller.traceLoading ? <Spinner size="sm" /> : '刷新'}
            </Button>
          </div>

          <div className="pipeline-trace-scroll">
            <Table size="sm" className="mb-0 align-middle">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>阶段</th>
                  <th>动作</th>
                </tr>
              </thead>
              <tbody>
                {controller.traces.map((item) => (
                  <tr
                    key={item.id}
                    className={`pipeline-row-action ${item.id === controller.selectedTraceId ? 'table-active' : ''}`}
                    onClick={() => controller.setSelectedTraceId(item.id)}
                  >
                    <td className="small text-muted">{item.created_at ? new Date(item.created_at).toLocaleTimeString() : '-'}</td>
                    <td className="small">
                      {(traceKindLabel[item.kind] || item.kind)}/
                      {(stageLabel[item.stage as StageKey] || item.stage)}
                    </td>
                    <td className="small text-muted">{item.action || '-'}</td>
                  </tr>
                ))}
                {controller.traces.length === 0 && (
                  <tr>
                    <td colSpan={3} className="small text-muted text-center py-3">
                      {controller.activeRunId ? '当前运行暂无追踪事件。' : '请选择一条运行记录查看追踪信息。'}
                    </td>
                  </tr>
                )}
              </tbody>
            </Table>
          </div>

          <Form.Group className="mt-2">
            <Form.Label className="small">追踪详情</Form.Label>
            <Form.Control
              as="textarea"
              rows={5}
              readOnly
              value={controller.selectedTrace ? toText(controller.selectedTrace.details) : ''}
              placeholder="点击上方追踪行可查看详情。"
            />
          </Form.Group>
        </Card.Body>
      </Card>

      <Card className="border-0 shadow-sm">
        <Card.Body className="d-flex flex-column gap-2">
          <h6 className="mb-0">流水线输出</h6>
          <Form.Group>
            <Form.Label className="small">生成的测试用例</Form.Label>
            <Form.Control as="textarea" rows={4} value={controller.generatedCases} readOnly />
          </Form.Group>
          <Form.Group>
            <Form.Label className="small">UI 脚本</Form.Label>
            <Form.Control as="textarea" rows={3} value={controller.uiScript} readOnly />
          </Form.Group>
          <Form.Group>
            <Form.Label className="small">UI 执行结果</Form.Label>
            <Form.Control as="textarea" rows={3} value={controller.uiExecutionResult} readOnly />
          </Form.Group>
          <Form.Group>
            <Form.Label className="small">接口脚本</Form.Label>
            <Form.Control as="textarea" rows={3} value={controller.apiScript} readOnly />
          </Form.Group>
          <Form.Group>
            <Form.Label className="small">接口执行结果</Form.Label>
            <Form.Control as="textarea" rows={3} value={controller.apiExecutionResult} readOnly />
          </Form.Group>
          <Form.Group>
            <Form.Label className="small">评估输出</Form.Label>
            <Form.Control as="textarea" rows={6} value={controller.evaluationOutput} readOnly />
          </Form.Group>
          <Form.Group>
            <Form.Label className="small">智能体洞察</Form.Label>
            <Form.Control as="textarea" rows={6} value={controller.agentInsights} readOnly />
          </Form.Group>
        </Card.Body>
      </Card>
    </>
  );
}
