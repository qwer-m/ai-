import { Button, Tab, Tabs } from 'react-bootstrap';
import { BizKeyTimeline } from './BizKeyTimeline';
import { CoverageTable } from './CoverageTable';
import { GenerationOverview } from './GenerationOverview';
import { useRagDebugStore } from './debugStore';
import './rag-debug-panel.css';

type Props = {
  className?: string;
  onRuleClick?: (ruleId: string) => void;
  activeRuleId?: string | null;
};

export function RagDebugPanel({ className, onRuleClick, activeRuleId }: Props) {
  const loadMock = useRagDebugStore((s) => s.loadMock);
  const reset = useRagDebugStore((s) => s.reset);
  const lastUpdatedAt = useRagDebugStore((s) => s.lastUpdatedAt);

  return (
    <div className={`rag-debug-panel-root ${className || ''}`}>
      <div className="rag-debug-card rounded-2xl shadow-md p-4 border bg-white dark:bg-slate-900">
        <div className="d-flex justify-content-between align-items-center mb-3">
          <div>
            <h5 className="mb-1 fw-bold">RAG / 测试用例生成调试面板</h5>
            <div className="small text-muted rag-debug-muted">
              {lastUpdatedAt ? `最近更新：${new Date(lastUpdatedAt).toLocaleString()}` : '等待 GEN_DIAG 数据流...'}
            </div>
          </div>
          <div className="d-flex gap-2">
            <Button variant="outline-secondary" size="sm" onClick={loadMock}>
              加载 Mock
            </Button>
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
        </Tabs>
      </div>
    </div>
  );
}

