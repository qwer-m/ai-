import { useState, useEffect, type MouseEvent } from 'react';
import { Button, Form, Spinner, Card, Row, Col, Badge, Accordion, Collapse, Nav, ListGroup } from 'react-bootstrap';
import { FaCheckCircle, FaExclamationTriangle, FaBug, FaCode, FaTerminal, FaPlay, FaPlus, FaTrash, FaChevronDown, FaChevronRight } from 'react-icons/fa';
import { api } from '../utils/api';

type APITestingProps = {
  projectId: number | null;
  onLog: (msg: string) => void;
};

type TestResult = {
  script: string;
  result: string; // Raw stdout/stderr
  structured_report?: {
    total: number;
    passed: number;
    failed: number;
    skipped: number;
    time: number;
    failures: Array<{
      name: string;
      message: string;
      details: string;
      type?: string;
    }>;
  };
};

type SavedInterface = {
    id: string;
    baseUrl: string; // Added field for persistence
    apiPath: string;
    method?: string; // Optional, parsed from requirement if possible
    requirement: string;
    mode: 'natural' | 'structured';
    testTypes: {
        functional: boolean;
        boundary: boolean;
        security: boolean;
    };
    timestamp: number;
};

const ErrorTrace = ({ details }: { details: string }) => {
    const [expanded, setExpanded] = useState(false);
    const lines = details ? details.split('\n') : [];
    const preview = lines.slice(0, 3).join('\n');
    const hasMore = lines.length > 3;

    return (
        <div className="d-flex flex-column gap-1">
            <small className="text-muted">堆栈详情:</small>
            <pre className="bg-white border p-2 rounded small text-secondary mb-1 font-monospace" style={{ whiteSpace: 'pre-wrap' }}>
                {expanded ? details : preview}
                {!expanded && hasMore && "..."}
            </pre>
            {hasMore && (
                <div className="text-end">
                    <Button 
                        variant="link" 
                        size="sm" 
                        className="p-0 text-decoration-none" 
                        onClick={() => setExpanded(!expanded)}
                    >
                        {expanded ? '收起详情' : '展开完整堆栈'}
                    </Button>
                </div>
            )}
        </div>
    );
};

export function APITesting({ projectId, onLog }: APITestingProps) {
  const [mode, setMode] = useState<'natural' | 'structured'>('natural');
  const [requirement, setRequirement] = useState('');
  const [baseUrl, setBaseUrl] = useState('http://localhost:8000');
  const [apiPath, setApiPath] = useState('');
  const [testTypes, setTestTypes] = useState({
      functional: true,
      boundary: false,
      security: false
  });
  
  const [loading, setLoading] = useState(false);
  const [loadingStage, setLoadingStage] = useState(''); // 'generating' | 'executing'
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [showLogs, setShowLogs] = useState(false);
  const [showScript, setShowScript] = useState(false);

  // Saved Interfaces State
  const [savedInterfaces, setSavedInterfaces] = useState<SavedInterface[]>(() => {
      try {
          const saved = localStorage.getItem('api_testing_saved_interfaces_v1');
          if (!saved) return [];
          const parsed = JSON.parse(saved);
          return Array.isArray(parsed) ? parsed : [];
      } catch (e) {
          console.error('Failed to load saved interfaces:', e);
          return [];
      }
  });
  const [showSavedList, setShowSavedList] = useState(false);

  // Persist to localStorage
  useEffect(() => {
      try {
          localStorage.setItem('api_testing_saved_interfaces_v1', JSON.stringify(savedInterfaces));
      } catch (e) {
          console.error('Failed to save interfaces:', e);
      }
  }, [savedInterfaces]);

  // Safe access for rendering
  const safeSavedInterfaces = Array.isArray(savedInterfaces) ? savedInterfaces : [];

  const handleSaveInterface = () => {
      if (!requirement && !apiPath) return alert('请至少填写接口路径或描述');
      
      const newItem: SavedInterface = {
          id: Date.now().toString(),
          baseUrl: baseUrl || '',
          apiPath: apiPath || 'Unknown Path',
          requirement,
          mode,
          testTypes: {...testTypes},
          timestamp: Date.now()
      };
      
      setSavedInterfaces(prev => [newItem, ...prev]);
      setShowSavedList(true);
  };

  const handleLoadInterface = (item: SavedInterface) => {
      if (item.baseUrl) setBaseUrl(item.baseUrl);
      setApiPath(item.apiPath);
      setRequirement(item.requirement);
      setMode(item.mode);
      setTestTypes(item.testTypes);
  };

  const handleDeleteInterface = (id: string, e: MouseEvent) => {
      e.stopPropagation();
      setSavedInterfaces(prev => prev.filter(i => i.id !== id));
  };

  const handleInsertTemplate = () => {
      const template = `[
  {
    "method": "POST",
    "path": "/users",
    "description": "Create a new user",
    "params": {
      "username": "string (required, 3-20 chars)",
      "age": "integer (0-120)",
      "email": "string (email format)"
    }
  }
]`;
      setRequirement(template);
  };

  const handleRun = async () => {
    if (!projectId) return alert('请先选择项目');
    if (!baseUrl) return alert('请输入 Base URL (例如 http://localhost:8000)');
    if (!baseUrl.startsWith('http')) return alert('Base URL 必须以 http:// 或 https:// 开头');
    if (!requirement) return alert('请输入内容');
    
    setLoading(true);
    setLoadingStage('generating');
    setTestResult(null);
    setShowLogs(false);
    onLog(`开始生成接口测试脚本 (${mode === 'natural' ? '自然语言' : '结构化'}模式)...`);
    
    try {
      const activeTypes = (Object.keys(testTypes) as Array<keyof typeof testTypes>)
        .filter(k => testTypes[k])
        .map(k => k.charAt(0).toUpperCase() + k.slice(1));

      // Introduce artificial delay for stage transition visual
      setTimeout(() => { if(loading) setLoadingStage('executing'); }, 2000);

      const data = await api.post<TestResult>('/api/api-testing', { 
        requirement, 
        project_id: projectId,
        base_url: baseUrl,
        test_types: activeTypes,
        mode
      });
      
      setTestResult(data);
      onLog('接口测试执行完成');
      
      if (data.structured_report && data.structured_report.failed > 0) {
          onLog(`测试发现 ${data.structured_report.failed} 个问题`);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      onLog(`接口测试失败: ${msg}`);
      alert(`执行失败: ${msg}`);
    } finally {
      setLoading(false);
      setLoadingStage('');
    }
  };

  const getErrorBadge = (msg: string) => {
      if (msg.includes('AI_GENERATION_ERROR')) return <Badge bg="dark">AI 生成失败</Badge>;
      if (msg.includes('COMPILATION_ERROR')) return <Badge bg="danger">编译错误</Badge>;
      if (msg.includes('EXECUTION_ERROR')) return <Badge bg="warning" text="dark">执行异常</Badge>;
      if (msg.includes('ERR_TEST_004')) return <Badge bg="warning" text="dark">断言失败</Badge>; // Tweak: Yellow for assertion failures
      return <Badge bg="danger">Failed</Badge>;
  };

  const renderDashboard = (report: NonNullable<TestResult['structured_report']>) => {
      const passRate = report.total > 0 ? (report.passed / report.total) * 100 : 0;
      
      return (
          <div className="d-flex flex-column gap-3 animate-fade-in">
              {/* Overview Metrics */}
              <Row className="g-3">
                  <Col md={3} xs={6}>
                      <Card className="text-center h-100 border-success shadow-sm">
                          <Card.Body className="d-flex flex-column justify-content-center">
                              <h2 className="text-success mb-0 fw-bold">{Math.round(passRate)}%</h2>
                              <div className="small text-muted">通过率</div>
                          </Card.Body>
                      </Card>
                  </Col>
                  <Col md={3} xs={6}>
                      <Card className="text-center h-100 border-primary shadow-sm">
                          <Card.Body className="d-flex flex-column justify-content-center">
                              <h2 className="text-primary mb-0 fw-bold">{report.total}</h2>
                              <div className="small text-muted">总用例</div>
                          </Card.Body>
                      </Card>
                  </Col>
                  <Col md={3} xs={6}>
                      <Card className={`text-center h-100 shadow-sm ${report.failed > 0 ? 'border-danger bg-danger bg-opacity-10' : 'border-light'}`}>
                          <Card.Body className="d-flex flex-column justify-content-center">
                              <h2 className={`mb-0 fw-bold ${report.failed > 0 ? 'text-danger' : 'text-secondary'}`}>{report.failed}</h2>
                              <div className="small text-muted">失败</div>
                          </Card.Body>
                      </Card>
                  </Col>
                  <Col md={3} xs={6}>
                      <Card className="text-center h-100 border-light shadow-sm">
                          <Card.Body className="d-flex flex-column justify-content-center">
                              <h4 className="text-secondary mb-0">{report.time.toFixed(2)}s</h4>
                              <div className="small text-muted">耗时</div>
                          </Card.Body>
                      </Card>
                  </Col>
              </Row>
              
              {/* Failure Analysis */}
              {report.failures.length > 0 ? (
                  <Card className="border-danger shadow-sm">
                      <Card.Header className="bg-danger text-white d-flex align-items-center gap-2">
                          <FaBug /> 失败用例透视
                      </Card.Header>
                      <Accordion flush alwaysOpen>
                          {report.failures.map((fail, idx) => (
                              <Accordion.Item eventKey={String(idx)} key={idx}>
                                  <Accordion.Header>
                                      <div className="d-flex align-items-center gap-2">
                                          {getErrorBadge(fail.message)}
                                          <span className="font-monospace">{fail.name}</span>
                                      </div>
                                  </Accordion.Header>
                                  <Accordion.Body className="bg-light">
                                      <div className="mb-2">
                                          <strong>错误归因:</strong> <span className="text-danger fw-bold ms-2">{fail.message}</span>
                                      </div>
                                      <ErrorTrace details={fail.details} />
                                  </Accordion.Body>
                              </Accordion.Item>
                          ))}
                      </Accordion>
                  </Card>
              ) : (
                   <div className="alert alert-success d-flex align-items-center">
                       <FaCheckCircle className="me-2" size={20} />
                       <div>
                           <strong>测试通过!</strong> 所有 {report.total} 个用例均执行成功。
                       </div>
                   </div>
              )}
          </div>
      );
  };

  return (
    <div className="h-100 d-flex flex-column gap-3 overflow-hidden">
      {/* Configuration Area */}
      <Card className="border-0 shadow-sm flex-shrink-0 bento-card">
          <Card.Body className="p-4">
              <div className="mb-3 d-flex align-items-center">
                  <div className="h5 fw-bold text-gradient mb-0">API 测试配置</div>
              </div>
              <Row className="g-3">
                  <Col md={6}>
                      <Form.Label className="small fw-bold text-secondary">基础 URL (Base URL) <span className="text-danger">*</span></Form.Label>
                      <Form.Control 
                          type="url" 
                          placeholder="e.g. http://localhost:8000" 
                          value={baseUrl} 
                          onChange={e => setBaseUrl(e.target.value)}
                          size="sm"
                          className="input-pro"
                      />
                  </Col>
                  <Col md={6}>
                      <Form.Label className="small fw-bold text-secondary">接口路径 (API Path)</Form.Label>
                      <Form.Control 
                          type="text" 
                          placeholder="e.g. /api/v1/users" 
                          value={apiPath} 
                          onChange={e => setApiPath(e.target.value)}
                          size="sm"
                          className="input-pro"
                      />
                  </Col>
                  <Col md={12}>
                      <Form.Label className="small fw-bold text-secondary">测试维度 (Focus)</Form.Label>
                      <div className="d-flex gap-3 pt-1">
                          <Form.Check 
                              type="checkbox" 
                              label="功能正确性" 
                              checked={testTypes.functional}
                              onChange={e => setTestTypes({...testTypes, functional: e.target.checked})}
                              id="check-func"
                          />
                          <Form.Check 
                              type="checkbox" 
                              label="边界健壮性" 
                              checked={testTypes.boundary}
                              onChange={e => setTestTypes({...testTypes, boundary: e.target.checked})}
                              id="check-bound"
                          />
                          <Form.Check 
                              type="checkbox" 
                              label="安全性 (SQL/XSS)" 
                              checked={testTypes.security}
                              onChange={e => setTestTypes({...testTypes, security: e.target.checked})}
                              id="check-sec"
                              className="text-danger"
                          />
                      </div>
                  </Col>
                  <Col md={12}>
                      <div className="d-flex justify-content-between align-items-center mb-2">
                          <Nav variant="pills" activeKey={mode} onSelect={(k) => setMode(k as any)} className="small">
                              <Nav.Item>
                                  <Nav.Link eventKey="natural" className="py-1 px-3 rounded-pill">需求描述</Nav.Link>
                              </Nav.Item>
                              <Nav.Item>
                                  <Nav.Link eventKey="structured" className="py-1 px-3 rounded-pill">接口定义 (JSON)</Nav.Link>
                              </Nav.Item>
                          </Nav>
                          {mode === 'structured' && (
                              <Button variant="link" size="sm" className="p-0 text-decoration-none small" onClick={handleInsertTemplate}>
                                  <FaCode className="me-1" /> 插入模板
                              </Button>
                          )}
                      </div>
                      <div className="d-flex gap-2">
                          <Form.Control 
                              as="textarea" 
                              rows={5} 
                              value={requirement} 
                              onChange={e => setRequirement(e.target.value)} 
                              placeholder={mode === 'natural' 
                                  ? "描述要测试的接口，例如：'测试用户登录接口 POST /login，参数为 username, password...'"
                                  : "粘贴 JSON 格式的接口定义，包含 method, path, params 等信息..."
                              }
                              className="font-monospace small input-pro"
                          />
                          <Button 
                            variant="outline-secondary"
                            onClick={handleSaveInterface}
                            title="暂存接口配置"
                            className="d-flex flex-column align-items-center justify-content-center input-pro border-0"
                            style={{minWidth: '50px'}}
                          >
                              <FaPlus />
                              <span style={{fontSize: '0.7em'}}>暂存</span>
                          </Button>
                          <Button 
                            className="d-flex flex-column align-items-center justify-content-center btn-pro-primary"
                            onClick={handleRun} 
                            disabled={loading || !projectId}
                            style={{minWidth: '120px'}}
                          >
                            {loading ? (
                                <>
                                    <Spinner size="sm" animation="border" className="mb-1" />
                                    <span style={{fontSize: '0.8em'}}>{loadingStage === 'generating' ? '生成中...' : '执行中...'}</span>
                                </>
                            ) : (
                                <>
                                    <FaPlay className="mb-1" />
                                    <span>开始测试</span>
                                </>
                            )}
                          </Button>
                      </div>
                  </Col>
              </Row>
          </Card.Body>
      </Card>
      
      {/* Saved Interfaces List */}
      {safeSavedInterfaces.length > 0 && (
          <Card className="border-0 flex-shrink-0 glass-panel">
              <Card.Header 
                className="bg-white py-2 px-3 d-flex align-items-center justify-content-between cursor-pointer user-select-none"
                onClick={() => setShowSavedList(!showSavedList)}
                style={{cursor: 'pointer'}}
              >
                  <div className="d-flex align-items-center gap-2 small fw-bold text-secondary">
                      {showSavedList ? <FaChevronDown /> : <FaChevronRight />}
                      <span>已添加接口列表 ({safeSavedInterfaces.length})</span>
                  </div>
                  <Badge bg="secondary" pill>{safeSavedInterfaces.length}</Badge>
              </Card.Header>
              <Collapse in={showSavedList}>
                  <ListGroup variant="flush" style={{maxHeight: '200px', overflowY: 'auto'}}>
                      {safeSavedInterfaces.map(item => (
                          <ListGroup.Item 
                            key={item.id} 
                            action 
                            onClick={() => handleLoadInterface(item)}
                            className="d-flex align-items-center justify-content-between py-2 px-3"
                          >
                              <div className="d-flex flex-column overflow-hidden me-3">
                                  <div className="d-flex align-items-center gap-2 mb-1">
                                      <Badge bg={item.mode === 'structured' ? 'info' : 'primary'} className="fw-normal" style={{fontSize: '0.7em'}}>
                                          {item.mode === 'structured' ? 'JSON' : 'Text'}
                                      </Badge>
                                      <span className="fw-bold small text-truncate" title={item.baseUrl + item.apiPath}>
                                        {item.baseUrl ? `${item.baseUrl.replace(/https?:\/\//, '')}${item.apiPath}` : item.apiPath}
                                      </span>
                                  </div>
                                  <small className="text-muted text-truncate" style={{fontSize: '0.75em'}}>
                                      {item.requirement.slice(0, 50) || '(无描述)'}
                                  </small>
                              </div>
                              <Button 
                                variant="link" 
                                size="sm" 
                                className="text-danger p-0 opacity-50 hover-opacity-100"
                                onClick={(e) => handleDeleteInterface(item.id, e)}
                              >
                                  <FaTrash />
                              </Button>
                          </ListGroup.Item>
                      ))}
                  </ListGroup>
              </Collapse>
          </Card>
      )}

      {/* Result Area */}
      <div className="flex-grow-1 overflow-auto px-1">
          {testResult ? (
              <div className="d-flex flex-column gap-3 pb-4">
                  {/* Dashboard */}
                  {testResult.structured_report ? (
                      renderDashboard(testResult.structured_report)
                  ) : (
                      <div className="alert alert-warning">
                          <FaExclamationTriangle className="me-2" />
                          未生成结构化报告，仅显示原始输出。
                      </div>
                  )}

                  {/* Actions Bar */}
                  <div className="d-flex gap-2 justify-content-end border-top pt-3">
                      <Button variant="outline-secondary" size="sm" onClick={() => setShowScript(!showScript)}>
                          <FaCode className="me-1" /> {showScript ? '隐藏脚本' : '查看脚本'}
                      </Button>
                      <Button variant="outline-secondary" size="sm" onClick={() => setShowLogs(!showLogs)}>
                          <FaTerminal className="me-1" /> {showLogs ? '隐藏日志' : '查看完整日志'}
                      </Button>
                  </div>

                  {/* Script Viewer */}
                  <Collapse in={showScript}>
                      <Card className="border-secondary bg-light">
                          <Card.Header className="py-1 px-2 small fw-bold text-muted">Generated Python Script</Card.Header>
                          <Card.Body className="p-0">
                              <pre className="m-0 p-3 small font-monospace" style={{maxHeight: '300px', overflow: 'auto'}}>
                                  {testResult.script}
                              </pre>
                          </Card.Body>
                      </Card>
                  </Collapse>

                  {/* Log Viewer */}
                  <Collapse in={showLogs}>
                      <Card className="bg-dark text-light border-0">
                          <Card.Header className="py-1 px-2 small fw-bold text-muted border-secondary">Execution Logs (stdout/stderr)</Card.Header>
                          <Card.Body className="p-0">
                              <pre className="m-0 p-3 small font-monospace" style={{maxHeight: '300px', overflow: 'auto'}}>
                                  {testResult.result}
                              </pre>
                          </Card.Body>
                      </Card>
                  </Collapse>
              </div>
          ) : (
              <div className="h-100 d-flex flex-column align-items-center justify-content-center text-muted opacity-50">
                  <FaTerminal size={48} className="mb-3" />
                  <p>配置参数并点击“开始测试”以查看结果</p>
              </div>
          )}
      </div>
    </div>
  );
}

