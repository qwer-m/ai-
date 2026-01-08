import { useState, useEffect } from 'react';
import { Button, Form, Badge } from 'react-bootstrap';
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend } from 'chart.js';
import { Bar } from 'react-chartjs-2';
import { FaClipboardCheck, FaDownload, FaChartLine, FaRobot, FaNetworkWired, FaCheckDouble, FaBug, FaSearch } from 'react-icons/fa';
import { api } from '../utils/api';

ChartJS.register(CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend);

type Props = {
  projectId: number | null;
  logs: any[]; // LogEntry[] but simplified for parsing here
  onLog: (msg: string) => void;
  // Shared state props from parent
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
};

export function Evaluation({ 
  projectId, logs, onLog,
  evalGenerated, setEvalGenerated,
  evalModified, setEvalModified,
  evalResult, setEvalResult,
  recallRetrieved, setRecallRetrieved,
  recallRelevant, setRecallRelevant,
  recallResult, setRecallResult,
  uiEvalScript, setUiEvalScript,
  uiEvalExec, setUiEvalExec,
  uiEvalOutput, setUiEvalOutput,
  apiEvalScript, setApiEvalScript,
  apiEvalExec, setApiEvalExec,
  apiEvalOutput, setApiEvalOutput
}: Props) {
  
  const [loading, setLoading] = useState<string | null>(null); // 'eval', 'recall', 'ui', 'api'
  const [chartData, setChartData] = useState<any>(null);

  // Latest Diagnostics & QM
  const latestDiag = logs.find(x => typeof x.message === 'string' && x.message.startsWith('GEN_DIAG:'));
  const diag = latestDiag ? JSON.parse(latestDiag.message.slice('GEN_DIAG:'.length)) : null;

  const latestQm = logs.find(x => typeof x.message === 'string' && x.message.startsWith('GEN_QM:'));
  const qm = latestQm ? JSON.parse(latestQm.message.slice('GEN_QM:'.length)) : null;

  // Prepare Chart Data
  useEffect(() => {
    if (!projectId) return;
    const qmLogs = logs.filter(l => typeof l.message === 'string' && l.message.startsWith('GEN_QM:')).slice(0, 10);
    if (qmLogs.length === 0) {
        setChartData(null);
        return;
    }
    const labels = qmLogs.map((_, i) => `#${qmLogs.length - i}`).reverse();
    const genCounts = qmLogs.map(l => {
        try { return JSON.parse(l.message.substring('GEN_QM:'.length)).generated_count || 0; } catch { return 0; }
    }).reverse();
    
    setChartData({
        labels,
        datasets: [{
            label: '生成数量',
            data: genCounts,
            backgroundColor: '#0d6efd',
            borderRadius: 6,
        }]
    });
  }, [logs, projectId]);


  const compareTestCases = async () => {
    if (!projectId) return alert('请先选择项目');
    if (!evalGenerated || !evalModified) return alert('请填写测试用例内容');
    
    setLoading('eval');
    setEvalResult(null);
    onLog('评估测试用例质量...');
    try {
      const data = await api.post<any>('/api/compare-test-cases', { generated_test_case: evalGenerated, modified_test_case: evalModified, project_id: projectId });
      setEvalResult(data.result || '');
    } catch (e) {
      setEvalResult(`Error: ${e}`);
    } finally {
      setLoading(null);
    }
  };

  const calcRecall = async () => {
    if (!projectId) return alert('请先选择项目');
    setLoading('recall');
    setRecallResult(null);
    onLog('计算召回率...');
    try {
      const retrieved = recallRetrieved.split(',').map(s => s.trim()).filter(s => s);
      const relevant = recallRelevant.split(',').map(s => s.trim()).filter(s => s);
      const data = await api.post<any>('/api/calculate-recall', { retrieved, relevant, project_id: projectId });
      setRecallResult(`Recall Rate: ${data.recall}`);
    } catch (e) {
      setRecallResult(`Error: ${e}`);
    } finally {
      setLoading(null);
    }
  };

  const evaluateUi = async () => {
    if (!projectId) return alert('请先选择项目');
    setLoading('ui');
    setUiEvalOutput(null);
    onLog('评估 UI 自动化...');
    try {
      const data = await api.post<any>('/api/evaluate-ui-automation', {
        script: uiEvalScript,
        execution_result: uiEvalExec,
        project_id: projectId
      });
      setUiEvalOutput(data.result || '');
    } catch (e) {
      setUiEvalOutput(`Error: ${e}`);
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
        project_id: projectId
      });
      setApiEvalOutput(data.result || '');
    } catch (e) {
      setApiEvalOutput(`Error: ${e}`);
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
      {/* Header Info */}
      <div className="bento-card col-span-12 p-4 d-flex align-items-center justify-content-between glass-panel">
        <div className="d-flex align-items-center gap-3">
            <div className="bg-primary bg-opacity-10 p-3 rounded-circle text-primary">
                <FaClipboardCheck size={24} />
            </div>
            <div>
                <h4 className="mb-1 fw-bold text-gradient">质量评估与召回</h4>
                <div className="d-flex align-items-center gap-2 small">
                    <span className="text-secondary">当前项目:</span>
                    <Badge bg={projectId ? 'success' : 'secondary'} className="bg-opacity-10 text-reset fw-normal">
                        {projectId ? '已同步' : '未选择'}
                    </Badge>
                </div>
            </div>
        </div>
      </div>

      {/* Diagnostics & QM Cards */}
      <div className="bento-card col-span-12 md:col-span-6 p-4 d-flex flex-column hover-lift">
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

      <div className="bento-card col-span-12 md:col-span-6 p-4 d-flex flex-column hover-lift">
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
      
      {/* Chart */}
      {chartData && (
        <div className="bento-card col-span-12 p-4" style={{ height: '300px' }}>
             <div className="d-flex align-items-center gap-2 mb-3 text-secondary">
                 <FaChartLine />
                 <span className="fw-bold">生成数量历史趋势</span>
             </div>
             <div className="h-100 pb-4">
                <Bar data={chartData} options={{ responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }} />
             </div>
        </div>
      )}

      {/* Test Case Eval */}
      <div className="bento-card col-span-12 md:col-span-6 p-4 d-flex flex-column">
        <div className="d-flex align-items-center gap-2 mb-3 text-secondary">
            <FaClipboardCheck />
            <span className="fw-bold">测试用例质量评估</span>
        </div>
        <Form.Group className="mb-3">
          <Form.Label className="small text-muted">生成的测试用例</Form.Label>
          <Form.Control as="textarea" rows={4} className="input-pro bg-light" value={evalGenerated} onChange={e => setEvalGenerated(e.target.value)} />
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label className="small text-muted">用户修改后的测试用例</Form.Label>
          <Form.Control as="textarea" rows={4} className="input-pro bg-light" value={evalModified} onChange={e => setEvalModified(e.target.value)} />
        </Form.Group>
        <Button className="btn-pro-primary w-100 mt-auto" disabled={loading === 'eval'} onClick={compareTestCases}>
          {loading === 'eval' ? '评估中...' : '开始评估质量'}
        </Button>
        {evalResult && <div className="mt-3 alert alert-light border small" style={{ whiteSpace: 'pre-wrap' }}>{evalResult}</div>}
      </div>

      {/* Recall */}
      <div className="bento-card col-span-12 md:col-span-6 p-4 d-flex flex-column">
        <div className="d-flex align-items-center gap-2 mb-3 text-secondary">
            <FaSearch />
            <span className="fw-bold">召回率计算</span>
        </div>
        <Form.Group className="mb-3">
          <Form.Label className="small text-muted">检索到的项目 (逗号分隔)</Form.Label>
          <Form.Control as="textarea" rows={4} className="input-pro bg-light" value={recallRetrieved} onChange={e => setRecallRetrieved(e.target.value)} placeholder="项目1, 项目2" />
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Label className="small text-muted">相关项目 (逗号分隔)</Form.Label>
          <Form.Control as="textarea" rows={4} className="input-pro bg-light" value={recallRelevant} onChange={e => setRecallRelevant(e.target.value)} placeholder="项目1, 项目3" />
        </Form.Group>
        <Button className="btn-pro-primary w-100 mt-auto" disabled={loading === 'recall'} onClick={calcRecall}>
          {loading === 'recall' ? '计算中...' : '计算召回率'}
        </Button>
        {recallResult && <div className="mt-3 alert alert-light border small">{recallResult}</div>}
      </div>

      {/* UI & API Eval */}
      <div className="bento-card col-span-12 md:col-span-6 p-4 d-flex flex-column">
        <div className="d-flex align-items-center gap-2 mb-3 text-secondary">
            <FaRobot />
            <span className="fw-bold">UI 自动化评估</span>
        </div>
        <Form.Group className="mb-3">
          <Form.Control as="textarea" rows={3} className="input-pro bg-light" value={uiEvalScript} onChange={e => setUiEvalScript(e.target.value)} placeholder="脚本内容..." />
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Control as="textarea" rows={3} className="input-pro bg-light" value={uiEvalExec} onChange={e => setUiEvalExec(e.target.value)} placeholder="执行结果..." />
        </Form.Group>
        <Button className="btn-pro-primary w-100 mt-auto" disabled={loading === 'ui'} onClick={evaluateUi}>
          {loading === 'ui' ? '评估中...' : '开始评估'}
        </Button>
        {uiEvalOutput && <div className="mt-3 alert alert-light border small" style={{ whiteSpace: 'pre-wrap' }}>{uiEvalOutput}</div>}
      </div>

      <div className="bento-card col-span-12 md:col-span-6 p-4 d-flex flex-column">
        <div className="d-flex align-items-center gap-2 mb-3 text-secondary">
            <FaNetworkWired />
            <span className="fw-bold">接口测试评估</span>
        </div>
        <Form.Group className="mb-3">
          <Form.Control as="textarea" rows={3} className="input-pro bg-light" value={apiEvalScript} onChange={e => setApiEvalScript(e.target.value)} placeholder="脚本内容..." />
        </Form.Group>
        <Form.Group className="mb-3">
          <Form.Control as="textarea" rows={3} className="input-pro bg-light" value={apiEvalExec} onChange={e => setApiEvalExec(e.target.value)} placeholder="执行结果..." />
        </Form.Group>
        <Button className="btn-pro-primary w-100 mt-auto" disabled={loading === 'api'} onClick={evaluateApi}>
          {loading === 'api' ? '评估中...' : '开始评估'}
        </Button>
        {apiEvalOutput && <div className="mt-3 alert alert-light border small" style={{ whiteSpace: 'pre-wrap' }}>{apiEvalOutput}</div>}
      </div>
    </div>
  );
}
