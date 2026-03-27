import { useEffect, useState } from 'react';
import { Alert, Tab, Tabs } from 'react-bootstrap';
import { listRagDatasets, translateError } from '../state/evaluationService';
import { RagBatchEvalPanel } from './RagBatchEvalPanel';
import { RagDatasetManagerPanel } from './RagDatasetManagerPanel';
import { RagSingleDebugPanel } from './RagSingleDebugPanel';
import type { RagDatasetRow } from './shared/types';
import './console/RagDebugConsole.css';

type Props = {
  projectId: number | null;
  onLog: (msg: string) => void;
};

export function RagWorkbenchPanel({ projectId, onLog }: Props) {
  const [mode, setMode] = useState<'debug' | 'datasets' | 'report'>('debug');
  const [datasets, setDatasets] = useState<RagDatasetRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  const loadDatasets = async () => {
    try {
      const rows = await listRagDatasets();
      setDatasets((rows || []) as RagDatasetRow[]);
    } catch (e) {
      setError(await translateError(e));
    }
  };

  useEffect(() => {
    void loadDatasets();
  }, []);

  return (
    <div className="rag-console bento-card col-span-12 p-4 d-flex flex-column gap-3 hover-lift">
      <div className="d-flex align-items-center justify-content-between rag-console-title-row">
        <h5 className="mb-0">RAG Debug Console</h5>
        <span className="small rag-console-subtitle">调试工作台 / 数据集管理 / 评测报告</span>
      </div>

      {error ? <Alert variant="danger" className="mb-0">{error}</Alert> : null}

      <Tabs className="rag-console-tabs" activeKey={mode} onSelect={(k) => setMode((k as 'debug' | 'datasets' | 'report') || 'debug')}>
        <Tab eventKey="debug" title="调试">
          <div className="pt-3 rag-console-tab-pane">
            <RagSingleDebugPanel projectId={projectId} onLog={onLog} datasets={datasets} />
          </div>
        </Tab>

        <Tab eventKey="datasets" title="数据集管理">
          <div className="pt-3 rag-console-tab-pane">
            <RagDatasetManagerPanel
              datasets={datasets}
              setDatasets={setDatasets}
              onLog={(msg) => {
                onLog(msg);
                void loadDatasets();
              }}
            />
          </div>
        </Tab>

        <Tab eventKey="report" title="评测报告">
          <div className="pt-3 d-flex flex-column gap-3 rag-console-tab-pane">
            <RagBatchEvalPanel projectId={projectId} onLog={onLog} datasets={datasets} />
          </div>
        </Tab>
      </Tabs>
    </div>
  );
}
