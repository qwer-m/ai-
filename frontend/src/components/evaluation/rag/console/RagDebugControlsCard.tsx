import { Badge, Button, Collapse, Form, Spinner } from 'react-bootstrap';
import type { RagDatasetRow } from '../shared/types';

type Props = {
  projectId: number | null;
  datasets: RagDatasetRow[];
  samples: any[];
  loadingSamples: boolean;
  query: string;
  setQuery: (v: string) => void;
  rewrittenQueries: string[];
  datasetId: number | null;
  setDatasetId: (v: number | null) => void;
  sampleId: number | null;
  setSampleId: (v: number | null) => void;
  limit: number;
  setLimit: (v: number) => void;
  maxTokens: number;
  setMaxTokens: (v: number) => void;
  retrievalMode: 'vector' | 'keyword' | 'hybrid' | 'bm25';
  setRetrievalMode: (v: 'vector' | 'keyword' | 'hybrid' | 'bm25') => void;
  recallTopK: number;
  setRecallTopK: (v: number) => void;
  rerankTopN: number;
  setRerankTopN: (v: number) => void;
  enableRewrite: boolean;
  setEnableRewrite: (v: boolean) => void;
  enableRerank: boolean;
  setEnableRerank: (v: boolean) => void;
  llmModel: string;
  setLlmModel: (v: string) => void;
  maxChunksPerDoc: number;
  setMaxChunksPerDoc: (v: number) => void;
  minDocs: number;
  setMinDocs: (v: number) => void;
  vectorWeight: number;
  setVectorWeight: (v: number) => void;
  keywordWeight: number;
  setKeywordWeight: (v: number) => void;
  titleWeight: number;
  setTitleWeight: (v: number) => void;
  redundancyThreshold: number;
  setRedundancyThreshold: (v: number) => void;
  goldChunksText: string;
  setGoldChunksText: (v: string) => void;
  running: boolean;
  advancedOpen: boolean;
  setAdvancedOpen: (v: boolean) => void;
  onRun: () => void;
  onSavePreset: () => void;
  onResetPreset: () => void;
};

export function RagDebugControlsCard({
  projectId,
  datasets,
  samples,
  loadingSamples,
  query,
  setQuery,
  rewrittenQueries,
  datasetId,
  setDatasetId,
  sampleId,
  setSampleId,
  limit,
  setLimit,
  maxTokens,
  setMaxTokens,
  retrievalMode,
  setRetrievalMode,
  recallTopK,
  setRecallTopK,
  rerankTopN,
  setRerankTopN,
  enableRewrite,
  setEnableRewrite,
  enableRerank,
  setEnableRerank,
  llmModel,
  setLlmModel,
  maxChunksPerDoc,
  setMaxChunksPerDoc,
  minDocs,
  setMinDocs,
  vectorWeight,
  setVectorWeight,
  keywordWeight,
  setKeywordWeight,
  titleWeight,
  setTitleWeight,
  redundancyThreshold,
  setRedundancyThreshold,
  goldChunksText,
  setGoldChunksText,
  running,
  advancedOpen,
  setAdvancedOpen,
  onRun,
  onSavePreset,
  onResetPreset,
}: Props) {
  const selectedDataset = datasets.find((d) => d.id === datasetId) || null;

  return (
    <div className="d-flex flex-column gap-3 rag-console-controls">
      <div className="rag-console-topbar rag-console-topbar-sticky d-flex flex-wrap align-items-center justify-content-between gap-2 p-3 rounded border bg-white">
        <div className="d-flex align-items-center gap-2 rag-console-topbar-title">
          <span className="fw-bold fs-5">RAG 校验测试</span>
          <Badge bg="primary" className="rag-console-pill">单条调试</Badge>
        </div>
        <div className="d-flex align-items-center gap-2">
          <Button variant="outline-primary" size="sm" className="rag-console-btn-outline" onClick={onSavePreset}>保存配置</Button>
          <Button variant="outline-secondary" size="sm" className="rag-console-btn-outline" onClick={onResetPreset}>重置</Button>
        </div>
        <div className="w-100 mt-1 d-flex flex-wrap gap-3 small rag-console-context">
          <span className="rag-console-kv">知识库：<b>项目 #{projectId ?? '-'}</b></span>
          <span className="rag-console-kv">数据集：<b>{selectedDataset ? `${selectedDataset.name}` : '未选择'}</b></span>
          <span className="rag-console-kv">模式：<b>单条调试</b></span>
        </div>
      </div>

      <div className="p-3 border rounded bg-white rag-console-surface">
        <div className="row g-3">
          <div className="col-lg-8 d-flex flex-column gap-3">
            <Form.Group className="rag-console-group">
              <Form.Label className="fw-bold">Query</Form.Label>
              <Form.Control
                className="rag-console-query-input"
                as="textarea"
                rows={6}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="输入问题，例如：登录接口在token过期时为什么会出现500？"
              />
            </Form.Group>

            {rewrittenQueries.length > 0 ? (
              <div className="p-2 bg-light border rounded rag-console-rewrite-box">
                <div className="small fw-bold mb-1">Rewrite 结果（自动展开）</div>
                <div className="small text-secondary">{rewrittenQueries.join(' | ')}</div>
              </div>
            ) : null}
          </div>

          <div className="col-lg-4 d-flex flex-column gap-2">
            <div className="small fw-bold rag-console-right-title">快速参数</div>
            <Form.Group className="rag-console-group">
              <Form.Label className="small text-muted mb-1">检索模式</Form.Label>
              <Form.Select className="rag-console-control" value={retrievalMode} onChange={(e) => setRetrievalMode(e.target.value as any)}>
                <option value="hybrid">hybrid</option>
                <option value="vector">vector</option>
                <option value="keyword">keyword</option>
                <option value="bm25">bm25</option>
              </Form.Select>
            </Form.Group>

            <div className="row g-2">
              <div className="col-6">
                <Form.Label className="small text-muted mb-1">原始召回</Form.Label>
                <Form.Control className="rag-console-control" type="number" value={recallTopK} onChange={(e) => setRecallTopK(Number(e.target.value))} />
              </div>
              <div className="col-6">
                <Form.Label className="small text-muted mb-1">重排保留</Form.Label>
                <Form.Control className="rag-console-control" type="number" value={rerankTopN} onChange={(e) => setRerankTopN(Number(e.target.value))} />
              </div>
            </div>

            <div className="d-flex gap-3 rag-console-checks">
              <Form.Check label="Query Rewrite" checked={enableRewrite} onChange={(e) => setEnableRewrite(e.target.checked)} />
              <Form.Check label="Rerank" checked={enableRerank} onChange={(e) => setEnableRerank(e.target.checked)} />
            </div>

            <div className="d-flex gap-2 mt-2">
              <Button variant="primary" className="flex-grow-1 rag-console-btn-primary" disabled={running} onClick={onRun}>
                {running ? <><Spinner animation="border" size="sm" className="me-2" />开始校验</> : '开始校验'}
              </Button>
              <Button variant="outline-secondary" className="rag-console-btn-outline" onClick={() => setAdvancedOpen(!advancedOpen)}>
                {advancedOpen ? '收起高级参数' : '高级参数'}
              </Button>
            </div>
          </div>
        </div>
      </div>

      <Collapse in={advancedOpen}>
        <div className="p-3 border rounded bg-white rag-console-surface rag-console-advanced">
          <div className="d-flex align-items-center justify-content-between mb-2">
            <div className="fw-bold">高级参数</div>
            <div className="small text-muted">按召回融合 / 文档治理 / 生成 / 评测分组</div>
          </div>

          <div className="row g-3">
            <div className="col-md-6">
              <div className="small fw-bold text-secondary mb-2 rag-console-advanced-title">召回融合</div>
              <div className="row g-2">
                <div className="col-4"><Form.Label className="small text-muted mb-1">向量权重</Form.Label><Form.Control className="rag-console-control" type="number" step="0.05" value={vectorWeight} onChange={(e) => setVectorWeight(Number(e.target.value))} /></div>
                <div className="col-4"><Form.Label className="small text-muted mb-1">关键词权重</Form.Label><Form.Control className="rag-console-control" type="number" step="0.05" value={keywordWeight} onChange={(e) => setKeywordWeight(Number(e.target.value))} /></div>
                <div className="col-4"><Form.Label className="small text-muted mb-1">标题权重</Form.Label><Form.Control className="rag-console-control" type="number" step="0.05" value={titleWeight} onChange={(e) => setTitleWeight(Number(e.target.value))} /></div>
              </div>
            </div>

            <div className="col-md-6">
              <div className="small fw-bold text-secondary mb-2 rag-console-advanced-title">文档治理</div>
              <div className="row g-2">
                <div className="col-4"><Form.Label className="small text-muted mb-1">单文档chunk数</Form.Label><Form.Control className="rag-console-control" type="number" value={maxChunksPerDoc} onChange={(e) => setMaxChunksPerDoc(Number(e.target.value))} /></div>
                <div className="col-4"><Form.Label className="small text-muted mb-1">最少文档数</Form.Label><Form.Control className="rag-console-control" type="number" value={minDocs} onChange={(e) => setMinDocs(Number(e.target.value))} /></div>
                <div className="col-4"><Form.Label className="small text-muted mb-1">去重阈值</Form.Label><Form.Control className="rag-console-control" type="number" step="0.01" value={redundancyThreshold} onChange={(e) => setRedundancyThreshold(Number(e.target.value))} /></div>
              </div>
            </div>

            <div className="col-md-6">
              <div className="small fw-bold text-secondary mb-2 rag-console-advanced-title">生成</div>
              <div className="row g-2">
                <div className="col-6"><Form.Label className="small text-muted mb-1">max_tokens</Form.Label><Form.Control className="rag-console-control" type="number" value={maxTokens} onChange={(e) => setMaxTokens(Number(e.target.value))} /></div>
                <div className="col-6"><Form.Label className="small text-muted mb-1">top_k</Form.Label><Form.Control className="rag-console-control" type="number" value={limit} onChange={(e) => setLimit(Number(e.target.value))} /></div>
                <div className="col-12"><Form.Label className="small text-muted mb-1">LLM 模型</Form.Label><Form.Control className="rag-console-control" value={llmModel} onChange={(e) => setLlmModel(e.target.value)} placeholder="例如 glm-4.7" /></div>
              </div>
            </div>

            <div className="col-md-6">
              <div className="small fw-bold text-secondary mb-2 rag-console-advanced-title">评测参考</div>
              <div className="d-flex gap-2 mb-2">
                <Form.Select className="rag-console-control" value={datasetId ?? ''} onChange={(e) => setDatasetId(e.target.value ? Number(e.target.value) : null)}>
                  <option value="">数据集</option>
                  {datasets.map((d) => <option key={d.id} value={d.id}>#{d.id} {d.name}</option>)}
                </Form.Select>
                <Form.Select className="rag-console-control" value={sampleId ?? ''} onChange={(e) => setSampleId(e.target.value ? Number(e.target.value) : null)} disabled={!datasetId || loadingSamples}>
                  <option value="">{loadingSamples ? '加载中...' : '样本'}</option>
                  {samples.map((s) => <option key={s.id} value={s.id}>#{s.id} {String(s.query || '').slice(0, 24)}</option>)}
                </Form.Select>
              </div>
              <Form.Control className="rag-console-control" as="textarea" rows={3} value={goldChunksText} onChange={(e) => setGoldChunksText(e.target.value)} placeholder="Gold chunk ids（支持换行/逗号分隔）" />
            </div>
          </div>
        </div>
      </Collapse>
    </div>
  );
}
