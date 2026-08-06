import { Badge, Button, Form, Spinner } from 'react-bootstrap';
import {
  FaBan,
  FaCheck,
  FaClock,
  FaPlay,
  FaRedo,
  FaRobot,
  FaSyncAlt,
  FaTimes,
  FaTools,
} from 'react-icons/fa';
import { useAgentWorkspace } from '../agent-platform/useAgentWorkspace';
import type { RunStatus } from '../agent-platform/types';

type Props = {
  projectId: number | null;
  onLog: (message: string) => void;
};

const statusLabel: Record<RunStatus, string> = {
  pending: '等待中',
  running: '运行中',
  waiting_approval: '等待审批',
  success: '成功',
  failed: '失败',
  cancelled: '已取消',
};

const statusVariant: Record<RunStatus, string> = {
  pending: 'secondary',
  running: 'primary',
  waiting_approval: 'warning',
  success: 'success',
  failed: 'danger',
  cancelled: 'dark',
};

function formatTime(value: string | null | undefined) {
  if (!value) return '-';
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

function JsonView({ value }: { value: unknown }) {
  return <pre className="agent-json-view mb-0">{JSON.stringify(value ?? {}, null, 2)}</pre>;
}

type QuotaMetric = {
  key: string;
  label: string;
  charged: number;
  actual: number;
  limit: number;
};

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function numericValue(record: Record<string, unknown>, key: string): number {
  const value = Number(record[key] ?? 0);
  return Number.isFinite(value) && value >= 0 ? value : 0;
}

function quotaMetrics(runContext: Record<string, unknown>): QuotaMetric[] {
  const limits = objectValue(runContext.execution_limits);
  const usage = objectValue(runContext.usage);
  const charged = objectValue(runContext.quota_usage);
  const definitions = [
    ['requests', '请求', 'attempted_requests', 'max_requests'],
    ['input', '输入 Token', 'input_tokens', 'max_input_tokens'],
    ['output', '输出 Token', 'output_tokens', 'max_output_tokens'],
    ['total', '总 Token', 'total_tokens', 'max_total_tokens'],
  ] as const;
  return definitions.map(([key, label, usageKey, limitKey]) => ({
    key,
    label,
    charged: numericValue(charged, usageKey) || numericValue(usage, usageKey),
    actual: numericValue(usage, usageKey),
    limit: numericValue(limits, limitKey),
  })).filter((item) => item.limit > 0);
}

export function AgentWorkspace({ projectId, onLog }: Props) {
  const controller = useAgentWorkspace({ projectId, onLog });
  const runBusy = controller.activeRun && ['pending', 'running'].includes(controller.activeRun.status);
  const activeQuotaMetrics = controller.activeRun
    ? quotaMetrics(controller.activeRun.run_context)
    : [];

  if (!projectId) {
    return <div className="agent-workspace-empty">请先选择项目</div>;
  }

  return (
    <div className="agent-workspace">
      <header className="agent-workspace-header">
        <div>
          <h2>Agent 工作台</h2>
          <span>项目 #{projectId}</span>
        </div>
        <Button
          variant="outline-secondary"
          size="sm"
          onClick={() => void controller.loadWorkspace()}
          disabled={controller.loading}
          title="刷新 Agent 工作台"
          aria-label="刷新 Agent 工作台"
        >
          <FaSyncAlt className={controller.loading ? 'agent-spin' : ''} />
        </Button>
      </header>

      {controller.error && <div className="agent-workspace-error">{controller.error}</div>}

      <div className="agent-workspace-grid">
        <aside className="agent-catalog-pane">
          <section className="agent-pane-section">
            <div className="agent-section-title">
              <FaRobot /> 智能体
              <Badge bg="secondary">{controller.catalog.agents.length}</Badge>
            </div>
            <div className="agent-catalog-list">
              {controller.catalog.agents.map((agent) => (
                <div className="agent-catalog-item" key={agent.id}>
                  <strong>{agent.name}</strong>
                  <small>{agent.agent_key} · v{agent.version}</small>
                  <span>{agent.description}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="agent-pane-section">
            <div className="agent-section-title">
              <FaTools /> 工具
              <Badge bg="secondary">{controller.catalog.tools.length}</Badge>
            </div>
            <div className="agent-catalog-list">
              {controller.catalog.tools.map((tool) => (
                <div className="agent-catalog-item" key={tool.id}>
                  <strong>{tool.name}</strong>
                  <small>{tool.tool_key} · 风险 {tool.risk_level}</small>
                  <span>{tool.description}</span>
                </div>
              ))}
            </div>
          </section>
        </aside>

        <main className="agent-run-pane">
          <section className="agent-run-form">
            <div className="agent-section-title">新建运行</div>
            <div className="agent-form-row">
              <Form.Group>
                <Form.Label>工作流</Form.Label>
                <Form.Select
                  value={controller.workflowKey}
                  onChange={(event) => controller.setWorkflowKey(event.target.value)}
                  disabled={controller.submitting}
                >
                  {controller.catalog.workflows.map((workflow) => (
                    <option value={workflow.workflow_key} key={workflow.id}>
                      {workflow.name} · v{workflow.version}
                    </option>
                  ))}
                </Form.Select>
              </Form.Group>
              <Form.Group>
                <Form.Label>用例预算</Form.Label>
                <Form.Control
                  type="number"
                  min={1}
                  max={200}
                  value={controller.caseBudget}
                  onChange={(event) => controller.setCaseBudget(Number(event.target.value))}
                  disabled={controller.submitting}
                />
              </Form.Group>
            </div>
            <Form.Group>
              <Form.Label>需求来源</Form.Label>
              <Form.Select
                value={controller.requirementDocId ?? ''}
                onChange={(event) => {
                  const value = event.target.value;
                  controller.setRequirementDocId(value ? Number(value) : null);
                }}
                disabled={controller.submitting}
              >
                <option value="">直接输入需求</option>
                {controller.requirementDocuments.map((document) => (
                  <option value={document.id} key={document.id}>
                    {document.filename}
                    {document.linked_test_case_count > 0
                      ? ` · 已关联 ${document.linked_test_case_count} 份用例`
                      : ''}
                  </option>
                ))}
              </Form.Select>
            </Form.Group>
            <Form.Group>
              <Form.Label>直接输入</Form.Label>
              <Form.Control
                as="textarea"
                rows={5}
                value={controller.requirement}
                onChange={(event) => controller.setRequirement(event.target.value)}
                placeholder={controller.requirementDocId ? '已选择需求文档，正文将由证据工具读取' : '输入本次工作流实际使用的需求内容'}
                disabled={controller.submitting || Boolean(controller.requirementDocId)}
              />
            </Form.Group>
            <div className="agent-run-form-actions">
              <Button
                onClick={() => void controller.runWorkflow()}
                disabled={
                  controller.submitting
                  || !controller.workflowKey
                  || (!controller.requirementDocId && !controller.requirement.trim())
                }
              >
                {controller.submitting ? <Spinner size="sm" /> : <FaPlay />} 启动 Run
              </Button>
            </div>
          </section>

          <section className="agent-workflow-section">
            <div className="agent-section-title">工作流节点</div>
            <div className="agent-node-list">
              {(controller.selectedWorkflow?.definition.nodes ?? []).map((node, index) => {
                const nodeRun = controller.activeRun?.nodes
                  .filter((item) => item.node_key === node.node_key)
                  .sort((a, b) => b.attempt - a.attempt)[0];
                return (
                  <div className="agent-node-row" key={node.node_key}>
                    <span className="agent-node-index">{index + 1}</span>
                    <div>
                      <strong>{node.node_key}</strong>
                      <small>{node.node_type} · {node.reference_key}</small>
                    </div>
                    {nodeRun ? (
                      <Badge bg={statusVariant[nodeRun.status]}>{statusLabel[nodeRun.status]}</Badge>
                    ) : (
                      <Badge bg="light" text="dark">未运行</Badge>
                    )}
                  </div>
                );
              })}
            </div>
          </section>

          <section className="agent-artifact-section">
            <div className="agent-section-title">运行产物</div>
            {controller.activeRun ? (
              <>
                <div className="agent-run-summary">
                  <span>Run #{controller.activeRun.id}</span>
                  <Badge bg={statusVariant[controller.activeRun.status]}>
                    {statusLabel[controller.activeRun.status]}
                  </Badge>
                  <span>{formatTime(controller.activeRun.created_at)}</span>
                  <div className="agent-run-actions">
                    {runBusy && (
                      <Button
                        size="sm"
                        variant="outline-danger"
                        onClick={() => void controller.cancelRun()}
                        title="取消运行"
                      >
                        <FaBan /> 取消
                      </Button>
                    )}
                    {['failed', 'cancelled'].includes(controller.activeRun.status) && (
                      <Button size="sm" variant="outline-primary" onClick={() => void controller.retryRun()}>
                        <FaRedo /> 重试
                      </Button>
                    )}
                  </div>
                </div>
                {controller.activeRun.error_message && (
                  <div className="agent-run-error">{controller.activeRun.error_message}</div>
                )}
                {activeQuotaMetrics.length > 0 && (
                  <div className="agent-quota-panel" aria-label="Agent Run 额度使用情况">
                    {activeQuotaMetrics.map((metric) => {
                      const percent = Math.min(100, (metric.charged / metric.limit) * 100);
                      return (
                        <div className="agent-quota-metric" key={metric.key}>
                          <div>
                            <strong>{metric.label}</strong>
                            <span>{metric.charged.toLocaleString()} / {metric.limit.toLocaleString()}</span>
                          </div>
                          <progress value={percent} max={100} />
                          <small>模型实际回报：{metric.actual.toLocaleString()}</small>
                        </div>
                      );
                    })}
                  </div>
                )}
                <JsonView value={controller.activeRun.output_payload} />
              </>
            ) : (
              <div className="agent-muted-state">选择历史 Run 或启动一次新运行</div>
            )}
          </section>
        </main>

        <aside className="agent-observe-pane">
          <section className="agent-pane-section agent-history-section">
            <div className="agent-section-title">
              <FaClock /> 运行历史
            </div>
            <div className="agent-history-list">
              {controller.runs.map((run) => (
                <button
                  type="button"
                  key={run.id}
                  className={controller.activeRun?.id === run.id ? 'active' : ''}
                  onClick={() => void controller.openRun(run.id)}
                >
                  <span>#{run.id}</span>
                  <Badge bg={statusVariant[run.status]}>{statusLabel[run.status]}</Badge>
                  <small>{formatTime(run.created_at)}</small>
                </button>
              ))}
            </div>
          </section>

          <section className="agent-pane-section">
            <div className="agent-section-title">待审批</div>
            {(controller.activeRun?.approvals ?? []).filter((item) => item.status === 'pending').map((approval) => (
              <div className="agent-approval" key={approval.id}>
                <strong>审批 #{approval.id}</strong>
                <JsonView value={approval.request_payload} />
                <div>
                  <Button size="sm" variant="success" onClick={() => void controller.decideApproval(approval.id, true)}>
                    <FaCheck /> 通过
                  </Button>
                  <Button size="sm" variant="outline-danger" onClick={() => void controller.decideApproval(approval.id, false)}>
                    <FaTimes /> 拒绝
                  </Button>
                </div>
              </div>
            ))}
            {!controller.activeRun?.approvals.some((item) => item.status === 'pending') && (
              <div className="agent-muted-state">当前无待审批操作</div>
            )}
          </section>

          <section className="agent-pane-section agent-events-section">
            <div className="agent-section-title">事件流</div>
            <div className="agent-event-list">
              {controller.events.map((event) => (
                <div key={event.id}>
                  <span>{event.sequence}</span>
                  <strong>{event.event_type}</strong>
                  <small>{formatTime(event.created_at)}</small>
                </div>
              ))}
              {controller.events.length === 0 && <div className="agent-muted-state">暂无事件</div>}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
