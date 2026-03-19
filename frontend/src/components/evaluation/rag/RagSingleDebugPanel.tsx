import { useEffect, useMemo, useState } from 'react';
import { Alert, Badge, Button, Form, Spinner, Table } from 'react-bootstrap';
import { listRagDatasetSamples, ragSingleDebugRequest, translateError } from '../evaluationService';
import type { RagDatasetRow } from './types';

type Props = {
  projectId: number | null;
  onLog: (msg: string) => void;
  datasets: RagDatasetRow[];
};

type ChunkRow = {
  chunk_id?: string;
  doc_id?: string | number;
  filename?: string;
  score?: number;
  final_score?: number;
  chunk_text?: string;
};

function ChunkTable({
  title,
  rows,
  goldSet,
}: {
  title: string;
  rows: ChunkRow[];
  goldSet: Set<string>;
}) {
  return (
    <div className="mt-3">
      <div className="fw-bold mb-2">{title}</div>
      <div className="table-responsive">
        <Table striped bordered hover size="sm" className="mb-0">
          <thead>
            <tr>
              <th>chunk_id</th>
              <th>doc_id/文档名</th>
              <th>score</th>
              <th>gold命中</th>
              <th>文本预览</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={5} className="text-center text-muted">
                  暂无数据
                </td>
              </tr>
            ) : (
              rows.map((row, idx) => {
                const cid = String(row.chunk_id || '');
                const hit = cid && goldSet.has(cid);
                const score = Number(row.final_score ?? row.score ?? 0);
                return (
                  <tr key={`${cid}-${idx}`}>
                    <td style={{ minWidth: 120 }}>{cid || '-'}</td>
                    <td>{String(row.doc_id || '-')}/{String(row.filename || '-')}</td>
                    <td>{score.toFixed(4)}</td>
                    <td>{hit ? <Badge bg="success">命中</Badge> : <Badge bg="secondary">未命中</Badge>}</td>
                    <td style={{ maxWidth: 480 }}>{String(row.chunk_text || '').slice(0, 260)}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </Table>
      </div>
    </div>
  );
}

export function RagSingleDebugPanel({ projectId, onLog, datasets }: Props) {
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(5);
  const [maxTokens, setMaxTokens] = useState(1800);
  const [llmModel, setLlmModel] = useState('');
  const [datasetId, setDatasetId] = useState<number | null>(null);
  const [sampleId, setSampleId] = useState<number | null>(null);
  const [samples, setSamples] = useState<any[]>([]);
  const [loadingSamples, setLoadingSamples] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);

  useEffect(() => {
    if (!datasetId) {
      setSamples([]);
      setSampleId(null);
      return;
    }
    setLoadingSamples(true);
    void listRagDatasetSamples(datasetId, { page_size: 200, enabled_only: true })
      .then((rows) => setSamples(Array.isArray(rows) ? rows : []))
      .catch(() => setSamples([]))
      .finally(() => setLoadingSamples(false));
  }, [datasetId]);

  const goldSet = useMemo(() => {
    const sample = samples.find((x) => Number(x.id) === Number(sampleId));
    return new Set<string>((sample?.gold_chunks || []).map((x: unknown) => String(x)));
  }, [samples, sampleId]);

  const runDebug = async () => {
    if (!projectId) return setError('请先选择项目');
    if (!query.trim()) return setError('请输入 query');
    setRunning(true);
    setError(null);
    setResult(null);
    onLog(`RAG 单条调试开始: ${query.trim()}`);
    try {
      const data = await ragSingleDebugRequest({
        project_id: projectId,
        query: query.trim(),
        limit: Math.max(1, Math.min(20, limit)),
        max_tokens: Math.max(128, Math.min(8000, maxTokens)),
        llm_model: llmModel.trim() || undefined,
      });
      setResult(data);
      onLog(`RAG 单条调试完成: total=${Number(data?.timing_ms?.total || 0).toFixed(0)}ms`);
    } catch (e) {
      const msg = await translateError(e);
      setError(msg);
      onLog(`RAG 单条调试失败: ${msg}`);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="d-flex flex-column gap-3">
      <div className="grid grid-cols-2 gap-3">
        <Form.Group>
          <Form.Label className="small text-muted">Query</Form.Label>
          <Form.Control as="textarea" rows={3} value={query} onChange={(e) => setQuery(e.target.value)} />
        </Form.Group>
        <div className="grid grid-cols-2 gap-3">
          <Form.Group>
            <Form.Label className="small text-muted">top_k</Form.Label>
            <Form.Control type="number" min={1} max={20} value={limit} onChange={(e) => setLimit(Number(e.target.value))} />
          </Form.Group>
          <Form.Group>
            <Form.Label className="small text-muted">max_tokens</Form.Label>
            <Form.Control type="number" min={128} max={8000} value={maxTokens} onChange={(e) => setMaxTokens(Number(e.target.value))} />
          </Form.Group>
          <Form.Group>
            <Form.Label className="small text-muted">llm_model（可选）</Form.Label>
            <Form.Control value={llmModel} onChange={(e) => setLlmModel(e.target.value)} placeholder="例如 glm-4.7" />
          </Form.Group>
          <Form.Group>
            <Form.Label className="small text-muted">Gold 对照样本（可选）</Form.Label>
            <div className="d-flex gap-2">
              <Form.Select value={datasetId ?? ''} onChange={(e) => setDatasetId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">数据集</option>
                {datasets.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </Form.Select>
              <Form.Select
                value={sampleId ?? ''}
                onChange={(e) => setSampleId(e.target.value ? Number(e.target.value) : null)}
                disabled={!datasetId || loadingSamples}
              >
                <option value="">{loadingSamples ? '加载中...' : '样本'}</option>
                {samples.map((s) => (
                  <option key={s.id} value={s.id}>
                    #{s.id}
                  </option>
                ))}
              </Form.Select>
            </div>
          </Form.Group>
        </div>
      </div>

      <div>
        <Button variant="primary" disabled={running} onClick={runDebug}>
          {running ? (
            <>
              <Spinner animation="border" size="sm" className="me-2" />
              调试中...
            </>
          ) : (
            '执行单条调试'
          )}
        </Button>
      </div>

      {error ? <Alert variant="danger" className="mb-0">{error}</Alert> : null}

      {result ? (
        <div className="d-flex flex-column gap-3">
          <div className="grid grid-cols-3 gap-3 small">
            <div className="p-2 bg-light rounded"><span className="text-muted d-block">retrieval(ms)</span>{Number(result?.timing_ms?.retrieval || 0).toFixed(1)}</div>
            <div className="p-2 bg-light rounded"><span className="text-muted d-block">generation(ms)</span>{Number(result?.timing_ms?.generation || 0).toFixed(1)}</div>
            <div className="p-2 bg-light rounded"><span className="text-muted d-block">total(ms)</span>{Number(result?.timing_ms?.total || 0).toFixed(1)}</div>
            <div className="p-2 bg-light rounded"><span className="text-muted d-block">token(total)</span>{String(result?.token_usage?.total_tokens ?? '-')}</div>
            <div className="p-2 bg-light rounded"><span className="text-muted d-block">final_status</span>{String(result?.debug?.final_status ?? '-')}</div>
          </div>

          <Form.Group>
            <Form.Label className="small text-muted">query rewrite 结果</Form.Label>
            <Form.Control as="textarea" rows={3} readOnly value={JSON.stringify(result?.rewritten_queries || [], null, 2)} />
          </Form.Group>

          <ChunkTable title="原始召回 chunks" rows={(result?.raw_retrieved_chunks || []) as ChunkRow[]} goldSet={goldSet} />
          <ChunkTable title="Rerank 后 chunks" rows={(result?.reranked_chunks || []) as ChunkRow[]} goldSet={goldSet} />

          <Form.Group>
            <Form.Label className="small text-muted">最终送入 LLM 的上下文</Form.Label>
            <Form.Control as="textarea" rows={10} readOnly value={String(result?.final_context || '')} />
          </Form.Group>
          <Form.Group>
            <Form.Label className="small text-muted">LLM 最终输出</Form.Label>
            <Form.Control as="textarea" rows={8} readOnly value={String(result?.llm_output || '')} />
          </Form.Group>
        </div>
      ) : null}
    </div>
  );
}
