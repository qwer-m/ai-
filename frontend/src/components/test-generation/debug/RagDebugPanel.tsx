import { Button, Tab, Tabs } from 'react-bootstrap';
import { BizKeyTimeline } from './BizKeyTimeline';
import { CoverageTable } from './CoverageTable';
import { GenerationOverview } from './GenerationOverview';
import { PriorityDebugTable } from './PriorityDebugTable';
import { useRagDebugStore } from './debugStore';
import './rag-debug-panel.css';

type ResultSource = 'none' | 'streaming_preview' | 'final_persisted';

type Props = {
  className?: string;
  onRuleClick?: (ruleId: string) => void;
  activeRuleId?: string | null;
  result?: any;
  resultSource?: ResultSource;
};

export function RagDebugPanel({
  className,
  onRuleClick,
  activeRuleId,
  result,
  resultSource = 'none',
}: Props) {
  const reset = useRagDebugStore((s) => s.reset);
  const lastUpdatedAt = useRagDebugStore((s) => s.lastUpdatedAt);

  return (
    <div className={`rag-debug-panel-root ${className || ''}`}>
      <div className="rag-debug-card rounded-2xl shadow-md p-4 border bg-white dark:bg-slate-900">
        <div className="d-flex justify-content-between align-items-center mb-3">
          <div>
            <h5 className="mb-1 fw-bold">RAG / 测试用例生成调试面板</h5>
            <div className="small text-muted rag-debug-muted">
              {lastUpdatedAt ? `最近更新时间：${new Date(lastUpdatedAt).toLocaleString()}` : '等待 GEN_DIAG 调试事件...'}
            </div>
          </div>
          <div className="d-flex gap-2">
            <Button variant="outline-danger" size="sm" onClick={reset}>
              清空
            </Button>
          </div>
        </div>

        <Tabs defaultActiveKey="overview" className="mb-3">
          <Tab eventKey="overview" title="生成概览">
            <GenerationOverview />
          </Tab>
          <Tab eventKey="timeline" title="执行时间线">
            <BizKeyTimeline />
          </Tab>
          <Tab eventKey="coverage" title="覆盖诊断">
            <CoverageTable onRuleClick={onRuleClick} activeRuleId={activeRuleId} />
          </Tab>
          <Tab eventKey="priority" title="优先级诊断">
            <PriorityDebugTable result={result} resultSource={resultSource} />
          </Tab>
        </Tabs>
      </div>
    </div>
  );
}
