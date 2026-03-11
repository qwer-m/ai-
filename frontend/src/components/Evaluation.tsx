import { useState, useEffect } from 'react';
import type { ClipboardEvent, ChangeEvent } from 'react';
import { Button, Form, OverlayTrigger, Popover, Toast, ToastContainer, Row, Col } from 'react-bootstrap';
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend, ArcElement, PointElement, LineElement } from 'chart.js';
import { Line } from 'react-chartjs-2';
import { FaClipboardCheck, FaDownload, FaRobot, FaNetworkWired, FaCheckDouble, FaBug, FaPlus } from 'react-icons/fa';
import { api } from '../utils/api';

ChartJS.register(CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend, ArcElement, PointElement, LineElement);

type Props = {
  projectId: number | null;
  logs: any[]; // LogEntry[] 但此处为了解析进行了简化
  onLog: (msg: string) => void;
  view?: 'root' | 'testcase' | 'ui' | 'api';
  // 来自父组件的共享状态属性
  evalGenerated: string;
  setEvalGenerated: (v: string) => void;
  evalModified: string;
  setEvalModified: (v: string) => void;
  evalResult: string | null;
  setEvalResult: (v: string | null) => void;
  recallRetrieved: string;
  setRecallRetrieved: (v: string) => void;
  recallRelevant: string;
  setRecallRelevant: (v: string) => void;
  recallResult: string | null;
  setRecallResult: (v: string | null) => void;
  uiEvalScript: string;
  setUiEvalScript: (v: string) => void;
  uiEvalExec: string;
  setUiEvalExec: (v: string) => void;
  uiEvalOutput: string | null;
  setUiEvalOutput: (v: string | null) => void;
  apiEvalScript: string;
  setApiEvalScript: (v: string) => void;
  apiEvalExec: string;
  setApiEvalExec: (v: string) => void;
  apiEvalOutput: string | null;
  setApiEvalOutput: (v: string | null) => void;
  shouldAutoEval?: boolean;
  setShouldAutoEval?: (v: boolean) => void;
};

export function Evaluation({ 
  projectId, logs, onLog,
  view = 'root',
  evalGenerated, setEvalGenerated,
  evalModified, setEvalModified,
  evalResult, setEvalResult,
  uiEvalScript, setUiEvalScript,
  uiEvalExec, setUiEvalExec,
  uiEvalOutput, setUiEvalOutput,
  apiEvalScript, setApiEvalScript,
  apiEvalExec, setApiEvalExec,
  apiEvalOutput, setApiEvalOutput,
  shouldAutoEval, setShouldAutoEval
}: Props) {
  
  const [loading, setLoading] = useState<string | null>(null); // 'eval', 'recall', 'ui', 'api'
  const [file, setFile] = useState<File | null>(null);
  const [showSupplement, setShowSupplement] = useState(false);
  const [supplementText, setSupplementText] = useState('');
  const [supplementImages, setSupplementImages] = useState<File[]>([]);
  
  // 历史记录状态
  const [history, setHistory] = useState<any[]>([]);
  const [genHistory, setGenHistory] = useState<any[]>([]);
  const [savedDocId, setSavedDocId] = useState<number | null>(null);
  const [lastSavedContent, setLastSavedContent] = useState<string>('');
  const [toastMsg, setToastMsg] = useState<{ type: 'success' | 'error', msg: string } | null>(null);

  // 加载最新的补充说明（仅在挂载或项目变更时，而非新的评估结果时）
  useEffect(() => {
    if (projectId) {
        api.get(`/api/evaluation/latest-supplement/${projectId}`).then((res: any) => {
             if (res.found) {
                 setSavedDocId(res.doc_id);
                 setSupplementText(res.supplement);
                 setLastSavedContent(res.supplement);
             } else {
                 setSavedDocId(null);
                 setSupplementText('');
                 setLastSavedContent('');
             }
        });
    }
  }, [projectId]); // 移除了 evalResult 依赖

  useEffect(() => {
    if (projectId) {
        api.get(`/api/evaluation/history/${projectId}`).then((res: any) => {
            if (res.history) setHistory(res.history);
        });
    }
  }, [projectId, evalResult]);

  useEffect(() => {
    if (projectId && view === 'testcase') {
        api.get(`/api/test-generations?project_id=${projectId}`).then((res: any) => {
             if (Array.isArray(res)) setGenHistory(res);
        });
    }
  }, [projectId, view]);

  // 中文注释：用户补充描述最多允许上传或粘贴10张图片
  const maxSupplementImages = 10;

  // 中文注释：错误管理前置处理，统一抽取可读错误文本
  const getErrorText = (error: any) => {
    if (!error) return '';
    if (typeof error === 'string') return error;
    if (error?.data?.error) return String(error.data.error);
    if (error?.data?.detail) return String(error.data.detail);
    if (error?.data?.message) return String(error.data.message);
    if (error?.message) return String(error.message);
    try {
      return JSON.stringify(error);
    } catch {
      return String(error);
    }
  };

  // 中文注释：错误管理Agent调用，将错误统一转为中文提示
  const translateError = async (error: any) => {
    const raw = getErrorText(error);
    try {
      const res = await api.post<any>('/api/error/translate', { error: raw });
      return res?.message ? String(res.message) : raw;
    } catch {
      return raw;
    }
  };

  // 中文注释：补充描述图片入列与数量上限控制
  const addSupplementImages = (files: File[]) => {
    if (!files.length) return;
    const imageFiles = files.filter(f => f.type.startsWith('image/'));
    const nonImages = files.filter(f => !f.type.startsWith('image/'));
    if (nonImages.length > 0) {
      setToastMsg({ type: 'error', msg: '仅支持图片文件' });
    }
    if (imageFiles.length === 0) return;
    setSupplementImages(prev => {
      const next = [...prev];
      for (const img of imageFiles) {
        if (next.length >= maxSupplementImages) break;
        next.push(img);
      }
      if (prev.length + imageFiles.length > maxSupplementImages) {
        setToastMsg({ type: 'error', msg: `最多只能上传${maxSupplementImages}张图片` });
      }
      return next;
    });
  };

  // 中文注释：支持在补充描述文本框内粘贴图片
  const handleSupplementPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const items = Array.from(e.clipboardData?.items || []);
    const imageFiles = items
      .filter(item => item.type.startsWith('image/'))
      .map(item => item.getAsFile())
      .filter(Boolean) as File[];
    if (imageFiles.length > 0) {
      e.preventDefault();
      addSupplementImages(imageFiles);
    }
  };

  // 中文注释：支持多图上传并复用统一校验逻辑
  const handleSupplementFilesChange = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length > 0) addSupplementImages(files);
    e.target.value = '';
  };

  // 中文注释：保存补充描述时携带图片附件并展示中文错误
  const handleSaveKnowledge = async (defectAnalysis: any) => {
      if (!projectId) return alert('请先选择项目');
      try {
          const formData = new FormData();
          formData.append('project_id', String(projectId));
          formData.append('defect_analysis', JSON.stringify(defectAnalysis));
          formData.append('user_supplement', supplementText);
          if (supplementImages.length > 0) {
              supplementImages.forEach(f => formData.append('files', f));
          }
          if (savedDocId) {
             formData.append('doc_id', String(savedDocId));
          }
          
          const res = await api.upload<any>('/api/evaluation/save-knowledge', formData);
          if (res.success) {
              onLog('已将缺陷分析和用户补充录入知识库');
              setSavedDocId(res.result.id);
              setLastSavedContent(supplementText);
              setSupplementImages([]);
              setToastMsg({ type: 'success', msg: '当前评估与描述已录入RAG知识库' });
              // Do NOT close window automatically
          }
      } catch (e) {
          const msg = await translateError(e);
          setToastMsg({ type: 'error', msg });
      }
  };

  const showRoot = view === 'root';
  const showTestcase = view === 'testcase';
  const showUi = view === 'ui';
  const showApi = view === 'api';

  const pickLatestByPrefix = (prefix: string) => {
    const matches = (Array.isArray(logs) ? logs : []).filter((x: any) => typeof x?.message === 'string' && x.message.startsWith(prefix));
    if (matches.length === 0) return null;
    let best = matches[0];
    let bestTime = new Date(best?.created_at || 0).getTime() || 0;
    for (let i = 1; i < matches.length; i++) {
      const t = new Date(matches[i]?.created_at || 0).getTime() || 0;
      if (t >= bestTime) {
        best = matches[i];
        bestTime = t;
      }
    }
    return best;
  };

  const latestDiag = pickLatestByPrefix('GEN_DIAG:');
  const diag = latestDiag ? JSON.parse(latestDiag.message.slice('GEN_DIAG:'.length)) : null;

  const latestQm = pickLatestByPrefix('GEN_QM:');
  const qm = latestQm ? JSON.parse(latestQm.message.slice('GEN_QM:'.length)) : null;

  const compareTestCases = async () => {
    if (!projectId) return alert('请先选择项目');
    if (!evalGenerated || (!evalModified && !file)) return alert('请填写测试用例内容或上传文件');
    
    // 为新的评估清除先前的上下文
    setSavedDocId(null);
    setSupplementText('');
    setLastSavedContent('');
    setSupplementImages([]);
    
    setLoading('eval');
    setEvalResult(null);
    onLog('评估测试用例质量...');
    try {
      const formData = new FormData();
      formData.append('generated_test_case', evalGenerated);
      if (evalModified) formData.append('modified_test_case', evalModified);
      formData.append('project_id', String(projectId));
      if (file) formData.append('file', file);

      const data = await api.upload<any>('/api/compare-test-cases', formData);
      setEvalResult(data.result || '');
    } catch (e) {
      // 中文注释：评估失败时通过错误管理Agent输出中文错误
      const msg = await translateError(e);
      setEvalResult(msg);
    } finally {
      setLoading(null);
    }
  };

  useEffect(() => {
    if (shouldAutoEval && evalGenerated && !loading && setShouldAutoEval) {
        onLog('测试用例生成完毕，自动触发质量评估...');
        compareTestCases();
        setShouldAutoEval(false);
    }
  }, [shouldAutoEval, evalGenerated, loading]);

  const [uiEvalJourney, setUiEvalJourney] = useState<string>('');
  const [apiEvalSpec, setApiEvalSpec] = useState<string>('');

  const evaluateUi = async () => {
    if (!projectId) return alert('请先选择项目');
    setLoading('ui');
    setUiEvalOutput(null);
    onLog('评估 UI 自动化...');
    try {
      const data = await api.post<any>('/api/evaluate-ui-automation', {
        script: uiEvalScript,
        execution_result: uiEvalExec,
        project_id: projectId,
        journey_json: uiEvalJourney || undefined
      });
      setUiEvalOutput(data.result || '');
    } catch (e) {
      // 中文注释：UI评估失败时通过错误管理Agent输出中文错误
      const msg = await translateError(e);
      setUiEvalOutput(msg);
    } finally {
      setLoading(null);
    }
  };

  const evaluateApi = async () => {
    if (!projectId) return alert('请先选择项目');
    setLoading('api');
    setApiEvalOutput(null);
    onLog('评估接口测试...');
    try {
      const data = await api.post<any>('/api/evaluate-api-test', {
        script: apiEvalScript,
        execution_result: apiEvalExec,
        project_id: projectId,
        openapi_spec: apiEvalSpec || undefined
      });
      setApiEvalOutput(data.result || '');
    } catch (e) {
      // 中文注释：接口评估失败时通过错误管理Agent输出中文错误
      const msg = await translateError(e);
      setApiEvalOutput(msg);
    } finally {
      setLoading(null);
    }
  };

  const exportHistory = async () => {
    if (!projectId) return alert('请先选择项目');
    try {
        const logs = await api.get<any[]>(`/api/logs/${projectId}`);
        const qmLogs = logs.filter((l: any) => typeof l.message === 'string' && l.message.startsWith('GEN_QM:')).slice(0, 50);
        if (qmLogs.length === 0) return alert('暂无历史质量指标');
        const header = ['created_at','positive','negative','edge','avg_steps','pending','generated_count'];
        const rows = qmLogs.map((l: any) => {
            let qm: any = {};
            try { qm = JSON.parse(l.message.substring('GEN_QM:'.length)); } catch {}
            const ts = l.created_at || '';
            return [ts, qm.positive||0, qm.negative||0, qm.edge||0, qm.avg_steps||0, qm.pending||0, qm.generated_count||0].join(',');
        });
        const csv = header.join(',') + '\n' + rows.join('\n');
        const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'quality_metrics_history.csv';
        document.body.appendChild(a);
        a.click();
        a.remove();
        onLog('已导出历史质量指标');
    } catch (e) {
        alert(`导出失败: ${e}`);
    }
  };

  return (
    <div className="bento-grid h-100 align-content-start">
      {showRoot && (
        <>
          <div className="bento-card col-span-12 p-4 d-flex align-items-center justify-content-between glass-panel">
            <h4 className="text-gradient mb-0 d-flex align-items-center gap-2">
              <FaClipboardCheck className="text-primary" />
              质量评估与召回
            </h4>
          </div>

          <div className="bento-card col-span-12 p-0 border-0 bg-transparent">
            <div className="d-flex flex-column flex-md-row gap-3">
              <div className="bento-card p-4 d-flex flex-column hover-lift flex-fill">
                <div className="d-flex align-items-center gap-2 mb-4 text-secondary">
                     <FaBug />
                     <span className="fw-bold">最新生成诊断</span>
                </div>
                <div className="grid grid-cols-2 gap-3 small">
                    <div className="p-2 bg-light rounded"><span className="text-muted d-block">模式</span> {String(diag?.mode ?? '-')}</div>
                    <div className="p-2 bg-light rounded"><span className="text-muted d-block">类型</span> {String(diag?.doc_type ?? '-')}</div>
                    <div className="p-2 bg-light rounded"><span className="text-muted d-block">压缩</span> {String(diag?.compress ?? '-')}</div>
                    <div className="p-2 bg-light rounded"><span className="text-muted d-block">预期数量</span> {String(diag?.expected_count ?? '-')}</div>
                    <div className="p-2 bg-light rounded"><span className="text-muted d-block">生成数量</span> {String(diag?.generated_count ?? '-')}</div>
                    <div className="p-2 bg-light rounded"><span className="text-muted d-block">模型</span> {String(diag?.model ?? '-')}</div>
                </div>
              </div>

              <div className="bento-card p-4 d-flex flex-column hover-lift flex-fill">
                <div className="d-flex justify-content-between align-items-center mb-4">
                    <div className="d-flex align-items-center gap-2 text-secondary">
                         <FaCheckDouble />
                         <span className="fw-bold">最新质量指标</span>
                    </div>
                    <Button variant="link" size="sm" className="p-0 text-decoration-none d-flex align-items-center gap-1" onClick={exportHistory}>
                        <FaDownload size={12} /> 导出
                    </Button>
                </div>
                <div className="grid grid-cols-3 gap-3 small">
                    <div className="p-2 bg-success bg-opacity-10 text-success rounded text-center">
                        <div className="fw-bold fs-5">{String(qm?.positive ?? '-')}</div>
                        <div className="small opacity-75">正向</div>
                    </div>
                    <div className="p-2 bg-danger bg-opacity-10 text-danger rounded text-center">
                        <div className="fw-bold fs-5">{String(qm?.negative ?? '-')}</div>
                        <div className="small opacity-75">负向</div>
                    </div>
                    <div className="p-2 bg-warning bg-opacity-10 text-warning rounded text-center">
                        <div className="fw-bold fs-5">{String(qm?.edge ?? '-')}</div>
                        <div className="small opacity-75">边界</div>
                    </div>
                    <div className="p-2 bg-info bg-opacity-10 text-info rounded text-center">
                        <div className="fw-bold fs-5">{String(qm?.avg_steps ?? '-')}</div>
                        <div className="small opacity-75">平均步骤</div>
                    </div>
                    <div className="p-2 bg-secondary bg-opacity-10 text-secondary rounded text-center">
                        <div className="fw-bold fs-5">{String(qm?.pending ?? '-')}</div>
                        <div className="small opacity-75">待确认</div>
                    </div>
                    <div className="p-2 bg-primary bg-opacity-10 text-primary rounded text-center">
                        <div className="fw-bold fs-5">{String(qm?.generated_count ?? '-')}</div>
                        <div className="small opacity-75">生成总数</div>
                    </div>
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {/* Test Case Eval */}
      {showTestcase && (
        <div className="bento-card col-span-12 p-4 d-flex flex-column">
          <div className="d-flex align-items-center gap-2 mb-3 text-secondary">
              <FaClipboardCheck />
              <span className="fw-bold">测试用例质量评估</span>
          </div>
          <div className="mb-3">
            <Row className="mb-3">
                <Col md={6}>
                    <Form.Group>
                        <Form.Label className="small text-muted">生成的测试用例</Form.Label>
                        <Form.Control as="textarea" rows={10} className="input-pro bg-light" value={evalGenerated} onChange={e => setEvalGenerated(e.target.value)} />
                    </Form.Group>
                </Col>
                <Col md={6}>
                    <Form.Group>
                        <Form.Label className="small text-muted">用户修改后的测试用例</Form.Label>
                        <Form.Control as="textarea" rows={10} className="input-pro bg-light" value={evalModified} onChange={e => setEvalModified(e.target.value)} placeholder="可以直接输入文本..." />
                    </Form.Group>
                </Col>
            </Row>

            <Row className="align-items-end">
                <Col md={6}>
                    <Form.Group>
                        <Form.Label className="small text-muted">从历史加载</Form.Label>
                        <Form.Select 
                            size="sm" 
                            className="input-pro bg-white"
                            onChange={async (e) => {
                                const id = Number(e.target.value);
                                if (!id) return;
                                try {
                                    const res = await api.get<any>(`/api/test-generations/${id}`);
                                    if (res) {
                                        let content = res;
                                        if (res.generated_result) content = res.generated_result;
                                        else if (typeof res === 'string') content = res;
                                        
                                        if (typeof content === 'string') {
                                            try {
                                                const parsed = JSON.parse(content);
                                                setEvalGenerated(JSON.stringify(parsed, null, 2));
                                            } catch {
                                                setEvalGenerated(content);
                                            }
                                        } else {
                                             setEvalGenerated(JSON.stringify(content, null, 2));
                                        }
                                    }
                                } catch (e) {
                                    console.error(e);
                                }
                            }}
                        >
                            <option value="">-- 选择历史记录 --</option>
                            {genHistory.map((h: any) => {
                                const rawTitle = (h.requirement_text || '').split(/[\n|]/)[0].trim();
                                const displayTitle = rawTitle.length > 20 ? rawTitle.substring(0, 20) + '...' : rawTitle;
                                return (
                                    <option key={h.id} value={h.id}>
                                        {displayTitle} ({new Date(h.created_at).toLocaleString()})
                                    </option>
                                );
                            })}
                        </Form.Select>
                    </Form.Group>
                </Col>
                <Col md={6}>
                    <Form.Group>
                        <Form.Label className="small text-muted">或上传文件 (Excel, CSV, PNG)</Form.Label>
                        <Form.Control 
                            type="file" 
                            size="sm"
                            accept=".xlsx,.xls,.csv,.png"
                            className="input-pro"
                            onChange={(e) => {
                                const target = e.target as HTMLInputElement;
                                if (target.files && target.files.length > 0) {
                                    setFile(target.files[0]);
                                } else {
                                    setFile(null);
                                }
                            }}
                        />
                    </Form.Group>
                </Col>
            </Row>
          </div>
          <Button className="btn-pro-primary w-100 mt-auto" disabled={loading === 'eval'} onClick={compareTestCases}>
            {loading === 'eval' ? '评估中...' : '开始评估质量 (含召回率/精确率/缺陷分析)'}
          </Button>
          {evalResult && (
              <div className="mt-3 alert alert-light border small">
                {(() => {
                  try {
                    let jsonStr = evalResult.trim();
                    // 尝试从 markdown 代码块中提取 JSON
                    const match = jsonStr.match(/```json\s*([\s\S]*?)\s*```/) || jsonStr.match(/```\s*([\s\S]*?)\s*```/);
                    if (match) {
                        jsonStr = match[1];
                    }
                    // 查找第一个 '{' 和最后一个 '}'
                    const firstOpen = jsonStr.indexOf('{');
                    const lastClose = jsonStr.lastIndexOf('}');
                    if (firstOpen !== -1 && lastClose !== -1) {
                        jsonStr = jsonStr.substring(firstOpen, lastClose + 1);
                        const res = JSON.parse(jsonStr);
                        const m = res.metrics || {};
                        const d = res.defect_analysis || {};
                        return (
                          <div>
                            <h6 className="border-bottom pb-2 mb-3">质量评估报告</h6>
                            
                            <div className="mb-4" style={{ height: '300px' }}>
                                 <Line data={{
                                     labels: history.length > 0 ? history.map(h => h.created_at) : ['Current'],
                                     datasets: [
                                         {
                                             label: 'Precision',
                                             data: history.length > 0 ? history.map(h => h.precision) : [m.precision],
                                             borderColor: '#0d6efd',
                                             tension: 0.1,
                                             pointRadius: 3,
                                             borderWidth: 2,
                                             hoverBorderWidth: 4,
                                         },
                                         {
                                             label: 'Recall',
                                             data: history.length > 0 ? history.map(h => h.recall) : [m.recall],
                                             borderColor: '#198754',
                                             tension: 0.1,
                                             pointRadius: 3,
                                             borderWidth: 2,
                                             hoverBorderWidth: 4,
                                         },
                                         {
                                             label: 'F1 分数',
                                             data: history.length > 0 ? history.map(h => h.f1_score) : [m.f1_score],
                                             borderColor: '#6f42c1',
                                             tension: 0.1,
                                             pointRadius: 3,
                                             borderWidth: 2,
                                             hoverBorderWidth: 4,
                                         },
                                         {
                                             label: '相似度',
                                             data: history.length > 0 ? history.map(h => h.semantic_similarity) : [m.semantic_similarity],
                                             borderColor: '#fd7e14',
                                             tension: 0.1,
                                             pointRadius: 3,
                                             borderWidth: 2,
                                             hoverBorderWidth: 4,
                                         }
                                     ]
                                 }} options={{
                                     responsive: true,
                                     maintainAspectRatio: false,
                                     interaction: {
                                         mode: 'nearest',
                                         intersect: true,
                                         axis: 'x'
                                     },
                                     plugins: {
                                         legend: { display: true, position: 'top', labels: { boxWidth: 10, usePointStyle: true, padding: 10, font: { size: 10 } } },
                                         title: { display: true, text: '质量评估历史趋势' },
                                         tooltip: {
                                             mode: 'index',
                                             intersect: false,
                                         }
                                     },
                                     scales: {
                                         y: {
                                             type: 'linear',
                                             display: true,
                                             position: 'left',
                                             title: { display: true, text: '评分 (0-1)' },
                                             min: 0,
                                             max: 1,
                                             beginAtZero: true,
                                             grid: { drawOnChartArea: true }
                                         },
                                         // Remove y1 axis as we don't have defect counts anymore
                                     }
                                 }} />
                            </div>

                            <div className="d-flex gap-2 mb-3 text-center">
                                <div className="p-2 bg-white border rounded flex-fill">
                                    <div className="fw-bold text-primary">{typeof m.precision === 'number' ? m.precision.toFixed(2) : '-'}</div>
                                    <div className="x-small text-muted">精确率</div>
                                </div>
                                <div className="p-2 bg-white border rounded flex-fill">
                                    <div className="fw-bold text-primary">{typeof m.recall === 'number' ? m.recall.toFixed(2) : '-'}</div>
                                    <div className="x-small text-muted">召回率</div>
                                </div>
                                <div className="p-2 bg-white border rounded flex-fill">
                                    <div className="fw-bold text-primary">{typeof m.f1_score === 'number' ? m.f1_score.toFixed(2) : '-'}</div>
                                    <div className="x-small text-muted">F1 分数</div>
                                </div>
                                <div className="p-2 bg-white border rounded flex-fill">
                                    <div className="fw-bold text-primary">{typeof m.semantic_similarity === 'number' ? m.semantic_similarity.toFixed(2) : '-'}</div>
                                    <div className="x-small text-muted">语义相似度</div>
                                </div>
                            </div>

                            <div className="d-flex align-items-center justify-content-between mb-2">
                                <strong>缺陷归因分析:</strong>
                                <OverlayTrigger
                                    trigger="click"
                                    placement="left"
                                    show={showSupplement}
                                    onToggle={(next) => setShowSupplement(next)}
                                    rootClose
                                    overlay={
                                        <Popover id="popover-supplement" style={{ maxWidth: '400px', width: '350px' }}>
                                            <Popover.Header as="h3">用户补充描述</Popover.Header>
                                            <Popover.Body>
                                                <Form.Group className="mb-2">
                                                    <Form.Control 
                                                        as="textarea" 
                                                        rows={3} 
                                                        placeholder="请输入补充描述..." 
                                                        value={supplementText}
                                                        onChange={e => setSupplementText(e.target.value)}
                                                        onPaste={handleSupplementPaste}
                                                    />
                                                </Form.Group>
                                                <Form.Group className="mb-3">
                                                    <Form.Label className="small text-muted">导入图片（最多10张）</Form.Label>
                                                    <Form.Control 
                                                        type="file" 
                                                        size="sm"
                                                        accept="image/*"
                                                        multiple
                                                        onChange={handleSupplementFilesChange}
                                                    />
                                                    {supplementImages.length > 0 && (
                                                        <div className="mt-2 d-flex flex-wrap gap-2">
                                                            {supplementImages.map((f, idx) => (
                                                                <div key={idx} className="border rounded p-1 small bg-white">
                                                                    <span className="text-muted">{f.name}</span>
                                                                </div>
                                                            ))}
                                                        </div>
                                                    )}
                                                </Form.Group>
                                                <div className="d-flex justify-content-end gap-2">
                                                    <Button variant="secondary" size="sm" onClick={() => { setShowSupplement(false); setSupplementText(''); setSupplementImages([]); }}>取消</Button>
                                                    <Button 
                                                        variant="primary" 
                                                        size="sm" 
                                                        onClick={() => handleSaveKnowledge(d)}
                                                        disabled={(!supplementText.trim() && supplementImages.length === 0) || (savedDocId !== null && supplementText === lastSavedContent && supplementImages.length === 0)}
                                                    >
                                                        确定
                                                    </Button>
                                                </div>
                                            </Popover.Body>
                                        </Popover>
                                    }
                                >
                                    <Button variant="outline-secondary" size="sm" className="py-0 px-2" onClick={() => setShowSupplement(!showSupplement)}>
                                        <FaPlus className="me-1" /> 用户补充描述
                                    </Button>
                                </OverlayTrigger>
                            </div>
                            
                            {d.missing_points?.length > 0 && (
                              <div className="mb-2">
                                  <span className="badge bg-warning text-dark me-2">遗漏点（召回损失）</span>
                                  <ul className="mb-1 ps-3 mt-1 text-muted">
                                      {d.missing_points.map((x:any, i:number) => <li key={i}>{x}</li>)}
                                  </ul>
                              </div>
                            )}
                             {d.hallucinations?.length > 0 && (
                              <div className="mb-2">
                                  <span className="badge bg-danger text-white me-2">幻觉/多余（精度损失）</span>
                                  <ul className="mb-1 ps-3 mt-1 text-muted">
                                      {d.hallucinations.map((x:any, i:number) => <li key={i}>{x}</li>)}
                                  </ul>
                              </div>
                            )}
                             {d.modifications?.length > 0 && (
                              <div className="mb-2">
                                  <span className="badge bg-info text-white me-2">逻辑修正</span>
                                  <ul className="mb-1 ps-3 mt-1 text-muted">
                                      {d.modifications.map((x:any, i:number) => <li key={i}>{x}</li>)}
                                  </ul>
                              </div>
                            )}
                            
                            <div className="mt-3 pt-2 border-top text-secondary">
                               <strong>总结:</strong> {res.summary}
                            </div>
                            <div className="mt-2 text-end text-muted x-small">
                                <FaRobot className="me-1" /> 缺陷归因分析由 AI 模型生成
                            </div>
                          </div>
                        );
                    }
                  } catch (e) {
                    // console.error(e);
                  }
                  return <div style={{ whiteSpace: 'pre-wrap' }}>{evalResult}</div>;
                })()}
              </div>
           )}
        </div>
      )}

      {/* UI & API Eval */}
      {showUi && (
        <div className="bento-card col-span-12 md:col-span-6 p-4 d-flex flex-column">
          <div className="d-flex align-items-center gap-2 mb-3 text-secondary">
              <FaRobot />
              <span className="fw-bold">UI 自动化评估</span>
          </div>
          <Form.Group className="mb-3">
            <Form.Label className="small text-muted">UI 自动化脚本</Form.Label>
            <Form.Control as="textarea" rows={3} className="input-pro bg-light" value={uiEvalScript} onChange={e => setUiEvalScript(e.target.value)} placeholder="Python Playwright/Selenium 脚本..." />
          </Form.Group>
          <Form.Group className="mb-3">
             <Form.Label className="small text-muted">用户旅程图 (JSON) - 黄金标准</Form.Label>
             <Form.Control 
                as="textarea" 
                rows={3} 
                className="input-pro bg-light" 
                value={uiEvalJourney} 
                onChange={e => setUiEvalJourney(e.target.value)} 
                placeholder={'{"user_journey": [{"step": "Login", "action": "click(\'#login\')"}]}'}
             />
             <Form.Text className="text-muted x-small">
               评估 AI 生成的脚本是否覆盖了您定义的关键用户旅程（如登录、支付）。
             </Form.Text>
          </Form.Group>
          <Form.Group className="mb-3">
            <Form.Label className="small text-muted">执行结果</Form.Label>
            <Form.Control as="textarea" rows={3} className="input-pro bg-light" value={uiEvalExec} onChange={e => setUiEvalExec(e.target.value)} placeholder="执行日志或输出..." />
          </Form.Group>
          <Button className="btn-pro-primary w-100 mt-auto" disabled={loading === 'ui'} onClick={evaluateUi}>
            {loading === 'ui' ? '评估中...' : '开始评估'}
          </Button>
          {uiEvalOutput && <div className="mt-3 alert alert-light border small" style={{ whiteSpace: 'pre-wrap' }}>{uiEvalOutput}</div>}
        </div>
      )}

      {showApi && (
        <div className="bento-card col-span-12 md:col-span-6 p-4 d-flex flex-column">
            <div className="d-flex align-items-center gap-2 mb-3 text-secondary">
              <FaNetworkWired />
              <span className="fw-bold">接口自动化评估（AI 响应评估）</span>
            </div>
            <Form.Group className="mb-3">
                <Form.Label className="small text-muted">API 测试脚本</Form.Label>
                <Form.Control as="textarea" rows={3} className="input-pro bg-light" value={apiEvalScript} onChange={e => setApiEvalScript(e.target.value)} placeholder="Pytest 脚本..." />
            </Form.Group>
            <Form.Group className="mb-3">
                 <Form.Label className="small text-muted">OpenAPI 规范（Swagger）- 黄金标准</Form.Label>
                 <Form.Control 
                    as="textarea" 
                    rows={3} 
                    className="input-pro bg-light" 
                    value={apiEvalSpec} 
                    onChange={e => setApiEvalSpec(e.target.value)} 
                    placeholder={'请输入 OpenAPI/Swagger JSON 或 YAML 内容...'}
                 />
                 <Form.Text className="text-muted x-small">
                   用于评估 AI 脚本的 API 端点覆盖率及参数正确性。
                 </Form.Text>
            </Form.Group>
            <Form.Group className="mb-3">
                <Form.Label className="small text-muted">执行结果</Form.Label>
                <Form.Control as="textarea" rows={3} className="input-pro bg-light" value={apiEvalExec} onChange={e => setApiEvalExec(e.target.value)} placeholder="执行日志..." />
            </Form.Group>
            
            <div className="d-flex gap-2 mb-3">
                <div className="form-check form-switch">
                    <input className="form-check-input" type="checkbox" id="checkSimilarity" defaultChecked />
                    <label className="form-check-label small" htmlFor="checkSimilarity">语义相似度</label>
                </div>
                <div className="form-check form-switch">
                    <input className="form-check-input" type="checkbox" id="checkLLMJudge" defaultChecked />
                    <label className="form-check-label small" htmlFor="checkLLMJudge">LLM 评审打分</label>
                </div>
                <div className="form-check form-switch">
                    <input className="form-check-input" type="checkbox" id="checkCost" />
                    <label className="form-check-label small" htmlFor="checkCost">成本/性能分析</label>
                </div>
            </div>

            <Button className="btn-pro-primary w-100 mt-auto" disabled={loading === 'api'} onClick={evaluateApi}>
                {loading === 'api' ? '多维评估中...' : '开始评估'}
            </Button>
            {apiEvalOutput && (
                <div className="mt-3 alert alert-light border small">
                    {(() => {
                        try {
                             // Try to parse JSON result if available
                             const jsonStr = apiEvalOutput.match(/\{[\s\S]*\}/)?.[0];
                             if (jsonStr) {
                                 const res = JSON.parse(jsonStr);
                                 return (
                                     <div>
                                         <h6 className="border-bottom pb-2 mb-2">评估报告</h6>
                                         <div className="row g-2 mb-3">
                                             <div className="col-4">
                                                 <div className="p-2 bg-white border rounded text-center">
                                                     <div className="fw-bold text-primary">{res.similarity ?? '-'}</div>
                                                     <div className="x-small text-muted">语义相似度</div>
                                                 </div>
                                             </div>
                                             <div className="col-4">
                                                 <div className="p-2 bg-white border rounded text-center">
                                                     <div className="fw-bold text-success">{res.score ?? '-'}</div>
                                                     <div className="x-small text-muted">LLM 评分</div>
                                                 </div>
                                             </div>
                                             <div className="col-4">
                                                 <div className="p-2 bg-white border rounded text-center">
                                                     <div className="fw-bold text-info">{res.coverage ?? '-'}%</div>
                                                     <div className="x-small text-muted">API 覆盖率</div>
                                                 </div>
                                             </div>
                                         </div>
                                         <div><strong>分析:</strong> {res.analysis}</div>
                                     </div>
                                 );
                             }
                        } catch (e) {}
                        return <div style={{ whiteSpace: 'pre-wrap' }}>{apiEvalOutput}</div>;
                    })()}
                </div>
            )}
        </div>
      )}
      <ToastContainer position="top-end" className="p-3" style={{ zIndex: 1100 }}>
        {toastMsg && (
            <Toast onClose={() => setToastMsg(null)} show={!!toastMsg} delay={3000} autohide bg={toastMsg.type === 'success' ? 'success' : 'danger'}>
                <Toast.Header>
                    <strong className="me-auto">{toastMsg.type === 'success' ? '成功' : '错误'}</strong>
                </Toast.Header>
                <Toast.Body className="text-white">{toastMsg.msg}</Toast.Body>
            </Toast>
        )}
      </ToastContainer>
    </div>
  );
}
