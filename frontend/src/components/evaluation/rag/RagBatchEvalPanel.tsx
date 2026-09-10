import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Collapse,
  Form,
  Modal,
  ProgressBar,
  Spinner,
  Table,
} from 'react-bootstrap';
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
import type { RagDatasetRow } from './shared/types';

type Props = {
  projectId: number | null;
  onLog: (message: string) => void;
  datasets: RagDatasetRow[];
};

type DatasetDifficulty = 'all' | 'easy' | 'medium' | 'hard';
type RetrievalMode = 'vector' | 'hybrid' | 'bm25';
type JudgeMode = 'rule' | 'llm' | 'hybrid';
type PromoteTarget = 'challenge' | 'regression';
type DetailKind = 'retrieved' | 'reranked' | 'context' | 'prompt' | 'full';

type RagEvalConfig = {
  dataset_selector: {
    dataset_type: RagDatasetRow['type'] | 'all';
    tags: string[];
    difficulty: DatasetDifficulty;
    sample_range: string;
    sample_ids: number[];
    enabled_only: boolean;
  };
  retrieval: {
    top_k: number;
    rerank_top_n: number;
    retrieval_mode: RetrievalMode;
    score_threshold: number | null;
  };
  context: {
    max_tokens: number;
    deduplication: boolean;
    compression: boolean;
    keep_order: boolean;
  };
  advanced: {
    enable_query_rewrite: boolean;
    enable_multi_query: boolean;
    enable_metadata_filter: boolean;
    enable_rerank: boolean;
    enable_generation: boolean;
  };
  model: {
    embedding_model: string;
    reranker_model: string;
    llm_model: string;
    judge_model: string;
  };
  judge: {
    answer_eval_mode: JudgeMode;
    faithfulness_eval_mode: JudgeMode;
  };
  run_control: {
    sample_range: string;
    only_unfinished: boolean;
  };
};

type RagEvalRunStatus = {
  run: {
    status: string;
  };
  progress: {
    progress_pct?: number;
    finished_samples?: number;
    total_samples?: number;
  };
  metrics: {
    overview?: Record<string, unknown>;
  };
};

type RagEvalSample = {
  id: number;
  sample_id: number;
  sample_query?: string | null;
  first_hit_rank?: number | null;
  recall_hit?: boolean;
  answer_text?: string | null;
  answer_correct?: boolean;
  faithfulness_score?: number | null;
  failure_reason?: string | null;
  latency_ms?: number | null;
  expected_answer?: string | null;
  retrieved_chunks?: unknown[] | null;
  reranked_chunks?: unknown[] | null;
  detail_json?: {
    debug?: {
      context?: unknown;
    };
  } | null;
};

type RagEvalSamplesPage = {
  items?: RagEvalSample[];
  total?: number;
};

type DetailContent = {
  title: string;
  content: string;
};

const PAGE_SIZE = 20;
const ACTIVE_STATUSES = new Set(['pending', 'running', 'stopping']);

const DEFAULT_CONFIG: RagEvalConfig = {
  dataset_selector: {
    dataset_type: 'all',
    tags: [],
    difficulty: 'all',
    sample_range: 'all',
    sample_ids: [],
    enabled_only: true,
  },
  retrieval: {
    top_k: 5,
    rerank_top_n: 5,
    retrieval_mode: 'vector',
    score_threshold: null,
  },
  context: {
    max_tokens: 1800,
    deduplication: true,
    compression: true,
    keep_order: false,
  },
  advanced: {
    enable_query_rewrite: true,
    enable_multi_query: false,
    enable_metadata_filter: false,
    enable_rerank: false,
    enable_generation: false,
  },
  model: {
    embedding_model: '',
    reranker_model: '',
    llm_model: '',
    judge_model: '',
  },
  judge: {
    answer_eval_mode: 'hybrid',
    faithfulness_eval_mode: 'hybrid',
  },
  run_control: {
    sample_range: 'all',
    only_unfinished: true,
  },
};

export function RagBatchEvalPanel({ projectId, onLog, datasets }: Props) {
  const [datasetId, setDatasetId] = useState<number | null>(datasets[0]?.id ?? null);
  const [tagsText, setTagsText] = useState('');
  const [sampleIdsText, setSampleIdsText] = useState('');
  const [runName, setRunName] = useState('');
  const [config, setConfig] = useState<RagEvalConfig>(DEFAULT_CONFIG);

  const [runId, setRunId] = useState<number | null>(null);
  const [runStatus, setRunStatus] = useState<RagEvalRunStatus | null>(null);
  const [samples, setSamples] = useState<RagEvalSample[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [compareOpen, setCompareOpen] = useState(true);
  const [candidateOpen, setCandidateOpen] = useState(true);
  const [detail, setDetail] = useState<DetailContent | null>(null);

  const status = String(runStatus?.run?.status || '');
  const isRunning = ACTIVE_STATUSES.has(status);
  const overview = useMemo(() => runStatus?.metrics?.overview || {}, [runStatus]);
  const progress = Number(runStatus?.progress?.progress_pct || 0);

  const loadRun = async (activeRunId: number, includeSamples = true) => {
    const statusResponse = (await getRagEvalRun(activeRunId)) as RagEvalRunStatus;
    setRunStatus(statusResponse);

    if (!includeSamples) return;

    const samplesResponse = (await getRagEvalRunSamples(activeRunId, {
      page,
      page_size: PAGE_SIZE,
    })) as RagEvalSamplesPage;
    setSamples(samplesResponse?.items || []);
    setTotal(Number(samplesResponse?.total || 0));
  };

  useEffect(() => {
    if (!runId) return undefined;

    const timer = window.setInterval(() => {
      void loadRun(runId, false);
    }, 1500);

    return () => window.clearInterval(timer);
  }, [runId]);

  useEffect(() => {
    if (runId) void loadRun(runId, true);
  }, [page, runId]);

  const buildConfig = (): RagEvalConfig => {
    const tags = tagsText
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
    const sampleIds = sampleIdsText
      .split(',')
      .map((item) => Number(item.trim()))
      .filter((value) => Number.isFinite(value) && value > 0);
    const dataset = datasets.find((item) => item.id === datasetId);

    return {
      ...config,
      dataset_selector: {
        ...config.dataset_selector,
        dataset_type: dataset?.type || 'all',
        tags,
        sample_ids: sampleIds,
      },
    };
  };

  const handleStart = async () => {
    if (!projectId) {
      setError('请先选择项目');
      return;
    }
    if (!datasetId) {
      setError('请选择数据集');
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const response = await startRagEvalRun(projectId, {
        dataset_id: datasetId,
        run_name: runName.trim() || undefined,
        config: buildConfig(),
      });
      const nextRunId = Number(response?.run_id);
      setRunId(nextRunId);
      onLog(`RAG 批量评测已启动: run_id=${nextRunId}`);
      await loadRun(nextRunId);
    } catch (reason) {
      setError(await translateError(reason));
    } finally {
      setBusy(false);
    }
  };

  const handleStop = async () => {
    if (!runId) return;

    setBusy(true);
    setError(null);
    try {
      await stopRagEvalRun(runId);
      onLog(`RAG 批量评测停止请求已发送: run_id=${runId}`);
      await loadRun(runId);
    } catch (reason) {
      setError(await translateError(reason));
    } finally {
      setBusy(false);
    }
  };

  const handleResume = async () => {
    if (!runId) return;

    setBusy(true);
    setError(null);
    try {
      await resumeRagEvalRun(runId);
      onLog(`RAG 批量评测继续执行: run_id=${runId}`);
      await loadRun(runId);
    } catch (reason) {
      setError(await translateError(reason));
    } finally {
      setBusy(false);
    }
  };

  const handlePromote = async (sampleId: number, target: PromoteTarget) => {
    try {
      await promoteRagSample(sampleId, target);
      onLog(`样本 #${sampleId} 已加入 ${target}`);
    } catch (reason) {
      setError(await translateError(reason));
    }
  };

  const openDetail = (sample: RagEvalSample, kind: DetailKind) => {
    const debug = sample.detail_json?.debug || {};

    if (kind === 'retrieved') {
      setDetail({
        title: `样本 ${sample.sample_id} - 召回 chunks`,
        content: JSON.stringify(sample.retrieved_chunks || [], null, 2),
      });
      return;
    }
    if (kind === 'reranked') {
      setDetail({
        title: `样本 ${sample.sample_id} - rerank 结果`,
        content: JSON.stringify(sample.reranked_chunks || [], null, 2),
      });
      return;
    }
    if (kind === 'context') {
      setDetail({
        title: `样本 ${sample.sample_id} - 最终上下文`,
        content: String(debug.context || sample.answer_text || ''),
      });
      return;
    }
    if (kind === 'prompt') {
      setDetail({
        title: `样本 ${sample.sample_id} - prompt 信息`,
        content: JSON.stringify(
          {
            query: sample.sample_query,
            context: debug.context || '',
            expected: sample.expected_answer || '',
          },
          null,
          2,
        ),
      });
      return;
    }

    setDetail({
      title: `样本 ${sample.sample_id} - 评测详情`,
      content: JSON.stringify(sample, null, 2),
    });
  };

  return (
    <div className="d-flex flex-column gap-3 rag-report-shell">
      {error ? <Alert variant="danger" className="mb-0">{error}</Alert> : null}

      <div className="rag-report-toolbar d-flex flex-wrap gap-2 align-items-center">
        <Button size="sm" variant="outline-secondary" onClick={() => setAdvancedOpen((value) => !value)}>
          {advancedOpen ? '收起高级参数' : '展开高级参数'}
        </Button>
        <Button size="sm" variant="outline-secondary" onClick={() => setCompareOpen((value) => !value)}>
          {compareOpen ? '收起运行对比' : '展开运行对比'}
        </Button>
        <Button size="sm" variant="outline-secondary" onClick={() => setCandidateOpen((value) => !value)}>
          {candidateOpen ? '收起候选回流' : '展开候选回流'}
        </Button>
        <span className="small text-muted ms-1">参数按层分组展示，先配基础项，再按需展开高级项</span>
      </div>

      <div className="rag-report-block rag-report-block-base ui-section-card">
        <div className="ui-section-title">基础参数</div>
        <div className="grid grid-cols-4 gap-3 rag-report-grid rag-report-grid-wide control-grid-lr">
          <Form.Group className="control-field">
            <Form.Label className="small text-muted">数据集</Form.Label>
            <Form.Select
              value={datasetId ?? ''}
              onChange={(event) => setDatasetId(event.target.value ? Number(event.target.value) : null)}
            >
              <option value="">请选择</option>
              {datasets.map((dataset) => (
                <option key={dataset.id} value={dataset.id}>
                  #{dataset.id} {dataset.name} ({dataset.type})
                </option>
              ))}
            </Form.Select>
          </Form.Group>

          <Form.Group className="control-field">
            <Form.Label className="small text-muted">运行名称</Form.Label>
            <Form.Control
              value={runName}
              onChange={(event) => setRunName(event.target.value)}
              placeholder="可选，便于后续对比检索"
            />
          </Form.Group>

          <Form.Group className="control-field">
            <Form.Label className="small text-muted">样本难度</Form.Label>
            <Form.Select
              value={config.dataset_selector.difficulty}
              onChange={(event) => setConfig((current) => ({
                ...current,
                dataset_selector: {
                  ...current.dataset_selector,
                  difficulty: event.target.value as DatasetDifficulty,
                },
              }))}
            >
              <option value="all">all</option>
              <option value="easy">easy</option>
              <option value="medium">medium</option>
              <option value="hard">hard</option>
            </Form.Select>
          </Form.Group>

          <Form.Group className="rag-col-span-2 control-field">
            <Form.Label className="small text-muted">标签过滤（逗号分隔）</Form.Label>
            <Form.Control
              value={tagsText}
              onChange={(event) => setTagsText(event.target.value)}
              placeholder="billing,permission"
            />
          </Form.Group>
        </div>

        <div className="grid grid-cols-4 gap-3 rag-report-grid rag-report-grid-wide mt-1 control-grid-lr">
          <Form.Group className="control-field">
            <Form.Label className="small text-muted">原始召回 TopK</Form.Label>
            <Form.Control
              type="number"
              min={1}
              max={20}
              value={config.retrieval.top_k}
              onChange={(event) => setConfig((current) => ({
                ...current,
                retrieval: {
                  ...current.retrieval,
                  top_k: Number(event.target.value) || 5,
                },
              }))}
            />
          </Form.Group>

          <Form.Group className="control-field">
            <Form.Label className="small text-muted">重排保留 N</Form.Label>
            <Form.Control
              type="number"
              min={1}
              max={20}
              value={config.retrieval.rerank_top_n}
              onChange={(event) => setConfig((current) => ({
                ...current,
                retrieval: {
                  ...current.retrieval,
                  rerank_top_n: Number(event.target.value) || 5,
                },
              }))}
            />
          </Form.Group>

          <Form.Group className="control-field">
            <Form.Label className="small text-muted">检索模式</Form.Label>
            <Form.Select
              value={config.retrieval.retrieval_mode}
              onChange={(event) => setConfig((current) => ({
                ...current,
                retrieval: {
                  ...current.retrieval,
                  retrieval_mode: event.target.value as RetrievalMode,
                },
              }))}
            >
              <option value="vector">vector</option>
              <option value="hybrid">hybrid</option>
              <option value="bm25">bm25</option>
            </Form.Select>
          </Form.Group>

          <Form.Group className="control-field">
            <Form.Label className="small text-muted">上下文 Token 上限</Form.Label>
            <Form.Control
              type="number"
              min={128}
              max={8000}
              value={config.context.max_tokens}
              onChange={(event) => setConfig((current) => ({
                ...current,
                context: {
                  ...current.context,
                  max_tokens: Number(event.target.value) || 1800,
                },
              }))}
            />
          </Form.Group>

          <Form.Group className="rag-col-span-2 control-field">
            <Form.Label className="small text-muted">指定样本 ID（逗号分隔）</Form.Label>
            <Form.Control
              value={sampleIdsText}
              onChange={(event) => setSampleIdsText(event.target.value)}
              placeholder="12,13,14"
            />
          </Form.Group>
        </div>
      </div>

      <div className="rag-report-block rag-report-block-advanced ui-section-card">
        <div className="ui-section-title-row">
          <div className="ui-section-title">高级参数</div>
          <Button size="sm" variant="outline-primary" onClick={() => setAdvancedOpen((value) => !value)}>
            {advancedOpen ? '隐藏' : '显示'}
          </Button>
        </div>

        <Collapse in={advancedOpen}>
          <div className="d-flex flex-column gap-3 mt-2">
            <div className="grid grid-cols-4 gap-3 rag-report-grid rag-report-grid-tight rag-report-check-grid">
              <Form.Group>
                <Form.Check
                  label="启用 Query Rewrite"
                  checked={config.advanced.enable_query_rewrite}
                  onChange={(event) => setConfig((current) => ({
                    ...current,
                    advanced: { ...current.advanced, enable_query_rewrite: event.target.checked },
                  }))}
                />
              </Form.Group>
              <Form.Group>
                <Form.Check
                  label="启用 Multi Query"
                  checked={config.advanced.enable_multi_query}
                  onChange={(event) => setConfig((current) => ({
                    ...current,
                    advanced: { ...current.advanced, enable_multi_query: event.target.checked },
                  }))}
                />
              </Form.Group>
              <Form.Group>
                <Form.Check
                  label="启用 Metadata 过滤"
                  checked={config.advanced.enable_metadata_filter}
                  onChange={(event) => setConfig((current) => ({
                    ...current,
                    advanced: { ...current.advanced, enable_metadata_filter: event.target.checked },
                  }))}
                />
              </Form.Group>
              <Form.Group>
                <Form.Check
                  label="启用 Rerank"
                  checked={config.advanced.enable_rerank}
                  onChange={(event) => setConfig((current) => ({
                    ...current,
                    advanced: { ...current.advanced, enable_rerank: event.target.checked },
                  }))}
                />
              </Form.Group>
              <Form.Group>
                <Form.Check
                  label="上下文去重"
                  checked={config.context.deduplication}
                  onChange={(event) => setConfig((current) => ({
                    ...current,
                    context: { ...current.context, deduplication: event.target.checked },
                  }))}
                />
              </Form.Group>
              <Form.Group>
                <Form.Check
                  label="上下文压缩"
                  checked={config.context.compression}
                  onChange={(event) => setConfig((current) => ({
                    ...current,
                    context: { ...current.context, compression: event.target.checked },
                  }))}
                />
              </Form.Group>
              <Form.Group>
                <Form.Check
                  label="保持原顺序"
                  checked={config.context.keep_order}
                  onChange={(event) => setConfig((current) => ({
                    ...current,
                    context: { ...current.context, keep_order: event.target.checked },
                  }))}
                />
              </Form.Group>
              <Form.Group>
                <Form.Check
                  label="仅评测未完成样本"
                  checked={config.run_control.only_unfinished}
                  onChange={(event) => setConfig((current) => ({
                    ...current,
                    run_control: { ...current.run_control, only_unfinished: event.target.checked },
                  }))}
                />
              </Form.Group>
            </div>

            <div className="grid grid-cols-3 gap-3 rag-report-grid rag-report-grid-wide control-grid-lr">
              <Form.Group className="control-field">
                <Form.Label className="small text-muted">样本范围</Form.Label>
                <Form.Control
                  value={config.run_control.sample_range}
                  onChange={(event) => setConfig((current) => ({
                    ...current,
                    run_control: {
                      ...current.run_control,
                      sample_range: event.target.value || 'all',
                    },
                  }))}
                  placeholder="all 或 1-100"
                />
              </Form.Group>

              <Form.Group className="control-field">
                <Form.Label className="small text-muted">判定模式</Form.Label>
                <Form.Select
                  value={config.judge.answer_eval_mode}
                  onChange={(event) => {
                    const mode = event.target.value as JudgeMode;
                    setConfig((current) => ({
                      ...current,
                      judge: {
                        ...current.judge,
                        answer_eval_mode: mode,
                        faithfulness_eval_mode: mode,
                      },
                    }));
                  }}
                >
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
          <Button variant="primary" disabled={busy || isRunning} onClick={() => void handleStart()}>
            {busy ? (
              <>
                <Spinner animation="border" size="sm" className="me-2" />
                处理中...
              </>
            ) : '开始批量评测'}
          </Button>
          <Button variant="outline-danger" disabled={busy || !runId || !isRunning} onClick={() => void handleStop()}>
            停止评测
          </Button>
          <Button variant="outline-secondary" disabled={busy || !runId || isRunning} onClick={() => void handleResume()}>
            断点续跑
          </Button>
          {runId ? <Badge bg="secondary">run_id={runId}</Badge> : null}
        </div>

        {runStatus ? (
          <div className="rag-report-status-card d-flex flex-column gap-2 mt-2">
            <ProgressBar now={progress} label={`${progress.toFixed(1)}%`} />
            <div className="grid grid-cols-5 gap-3 small rag-report-kpi-grid">
              <div className="ui-kpi-card">
                <div className="ui-kpi-title">状态</div>
                {status || '-'}
              </div>
              <div className="ui-kpi-card">
                <div className="ui-kpi-title">样本进度</div>
                {runStatus.progress?.finished_samples || 0}/{runStatus.progress?.total_samples || 0}
              </div>
              <div className="ui-kpi-card">
                <div className="ui-kpi-title">Recall@5</div>
                {Number(overview['recall@5'] || 0).toFixed(4)}
              </div>
              <div className="ui-kpi-card">
                <div className="ui-kpi-title">MRR</div>
                {Number(overview.mrr || 0).toFixed(4)}
              </div>
              <div className="ui-kpi-card">
                <div className="ui-kpi-title">Pass Rate</div>
                {Number(overview.pass_rate || 0).toFixed(4)}
              </div>
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
              {samples.length === 0 ? (
                <tr>
                  <td colSpan={9} className="text-center text-muted">暂无样本结果</td>
                </tr>
              ) : samples.map((sample) => (
                <tr key={sample.id}>
                  <td>{sample.sample_id}</td>
                  <td className="rag-report-query-cell">{String(sample.sample_query || '').slice(0, 100)}</td>
                  <td>{sample.first_hit_rank ?? '-'}</td>
                  <td>{sample.recall_hit ? 'Y' : 'N'}</td>
                  <td>{sample.answer_correct ? 'Y' : 'N'}</td>
                  <td>{Number(sample.faithfulness_score ?? 0).toFixed(3)}</td>
                  <td>{sample.failure_reason || 'pass'}</td>
                  <td>{Number(sample.latency_ms || 0).toFixed(1)}</td>
                  <td className="d-flex gap-2 flex-wrap rag-report-action-cell">
                    <Button size="sm" variant="outline-secondary" onClick={() => openDetail(sample, 'retrieved')}>召回</Button>
                    <Button size="sm" variant="outline-secondary" onClick={() => openDetail(sample, 'reranked')}>重排</Button>
                    <Button size="sm" variant="outline-secondary" onClick={() => openDetail(sample, 'context')}>上下文</Button>
                    <Button size="sm" variant="outline-secondary" onClick={() => openDetail(sample, 'prompt')}>Prompt</Button>
                    <Button size="sm" variant="outline-secondary" onClick={() => openDetail(sample, 'full')}>详情</Button>
                    <Button
                      size="sm"
                      variant="outline-warning"
                      onClick={() => void handlePromote(sample.sample_id, 'challenge')}
                    >
                      加入挑战集
                    </Button>
                    <Button
                      size="sm"
                      variant="outline-info"
                      onClick={() => void handlePromote(sample.sample_id, 'regression')}
                    >
                      加入回归集
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>

        <div className="d-flex gap-2 rag-report-pagination mt-2">
          <Button
            size="sm"
            variant="outline-secondary"
            disabled={!runId || page <= 1}
            onClick={() => setPage((value) => value - 1)}
          >
            上一页
          </Button>
          <Button
            size="sm"
            variant="outline-secondary"
            disabled={!runId || page * PAGE_SIZE >= total}
            onClick={() => setPage((value) => value + 1)}
          >
            下一页
          </Button>
          <span className="small text-muted align-self-center">共 {total} 条</span>
        </div>
      </div>

      <div className="d-flex flex-column gap-2 rag-report-card ui-section-card">
        <div className="ui-section-title-row">
          <div className="ui-section-title mb-0">评测运行对比</div>
          <Button size="sm" variant="outline-secondary" onClick={() => setCompareOpen((value) => !value)}>
            {compareOpen ? '收起' : '展开'}
          </Button>
        </div>
        <Collapse in={compareOpen}>
          <div className="pt-2">
            <RagRunComparePanel onLog={onLog} currentRunId={runId} />
          </div>
        </Collapse>
      </div>

      <div className="d-flex flex-column gap-2 rag-report-card ui-section-card">
        <div className="ui-section-title-row">
          <div className="ui-section-title mb-0">候选回流</div>
          <Button size="sm" variant="outline-secondary" onClick={() => setCandidateOpen((value) => !value)}>
            {candidateOpen ? '收起' : '展开'}
          </Button>
        </div>
        <Collapse in={candidateOpen}>
          <div className="pt-2">
            <RagCandidatePanel onLog={onLog} currentRunId={runId} />
          </div>
        </Collapse>
      </div>

      <Modal show={Boolean(detail)} onHide={() => setDetail(null)} size="lg" className="rag-detail-modal">
        <Modal.Header closeButton>
          <Modal.Title>{detail?.title}</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          <Form.Control as="textarea" rows={18} readOnly value={detail?.content || ''} />
        </Modal.Body>
      </Modal>
    </div>
  );
}
