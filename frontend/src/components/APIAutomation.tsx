import { useEffect, useMemo, useState } from 'react';
import { Alert, Badge, Button, Card, Form, Nav, Spinner, Table } from 'react-bootstrap';
import { FaLayerGroup, FaPlay, FaRedo } from 'react-icons/fa';
import { api } from '../utils/api';

type APIAutomationProps = {
  projectId: number | null;
  onLog: (msg: string) => void;
  view?: 'orchestration' | 'runner';
};

type APIHistoryItem = {
  id: number;
  requirement: string;
  status: 'success' | 'failed' | 'unknown';
  total: number;
  failed: number;
  created_at: string;
};

type GenerateScriptResp = {
  script: string;
  context_diagnostics?: Record<string, unknown>;
};

type ExecuteScriptResp = {
  result?: string;
  structured_report?: Record<string, unknown>;
};

type GenerateChainResp = {
  script: string;
  interfaces_count: number;
};

type GenerateMockResp = {
  mock_data?: unknown[];
};

type APIHistoryResp = {
  items: APIHistoryItem[];
};

const testTypeOptions = ['Functional', 'Boundary', 'Negative', 'Security', 'Performance'];
const testTypeLabel: Record<string, string> = {
  Functional: '功能',
  Boundary: '边界',
  Negative: '异常',
  Security: '安全',
  Performance: '性能',
};
const statusLabel: Record<APIHistoryItem['status'], string> = {
  success: '成功',
  failed: '失败',
  unknown: '未知',
};

export function APIAutomation({ projectId, onLog, view }: APIAutomationProps) {
  const [internalTab, setInternalTab] = useState<'orchestration' | 'runner'>('orchestration');
  const activeTab = view || internalTab;

  const [mode, setMode] = useState<'natural' | 'structured'>('structured');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiPath, setApiPath] = useState('');
  const [requirement, setRequirement] = useState('');
  const [testTypes, setTestTypes] = useState<string[]>(['Functional', 'Boundary']);
  const [scenarioDesc, setScenarioDesc] = useState('');
  const [mockInterfaceJson, setMockInterfaceJson] = useState(
    JSON.stringify(
      {
        method: 'GET',
        url: '/api/example',
        params: [{ key: 'id', value: '1' }],
        body: '',
      },
      null,
      2,
    ),
  );
  const [mockCount, setMockCount] = useState(5);

  const [script, setScript] = useState('');
  const [executionOutput, setExecutionOutput] = useState('');
  const [structuredReport, setStructuredReport] = useState<Record<string, unknown> | null>(null);
  const [mockPreview, setMockPreview] = useState<unknown[] | null>(null);
  const [history, setHistory] = useState<APIHistoryItem[]>([]);
  const [selectedHistory, setSelectedHistory] = useState<number[]>([]);

  const [loadingGenerate, setLoadingGenerate] = useState(false);
  const [loadingExecute, setLoadingExecute] = useState(false);
  const [loadingChain, setLoadingChain] = useState(false);
  const [loadingMock, setLoadingMock] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const canRun = useMemo(() => Boolean(projectId && script.trim()), [projectId, script]);

  const toggleType = (t: string) => {
    setTestTypes((prev) => {
      if (prev.includes(t)) return prev.filter((x) => x !== t);
      return [...prev, t];
    });
  };

  const refreshHistory = async () => {
    if (!projectId) {
      setHistory([]);
      return;
    }
    setLoadingHistory(true);
    try {
      const data = await api.get<APIHistoryResp>(`/api/api-automation/history?project_id=${projectId}`);
      setHistory(Array.isArray(data?.items) ? data.items : []);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingHistory(false);
    }
  };

  useEffect(() => {
    refreshHistory();
  }, [projectId]);

  const handleGenerate = async () => {
    if (!projectId) return;
    if (!requirement.trim()) {
      setErrorMsg('请输入需求说明。');
      return;
    }
    setLoadingGenerate(true);
    setErrorMsg(null);
    try {
      onLog('正在生成接口自动化脚本...');
      const data = await api.post<GenerateScriptResp>('/api/api-automation/generate-script', {
        project_id: projectId,
        requirement,
        base_url: baseUrl || undefined,
        api_path: apiPath || undefined,
        test_types: testTypes.length ? testTypes : undefined,
        mode,
      });
      setScript(data.script || '');
      onLog('接口自动化脚本已生成。');
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setErrorMsg(msg);
      onLog(`生成失败: ${msg}`);
    } finally {
      setLoadingGenerate(false);
    }
  };

  const handleGenerateChain = async () => {
    if (!projectId) return;
    if (!scenarioDesc.trim()) {
      setErrorMsg('请输入链路场景说明。');
      return;
    }
    setLoadingChain(true);
    setErrorMsg(null);
    try {
      onLog('正在生成接口链路脚本...');
      const data = await api.post<GenerateChainResp>('/api/api-automation/generate-chain', {
        project_id: projectId,
        scenario_desc: scenarioDesc,
      });
      setScript(data.script || '');
      onLog(`链路脚本生成完成，共 ${data.interfaces_count} 个接口。`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setErrorMsg(msg);
      onLog(`链路脚本生成失败: ${msg}`);
    } finally {
      setLoadingChain(false);
    }
  };

  const handleGenerateMock = async () => {
    if (!projectId) return;
    setLoadingMock(true);
    setErrorMsg(null);
    try {
      const parsed = JSON.parse(mockInterfaceJson);
      onLog('正在生成接口模拟数据...');
      const data = await api.post<GenerateMockResp>('/api/api-automation/generate-mock-data', {
        project_id: projectId,
        interface_info: parsed,
        mock_type: 'single',
        count: mockCount,
      });
      setMockPreview(Array.isArray(data.mock_data) ? data.mock_data : []);
      onLog('模拟数据已生成。');
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setErrorMsg(msg);
      onLog(`模拟数据生成失败: ${msg}`);
    } finally {
      setLoadingMock(false);
    }
  };

  const handleExecute = async () => {
    if (!projectId || !script.trim()) return;
    setLoadingExecute(true);
    setErrorMsg(null);
    try {
      onLog('正在执行接口自动化脚本...');
      const data = await api.post<ExecuteScriptResp>('/api/api-automation/execute-script', {
        project_id: projectId,
        script_content: script,
        requirement,
        base_url: baseUrl || '',
      });
      setExecutionOutput(data.result || '');
      setStructuredReport(
        data.structured_report && typeof data.structured_report === 'object'
          ? data.structured_report
          : null,
      );
      onLog('接口自动化执行完成。');
      refreshHistory();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setErrorMsg(msg);
      onLog(`执行失败: ${msg}`);
    } finally {
      setLoadingExecute(false);
    }
  };

  const toggleHistorySelect = (id: number) => {
    setSelectedHistory((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  return (
    <div className="d-flex flex-column h-100 w-100 bg-white">
      {!view && (
        <div className="border-bottom bg-light px-3 pt-2">
          <Nav variant="tabs" activeKey={activeTab} onSelect={(k) => setInternalTab(k as 'orchestration' | 'runner')}>
            <Nav.Item>
              <Nav.Link eventKey="orchestration" className="d-flex align-items-center gap-2">
                <FaLayerGroup /> 自动化编排
              </Nav.Link>
            </Nav.Item>
            <Nav.Item>
              <Nav.Link eventKey="runner" className="d-flex align-items-center gap-2">
                <FaPlay /> 批量运行
              </Nav.Link>
            </Nav.Item>
          </Nav>
        </div>
      )}

      <div className="flex-grow-1 overflow-auto p-3 d-flex flex-column gap-3">
        {errorMsg && <Alert variant="danger" className="mb-0">{errorMsg}</Alert>}
        {!projectId && <Alert variant="warning" className="mb-0">请先选择项目。</Alert>}

        {activeTab === 'orchestration' && (
          <>
            <Card className="border-0 shadow-sm">
              <Card.Body className="d-flex flex-column gap-3">
                <div className="d-flex align-items-center justify-content-between">
                  <h5 className="mb-0 text-secondary">脚本生成</h5>
                  <Button variant="outline-secondary" size="sm" onClick={refreshHistory} disabled={loadingHistory}>
                    <FaRedo className="me-1" />
                    刷新历史
                  </Button>
                </div>

                <div className="row g-3">
                  <div className="col-md-4">
                    <Form.Label>模式</Form.Label>
                    <Form.Select value={mode} onChange={(e) => setMode(e.target.value as 'natural' | 'structured')}>
                      <option value="structured">结构化</option>
                      <option value="natural">自然语言</option>
                    </Form.Select>
                  </div>
                  <div className="col-md-4">
                    <Form.Label>基础 URL</Form.Label>
                    <Form.Control value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://localhost:8000" />
                  </div>
                  <div className="col-md-4">
                    <Form.Label>接口路径</Form.Label>
                    <Form.Control value={apiPath} onChange={(e) => setApiPath(e.target.value)} placeholder="/api/v1/orders" />
                  </div>
                </div>

                <div>
                  <Form.Label>需求描述</Form.Label>
                  <Form.Control
                    as="textarea"
                    rows={4}
                    value={requirement}
                    onChange={(e) => setRequirement(e.target.value)}
                    placeholder="请描述你希望覆盖的接口测试范围。"
                  />
                </div>

                <div className="d-flex gap-3 flex-wrap">
                  {testTypeOptions.map((t) => (
                    <Form.Check
                      key={t}
                      inline
                      type="checkbox"
                      id={`api-type-${t}`}
                      label={testTypeLabel[t] || t}
                      checked={testTypes.includes(t)}
                      onChange={() => toggleType(t)}
                    />
                  ))}
                </div>

                <div>
                  <Form.Label>链路场景描述</Form.Label>
                  <Form.Control
                    as="textarea"
                    rows={2}
                    value={scenarioDesc}
                    onChange={(e) => setScenarioDesc(e.target.value)}
                    placeholder="请描述用于生成链路脚本的多步骤接口场景。"
                  />
                </div>

                <div className="d-flex gap-2 flex-wrap">
                    <Button variant="primary" onClick={handleGenerate} disabled={!projectId || loadingGenerate}>
                      {loadingGenerate ? <Spinner size="sm" /> : null}
                      <span className={loadingGenerate ? 'ms-2' : ''}>生成脚本</span>
                    </Button>
                    <Button variant="outline-primary" onClick={handleGenerateChain} disabled={!projectId || loadingChain}>
                      {loadingChain ? <Spinner size="sm" /> : null}
                      <span className={loadingChain ? 'ms-2' : ''}>生成链路脚本</span>
                    </Button>
                    <Button variant="success" onClick={handleExecute} disabled={!canRun || loadingExecute}>
                      {loadingExecute ? <Spinner size="sm" /> : null}
                      <span className={loadingExecute ? 'ms-2' : ''}>执行脚本</span>
                    </Button>
                  </div>
              </Card.Body>
            </Card>

            <Card className="border-0 shadow-sm">
              <Card.Body className="d-flex flex-column gap-3">
                <h6 className="mb-0">脚本编辑器</h6>
                <Form.Control
                  as="textarea"
                  rows={12}
                  value={script}
                  onChange={(e) => setScript(e.target.value)}
                  placeholder="生成后的脚本会显示在这里。"
                />
              </Card.Body>
            </Card>

            <Card className="border-0 shadow-sm">
              <Card.Body className="d-flex flex-column gap-3">
                <h6 className="mb-0">模拟数据助手</h6>
                <div className="row g-3">
                  <div className="col-md-8">
                    <Form.Label>接口信息 JSON</Form.Label>
                    <Form.Control
                      as="textarea"
                      rows={6}
                      value={mockInterfaceJson}
                      onChange={(e) => setMockInterfaceJson(e.target.value)}
                    />
                  </div>
                  <div className="col-md-4">
                    <Form.Label>数量</Form.Label>
                    <Form.Control
                      type="number"
                      min={1}
                      max={50}
                      value={mockCount}
                      onChange={(e) => setMockCount(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
                    />
                    <div className="mt-3">
                      <Button variant="outline-success" onClick={handleGenerateMock} disabled={!projectId || loadingMock}>
                        {loadingMock ? <Spinner size="sm" /> : null}
                        <span className={loadingMock ? 'ms-2' : ''}>生成模拟数据</span>
                      </Button>
                    </div>
                  </div>
                </div>
                {mockPreview && (
                  <Form.Control
                    as="textarea"
                    rows={6}
                    readOnly
                    value={JSON.stringify(mockPreview, null, 2)}
                  />
                )}
              </Card.Body>
            </Card>

            <Card className="border-0 shadow-sm">
              <Card.Body className="d-flex flex-column gap-3">
                <h6 className="mb-0">执行结果</h6>
                <Form.Control as="textarea" rows={8} value={executionOutput} readOnly />
                {structuredReport && (
                  <Form.Control as="textarea" rows={8} value={JSON.stringify(structuredReport, null, 2)} readOnly />
                )}
              </Card.Body>
            </Card>
          </>
        )}

        {activeTab === 'runner' && (
          <>
            <Card className="border-0 shadow-sm">
              <Card.Body className="d-flex justify-content-between align-items-center">
                <h5 className="mb-0 text-secondary">批量运行</h5>
                <div className="d-flex gap-2">
                  <Button variant="outline-secondary" size="sm" onClick={refreshHistory} disabled={loadingHistory}>
                    <FaRedo className="me-1" />
                    刷新
                  </Button>
                  <Button variant="success" size="sm" onClick={handleExecute} disabled={!canRun || loadingExecute}>
                    <FaPlay className="me-1" />
                    运行当前脚本
                  </Button>
                </div>
              </Card.Body>
            </Card>

            <Card className="border-0 shadow-sm">
              <Table hover responsive className="mb-0 align-middle">
                <thead className="bg-light">
                  <tr>
                    <th style={{ width: 36 }}>
                      <Form.Check
                        checked={history.length > 0 && selectedHistory.length === history.length}
                        onChange={(e) =>
                          setSelectedHistory(e.target.checked ? history.map((x) => x.id) : [])
                        }
                      />
                    </th>
                    <th>ID</th>
                    <th>需求</th>
                    <th>总数</th>
                    <th>失败</th>
                    <th>状态</th>
                    <th>创建时间</th>
                  </tr>
                </thead>
                <tbody>
                  {loadingHistory && (
                    <tr>
                      <td colSpan={7} className="text-center py-4">
                        <Spinner animation="border" size="sm" className="me-2" />
                        正在加载历史记录...
                      </td>
                    </tr>
                  )}
                  {!loadingHistory && history.length === 0 && (
                    <tr>
                      <td colSpan={7} className="text-center py-4 text-muted">
                        暂无接口执行历史。
                      </td>
                    </tr>
                  )}
                  {!loadingHistory &&
                    history.map((item) => (
                      <tr key={item.id}>
                        <td>
                          <Form.Check
                            checked={selectedHistory.includes(item.id)}
                            onChange={() => toggleHistorySelect(item.id)}
                          />
                        </td>
                        <td>#{item.id}</td>
                        <td className="small">{item.requirement || '-'}</td>
                        <td>{item.total}</td>
                        <td>{item.failed}</td>
                        <td>
                          <Badge bg={item.status === 'success' ? 'success' : item.status === 'failed' ? 'danger' : 'secondary'}>
                            {statusLabel[item.status]}
                          </Badge>
                        </td>
                        <td className="small text-muted">{item.created_at ? new Date(item.created_at).toLocaleString() : '-'}</td>
                      </tr>
                    ))}
                </tbody>
              </Table>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
