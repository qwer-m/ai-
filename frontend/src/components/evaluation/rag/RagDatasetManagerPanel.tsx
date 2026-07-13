import { useRef, useState } from 'react';
import { Alert, Button, Form, Table } from 'react-bootstrap';
import {
  createRagDataset,
  deleteRagDataset,
  exportRagDataset,
  importRagDataset,
  listRagDatasets,
  translateError,
} from '../state/evaluationService';
import type { RagDatasetRow, RagDatasetType } from './shared/types';

type Props = {
  datasets: RagDatasetRow[];
  setDatasets: (rows: RagDatasetRow[]) => void;
  onLog: (msg: string) => void;
};

const DATASET_TYPES: RagDatasetType[] = ['validation', 'test', 'challenge', 'regression'];

export function RagDatasetManagerPanel({ datasets, setDatasets, onLog }: Props) {
  const [name, setName] = useState('');
  const [type, setType] = useState<RagDatasetType>('validation');
  const [description, setDescription] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [importDatasetId, setImportDatasetId] = useState<number | ''>('');
  const importRef = useRef<HTMLInputElement | null>(null);

  const refresh = async () => {
    const rows = await listRagDatasets();
    setDatasets((rows || []) as RagDatasetRow[]);
  };

  const createDataset = async () => {
    if (!name.trim()) {
      return setError('数据集名称不能为空');
    }
    setBusy(true);
    setError(null);
    try {
      await createRagDataset({ name: name.trim(), type, description: description.trim() || undefined });
      await refresh();
      onLog(`RAG 数据集已创建: ${name.trim()}`);
      setName('');
      setDescription('');
    } catch (e) {
      setError(await translateError(e));
    } finally {
      setBusy(false);
    }
  };

  const removeDataset = async (datasetId: number) => {
    setBusy(true);
    setError(null);
    try {
      await deleteRagDataset(datasetId);
      await refresh();
      onLog(`RAG 数据集已删除: #${datasetId}`);
    } catch (e) {
      setError(await translateError(e));
    } finally {
      setBusy(false);
    }
  };

  const doImport = async () => {
    const file = importRef.current?.files?.[0];
    if (!file) {
      return setError('请选择 JSONL 文件');
    }

    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append('file', file);
      if (importDatasetId) {
        form.append('dataset_id', String(importDatasetId));
      } else {
        form.append('name', `import-${Date.now()}`);
        form.append('type', type);
      }
      const resp = await importRagDataset(form);
      await refresh();
      onLog(`RAG 数据集导入完成: imported=${resp?.imported_count ?? 0}, skipped=${resp?.skipped_count ?? 0}`);
      if (importRef.current) {
        importRef.current.value = '';
      }
    } catch (e) {
      setError(await translateError(e));
    } finally {
      setBusy(false);
    }
  };

  const doExport = async (datasetId: number) => {
    setBusy(true);
    setError(null);
    try {
      const blob = await exportRagDataset(datasetId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `rag_dataset_${datasetId}.jsonl`;
      a.click();
      URL.revokeObjectURL(url);
      onLog(`RAG 数据集导出完成: #${datasetId}`);
    } catch (e) {
      setError(await translateError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="d-flex flex-column gap-3 rag-report-subpanel rag-dataset-manager">
      {error ? <Alert variant="danger" className="mb-0">{error}</Alert> : null}

      <div className="ui-section-card rag-dataset-block rag-dataset-block-create">
        <div className="ui-section-title">创建数据集</div>
        <div className="row g-3 align-items-end rag-dataset-row rag-dataset-row-create control-grid-lr">
          <Form.Group className="col-12 col-lg-6 control-field">
            <Form.Label className="small text-muted">数据集名称</Form.Label>
            <Form.Control value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：验证集-基础" />
          </Form.Group>
          <Form.Group className="col-12 col-lg-6 control-field">
            <Form.Label className="small text-muted">类型</Form.Label>
            <Form.Select value={type} onChange={(e) => setType(e.target.value as RagDatasetType)}>
              {DATASET_TYPES.map((item) => <option key={item} value={item}>{item}</option>)}
            </Form.Select>
          </Form.Group>
          <Form.Group className="col-12 col-lg-8 control-field">
            <Form.Label className="small text-muted">描述</Form.Label>
            <Form.Control value={description} onChange={(e) => setDescription(e.target.value)} placeholder="可选" />
          </Form.Group>
          <div className="col-12 col-lg-4 d-flex align-items-end">
            <Button variant="primary" className="w-100" disabled={busy} onClick={createDataset}>创建数据集</Button>
          </div>
        </div>
      </div>

      <div className="ui-section-card rag-dataset-block rag-dataset-block-import">
        <div className="ui-section-title">导入数据集</div>
        <div className="row g-3 align-items-end rag-dataset-row rag-dataset-row-import control-grid-lr">
          <Form.Group className="col-12 col-lg-6 control-field">
            <Form.Label className="small text-muted">导入目标数据集（可选）</Form.Label>
            <Form.Select value={importDatasetId} onChange={(e) => setImportDatasetId(e.target.value ? Number(e.target.value) : '')}>
              <option value="">新建并导入</option>
              {datasets.map((d) => <option key={d.id} value={d.id}>#{d.id} {d.name}</option>)}
            </Form.Select>
          </Form.Group>
          <Form.Group className="col-12 col-lg-6 control-field">
            <Form.Label className="small text-muted">JSONL 文件</Form.Label>
            <Form.Control type="file" ref={importRef} accept=".jsonl,.json" />
          </Form.Group>
          <div className="col-12 d-flex align-items-end rag-dataset-actions">
            <Button variant="outline-primary" disabled={busy} onClick={doImport}>导入数据集</Button>
          </div>
        </div>
      </div>

      <div className="ui-section-card rag-dataset-block rag-dataset-block-table">
        <div className="ui-section-title">数据集列表</div>
        <div className="table-responsive rag-report-table scroll-table-md">
          <Table striped bordered hover size="sm" className="mb-0">
            <thead>
              <tr>
                <th>ID</th>
                <th>名称</th>
                <th>类型</th>
                <th>样本数</th>
                <th>描述</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {datasets.length === 0 ? (
                <tr><td colSpan={6} className="text-center text-muted">暂无数据集</td></tr>
              ) : (
                datasets.map((d) => (
                  <tr key={d.id}>
                    <td>#{d.id}</td>
                    <td>{d.name}</td>
                    <td>{d.type}</td>
                    <td>{d.sample_count ?? 0}</td>
                    <td>{d.description || '-'}</td>
                    <td className="d-flex gap-2 flex-wrap rag-report-action-cell">
                      <Button size="sm" variant="outline-secondary" disabled={busy} onClick={() => void doExport(d.id)}>导出</Button>
                      <Button size="sm" variant="outline-danger" disabled={busy} onClick={() => void removeDataset(d.id)}>删除</Button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </Table>
        </div>
      </div>
    </div>
  );
}
