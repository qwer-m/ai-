import { Alert, Badge, Form } from 'react-bootstrap';
import { ChunkTable, DocHitStatsTable, type RagChunkRow, type RagDocHitRow } from '../shared/RagSingleDebugTables';

type Props = {
  result: any;
  query: string;
  goldSet: Set<string>;
  displayFinalContext: string;
  displayLlmOutput: string;
  dominance?: { message?: string } | null;
  multiDocHint?: { message?: string } | null;
};

type Cause = {
  key: 'query' | 'rerank' | 'recall' | 'context';
  label: string;
  hit: boolean;
};

const getChunkId = (row: any, fallback: number) => String(row?.chunk_id ?? row?.id ?? row?.chunk_index ?? fallback);

function computeCoverage(result: any, goldSet: Set<string>): number | null {
  const explicit = result?.coverage ?? result?.debug?.coverage;
  if (explicit !== undefined && explicit !== null && !Number.isNaN(Number(explicit))) {
    const normalized = Number(explicit);
    return normalized <= 1 ? normalized * 100 : normalized;
  }
  if (goldSet.size === 0) return null;
  const reranked = Array.isArray(result?.reranked_chunks) ? result.reranked_chunks : [];
  const hit = reranked.filter((row: any, idx: number) => goldSet.has(getChunkId(row, idx + 1))).length;
  return (hit / goldSet.size) * 100;
}

function buildRootCauses(result: any, goldSet: Set<string>, finalContext: string): Cause[] {
  const raw = Array.isArray(result?.raw_retrieved_chunks) ? result.raw_retrieved_chunks : [];
  const rerank = Array.isArray(result?.reranked_chunks) ? result.reranked_chunks : [];
  const rewrites = Array.isArray(result?.rewritten_queries) ? result.rewritten_queries : [];

  const rawIds = new Set(raw.map((x: any, idx: number) => getChunkId(x, idx + 1)));
  const rerankIds = new Set(rerank.map((x: any, idx: number) => getChunkId(x, idx + 1)));
  const hasGoldInRaw = goldSet.size > 0 && Array.from(goldSet).some((id) => rawIds.has(id));
  const hasGoldInRerank = goldSet.size > 0 && Array.from(goldSet).some((id) => rerankIds.has(id));

  const contextEmpty = !String(finalContext || '').trim() || String(result?.context_blocked_reason || '').trim().length > 0;
  const rerankKill = hasGoldInRaw && !hasGoldInRerank && rerank.length > 0;
  const recallPoor = raw.length === 0 || (goldSet.size > 0 && !hasGoldInRaw);
  const queryIssue = rewrites.length === 0 && !recallPoor && !rerankKill && contextEmpty;

  return [
    { key: 'query', label: 'Query问题', hit: queryIssue },
    { key: 'rerank', label: 'Rerank误杀', hit: rerankKill },
    { key: 'recall', label: 'Recall不足', hit: recallPoor },
    { key: 'context', label: 'Context丢失', hit: contextEmpty },
  ];
}

function buildSuggestions(causes: Cause[]): string[] {
  const hit = new Set(causes.filter((c) => c.hit).map((c) => c.key));
  const suggestions: string[] = [];
  if (hit.has('query')) suggestions.push('补充Query约束词，或开启/增强Query Rewrite。');
  if (hit.has('rerank')) suggestions.push('降低rerank收敛强度，提升rerank_top_n并观察误杀样本。');
  if (hit.has('recall')) suggestions.push('提高recall_top_k，增加关键词召回权重与title_weight。');
  if (hit.has('context')) suggestions.push('放宽redundancy_threshold，提升min_docs与max_chunks_per_doc。');
  if (suggestions.length === 0) suggestions.push('当前链路未发现明显瓶颈，可继续做模型侧提示词优化。');
  return suggestions;
}

function buildRerankMovement(result: any): Array<{ id: string; from: number; to: number }> {
  const raw = Array.isArray(result?.raw_retrieved_chunks) ? result.raw_retrieved_chunks : [];
  const rerank = Array.isArray(result?.reranked_chunks) ? result.reranked_chunks : [];
  const rawPos = new Map<string, number>();
  raw.forEach((row: any, idx: number) => rawPos.set(getChunkId(row, idx + 1), idx + 1));
  return rerank.map((row: any, idx: number) => {
    const id = getChunkId(row, idx + 1);
    return { id, from: rawPos.get(id) || 999, to: idx + 1 };
  });
}

export function RagDebugResultBoard({
  result,
  query,
  goldSet,
  displayFinalContext,
  displayLlmOutput,
  dominance,
  multiDocHint,
}: Props) {
  const docStats: RagDocHitRow[] = (result?.doc_hit_stats || result?.debug?.doc_hit_stats || []) as RagDocHitRow[];
  const rawChunks: RagChunkRow[] = (result?.raw_retrieved_chunks || []) as RagChunkRow[];
  const rerankedChunks: RagChunkRow[] = (result?.reranked_chunks || []) as RagChunkRow[];
  const finalChunks: RagChunkRow[] = (result?.debug?.diverse_chunks || []) as RagChunkRow[];

  const rewrittenQueries = Array.isArray(result?.rewritten_queries) ? result.rewritten_queries : [];
  const coverage = computeCoverage(result, goldSet);
  const causes = buildRootCauses(result, goldSet, displayFinalContext);
  const suggestions = buildSuggestions(causes);
  const movements = buildRerankMovement(result);

  const correctness = result?.answer_correct ?? result?.metrics?.answer_correctness;
  const faithfulness = result?.faithfulness ?? result?.metrics?.faithfulness;
  const hallucination = faithfulness !== undefined
    ? Number(faithfulness) < 0.7
    : !!String(result?.context_blocked_reason || '').trim();

  return (
    <div className="d-flex flex-column gap-3 rag-console-results">
      {dominance ? <Alert variant="warning" className="mb-0">{String(dominance.message || '检测到文档霸榜')}</Alert> : null}
      {multiDocHint ? <Alert variant="info" className="mb-0">{String(multiDocHint.message || '')}</Alert> : null}

      <div className="row g-3 rag-console-result-grid">
        <div className="col-lg-3 rag-console-col">
          <div className="h-100 p-3 border rounded bg-white d-flex flex-column gap-2 rag-console-panel">
            <div className="fw-bold rag-console-panel-title">输入理解</div>
            <div>
              <div className="small text-muted">Query</div>
              <div className="small">{query || '-'}</div>
            </div>
            <div>
              <div className="small text-muted">Rewrite</div>
              <div className="small">{rewrittenQueries.length > 0 ? rewrittenQueries.join(' | ') : '未触发改写'}</div>
            </div>
            <div>
              <div className="small text-muted">期望命中</div>
              <div className="small">{goldSet.size > 0 ? `Gold chunk ids: ${Array.from(goldSet).join(', ')}` : '未设置 Gold 参考'}</div>
            </div>
            <div className="d-flex flex-wrap gap-1 mt-1">
              <Badge bg="secondary" className="rag-console-pill">配置类</Badge>
              <Badge bg="info" className="rag-console-pill">流程类</Badge>
              {goldSet.size > 0 ? <Badge bg="success" className="rag-console-pill">有对照</Badge> : <Badge bg="warning" text="dark" className="rag-console-pill">无对照</Badge>}
            </div>
          </div>
        </div>

        <div className="col-lg-5 rag-console-col">
          <div className="h-100 p-3 border rounded bg-white d-flex flex-column gap-3 rag-console-panel">
            <div className="fw-bold rag-console-panel-title">检索链路</div>
            <DocHitStatsTable rows={docStats} />
            <ChunkTable title={`Recall（${rawChunks.length}）`} rows={rawChunks} goldSet={goldSet} hasGoldReference={goldSet.size > 0} />
            <div className="p-2 border rounded bg-light rag-console-movement-card">
              <div className="small fw-bold mb-1">Rerank变化</div>
              <div className="small d-flex flex-wrap gap-2">
                {movements.length === 0 ? '无' : movements.slice(0, 16).map((m) => {
                  const trend = m.to < m.from ? '↑' : m.to > m.from ? '↓' : '→';
                  const trendClass = trend === '↑' ? 'rag-console-up' : trend === '↓' ? 'rag-console-down' : 'rag-console-flat';
                  return <span className={trendClass} key={`${m.id}-${m.from}-${m.to}`}>{trend} {m.id} ({m.from}→{m.to})</span>;
                })}
              </div>
            </div>
            <ChunkTable title={`Rerank后（${rerankedChunks.length}）`} rows={rerankedChunks} goldSet={goldSet} hasGoldReference={goldSet.size > 0} />
            <ChunkTable title={`Final Context（${finalChunks.length}）`} rows={finalChunks} goldSet={goldSet} hasGoldReference={goldSet.size > 0} />
            <Form.Group>
              <Form.Label className="small text-muted mb-1">最终上下文</Form.Label>
              <Form.Control as="textarea" rows={8} readOnly value={displayFinalContext} />
            </Form.Group>
          </div>
        </div>

        <div className="col-lg-4 rag-console-col">
          <div className="h-100 p-3 border rounded bg-white d-flex flex-column gap-3 rag-console-panel">
            <div className="fw-bold rag-console-panel-title">模型回答与诊断</div>
            <Form.Control className="rag-console-answer" as="textarea" rows={10} readOnly value={displayLlmOutput} />

            <div className="p-2 border rounded bg-light rag-console-score-card">
              <div className="small fw-bold mb-2">自动评分</div>
              <div className="small d-flex flex-column gap-1">
                <div>正确性：<span className={correctness === undefined ? '' : Number(correctness) >= 0.5 ? 'rag-console-ok' : 'rag-console-bad'}>{correctness === undefined ? '—' : (Number(correctness) >= 0.5 ? '✅' : '❌')}</span></div>
                <div>覆盖率：{coverage === null ? '—' : `${coverage.toFixed(1)}%`}</div>
                <div>幻觉风险：<span className={hallucination ? 'rag-console-warn' : 'rag-console-ok'}>{hallucination ? '⚠️' : '✅'}</span></div>
              </div>
            </div>

            <div className="p-2 border rounded bg-light rag-console-cause-card">
              <div className="small fw-bold mb-2">问题归因</div>
              <div className="small d-flex flex-column gap-1">
                {causes.map((c) => (
                  <div className={c.hit ? 'rag-console-cause-hit' : 'rag-console-cause-miss'} key={c.key}>[{c.hit ? '✓' : ' '}] {c.label}</div>
                ))}
              </div>
            </div>

            <div className="p-2 border rounded bg-light rag-console-suggestion-card">
              <div className="small fw-bold mb-2">优化建议</div>
              <div className="small d-flex flex-column gap-1">
                {suggestions.map((s, idx) => <div key={`${idx}-${s}`}>- {s}</div>)}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
