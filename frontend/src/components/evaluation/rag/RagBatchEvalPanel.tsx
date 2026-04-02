import { useEffect, useMemo, useState } from 'react';
import { Alert, Badge, Button, Collapse, Form, Modal, ProgressBar, Spinner, Table } from 'react-bootstrap';
import {
  getRagEvalRun,
  getRagEvalRunSamples,
  promoteRagSample,
  resumeRagEvalRun,
  startRagEvalRun,
  stopRagEvalRun,
  translateError,
} from '../state/evaluationService';
import { RagCandidatePanel } from './RagCandidatePanel';
import { RagRunComparePanel } from './RagRunComparePanel';
import type { RagDatasetRow, RagEvalConfig } from './shared/types';

type Props = { projectId: number | null; onLog: (msg: string) => void; datasets: RagDatasetRow[] };

const DEFAULT_CONFIG: RagEvalConfig = {
  dataset_selector: { dataset_type: 'all', tags: [], difficulty: 'all', sample_range: 'all', sample_ids: [], enabled_only: true },
  retrieval: { top_k: 5, rerank_top_n: 5, retrieval_mode: 'vector', score_threshold: null },
  context: { max_tokens: 1800, deduplication: true, compression: true, keep_order: false },
  advanced: { enable_query_rewrite: true, enable_multi_query: false, enable_metadata_filter: false, enable_rerank: true, enable_generation: true },
  model: { embedding_model: '', reranker_model: '', llm_model: '', judge_model: '' },
  judge: { answer_eval_mode: 'hybrid', faithfulness_eval_mode: 'hybrid' },
  run_control: { sample_range: 'all', only_unfinished: true },
};

export function RagBatchEvalPanel({ projectId, onLog, datasets }: Props) {
  const [datasetId, setDatasetId] = useState<number | null>(datasets[0]?.id ?? null);
  const [tagsInput, setTagsInput] = useState('');
  const [sampleIdsInput, setSampleIdsInput] = useState('');
  const [runName, setRunName] = useState('');
  const [config, setConfig] = useState<RagEvalConfig>(DEFAULT_CONFIG);
  const [runId, setRunId] = useState<number | null>(null);
  const [runStatus, setRunStatus] = useState<any>(null);
  const [sampleRows, setSampleRows] = useState<any[]>([]);
  const [samplePage, setSamplePage] = useState(1);
  const [sampleTotal, setSampleTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showCompare, setShowCompare] = useState(true);
  const [showCandidate, setShowCandidate] = useState(true);
  const [detailModal, setDetailModal] = useState<{ title: string; content: string } | null>(null);

  const activeRunStatus = String(runStatus?.run?.status || '');
  const runActive = ['pending', 'running', 'stopping'].includes(activeRunStatus);
  const overview = useMemo(() => runStatus?.metrics?.overview || {}, [runStatus]);
  const progressPct = Number(runStatus?.progress?.progress_pct || 0);

  useEffect(() => {
    if (!runId) return;
    const timer = window.setInterval(() => void refreshRun(runId, false), 1500);
    return () => window.clearInterval(timer);
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    void refreshRun(runId, true);
  }, [samplePage, runId]);

  const parseConfig = (): RagEvalConfig => {
    const tags = tagsInput
      .split(',')
      .map((x) => x.trim())
      .filter(Boolean);
    const sampleIds = sampleIdsInput
      .split(',')
      .map((x) => Number(x.trim()))
      .filter((x) => Number.isFinite(x) && x > 0);
    const selectedDataset = datasets.find((d) => d.id === datasetId);
    return {
      ...config,
      dataset_selector: {
        ...config.dataset_selector,
        dataset_type: selectedDataset?.type || 'all',
        tags,
        sample_ids: sampleIds,
      },
    };
  };

  const refreshRun = async (id: number, withSamples = true) => {
    const status = await getRagEvalRun(id);
    setRunStatus(status);
    if (!withSamples) return;
    const pageResp = await getRagEvalRunSamples(id, { page: samplePage, page_size: 20 });
    setSampleRows(pageResp?.items || []);
    setSampleTotal(Number(pageResp?.total || 0));
  };

  const startRun = async () => {
    if (!projectId) return setError('请先选择项目');
    if (!datasetId) return setError('请选择数据集');

    setBusy(true);
    setError(null);
    try {
      const resp = await startRagEvalRun(projectId, {
        dataset_id: datasetId,
        run_name: runName.trim() || undefined,
        config: parseConfig(),
      });
      const nextRunId = Number(resp?.run_id);
      setRunId(nextRunId);
      onLog(`RAG 批量评测已启动: run_id=${nextRunId}`);
      await refreshRun(nextRunId);
    } catch (e) {
      setError(await translateError(e));
    } finally {
      setBusy(false);
    }
  };

  const stopRun = async () => {
    if (!runId) return;

    setBusy(true);
    setError(null);
    try {
      await stopRagEvalRun(runId);
      onLog(`RAG 批量评测停止请求已发送: run_id=${runId}`);
      await refreshRun(runId);
    } catch (e) {
      setError(await translateError(e));
    } finally {
      setBusy(false);
    }
  };

  const resumeRun = async () => {
    if (!runId) return;

    setBusy(true);
    setError(null);
    try {
      await resumeRagEvalRun(runId);
      onLog(`RAG 批量评测继续执行: run_id=${runId}`);
      await refreshRun(runId);
    } catch (e) {
      setError(await translateError(e));
    } finally {
      setBusy(false);
    }
  };

  const promote = async (sampleId: number, target: 'challenge' | 'regression') => {
    try {
      await promoteRagSample(sampleId, target);
      onLog(`样本 #${sampleId} 已加入 ${target}`);
    } catch (e) {
      setError(await translateError(e));
    }
  };

  const openDetail = (row: any, kind: 'retrieved' | 'reranked' | 'context' | 'prompt' | 'full') => {
    const debug = row?.detail_json?.debug || {};
    if (kind === 'retrieved') {
      return setDetailModal({ title: `样本 ${row.sample_id} - 召回 chunks`, content: JSON.stringify(row?.retrieved_chunks || [], null, 2) });
    }
    if (kind === 'reranked') {
      return setDetailModal({ title: `样本 ${row.sample_id} - rerank 结果`, content: JSON.stringify(row?.reranked_chunks || [], null, 2) });
    }
    if (kind === 'context') {
      return setDetailModal({ title: `样本 ${row.sample_id} - 最终上下文`, content: String(debug?.context || row?.answer_text || '') });
    }
    if (kind === 'prompt') {
      return setDetailModal({
        title: `样本 ${row.sample_id} - prompt 信息`,
        content: JSON.stringify({ query: row?.sample_query, context: debug?.context || '', expected: row?.expected_answer || '' }, null, 2),
      });
    }
    return setDetailModal({ title: `样本 ${row.sample_id} - 评测详情`, content: JSON.stringify(row, null, 2) });
  };

  return (
    <div className="d-flex flex-column gap-3 rag-report-shell">
      {error ? <Alert variant="danger" className="mb-0">{error}</Alert> : null}

      <div className="rag-report-toolbar d-flex flex-wrap gap-2 align-items-center">
        <Button size="sm" variant="outline-secondary" onClick={() => setShowAdvanced((v) => !v)}>
          {showAdvanced ? '收起高级参数' : '展开高级参数'}
        </Button>
        <Button size="sm" variant="outline-secondary" onClick={() => setShowCompare((v) => !v)}>
          {showCompare ? '收起运行对比' : '展开运行对比'}
        </Button>
        <Button size="sm" variant="outline-secondary" onClick={() => setShowCandidate((v) => !v)}>
          {showCandidate ? '收起候选回流' : '展开候选回流'}
        </Button>
        <span className="small text-muted ms-1">参数按层分组展示，先配基础项，再按需展开高级项</span>
      </div>

      <div className="rag-report-block rag-report-block-base ui-section-card">
        <div className="ui-section-title">基础参数</div>
        <div className="grid grid-cols-4 gap-3 rag-report-grid rag-report-grid-wide control-grid-lr">
          <Form.Group className="control-field">
            <Form.Label className="small text-muted">数据集</Form.Label>
            <Form.Select value={datasetId ?? ''} onChange={(e) => setDatasetId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">请选择</option>
              {datasets.map((d) => <option key={d.id} value={d.id}>#{d.id} {d.name} ({d.type})</option>)}
            </Form.Select>
          </Form.Group>
          <Form.Group className="control-field">
            <Form.Label className="small text-muted">运行名称</Form.Label>
            <Form.Control value={runName} onChange={(e) => setRunName(e.target.value)} placeholder="可选，便于后续对比检索" />
          </Form.Group>
          <Form.Group className="control-field">
            <Form.Label className="small text-muted">样本难度</Form.Label>
            <Form.Select value={config.dataset_selector.difficulty} onChange={(e) => setConfig((v) => ({ ...v, dataset_selector: { ...v.dataset_selector, difficulty: e.target.value as any } }))}>
              <option value="all">all</option>
              <option value="easy">easy</option>
              <option value="medium">medium</option>
              <option value="hard">hard</option>
            </Form.Select>
          </Form.Group>
          <Form.Group className="rag-col-span-2 control-field">
            <Form.Label className="small text-muted">标签过滤（逗号分隔）</Form.Label>
            <Form.Control value={tagsInput} onChange={(e) => setTagsInput(e.target.value)} placeholder="billing,permission" />
          </Form.Group>
        </div>

        <div className="grid grid-cols-4 gap-3 rag-report-grid rag-report-grid-wide mt-1 control-grid-lr">
          <Form.Group className="control-field">
            <Form.Label className="small text-muted">原始召回 TopK</Form.Label>
            <Form.Control type="number" min={1} max={20} value={config.retrieval.top_k} onChange={(e) => setConfig((v) => ({ ...v, retrieval: { ...v.retrieval, top_k: Number(e.target.value) || 5 } }))} />
          </Form.Group>
          <Form.Group className="control-field">
            <Form.Label className="small text-muted">重排保留 N</Form.Label>
            <Form.Control type="number" min={1} max={20} value={config.retrieval.rerank_top_n} onChange={(e) => setConfig((v) => ({ ...v, retrieval: { ...v.retrieval, rerank_top_n: Number(e.target.value) || 5 } }))} />
          </Form.Group>
          <Form.Group className="control-field">
            <Form.Label className="small text-muted">检索模式</Form.Label>
            <Form.Select value={config.retrieval.retrieval_mode} onChange={(e) => setConfig((v) => ({ ...v, retrieval: { ...v.retrieval, retrieval_mode: e.target.value as any } }))}>
              <option value="vector">vector</option>
              <option value="hybrid">hybrid</option>
              <option value="bm25">bm25</option>
            </Form.Select>
          </Form.Group>
          <Form.Group className="control-field">
            <Form.Label className="small text-muted">上下文 Token 上限</Form.Label>
            <Form.Control type="number" min={128} max={8000} value={config.context.max_tokens} onChange={(e) => setConfig((v) => ({ ...v, context: { ...v.context, max_tokens: Number(e.target.value) || 1800 } }))} />
          </Form.Group>
          <Form.Group className="rag-col-span-2 control-field">
            <Form.Label className="small text-muted">指定样本 ID（逗号分隔）</Form.Label>
            <Form.Control value={sampleIdsInput} onChange={(e) => setSampleIdsInput(e.target.value)} placeholder="12,13,14" />
          </Form.Group>
        </div>
      </div>

      <div className="rag-report-block rag-report-block-advanced ui-section-card">
        <div className="ui-section-title-row">
          <div className="ui-section-title">高级参数</div>
          <Button size="sm" variant="outline-primary" onClick={() => setShowAdvanced((v) => !v)}>
            {showAdvanced ? '隐藏' : '显示'}
          </Button>
        </div>

        <Collapse in={showAdvanced}>
          <div className="d-flex flex-column gap-3 mt-2">
            <div className="grid grid-cols-4 gap-3 rag-report-grid rag-report-grid-tight rag-report-check-grid">
              <Form.Group><Form.Check label="启用 Query Rewrite" checked={config.advanced.enable_query_rewrite} onChange={(e) => setConfig((v) => ({ ...v, advanced: { ...v.advanced, enable_query_rewrite: e.target.checked } }))} /></Form.Group>
              <Form.Group><Form.Check label="启用 Multi Query" checked={config.advanced.enable_multi_query} onChange={(e) => setConfig((v) => ({ ...v, advanced: { ...v.advanced, enable_multi_query: e.target.checked } }))} /></Form.Group>
              <Form.Group><Form.Check label="启用 Metadata 过滤" checked={config.advanced.enable_metadata_filter} onChange={(e) => setConfig((v) => ({ ...v, advanced: { ...v.advanced, enable_metadata_filter: e.target.checked } }))} /></Form.Group>
              <Form.Group><Form.Check label="启用 Rerank" checked={config.advanced.enable_rerank} onChange={(e) => setConfig((v) => ({ ...v, advanced: { ...v.advanced, enable_rerank: e.target.checked } }))} /></Form.Group>
              <Form.Group><Form.Check label="上下文去重" checked={config.context.deduplication} onChange={(e) => setConfig((v) => ({ ...v, context: { ...v.context, deduplication: e.target.checked } }))} /></Form.Group>
              <Form.Group><Form.Check label="上下文压缩" checked={config.context.compression} onChange={(e) => setConfig((v) => ({ ...v, context: { ...v.context, compression: e.target.checked } }))} /></Form.Group>
              <Form.Group><Form.Check label="保持原顺序" checked={config.context.keep_order} onChange={(e) => setConfig((v) => ({ ...v, context: { ...v.context, keep_order: e.target.checked } }))} /></Form.Group>
              <Form.Group><Form.Check label="仅评测未完成样本" checked={config.run_control.only_unfinished} onChange={(e) => setConfig((v) => ({ ...v, run_control: { ...v.run_control, only_unfinished: e.target.checked } }))} /></Form.Group>
            </div>

            <div className="grid grid-cols-3 gap-3 rag-report-grid rag-report-grid-wide control-grid-lr">
              <Form.Group className="control-field">
                <Form.Label className="small text-muted">样本范围</Form.Label>
                <Form.Control value={config.run_control.sample_range} onChange={(e) => setConfig((v) => ({ ...v, run_control: { ...v.run_control, sample_range: e.target.value || 'all' } }))} placeholder="all 或 1-100" />
              </Form.Group>
              <Form.Group className="control-field">
                <Form.Label className="small text-muted">判定模式</Form.Label>
                <Form.Select value={config.judge.answer_eval_mode} onChange={(e) => setConfig((v) => ({ ...v, judge: { ...v.judge, answer_eval_mode: e.target.value as any, faithfulness_eval_mode: e.target.value as any } }))}>
                  <option value="rule">rule</option>
                  <option value="llm">llm</option>
                  <option value="hybrid">hybrid</option>
                </Form.Select>
              </Form.Group>
            </div>
          </div>
        </Collapse>
      </div>

      <div className="rag-report-block rag-report-block-run ui-section-card">
        <div className="ui-section-title">评测执行</div>
        <div className="ui-actions-row rag-report-actions">
          <Button variant="primary" disabled={busy || runActive} onClick={() => void startRun()}>
            {busy ? <><Spinner animation="border" size="sm" className="me-2" />处理中...</> : '开始批量评测'}
          </Button>
          <Button variant="outline-danger" disabled={busy || !runId || !runActive} onClick={() => void stopRun()}>停止评测</Button>
          <Button variant="outline-secondary" disabled={busy || !runId || runActive} onClick={() => void resumeRun()}>断点续跑</Button>
          {runId ? <Badge bg="secondary">run_id={runId}</Badge> : null}
        </div>

        {runStatus ? (
          <div className="rag-report-status-card d-flex flex-column gap-2 mt-2">
            <ProgressBar now={progressPct} label={`${progressPct.toFixed(1)}%`} />
            <div className="grid grid-cols-5 gap-3 small rag-report-kpi-grid">
              <div className="ui-kpi-card"><div className="ui-kpi-title">状态</div>{activeRunStatus || '-'}</div>
              <div className="ui-kpi-card"><div className="ui-kpi-title">样本进度</div>{runStatus?.progress?.finished_samples || 0}/{runStatus?.progress?.total_samples || 0}</div>
              <div className="ui-kpi-card"><div className="ui-kpi-title">Recall@5</div>{Number(overview['recall@5'] || 0).toFixed(4)}</div>
              <div className="ui-kpi-card"><div className="ui-kpi-title">MRR</div>{Number(overview?.mrr || 0).toFixed(4)}</div>
              <div className="ui-kpi-card"><div className="ui-kpi-title">Pass Rate</div>{Number(overview?.pass_rate || 0).toFixed(4)}</div>
            </div>
          </div>
        ) : null}
      </div>

      <div className="rag-report-block rag-report-block-samples ui-section-card">
        <div className="ui-section-title">样本结果</div>
        <div className="table-responsive rag-report-table scroll-table-lg">
          <Table striped bordered hover size="sm" className="mb-0">
            <thead>
              <tr>
                <th>sample_id</th>
                <th>query</th>
                <th>first_hit_rank</th>
                <th>RecallHit</th>
                <th>Correct</th>
                <th>Faithful</th>
                <th>failure_reason</th>
                <th>latency_ms</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {sampleRows.length === 0 ? (
                <tr><td colSpan={9} className="text-center text-muted">暂无样本结果</td></tr>
              ) : sampleRows.map((row) => (
                <tr key={row.id}>
                  <td>{row.sample_id}</td>
                  <td className="rag-report-query-cell">{String(row.sample_query || '').slice(0, 100)}</td>
                  <td>{row.first_hit_rank ?? '-'}</td>
                  <td>{row.recall_hit ? 'Y' : 'N'}</td>
                  <td>{row.answer_correct ? 'Y' : 'N'}</td>
                  <td>{Number(row.faithfulness_score ?? 0).toFixed(3)}</td>
                  <td>{row.failure_reason || 'pass'}</td>
                  <td>{Number(row.latency_ms || 0).toFixed(1)}</td>
                  <td className="d-flex gap-2 flex-wrap rag-report-action-cell">
                    <Button size="sm" variant="outline-secondary" onClick={() => openDetail(row, 'retrieved')}>召回</Button>
                    <Button size="sm" variant="outline-secondary" onClick={() => openDetail(row, 'reranked')}>重排</Button>
                    <Button size="sm" variant="outline-secondary" onClick={() => openDetail(row, 'context')}>上下文</Button>
                    <Button size="sm" variant="outline-secondary" onClick={() => openDetail(row, 'prompt')}>Prompt</Button>
                    <Button size="sm" variant="outline-secondary" onClick={() => openDetail(row, 'full')}>详情</Button>
                    <Button size="sm" variant="outline-warning" onClick={() => void promote(row.sample_id, 'challenge')}>加入挑战集</Button>
                    <Button size="sm" variant="outline-info" onClick={() => void promote(row.sample_id, 'regression')}>加入回归集</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>

        <div className="d-flex gap-2 rag-report-pagination mt-2">
          <Button size="sm" variant="outline-secondary" disabled={!runId || samplePage <= 1} onClick={() => setSamplePage((p) => p - 1)}>上一页</Button>
          <Button size="sm" variant="outline-secondary" disabled={!runId || samplePage * 20 >= sampleTotal} onClick={() => setSamplePage((p) => p + 1)}>下一页</Button>
          <span className="small text-muted align-self-center">共 {sampleTotal} 条</span>
        </div>
      </div>

      <div className="d-flex flex-column gap-2 rag-report-card ui-section-card">
        <div className="ui-section-title-row">
          <div className="ui-section-title mb-0">评测运行对比</div>
          <Button size="sm" variant="outline-secondary" onClick={() => setShowCompare((v) => !v)}>{showCompare ? '收起' : '展开'}</Button>
        </div>
        <Collapse in={showCompare}>
          <div className="pt-2">
            <RagRunComparePanel onLog={onLog} currentRunId={runId} />
          </div>
        </Collapse>
      </div>

      <div className="d-flex flex-column gap-2 rag-report-card ui-section-card">
        <div className="ui-section-title-row">
          <div className="ui-section-title mb-0">候选回流</div>
          <Button size="sm" variant="outline-secondary" onClick={() => setShowCandidate((v) => !v)}>{showCandidate ? '收起' : '展开'}</Button>
        </div>
        <Collapse in={showCandidate}>
          <div className="pt-2">
            <RagCandidatePanel onLog={onLog} currentRunId={runId} />
          </div>
        </Collapse>
      </div>

      <Modal show={Boolean(detailModal)} onHide={() => setDetailModal(null)} size="lg" className="rag-detail-modal">
        <Modal.Header closeButton><Modal.Title>{detailModal?.title}</Modal.Title></Modal.Header>
        <Modal.Body><Form.Control as="textarea" rows={18} readOnly value={detailModal?.content || ''} /></Modal.Body>
      </Modal>
    </div>
  );
}
