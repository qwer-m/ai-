import { useEffect, useMemo, useState } from 'react';
import { Alert } from 'react-bootstrap';
import { listRagDatasetSamples, ragSingleDebugRequest, translateError } from '../state/evaluationService';
import { RagDebugControlsCard } from './console/RagDebugControlsCard';
import { RagDebugResultBoard } from './console/RagDebugResultBoard';
import type { RagDatasetRow } from './shared/types';

type Props = {
  projectId: number | null;
  onLog: (msg: string) => void;
  datasets: RagDatasetRow[];
};

const PRESET_KEY = 'rag_debug_console_preset_v1';

const DEFAULTS = {
  limit: 5,
  maxTokens: 1800,
  llmModel: '',
  retrievalMode: 'hybrid' as 'vector' | 'keyword' | 'hybrid' | 'bm25',
  recallTopK: 25,
  rerankTopN: 12,
  maxChunksPerDoc: 2,
  minDocs: 2,
  enableRewrite: true,
  enableRerank: true,
  vectorWeight: 0.6,
  keywordWeight: 0.25,
  titleWeight: 0.15,
  redundancyThreshold: 0.88,
};

export function RagSingleDebugPanel({ projectId, onLog, datasets }: Props) {
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(DEFAULTS.limit);
  const [maxTokens, setMaxTokens] = useState(DEFAULTS.maxTokens);
  const [llmModel, setLlmModel] = useState(DEFAULTS.llmModel);
  const [retrievalMode, setRetrievalMode] = useState(DEFAULTS.retrievalMode);
  const [recallTopK, setRecallTopK] = useState(DEFAULTS.recallTopK);
  const [rerankTopN, setRerankTopN] = useState(DEFAULTS.rerankTopN);
  const [maxChunksPerDoc, setMaxChunksPerDoc] = useState(DEFAULTS.maxChunksPerDoc);
  const [minDocs, setMinDocs] = useState(DEFAULTS.minDocs);
  const [enableRewrite, setEnableRewrite] = useState(DEFAULTS.enableRewrite);
  const [enableRerank, setEnableRerank] = useState(DEFAULTS.enableRerank);
  const [vectorWeight, setVectorWeight] = useState(DEFAULTS.vectorWeight);
  const [keywordWeight, setKeywordWeight] = useState(DEFAULTS.keywordWeight);
  const [titleWeight, setTitleWeight] = useState(DEFAULTS.titleWeight);
  const [redundancyThreshold, setRedundancyThreshold] = useState(DEFAULTS.redundancyThreshold);

  const [datasetId, setDatasetId] = useState<number | null>(null);
  const [sampleId, setSampleId] = useState<number | null>(null);
  const [samples, setSamples] = useState<any[]>([]);
  const [loadingSamples, setLoadingSamples] = useState(false);
  const [goldChunksText, setGoldChunksText] = useState('');

  const [running, setRunning] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(PRESET_KEY);
      if (!raw) return;
      const preset = JSON.parse(raw);
      if (!preset || typeof preset !== 'object') return;
      if (typeof preset.limit === 'number') setLimit(preset.limit);
      if (typeof preset.maxTokens === 'number') setMaxTokens(preset.maxTokens);
      if (typeof preset.llmModel === 'string') setLlmModel(preset.llmModel);
      if (['vector', 'keyword', 'hybrid', 'bm25'].includes(String(preset.retrievalMode))) {
        setRetrievalMode(preset.retrievalMode as any);
      }
      if (typeof preset.recallTopK === 'number') setRecallTopK(preset.recallTopK);
      if (typeof preset.rerankTopN === 'number') setRerankTopN(preset.rerankTopN);
      if (typeof preset.maxChunksPerDoc === 'number') setMaxChunksPerDoc(preset.maxChunksPerDoc);
      if (typeof preset.minDocs === 'number') setMinDocs(preset.minDocs);
      if (typeof preset.enableRewrite === 'boolean') setEnableRewrite(preset.enableRewrite);
      if (typeof preset.enableRerank === 'boolean') setEnableRerank(preset.enableRerank);
      if (typeof preset.vectorWeight === 'number') setVectorWeight(preset.vectorWeight);
      if (typeof preset.keywordWeight === 'number') setKeywordWeight(preset.keywordWeight);
      if (typeof preset.titleWeight === 'number') setTitleWeight(preset.titleWeight);
      if (typeof preset.redundancyThreshold === 'number') setRedundancyThreshold(preset.redundancyThreshold);
    } catch {
      // ignore broken local preset
    }
  }, []);

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
    if (typeof sample?.query === 'string' && sample.query.trim()) {
      setQuery(sample.query.trim());
    }
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

  const onSavePreset = () => {
    const preset = {
      limit,
      maxTokens,
      llmModel,
      retrievalMode,
      recallTopK,
      rerankTopN,
      maxChunksPerDoc,
      minDocs,
      enableRewrite,
      enableRerank,
      vectorWeight,
      keywordWeight,
      titleWeight,
      redundancyThreshold,
    };
    localStorage.setItem(PRESET_KEY, JSON.stringify(preset));
    onLog('RAG 调试参数已保存到本地预设');
  };

  const onResetPreset = () => {
    setLimit(DEFAULTS.limit);
    setMaxTokens(DEFAULTS.maxTokens);
    setLlmModel(DEFAULTS.llmModel);
    setRetrievalMode(DEFAULTS.retrievalMode);
    setRecallTopK(DEFAULTS.recallTopK);
    setRerankTopN(DEFAULTS.rerankTopN);
    setMaxChunksPerDoc(DEFAULTS.maxChunksPerDoc);
    setMinDocs(DEFAULTS.minDocs);
    setEnableRewrite(DEFAULTS.enableRewrite);
    setEnableRerank(DEFAULTS.enableRerank);
    setVectorWeight(DEFAULTS.vectorWeight);
    setKeywordWeight(DEFAULTS.keywordWeight);
    setTitleWeight(DEFAULTS.titleWeight);
    setRedundancyThreshold(DEFAULTS.redundancyThreshold);
    setQuery('');
    setGoldChunksText('');
    setSampleId(null);
    setAdvancedOpen(false);
    localStorage.removeItem(PRESET_KEY);
    onLog('RAG 调试参数已重置');
  };

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

  const rewrittenQueries = useMemo(() => {
    const values = Array.isArray(result?.rewritten_queries) ? result.rewritten_queries : [];
    return values.map((x: unknown) => String(x || '').trim()).filter(Boolean);
  }, [result]);

  return (
    <div className="d-flex flex-column gap-3">
      <RagDebugControlsCard
        projectId={projectId}
        datasets={datasets}
        samples={samples}
        loadingSamples={loadingSamples}
        query={query}
        setQuery={setQuery}
        rewrittenQueries={rewrittenQueries}
        datasetId={datasetId}
        setDatasetId={setDatasetId}
        sampleId={sampleId}
        setSampleId={setSampleId}
        limit={limit}
        setLimit={setLimit}
        maxTokens={maxTokens}
        setMaxTokens={setMaxTokens}
        retrievalMode={retrievalMode}
        setRetrievalMode={setRetrievalMode}
        recallTopK={recallTopK}
        setRecallTopK={setRecallTopK}
        rerankTopN={rerankTopN}
        setRerankTopN={setRerankTopN}
        enableRewrite={enableRewrite}
        setEnableRewrite={setEnableRewrite}
        enableRerank={enableRerank}
        setEnableRerank={setEnableRerank}
        llmModel={llmModel}
        setLlmModel={setLlmModel}
        maxChunksPerDoc={maxChunksPerDoc}
        setMaxChunksPerDoc={setMaxChunksPerDoc}
        minDocs={minDocs}
        setMinDocs={setMinDocs}
        vectorWeight={vectorWeight}
        setVectorWeight={setVectorWeight}
        keywordWeight={keywordWeight}
        setKeywordWeight={setKeywordWeight}
        titleWeight={titleWeight}
        setTitleWeight={setTitleWeight}
        redundancyThreshold={redundancyThreshold}
        setRedundancyThreshold={setRedundancyThreshold}
        goldChunksText={goldChunksText}
        setGoldChunksText={setGoldChunksText}
        running={running}
        advancedOpen={advancedOpen}
        setAdvancedOpen={setAdvancedOpen}
        onRun={runDebug}
        onSavePreset={onSavePreset}
        onResetPreset={onResetPreset}
      />

      {error ? <Alert variant="danger" className="mb-0">{error}</Alert> : null}

      {result ? (
        <RagDebugResultBoard
          result={result}
          query={query}
          goldSet={goldSet}
          displayFinalContext={displayFinalContext}
          displayLlmOutput={displayLlmOutput}
          dominance={result?.dominance_warning || result?.debug?.dominance_warning}
          multiDocHint={result?.multi_doc_hint || result?.debug?.multi_doc_hint}
        />
      ) : null}
    </div>
  );
}
