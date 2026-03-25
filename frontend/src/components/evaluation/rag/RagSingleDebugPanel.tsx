import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Form, Spinner } from 'react-bootstrap';
import { listRagDatasetSamples, ragSingleDebugRequest, translateError } from '../state/evaluationService';
import { ChunkTable, DocHitStatsTable, type RagChunkRow, type RagDocHitRow } from './shared/RagSingleDebugTables';
import type { RagDatasetRow } from './shared/types';

type Props = {
  projectId: number | null;
  onLog: (msg: string) => void;
  datasets: RagDatasetRow[];
};

export function RagSingleDebugPanel({ projectId, onLog, datasets }: Props) {
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(5);
  const [maxTokens, setMaxTokens] = useState(1800);
  const [llmModel, setLlmModel] = useState('');

  const [retrievalMode, setRetrievalMode] = useState<'vector' | 'keyword' | 'hybrid' | 'bm25'>('hybrid');
  const [recallTopK, setRecallTopK] = useState(25);
  const [rerankTopN, setRerankTopN] = useState(12);
  const [maxChunksPerDoc, setMaxChunksPerDoc] = useState(2);
  const [minDocs, setMinDocs] = useState(2);
  const [enableRewrite, setEnableRewrite] = useState(true);
  const [enableRerank, setEnableRerank] = useState(true);
  const [vectorWeight, setVectorWeight] = useState(0.6);
  const [keywordWeight, setKeywordWeight] = useState(0.25);
  const [titleWeight, setTitleWeight] = useState(0.15);
  const [redundancyThreshold, setRedundancyThreshold] = useState(0.88);

  const [datasetId, setDatasetId] = useState<number | null>(null);
  const [sampleId, setSampleId] = useState<number | null>(null);
  const [samples, setSamples] = useState<any[]>([]);
  const [loadingSamples, setLoadingSamples] = useState(false);
  const [goldChunksText, setGoldChunksText] = useState('');

  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);

  useEffect(() => {
    if (!datasetId) {
      setSamples([]);
      setSampleId(null);
      setGoldChunksText('');
      return;
    }
    setLoadingSamples(true);
    void listRagDatasetSamples(datasetId, { page_size: 200, enabled_only: true })
      .then((rows) => setSamples(Array.isArray(rows) ? rows : []))
      .catch(() => setSamples([]))
      .finally(() => setLoadingSamples(false));
  }, [datasetId]);

  useEffect(() => {
    if (!sampleId) return;
    const sample = samples.find((x) => Number(x.id) === Number(sampleId));
    const gold = Array.isArray(sample?.gold_chunks) ? sample.gold_chunks : [];
    setGoldChunksText(gold.map((x: unknown) => String(x)).join('\n'));
  }, [sampleId, samples]);

  const goldSet = useMemo(() => {
    const items = String(goldChunksText || '')
      .split(/[\n,，;；\s]+/g)
      .map((x) => x.trim())
      .filter(Boolean);
    return new Set<string>(items);
  }, [goldChunksText]);

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
        limit: Math.max(1, Math.min(20, Number(limit) || 5)),
        max_tokens: Math.max(128, Math.min(8000, Number(maxTokens) || 1800)),
        llm_model: llmModel.trim() || undefined,
        retrieval_mode: retrievalMode,
        recall_top_k: Math.max(6, Math.min(80, Number(recallTopK) || 25)),
        rerank_top_n: Math.max(4, Math.min(80, Number(rerankTopN) || 12)),
        max_chunks_per_doc: Math.max(1, Math.min(6, Number(maxChunksPerDoc) || 2)),
        min_docs: Math.max(1, Math.min(12, Number(minDocs) || 2)),
        enable_query_rewrite: enableRewrite,
        enable_rerank: enableRerank,
        vector_weight: Math.max(0, Math.min(3, Number(vectorWeight) || 0.6)),
        keyword_weight: Math.max(0, Math.min(3, Number(keywordWeight) || 0.25)),
        title_weight: Math.max(0, Math.min(3, Number(titleWeight) || 0.15)),
        redundancy_threshold: Math.max(0.5, Math.min(0.99, Number(redundancyThreshold) || 0.88)),
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

  const docStats: RagDocHitRow[] = (result?.doc_hit_stats || result?.debug?.doc_hit_stats || []) as RagDocHitRow[];
  const dominance = result?.dominance_warning || result?.debug?.dominance_warning;
  const multiDocHint = result?.multi_doc_hint || result?.debug?.multi_doc_hint;
  const displayFinalContext = useMemo(() => {
    const direct = String(result?.final_context || '').trim();
    if (direct) return direct;

    const debug = result?.debug || {};
    const debugContext = String(debug?.context || '').trim();
    if (debugContext) return debugContext;

    const fallbackChunks = (Array.isArray(debug?.final_chunks) && debug.final_chunks.length > 0)
      ? debug.final_chunks
      : (Array.isArray(debug?.diverse_chunks) && debug.diverse_chunks.length > 0)
        ? debug.diverse_chunks
        : Array.isArray(result?.reranked_chunks)
          ? result.reranked_chunks
          : [];

    if (fallbackChunks.length > 0) {
      return fallbackChunks
        .map((chunk: any) => {
          const text = String(chunk?.chunk_text || '').trim();
          if (!text) return '';
          const filename = String(chunk?.filename || 'Unknown');
          const docType = String(chunk?.doc_type || 'Unknown');
          return `--- Relevant Knowledge: ${filename} (${docType}) ---\n${text}`;
        })
        .filter(Boolean)
        .join('\n\n');
    }

    const reason = String(result?.context_blocked_reason || debug?.final_failure_reason || '').trim();
    return reason ? `[上下文为空] ${reason}` : '';
  }, [result]);

  const displayLlmOutput = useMemo(() => {
    const direct = String(result?.llm_output || '').trim();
    if (direct) return direct;

    const reason = String(result?.context_blocked_reason || result?.debug?.final_failure_reason || '').trim();
    return reason ? `[未调用 LLM] ${reason}` : '';
  }, [result]);

  return (
    <div className="d-flex flex-column gap-3">
      <div className="grid grid-cols-2 gap-3">
        <Form.Group>
          <Form.Label className="small text-muted">Query</Form.Label>
          <Form.Control as="textarea" rows={3} value={query} onChange={(e) => setQuery(e.target.value)} />
        </Form.Group>

        <div className="grid grid-cols-2 gap-3">
          <Form.Group><Form.Label className="small text-muted">top_k</Form.Label><Form.Control type="number" value={limit} onChange={(e) => setLimit(Number(e.target.value))} /></Form.Group>
          <Form.Group><Form.Label className="small text-muted">max_tokens</Form.Label><Form.Control type="number" value={maxTokens} onChange={(e) => setMaxTokens(Number(e.target.value))} /></Form.Group>
          <Form.Group><Form.Label className="small text-muted">llm_model（可选）</Form.Label><Form.Control value={llmModel} onChange={(e) => setLlmModel(e.target.value)} placeholder="例如 glm-4.7" /></Form.Group>
          <Form.Group><Form.Label className="small text-muted">retrieval_mode</Form.Label><Form.Select value={retrievalMode} onChange={(e) => setRetrievalMode(e.target.value as any)}><option value="hybrid">hybrid</option><option value="vector">vector</option><option value="keyword">keyword</option><option value="bm25">bm25</option></Form.Select></Form.Group>

          <Form.Group><Form.Label className="small text-muted">recall_top_k</Form.Label><Form.Control type="number" value={recallTopK} onChange={(e) => setRecallTopK(Number(e.target.value))} /></Form.Group>
          <Form.Group><Form.Label className="small text-muted">rerank_top_n</Form.Label><Form.Control type="number" value={rerankTopN} onChange={(e) => setRerankTopN(Number(e.target.value))} /></Form.Group>
          <Form.Group><Form.Label className="small text-muted">max_chunks_per_doc</Form.Label><Form.Control type="number" value={maxChunksPerDoc} onChange={(e) => setMaxChunksPerDoc(Number(e.target.value))} /></Form.Group>
          <Form.Group><Form.Label className="small text-muted">min_docs</Form.Label><Form.Control type="number" value={minDocs} onChange={(e) => setMinDocs(Number(e.target.value))} /></Form.Group>

          <Form.Group><Form.Label className="small text-muted">vector_weight</Form.Label><Form.Control type="number" step="0.05" value={vectorWeight} onChange={(e) => setVectorWeight(Number(e.target.value))} /></Form.Group>
          <Form.Group><Form.Label className="small text-muted">keyword_weight</Form.Label><Form.Control type="number" step="0.05" value={keywordWeight} onChange={(e) => setKeywordWeight(Number(e.target.value))} /></Form.Group>
          <Form.Group><Form.Label className="small text-muted">title_weight</Form.Label><Form.Control type="number" step="0.05" value={titleWeight} onChange={(e) => setTitleWeight(Number(e.target.value))} /></Form.Group>
          <Form.Group><Form.Label className="small text-muted">redundancy_threshold</Form.Label><Form.Control type="number" step="0.01" value={redundancyThreshold} onChange={(e) => setRedundancyThreshold(Number(e.target.value))} /></Form.Group>

          <Form.Group><Form.Check label="enable_query_rewrite" checked={enableRewrite} onChange={(e) => setEnableRewrite(e.target.checked)} /></Form.Group>
          <Form.Group><Form.Check label="enable_rerank" checked={enableRerank} onChange={(e) => setEnableRerank(e.target.checked)} /></Form.Group>

          <Form.Group className="col-span-2">
            <Form.Label className="small text-muted">Gold 对照样本（可选）</Form.Label>
            <div className="d-flex gap-2">
              <Form.Select value={datasetId ?? ''} onChange={(e) => setDatasetId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">数据集</option>
                {datasets.map((d) => <option key={d.id} value={d.id}>#{d.id} {d.name}</option>)}
              </Form.Select>
              <Form.Select value={sampleId ?? ''} onChange={(e) => setSampleId(e.target.value ? Number(e.target.value) : null)} disabled={!datasetId || loadingSamples}>
                <option value="">{loadingSamples ? '加载中...' : '样本'}</option>
                {samples.map((s) => <option key={s.id} value={s.id}>#{s.id} {String(s.query || '').slice(0, 24)}</option>)}
              </Form.Select>
            </div>
          </Form.Group>

          <Form.Group className="col-span-2">
            <Form.Label className="small text-muted">Gold chunk_ids（可编辑）</Form.Label>
            <Form.Control as="textarea" rows={3} value={goldChunksText} onChange={(e) => setGoldChunksText(e.target.value)} placeholder="支持换行/逗号分隔" />
          </Form.Group>
        </div>
      </div>

      <div><Button variant="primary" disabled={running} onClick={runDebug}>{running ? <><Spinner animation="border" size="sm" className="me-2" />调试中...</> : '执行单条调试'}</Button></div>
      {error ? <Alert variant="danger" className="mb-0">{error}</Alert> : null}

      {result ? (
        <div className="d-flex flex-column gap-3">
          {dominance ? <Alert variant="warning" className="mb-0">{String(dominance.message || '检测到文档霸榜')}</Alert> : null}
          {multiDocHint ? <Alert variant="info" className="mb-0">{String(multiDocHint.message || '')}</Alert> : null}

          <div className="grid grid-cols-4 gap-3 small">
            <div className="p-2 bg-light rounded"><span className="text-muted d-block">retrieval(ms)</span>{Number(result?.timing_ms?.retrieval || 0).toFixed(1)}</div>
            <div className="p-2 bg-light rounded"><span className="text-muted d-block">generation(ms)</span>{Number(result?.timing_ms?.generation || 0).toFixed(1)}</div>
            <div className="p-2 bg-light rounded"><span className="text-muted d-block">total(ms)</span>{Number(result?.timing_ms?.total || 0).toFixed(1)}</div>
            <div className="p-2 bg-light rounded"><span className="text-muted d-block">final_status</span>{String(result?.debug?.final_status ?? '-')}</div>
            <div className="p-2 bg-light rounded"><span className="text-muted d-block">token(total)</span>{String(result?.token_usage?.total_tokens ?? '-')}</div>
          </div>

          <Form.Group>
            <Form.Label className="small text-muted">检索调参快照</Form.Label>
            <Form.Control as="textarea" rows={4} readOnly value={JSON.stringify(result?.retrieval_options || result?.debug?.retrieval_tuning || {}, null, 2)} />
          </Form.Group>

          <Form.Group>
            <Form.Label className="small text-muted">query rewrite 结果</Form.Label>
            <Form.Control as="textarea" rows={3} readOnly value={JSON.stringify(result?.rewritten_queries || [], null, 2)} />
          </Form.Group>

          <div className="fw-bold">文档级命中统计</div>
          <DocHitStatsTable rows={docStats} />

          <ChunkTable title="原始召回 chunks" rows={(result?.raw_retrieved_chunks || []) as RagChunkRow[]} goldSet={goldSet} hasGoldReference={goldSet.size > 0} />
          <ChunkTable title="Rerank 后 chunks" rows={(result?.reranked_chunks || []) as RagChunkRow[]} goldSet={goldSet} hasGoldReference={goldSet.size > 0} />
          <ChunkTable title="最终上下文候选（多文档覆盖后）" rows={(result?.debug?.diverse_chunks || []) as RagChunkRow[]} goldSet={goldSet} hasGoldReference={goldSet.size > 0} />

          <Form.Group>
            <Form.Label className="small text-muted">最终送入 LLM 的上下文</Form.Label>
            <Form.Control as="textarea" rows={10} readOnly value={displayFinalContext} />
          </Form.Group>

          <Form.Group>
            <Form.Label className="small text-muted">LLM 最终输出</Form.Label>
            <Form.Control as="textarea" rows={8} readOnly value={displayLlmOutput} />
          </Form.Group>
        </div>
      ) : null}
    </div>
  );
}
