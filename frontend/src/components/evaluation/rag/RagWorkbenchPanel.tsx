import { useEffect, useState } from 'react';
import { Alert, Card, Form, Tab, Tabs } from 'react-bootstrap';
import { listRagDatasets, translateError } from '../evaluationService';
import { RagBatchEvalPanel } from './RagBatchEvalPanel';
import { RagDatasetManagerPanel } from './RagDatasetManagerPanel';
import { RagSingleDebugPanel } from './RagSingleDebugPanel';
import type { RagDatasetRow } from './types';

type Props = {
  projectId: number | null;
  onLog: (msg: string) => void;
};

export function RagWorkbenchPanel({ projectId, onLog }: Props) {
  const [mode, setMode] = useState<'single' | 'batch'>('single');
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
    <div className="bento-card col-span-12 p-4 d-flex flex-column gap-3 hover-lift">
      <div className="d-flex align-items-center justify-content-between">
        <h5 className="mb-0">RAG 评测工作台</h5>
        <span className="small text-muted">支持单条调试 + 批量评测 + 数据集管理</span>
      </div>

      {error ? <Alert variant="danger" className="mb-0">{error}</Alert> : null}

      <Tabs activeKey={mode} onSelect={(k) => setMode((k as 'single' | 'batch') || 'single')}>
        <Tab eventKey="single" title="单条调试">
          <div className="pt-3">
            <RagSingleDebugPanel projectId={projectId} onLog={onLog} datasets={datasets} />
          </div>
        </Tab>
        <Tab eventKey="batch" title="批量评测">
          <div className="pt-3 d-flex flex-column gap-3">
            <RagBatchEvalPanel projectId={projectId} onLog={onLog} datasets={datasets} />
          </div>
        </Tab>
      </Tabs>

      <Card>
        <Card.Header className="py-2 d-flex align-items-center justify-content-between">
          <span className="fw-bold">数据集管理</span>
          <Form.Text className="text-muted">支持 validation / test / challenge / regression</Form.Text>
        </Card.Header>
        <Card.Body>
          <RagDatasetManagerPanel
            datasets={datasets}
            setDatasets={setDatasets}
            onLog={(msg) => {
              onLog(msg);
              void loadDatasets();
            }}
          />
        </Card.Body>
      </Card>
    </div>
  );
}

