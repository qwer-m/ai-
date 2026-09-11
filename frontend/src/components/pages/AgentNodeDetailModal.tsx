import { Badge, Modal } from 'react-bootstrap';
import type { AgentDefinition, AgentNodeRun } from '../agent-platform/types';

export type AgentNodeTiming = {
  duration: string;
  reused: boolean;
  reuseLabel: string;
  sourceDuration: string;
};

export type AgentNodeParallelism = {
  maxConcurrency: number;
  activeInstances: number;
  items: Record<string, unknown>[];
};

export function JsonView({ value }: { value: unknown }) {
  return <pre className="agent-json-view mb-0">{JSON.stringify(value ?? {}, null, 2)}</pre>;
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function instanceStatusLabel(value: unknown): string {
  const status = stringValue(value);
  if (status === 'success') return '已完成';
  if (status === 'failed') return '失败';
  if (status === 'retrying') return '重试中';
  if (status === 'queued') return '等待调度';
  return '执行中';
}

function instanceStatusVariant(value: unknown): string {
  const status = stringValue(value);
  if (status === 'success') return 'success';
  if (status === 'failed') return 'danger';
  if (status === 'retrying') return 'warning';
  if (status === 'queued') return 'secondary';
  return 'primary';
}

function friendlyErrorMessage(value: string): string {
  const message = value.trim();
  if (!message) return '';
  if (/504|Gateway Time-out/i.test(message)) return '模型服务响应超时，当前子智能体未完成任务。请稍后重新开始生成。';
  if (/InternalServerError|<html/i.test(message)) return '模型服务执行失败，技术详情已收起，可在实时日志中查看。';
  if (/未返回可校验的最终结构化正文|invalid_final_output/i.test(message)) return '模型未返回完整结构化结果，可能是输出被截断；已保留技术详情供重试和追踪。';
  if (/业务规划证据路由|业务规划模块内部重复 evidence_ids/i.test(message)) return '业务规划未完整覆盖有效事实，或引用了无效证据；技术详情中已列出具体证据 ID。';
  if (/字段\s*=\s*risks(?:\.|\s|$)|risks\.\d+/i.test(message)) return '业务规划风险字段结构不符合要求，技术详情中已保留风险内容和校验路径。';
  if (/来源锚点|source_anchor/i.test(message)) return '模型返回的来源锚点结构不符合要求，已保留技术详情供重试和追踪。';
  if (/契约校验失败|不满足 JSON 契约|is not valid under|Additional properties/i.test(message)) return '当前智能体输出未通过结构化契约校验，技术详情中已列出具体字段和校验路径。';
  if (/修复批次仍未覆盖要求事实/i.test(message)) {
    const factIds = message.match(/DOC\d+-[A-Za-z0-9-]+|FACT-[A-Za-z0-9-]+/g) ?? [];
    const factSummary = [...new Set(factIds)].slice(0, 4).join('、');
    return factSummary ? `终审修复遗漏了必须覆盖的事实：${factSummary}。` : '终审修复遗漏了必须覆盖的事实，已定位到对应修复批次。';
  }
  if (/终审difference文字引用的事实与字段落点不一致/i.test(message)) return '终审建议引用了待补充绑定的本批次事实，本次运行按旧校验规则失败。';
  if (/单项结果校验失败|postprocessor/i.test(message)) return '当前智能体输出未通过平台校验，已定位到对应任务和校验规则。';
  if (/硬截止|deadline/i.test(message)) return '本次生成已达到运行时限并停止，不会继续后台重试。';
  if (/阶段预算/i.test(message)) return '当前阶段已达到分配的时间预算并停止，技术详情中已标明具体节点。';
  if (/ModelBehaviorError/i.test(message)) return '模型未按预期返回可处理结果，已保留技术详情供重试和追踪。';
  return message.length > 220 ? `${message.slice(0, 220)}…` : message;
}

export function AgentErrorDetails({ message, className }: { message: string; className: string }) {
  const summary = friendlyErrorMessage(message);
  return (
    <div className={className} role="alert">
      <div>{summary}</div>
      {summary !== message.trim() && (
        <details className="agent-error-details">
          <summary>错误详情</summary>
          <pre>{message}</pre>
        </details>
      )}
    </div>
  );
}

type Props = {
  show: boolean;
  onHide: () => void;
  nodeRun: AgentNodeRun | undefined;
  agent: AgentDefinition | undefined;
  role: string;
  model: { name: string; source: string } | null;
  timing: AgentNodeTiming | null;
  progress: string | null;
  statusLabel: string;
  parallelism: AgentNodeParallelism | null;
};

export function AgentNodeDetailModal({
  show,
  onHide,
  nodeRun,
  agent,
  role,
  model,
  timing,
  progress,
  statusLabel,
  parallelism,
}: Props) {
  return (
    <Modal
      id="agent-node-detail-dialog"
      show={show}
      onHide={onHide}
      centered
      scrollable
      size="xl"
      dialogClassName="agent-node-detail-dialog"
      aria-labelledby="agent-node-detail-title"
      autoFocus
      enforceFocus
      restoreFocus
    >
      {nodeRun && (
        <>
          <Modal.Header closeButton closeLabel="关闭智能体详情">
            <div className="agent-node-detail-heading">
              <span>{role}</span>
              <Modal.Title as="h2" id="agent-node-detail-title">
                {agent?.name || nodeRun.node_key}
              </Modal.Title>
            </div>
          </Modal.Header>
          <Modal.Body className="agent-node-detail-body">
            <p>{agent?.description || '该 Agent 由本次生成按需激活。'}</p>
            <div className="agent-definition-grid">
              <div><span>实际模型</span><strong>{model?.name}</strong><small className="agent-model-source">{model?.source}</small></div>
              <div><span>任务进度</span><strong>{progress || statusLabel}</strong></div>
              <div>
                <span>执行时间</span>
                <strong>{timing?.reused ? `本次运行${timing.reuseLabel}` : timing?.duration || '尚未开始'}</strong>
                {timing?.reused && timing.sourceDuration && <small className="agent-model-source">来源节点原始耗时 {timing.sourceDuration}</small>}
              </div>
              {nodeRun.node_type === 'agent_map' && parallelism && (
                <div><span>并行方式</span><strong>最多 {parallelism.maxConcurrency} 个独立实例</strong><small className="agent-model-source">当前运行 {parallelism.activeInstances} 个</small></div>
              )}
            </div>
            {parallelism && parallelism.items.length > 0 && (
              <div className="agent-instance-section">
                <div className="agent-instance-heading"><strong>子智能体实例</strong><span>每个实例只处理分配给自己的页面或任务</span></div>
                <div className="agent-instance-grid">
                  {parallelism.items.map((item, itemIndex) => {
                    const taskNumber = Number(item.item_index ?? itemIndex) + 1;
                    return <div className="agent-instance-card" key={stringValue(item.instance_id) || taskNumber}>
                      <span>{stringValue(item.instance_id) || `实例-${taskNumber}`}</span>
                      <strong>{stringValue(item.task_label) || `任务 ${taskNumber}`}</strong>
                      <Badge bg={instanceStatusVariant(item.status)}>{instanceStatusLabel(item.status)}</Badge>
                    </div>;
                  })}
                </div>
              </div>
            )}
            {nodeRun.error_message && <AgentErrorDetails className="agent-node-friendly-error" message={nodeRun.error_message} />}
            <details className="agent-technical-details">
              <summary>查看技术详情</summary>
              <div className="agent-technical-meta"><span>Agent Key</span><strong>{agent?.agent_key || nodeRun.node_key}</strong></div>
              {agent?.instructions && <details><summary>执行指令</summary><p className="agent-technical-instructions">{agent.instructions}</p></details>}
              <details><summary>节点输入</summary><JsonView value={nodeRun.input_payload} /></details>
              <details><summary>节点输出</summary><JsonView value={nodeRun.output_payload} /></details>
              {Object.keys(nodeRun.sdk_state).length > 0 && <details><summary>SDK 执行状态</summary><JsonView value={nodeRun.sdk_state} /></details>}
            </details>
          </Modal.Body>
        </>
      )}
    </Modal>
  );
}
