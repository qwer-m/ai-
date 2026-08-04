import { useEffect, useMemo, useState } from 'react';
import { Alert, Badge, Button, Card, Form, Nav, Spinner, Table } from 'react-bootstrap';
import { FaLayerGroup, FaPlay, FaRedo } from 'react-icons/fa';
import { api } from '../../../utils/api';

type AutomationView = 'orchestration' | 'runner';
type GenerationMode = 'structured' | 'natural';
type TestType = 'Functional' | 'Boundary' | 'Negative' | 'Security' | 'Performance';
type HistoryStatus = 'success' | 'failed' | 'unknown';

type Props = {
  projectId: number | null;
  onLog: (message: string) => void;
  view?: AutomationView;
};

type ScriptResponse = {
  script?: string | null;
};

type ChainResponse = ScriptResponse & {
  interfaces_count?: number;
};

type MockDataResponse = {
  mock_data?: unknown;
};

type ExecuteResponse = {
  result?: string | null;
  structured_report?: unknown;
};

type HistoryRow = {
  id: number;
  requirement?: string | null;
  total: number;
  failed: number;
  status: HistoryStatus;
  created_at?: string | null;
};

type HistoryResponse = {
  items?: HistoryRow[];
};

const TEST_TYPES: readonly TestType[] = [
  'Functional',
  'Boundary',
  'Negative',
  'Security',
  'Performance',
];

const TEST_TYPE_LABELS: Record<TestType, string> = {
  Functional: '功能',
  Boundary: '边界',
  Negative: '异常',
  Security: '安全',
  Performance: '性能',
};

const STATUS_LABELS: Record<HistoryStatus, string> = {
  success: '成功',
  failed: '失败',
  unknown: '未知',
};

const DEFAULT_INTERFACE_INFO = JSON.stringify(
  {
    method: 'GET',
    url: '/api/example',
    params: [{ key: 'id', value: '1' }],
    body: '',
  },
  null,
  2,
);

function toErrorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

function statusBadgeVariant(status: HistoryStatus): string {
  if (status === 'success') return 'success';
  if (status === 'failed') return 'danger';
  return 'secondary';
}

export function APIAutomation({ projectId, onLog, view }: Props) {
  const [internalView, setInternalView] = useState<AutomationView>('orchestration');
  const activeView = view || internalView;

  const [mode, setMode] = useState<GenerationMode>('structured');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiPath, setApiPath] = useState('');
  const [requirement, setRequirement] = useState('');
  const [selectedTestTypes, setSelectedTestTypes] = useState<TestType[]>(['Functional', 'Boundary']);
  const [scenarioDescription, setScenarioDescription] = useState('');
  const [interfaceInfo, setInterfaceInfo] = useState(DEFAULT_INTERFACE_INFO);
  const [mockCount, setMockCount] = useState(5);

  const [script, setScript] = useState('');
  const [executionResult, setExecutionResult] = useState('');
  const [structuredReport, setStructuredReport] = useState<Record<string, unknown> | null>(null);
  const [mockData, setMockData] = useState<unknown[] | null>(null);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [selectedHistoryIds, setSelectedHistoryIds] = useState<number[]>([]);

  const [generatingScript, setGeneratingScript] = useState(false);
  const [executingScript, setExecutingScript] = useState(false);
  const [generatingChain, setGeneratingChain] = useState(false);
  const [generatingMockData, setGeneratingMockData] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canExecute = useMemo(() => Boolean(projectId && script.trim()), [projectId, script]);

  const toggleTestType = (testType: TestType) => {
    setSelectedTestTypes((current) => (
      current.includes(testType)
        ? current.filter((item) => item !== testType)
        : [...current, testType]
    ));
  };

  const loadHistory = async () => {
    if (!projectId) {
      setHistory([]);
      return;
    }

    setLoadingHistory(true);
    try {
      const response = await api.get<HistoryResponse>(`/api/api-automation/history?project_id=${projectId}`);
      setHistory(Array.isArray(response?.items) ? response.items : []);
    } catch (reason) {
      setError(toErrorMessage(reason));
    } finally {
      setLoadingHistory(false);
    }
  };

  useEffect(() => {
    void loadHistory();
  }, [projectId]);

  const handleGenerateScript = async () => {
    if (!projectId) return;
    if (!requirement.trim()) {
      setError('请输入需求说明。');
      return;
    }

    setGeneratingScript(true);
    setError(null);
    try {
      onLog('正在生成 API 自动化脚本...');
      const response = await api.post<ScriptResponse>('/api/api-automation/generate-script', {
        project_id: projectId,
        requirement,
        base_url: baseUrl || undefined,
        api_path: apiPath || undefined,
        test_types: selectedTestTypes.length ? selectedTestTypes : undefined,
        mode,
      });
      setScript(response.script || '');
      onLog('API 自动化脚本已生成。');
    } catch (reason) {
      const message = toErrorMessage(reason);
      setError(message);
      onLog(`生成失败: ${message}`);
    } finally {
      setGeneratingScript(false);
    }
  };

  const handleGenerateChain = async () => {
    if (!projectId) return;
    if (!scenarioDescription.trim()) {
      setError('请输入链路场景说明。');
      return;
    }

    setGeneratingChain(true);
    setError(null);
    try {
      onLog('正在生成接口链路脚本...');
      const response = await api.post<ChainResponse>('/api/api-automation/generate-chain', {
        project_id: projectId,
        scenario_desc: scenarioDescription,
      });
      setScript(response.script || '');
      onLog(`链路脚本生成完成，共 ${response.interfaces_count} 个接口。`);
    } catch (reason) {
      const message = toErrorMessage(reason);
      setError(message);
      onLog(`链路脚本生成失败: ${message}`);
    } finally {
      setGeneratingChain(false);
    }
  };

  const handleGenerateMockData = async () => {
    if (!projectId) return;

    setGeneratingMockData(true);
    setError(null);
    try {
      const parsedInterfaceInfo: unknown = JSON.parse(interfaceInfo);
      onLog('正在生成接口模拟数据...');
      const response = await api.post<MockDataResponse>('/api/api-automation/generate-mock-data', {
        project_id: projectId,
        interface_info: parsedInterfaceInfo,
        mock_type: 'single',
        count: mockCount,
      });
      setMockData(Array.isArray(response.mock_data) ? response.mock_data : []);
      onLog('模拟数据已生成。');
    } catch (reason) {
      const message = toErrorMessage(reason);
      setError(message);
      onLog(`模拟数据生成失败: ${message}`);
    } finally {
      setGeneratingMockData(false);
    }
  };

  const handleExecuteScript = async () => {
    if (!projectId || !script.trim()) return;

    setExecutingScript(true);
    setError(null);
    try {
      onLog('正在执行 API 自动化脚本...');
      const response = await api.post<ExecuteResponse>('/api/api-automation/execute-script', {
        project_id: projectId,
        script_content: script,
        requirement,
        base_url: baseUrl || '',
      });
      setExecutionResult(response.result || '');
      setStructuredReport(
        response.structured_report && typeof response.structured_report === 'object'
          ? response.structured_report as Record<string, unknown>
          : null,
      );
      onLog('API 自动化执行完成。');
      void loadHistory();
    } catch (reason) {
      const message = toErrorMessage(reason);
      setError(message);
      onLog(`执行失败: ${message}`);
    } finally {
      setExecutingScript(false);
    }
  };

  const toggleHistorySelection = (historyId: number) => {
    setSelectedHistoryIds((current) => (
      current.includes(historyId)
        ? current.filter((item) => item !== historyId)
        : [...current, historyId]
    ));
  };

  return (
    <div className="api-automation-shell d-flex flex-column h-100 w-100 postman-theme">
      {view ? null : (
        <div className="border-bottom bg-light px-3 pt-2 api-automation-tabs-head">
          <Nav
            variant="tabs"
            activeKey={activeView}
            onSelect={(eventKey) => {
              if (eventKey === 'orchestration' || eventKey === 'runner') {
                setInternalView(eventKey);
              }
            }}
          >
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
        {error ? <Alert variant="danger" className="mb-0">{error}</Alert> : null}
        {projectId ? null : <Alert variant="warning" className="mb-0">请先选择项目。</Alert>}

        {activeView === 'orchestration' ? (
          <>
            <Card className="api-automation-card border-0 shadow-sm">
              <Card.Body className="d-flex flex-column gap-3">
                <div className="d-flex align-items-center justify-content-between">
                  <h5 className="mb-0 text-secondary">脚本生成</h5>
                  <Button
                    variant="outline-secondary"
                    size="sm"
                    onClick={() => void loadHistory()}
                    disabled={loadingHistory}
                  >
                    <FaRedo className="me-1" />
                    刷新历史
                  </Button>
                </div>

                <div className="row g-3">
                  <div className="col-md-4">
                    <Form.Label>模式</Form.Label>
                    <Form.Select
                      value={mode}
                      onChange={(event) => setMode(event.target.value as GenerationMode)}
                    >
                      <option value="structured">结构化</option>
                      <option value="natural">自然语言</option>
                    </Form.Select>
                  </div>
                  <div className="col-md-4">
                    <Form.Label>基础 URL</Form.Label>
                    <Form.Control
                      value={baseUrl}
                      onChange={(event) => setBaseUrl(event.target.value)}
                      placeholder="http://localhost:8000"
                    />
                  </div>
                  <div className="col-md-4">
                    <Form.Label>接口路径</Form.Label>
                    <Form.Control
                      value={apiPath}
                      onChange={(event) => setApiPath(event.target.value)}
                      placeholder="/api/v1/orders"
                    />
                  </div>
                </div>

                <div>
                  <Form.Label>需求描述</Form.Label>
                  <Form.Control
                    as="textarea"
                    rows={4}
                    value={requirement}
                    onChange={(event) => setRequirement(event.target.value)}
                    placeholder="请描述希望覆盖的接口测试范围。"
                  />
                </div>

                <div className="d-flex gap-3 flex-wrap">
                  {TEST_TYPES.map((testType) => (
                    <Form.Check
                      key={testType}
                      inline
                      type="checkbox"
                      id={`api-type-${testType}`}
                      label={TEST_TYPE_LABELS[testType]}
                      checked={selectedTestTypes.includes(testType)}
                      onChange={() => toggleTestType(testType)}
                    />
                  ))}
                </div>

                <div>
                  <Form.Label>链路场景描述</Form.Label>
                  <Form.Control
                    as="textarea"
                    rows={2}
                    value={scenarioDescription}
                    onChange={(event) => setScenarioDescription(event.target.value)}
                    placeholder="请描述多步骤接口调用场景。"
                  />
                </div>

                <div className="d-flex gap-2 flex-wrap">
                  <Button
                    variant="primary"
                    onClick={() => void handleGenerateScript()}
                    disabled={!projectId || generatingScript}
                  >
                    {generatingScript ? <Spinner size="sm" /> : null}
                    <span className={generatingScript ? 'ms-2' : ''}>生成脚本</span>
                  </Button>
                  <Button
                    variant="outline-primary"
                    onClick={() => void handleGenerateChain()}
                    disabled={!projectId || generatingChain}
                  >
                    {generatingChain ? <Spinner size="sm" /> : null}
                    <span className={generatingChain ? 'ms-2' : ''}>生成链路脚本</span>
                  </Button>
                  <Button
                    variant="success"
                    onClick={() => void handleExecuteScript()}
                    disabled={!canExecute || executingScript}
                  >
                    {executingScript ? <Spinner size="sm" /> : null}
                    <span className={executingScript ? 'ms-2' : ''}>执行脚本</span>
                  </Button>
                </div>
              </Card.Body>
            </Card>

            <Card className="api-automation-card border-0 shadow-sm">
              <Card.Body className="d-flex flex-column gap-3">
                <h6 className="mb-0">脚本编辑器</h6>
                <Form.Control
                  as="textarea"
                  rows={12}
                  value={script}
                  onChange={(event) => setScript(event.target.value)}
                  placeholder="生成后的脚本会显示在这里。"
                />
              </Card.Body>
            </Card>

            <Card className="api-automation-card border-0 shadow-sm">
              <Card.Body className="d-flex flex-column gap-3">
                <h6 className="mb-0">模拟数据助手</h6>
                <div className="row g-3">
                  <div className="col-md-8">
                    <Form.Label>接口信息 JSON</Form.Label>
                    <Form.Control
                      as="textarea"
                      rows={6}
                      value={interfaceInfo}
                      onChange={(event) => setInterfaceInfo(event.target.value)}
                    />
                  </div>
                  <div className="col-md-4">
                    <Form.Label>数量</Form.Label>
                    <Form.Control
                      type="number"
                      min={1}
                      max={50}
                      value={mockCount}
                      onChange={(event) => setMockCount(
                        Math.max(1, Math.min(50, Number(event.target.value) || 1)),
                      )}
                    />
                    <div className="mt-3">
                      <Button
                        variant="outline-success"
                        onClick={() => void handleGenerateMockData()}
                        disabled={!projectId || generatingMockData}
                      >
                        {generatingMockData ? <Spinner size="sm" /> : null}
                        <span className={generatingMockData ? 'ms-2' : ''}>生成模拟数据</span>
                      </Button>
                    </div>
                  </div>
                </div>

                {mockData ? (
                  <Form.Control
                    as="textarea"
                    rows={6}
                    readOnly
                    value={JSON.stringify(mockData, null, 2)}
                  />
                ) : null}
              </Card.Body>
            </Card>

            <Card className="api-automation-card border-0 shadow-sm">
              <Card.Body className="d-flex flex-column gap-3">
                <h6 className="mb-0">执行结果</h6>
                <Form.Control as="textarea" rows={8} value={executionResult} readOnly />
                {structuredReport ? (
                  <Form.Control
                    as="textarea"
                    rows={8}
                    value={JSON.stringify(structuredReport, null, 2)}
                    readOnly
                  />
                ) : null}
              </Card.Body>
            </Card>
          </>
        ) : null}

        {activeView === 'runner' ? (
          <>
            <Card className="api-automation-card border-0 shadow-sm">
              <Card.Body className="d-flex justify-content-between align-items-center">
                <h5 className="mb-0 text-secondary">批量运行</h5>
                <div className="d-flex gap-2">
                  <Button
                    variant="outline-secondary"
                    size="sm"
                    onClick={() => void loadHistory()}
                    disabled={loadingHistory}
                  >
                    <FaRedo className="me-1" /> 刷新
                  </Button>
                  <Button
                    variant="success"
                    size="sm"
                    onClick={() => void handleExecuteScript()}
                    disabled={!canExecute || executingScript}
                  >
                    <FaPlay className="me-1" /> 运行当前脚本
                  </Button>
                </div>
              </Card.Body>
            </Card>

            <Card className="api-automation-card border-0 shadow-sm">
              <Table hover responsive className="mb-0 align-middle">
                <thead className="bg-light">
                  <tr>
                    <th className="api-automation-check-col">
                      <Form.Check
                        checked={history.length > 0 && selectedHistoryIds.length === history.length}
                        onChange={(event) => setSelectedHistoryIds(
                          event.target.checked ? history.map((item) => item.id) : [],
                        )}
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
                  {loadingHistory ? (
                    <tr>
                      <td colSpan={7} className="text-center py-4">
                        <Spinner animation="border" size="sm" className="me-2" />
                        正在加载历史记录...
                      </td>
                    </tr>
                  ) : null}

                  {!loadingHistory && history.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="text-center py-4 text-muted">
                        暂无接口执行历史。
                      </td>
                    </tr>
                  ) : null}

                  {loadingHistory ? null : history.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <Form.Check
                          checked={selectedHistoryIds.includes(item.id)}
                          onChange={() => toggleHistorySelection(item.id)}
                        />
                      </td>
                      <td>#{item.id}</td>
                      <td className="small">{item.requirement || '-'}</td>
                      <td>{item.total}</td>
                      <td>{item.failed}</td>
                      <td>
                        <Badge bg={statusBadgeVariant(item.status)}>
                          {STATUS_LABELS[item.status]}
                        </Badge>
                      </td>
                      <td className="small text-muted">
                        {item.created_at ? new Date(item.created_at).toLocaleString() : '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </Card>
          </>
        ) : null}
      </div>
    </div>
  );
}
