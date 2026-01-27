import { useState, useRef, useEffect } from 'react';
import { Row, Col, Form, Button, Table, Badge, InputGroup, Card, Dropdown, ButtonGroup, Modal } from 'react-bootstrap';
import { FaGlobe, FaCheck, FaTimes, FaForward, FaSave, FaFilter, FaCalendarAlt, FaMobileAlt, FaRedo, FaUpload, FaPaste, FaPlus, FaTrash, FaRobot, FaWeixin, FaLink, FaEllipsisV, FaDesktop, FaBook } from 'react-icons/fa';
import { api } from '../utils/api';

type Props = {
  projectId: number | null;
  onLog: (msg: string) => void;
  view?: 'web' | 'app' | 'regression' | 'report';
};

type Report = {
  id: number;
  name: string;
  type: 'APP自动化' | 'WEB自动化' | '回归测试';
  status: 'Pass' | 'Fail';
  time: string;
  duration: string;
};

type TestCaseStep = {
    id: string;
    description: string;
    status: 'pending' | 'running' | 'pass' | 'fail' | 'skipped';
    ai_feedback?: string;
};

export function UIAutomation({ projectId, onLog, view = 'web' }: Props) {
  
  // --- Report View State ---
  const [reportFilter, setReportFilter] = useState<'ALL' | 'APP自动化' | 'WEB自动化' | '回归测试'>('ALL');
  const [dateFilter, setDateFilter] = useState('');
  
  // Mock Reports
  const reports: Report[] = [
    { id: 1, name: '登录流程测试', type: 'WEB自动化', status: 'Pass', time: '2024-01-15 10:00', duration: '2m 30s' },
    { id: 2, name: '支付功能验证', type: 'APP自动化', status: 'Fail', time: '2024-01-15 09:45', duration: '1m 15s' },
    { id: 3, name: '全量回归-V1.2', type: '回归测试', status: 'Pass', time: '2024-01-14 18:00', duration: '45m' },
    { id: 4, name: '用户注册', type: 'WEB自动化', status: 'Pass', time: '2024-01-14 14:20', duration: '3m 10s' },
  ];

  const filteredReports = reports.filter(r => {
    if (reportFilter !== 'ALL' && r.type !== reportFilter) return false;
    if (dateFilter && !r.time.startsWith(dateFilter)) return false;
    return true;
  });

  // --- Web View State ---
  const [targetUrl, setTargetUrl] = useState(() => {
      return localStorage.getItem('ui_auto_target_url') || 'https://www.example.com';
  });
  
  const [miniProgramConfig, setMiniProgramConfig] = useState(() => {
      return localStorage.getItem('ui_auto_miniprogram_config') || '';
  });
  
  const [appConnectionMode, setAppConnectionMode] = useState<'simulator' | 'device'>(() => {
      return (localStorage.getItem('ui_auto_app_connection_mode') as 'simulator' | 'device') || 'simulator';
  });
  const [appPackageInfo, setAppPackageInfo] = useState(() => {
      return localStorage.getItem('ui_auto_app_package_info') || '';
  });

  const [imageModel] = useState(() => {
      return localStorage.getItem('ui_auto_image_model') || 'qwen-vl-plus';
  });

  // Persist configurations
  useEffect(() => {
      localStorage.setItem('ui_auto_target_url', targetUrl);
  }, [targetUrl]);

  useEffect(() => {
      localStorage.setItem('ui_auto_miniprogram_config', miniProgramConfig);
  }, [miniProgramConfig]);

  useEffect(() => {
      localStorage.setItem('ui_auto_app_connection_mode', appConnectionMode);
  }, [appConnectionMode]);

  useEffect(() => {
      localStorage.setItem('ui_auto_app_package_info', appPackageInfo);
  }, [appPackageInfo]);

  useEffect(() => {
      localStorage.setItem('ui_auto_image_model', imageModel);
  }, [imageModel]);

  // Derived Execution Mode
  const getExecutionMode = () => {
      if (targetUrl && miniProgramConfig) return 'hybrid';
      if (miniProgramConfig) return 'miniprogram';
      if (targetUrl) return 'web';
      return 'none';
  };

  const [isTracking, setIsTracking] = useState(false);
  const [feedbackType, setFeedbackType] = useState<'fail' | 'skip' | null>(null);
  const [feedbackText, setFeedbackText] = useState('');
  const [savingFeedback, setSavingFeedback] = useState(false);
  
  // New State for Test Cases & Execution
  const [testCases, setTestCases] = useState<TestCaseStep[]>([]);
  const [importText, setImportText] = useState('');
  const [isExecuting, setIsExecuting] = useState(false);
  const [currentStepIndex, setCurrentStepIndex] = useState<number>(-1);
  const [executionLogs, setExecutionLogs] = useState<string[]>([]);
  
  // Import Modal State
  const [showImportModal, setShowImportModal] = useState(false);
  const [importMode, setImportMode] = useState<'file' | 'text'>('file');
  const [importTarget, setImportTarget] = useState<'testcase' | 'requirement'>('testcase');
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Requirement Context State
  const [requirementContext, setRequirementContext] = useState('');
  const [requirementSource, setRequirementSource] = useState('');
  // const [showReqModal, setShowReqModal] = useState(false); // Deprecated in favor of unified Import Modal
  // const [reqImportType, setReqImportType] = useState<'file' | 'text' | 'kb'>('text'); // Merged into importMode

  const addExecutionLog = (msg: string) => {
      const time = new Date().toLocaleTimeString();
      setExecutionLogs(prev => [`${time} - ${msg}`, ...prev]);
      onLog(msg);
  };

  const handleRetrieveAppInfo = async () => {
      addExecutionLog('正在尝试获取当前应用信息...');
      try {
          const response = await api.get<{package: string, activity: string, full_activity: string}>('/api/get-current-app-info');
          if (response && response.package) {
              setAppPackageInfo(response.package);
              addExecutionLog(`获取成功: ${response.package}`);
          } else {
              addExecutionLog('获取失败: 未能识别当前应用');
          }
      } catch (e) {
          addExecutionLog(`获取失败: ${e instanceof Error ? e.message : String(e)}`);
      }
  };

  const handleConnect = async () => {
      // Logic for App View
      if (view === 'app') {
          if (!appPackageInfo) {
              alert('请输入应用程序名或包名');
              return;
          }
          addExecutionLog(`正在连接... 模式: APP自动化 (${appConnectionMode === 'simulator' ? '模拟器' : '真机'})`);
          addExecutionLog(`目标应用: ${appPackageInfo}`);
          
          try {
            await new Promise(resolve => setTimeout(resolve, 1000));
            setIsTracking(true);
            addExecutionLog(`连接成功!`);
            if (testCases.length > 0) startExecution();
          } catch (e) {
              addExecutionLog(`连接失败`);
          }
          return;
      }

      const mode = getExecutionMode();
      if (mode === 'none') {
          alert('请至少填写 目标网址 或 小程序配置 之一');
          return;
      }
      
      // Basic URL validation only if in web/hybrid mode
      if ((mode === 'web' || mode === 'hybrid') && !targetUrl.startsWith('http')) {
          alert('请输入有效的网址 (http:// 或 https://)');
          return;
      }

      addExecutionLog(`正在连接... 模式: ${mode.toUpperCase()}`);
      if (mode === 'hybrid') addExecutionLog(`联动执行: Web(${targetUrl}) + MP(${miniProgramConfig})`);
      else if (mode === 'web') addExecutionLog(`Web执行: ${targetUrl}`);
      else addExecutionLog(`小程序执行: ${miniProgramConfig}`);
      
      try {
          // Simulate Backend Reachability Check
          await new Promise(resolve => setTimeout(resolve, 1000));
          
          // Assume success for demo
          setIsTracking(true);
          addExecutionLog(`连接成功!`);
          
          if (testCases.length > 0) {
             startExecution();
          } else {
             addExecutionLog('未检测到测试用例，等待用户导入...');
          }

      } catch (e) {
          addExecutionLog(`连接失败: ${e instanceof Error ? e.message : String(e)}`);
          alert('无法连接到目标，请检查配置。');
          setIsTracking(false);
      }
  };

  const startExecution = async () => {
      if (testCases.length === 0) return;
      setIsExecuting(true);
      setCurrentStepIndex(0);
      addExecutionLog('开始执行测试用例...');
  };

  // Real Step Execution Effect
  useEffect(() => {
      if (!isExecuting || currentStepIndex < 0 || currentStepIndex >= testCases.length) return;
      
      const step = testCases[currentStepIndex];
      // Only execute if status is pending (prevent re-execution on state updates)
      if (step.status !== 'pending') return;

      const executeStep = async () => {
          // Update status to running
          setTestCases(prev => prev.map((s, i) => i === currentStepIndex ? { ...s, status: 'running' } : s));
          addExecutionLog(`正在执行步骤 ${currentStepIndex + 1}: ${step.description}`);
          
          try {
              // Determine parameters
              let url = targetUrl;
              let type = 'web';
              
              if (view === 'app') {
                  url = appPackageInfo;
                  type = 'app';
              } else {
                  const mode = getExecutionMode();
                  if (mode === 'miniprogram') {
                      url = miniProgramConfig;
                      type = 'app'; // Treat miniprogram as app for now or needs specific type
                  } else if (mode === 'hybrid') {
                      url = targetUrl; // Primary URL
                  }
              }

              const payload = {
                  url: url,
                  task: step.description,
                  project_id: projectId || 1, // Default to 1 if not provided for dev
                  automation_type: type,
                  image_model: imageModel,
                  requirement_context: requirementContext || undefined
              };

              // Call API
              addExecutionLog(`调用 AI 模型: ${imageModel}...`);
              await api.post('/api/ui-automation', payload);
              
              // If successful, auto-advance
              // Note: In a real scenario, we might want to wait for visual confirmation or AI verification
              addExecutionLog(`步骤 ${currentStepIndex + 1} 执行成功`);
              handleStepFeedback('pass');
          } catch (e) {
              addExecutionLog(`执行出错: ${e instanceof Error ? e.message : String(e)}`);
              setTestCases(prev => prev.map((s, i) => i === currentStepIndex ? { ...s, status: 'fail' } : s));
              setIsExecuting(false);
          }
      };

      executeStep();
  }, [isExecuting, currentStepIndex, testCases]); // testCases needed to get current step status

  const handleStepFeedback = (status: 'pass' | 'fail' | 'skipped') => {
      if (currentStepIndex < 0) return;
      
      setTestCases(prev => prev.map((s, i) => i === currentStepIndex ? { ...s, status: status } : s));
      addExecutionLog(`步骤 ${currentStepIndex + 1} 标记为: ${status}`);

      if (status === 'fail' || status === 'skipped') {
          setFeedbackType(status === 'fail' ? 'fail' : 'skip');
          // Pause execution
          setIsExecuting(false);
      } else {
          // Continue to next step
          if (currentStepIndex < testCases.length - 1) {
              setCurrentStepIndex(prev => prev + 1);
          } else {
              setIsExecuting(false);
              addExecutionLog('所有测试用例执行完毕。');
          }
      }
  };

  const openImportModal = (mode: 'file' | 'text', target: 'testcase' | 'requirement' = 'testcase') => {
      setImportMode(mode);
      setImportTarget(target);
      setImportText('');
      setShowImportModal(true);
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;

      addExecutionLog(`正在读取文件: ${file.name}...`);
      
      const reader = new FileReader();
      reader.onload = (event) => {
          const content = event.target?.result as string;
          try {
              let steps: TestCaseStep[] = [];
              if (file.name.endsWith('.json')) {
                  const data = JSON.parse(content);
                  if (Array.isArray(data)) {
                      steps = data.map((d: any, i) => ({
                          id: String(Date.now() + i),
                          description: typeof d === 'string' ? d : d.step || d.description || JSON.stringify(d),
                          status: 'pending'
                      }));
                  }
              } else {
                  // CSV/Text (Simple line split)
                  steps = content.split('\n').filter(l => l.trim()).map((line, i) => ({
                      id: String(Date.now() + i),
                      description: line.trim(),
                      status: 'pending'
                  }));
              }
              
              setTestCases(prev => [...prev, ...steps]);
              addExecutionLog(`成功导入 ${steps.length} 个步骤。`);
              setShowImportModal(false);
          } catch (err) {
              addExecutionLog('文件解析失败');
          }
      };
      reader.readAsText(file);
  };

  const handlePasteImport = () => {
      if (!importText.trim()) return;
      const steps: TestCaseStep[] = importText.split('\n').filter(l => l.trim()).map((line, i) => ({
          id: String(Date.now() + i),
          description: line.trim(),
          status: 'pending'
      }));
      setTestCases(prev => [...prev, ...steps]);
      setImportText('');
      addExecutionLog(`从文本导入 ${steps.length} 个步骤。`);
      setShowImportModal(false);
  };

  const handleAddStep = () => {
      const newStep: TestCaseStep = {
          id: String(Date.now()),
          description: '新步骤 (请编辑)',
          status: 'pending'
      };
      setTestCases(prev => [...prev, newStep]);
  };

  const handleUpdateStep = (id: string, newDesc: string) => {
      setTestCases(prev => prev.map(s => s.id === id ? { ...s, description: newDesc } : s));
  };
  
  const handleDeleteStep = (id: string) => {
      setTestCases(prev => prev.filter(s => s.id !== id));
  };

  const handleClearSteps = () => {
      if (confirm('确定要清空所有步骤吗？')) {
          setTestCases([]);
          addExecutionLog('已清空测试步骤列表。');
      }
  };

  const handleReqFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (event) => {
          setRequirementContext(event.target?.result as string);
          setRequirementSource(file.name);
          setShowImportModal(false);
          addExecutionLog(`已关联需求文档: ${file.name}`);
      };
      reader.readAsText(file);
  };

  const handleSaveFeedback = async () => {
    if (!feedbackText.trim() || !projectId) return;
    setSavingFeedback(true);
    try {
        const blob = new Blob([`Type: ${feedbackType}\nURL: ${targetUrl}\nMP: ${miniProgramConfig}\nFeedback: ${feedbackText}`], { type: 'text/plain' });
        const file = new File([blob], `feedback_${Date.now()}.txt`);
        
        const formData = new FormData();
        formData.append('file', file);
        formData.append('doc_type', 'feedback');
        formData.append('project_id', String(projectId));

        await api.post('/api/upload-knowledge', formData);
        
        addExecutionLog(`已保存反馈到知识库: ${feedbackType} - ${feedbackText}`);
        setFeedbackText('');
        setFeedbackType(null);
    } catch (e) {
        console.error(e);
        addExecutionLog(`保存反馈失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
        setSavingFeedback(false);
    }
  };

  const ReportView = () => (
    <div className="h-100 d-flex flex-column p-4">
        <div className="d-flex justify-content-between align-items-center mb-4">
            <h4 className="mb-0 fw-bold text-primary">UI测试报告</h4>
            <div className="d-flex gap-3">
                <Dropdown>
                    <Dropdown.Toggle variant="light" className="d-flex align-items-center gap-2 border shadow-sm">
                        <FaFilter className="text-secondary" />
                        {reportFilter === 'ALL' ? '所有类型' : reportFilter}
                    </Dropdown.Toggle>
                    <Dropdown.Menu>
                        <Dropdown.Item onClick={() => setReportFilter('ALL')}>所有类型</Dropdown.Item>
                        <Dropdown.Item onClick={() => setReportFilter('APP自动化')}>APP自动化</Dropdown.Item>
                        <Dropdown.Item onClick={() => setReportFilter('WEB自动化')}>WEB自动化</Dropdown.Item>
                        <Dropdown.Item onClick={() => setReportFilter('回归测试')}>回归测试</Dropdown.Item>
                    </Dropdown.Menu>
                </Dropdown>
                <InputGroup style={{ width: '200px' }}>
                    <InputGroup.Text className="bg-white border-end-0">
                        <FaCalendarAlt className="text-secondary" />
                    </InputGroup.Text>
                    <Form.Control 
                        type="date" 
                        className="border-start-0 shadow-sm"
                        value={dateFilter}
                        onChange={(e) => setDateFilter(e.target.value)}
                    />
                </InputGroup>
            </div>
        </div>
        
        <Card className="flex-grow-1 border-0 shadow-sm overflow-hidden">
            <div className="table-responsive h-100">
                <Table hover className="align-middle mb-0">
                    <thead className="bg-light sticky-top">
                        <tr>
                            <th className="ps-4">任务名称</th>
                            <th>类型</th>
                            <th>状态</th>
                            <th>执行时间</th>
                            <th>耗时</th>
                            <th>操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        {filteredReports.map(report => (
                            <tr key={report.id}>
                                <td className="ps-4 fw-medium">{report.name}</td>
                                <td>
                                    <Badge bg={
                                        report.type === 'WEB自动化' ? 'info' : 
                                        report.type === 'APP自动化' ? 'primary' : 'warning'
                                    }>
                                        {report.type}
                                    </Badge>
                                </td>
                                <td>
                                    {report.status === 'Pass' ? 
                                        <Badge bg="success" className="d-flex align-items-center gap-1 w-fit"><FaCheck /> Pass</Badge> : 
                                        <Badge bg="danger" className="d-flex align-items-center gap-1 w-fit"><FaTimes /> Fail</Badge>
                                    }
                                </td>
                                <td className="text-secondary small">{report.time}</td>
                                <td className="text-secondary small">{report.duration}</td>
                                <td>
                                    <Button variant="link" size="sm" className="text-decoration-none">查看详情</Button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </Table>
                {filteredReports.length === 0 && (
                    <div className="text-center text-muted mt-5">暂无符合条件的报告</div>
                )}
            </div>
        </Card>
    </div>
  );

  const WebAutomationView = () => {
    const mode = getExecutionMode();
    return (
    <div className="h-100 d-flex flex-column">
        {/* Main Split Content */}
        <Row className="g-0 flex-grow-1 overflow-hidden h-100">
            {/* Left: Real-time View & Controls & Logs */}
            <Col md={9} className="d-flex flex-column bg-light border-end position-relative h-100">
                {/* 0. Top Bar (Dual Input) */}
                <div className="bg-light border-bottom px-3 d-flex gap-2 align-items-center py-2" style={{ height: 'auto', minHeight: '50px' }}>
                    <div className="d-flex flex-column flex-grow-1 gap-2">
                         <div className="d-flex gap-2">
                             <InputGroup size="sm" className="flex-grow-1">
                                <InputGroup.Text className="bg-white text-secondary" title="小程序配置"><FaWeixin /></InputGroup.Text>
                                <Form.Control 
                                    value={miniProgramConfig}
                                    onChange={(e) => setMiniProgramConfig(e.target.value)}
                                    placeholder="小程序 AppID 或 启动路径 (选填)..."
                                />
                             </InputGroup>
                             <InputGroup size="sm" className="flex-grow-1">
                                <InputGroup.Text className="bg-white text-secondary" title="Web目标网址"><FaGlobe /></InputGroup.Text>
                                <Form.Control 
                                    value={targetUrl}
                                    onChange={(e) => setTargetUrl(e.target.value)}
                                    placeholder="Web 目标网址 (选填)..."
                                />
                             </InputGroup>
                         </div>
                         {/* Mode Indicators */}
                         <div className="d-flex align-items-center gap-2 small">
                             <span className="text-muted" style={{fontSize: '0.8em'}}>当前模式:</span>
                             {mode === 'none' && <Badge bg="secondary">未配置</Badge>}
                             {mode === 'web' && <Badge bg="info"><FaGlobe className="me-1"/>Web 自动化</Badge>}
                             {mode === 'miniprogram' && <Badge bg="success"><FaWeixin className="me-1"/>小程序自动化</Badge>}
                             {mode === 'hybrid' && <Badge bg="primary"><FaLink className="me-1"/>Web + 小程序联动</Badge>}
                         </div>
                    </div>
                    <Button variant="primary" onClick={handleConnect} disabled={isTracking} className="align-self-start mt-1">
                        {isTracking ? '重新连接' : '开始连接'}
                    </Button>
                </div>

                {/* 1. Viewport (Enlarged) */}
                <div className="flex-grow-1 d-flex align-items-center justify-content-center overflow-hidden" style={{ position: 'relative' }}>
                    {isTracking ? (
                        <div className="w-100 h-100 bg-white border rounded shadow-sm position-relative overflow-hidden">
                             {/* Mock Viewport Content based on Mode */}
                             <div className="w-100 h-100 d-flex flex-column">
                                 {/* Web View */}
                                 {(mode === 'web' || mode === 'hybrid') && (
                                     <iframe 
                                        src={targetUrl.startsWith('http') ? targetUrl : `https://${targetUrl}`} 
                                        title="Target View"
                                        style={{ width: '100%', height: mode === 'hybrid' ? '50%' : '100%', border: 'none', pointerEvents: 'none', opacity: 0.7 }}
                                     />
                                 )}
                                 {/* Mini Program View (Mock) */}
                                 {(mode === 'miniprogram' || mode === 'hybrid') && (
                                     <div className={`bg-dark text-white d-flex align-items-center justify-content-center ${mode === 'hybrid' ? 'border-top' : ''}`} style={{ flex: 1 }}>
                                         <div className="text-center opacity-50">
                                             <FaWeixin size={32} className="mb-2"/>
                                             <div>正在运行小程序: {miniProgramConfig}</div>
                                         </div>
                                     </div>
                                 )}
                             </div>

                             {isExecuting && (
                                 <div className="position-absolute bottom-0 start-50 translate-middle-x mb-3 bg-dark text-white px-3 py-1 rounded-pill small opacity-75">
                                     AI 正在执行: 步骤 {currentStepIndex + 1}
                                 </div>
                             )}
                        </div>
                    ) : (
                        <div className="text-center text-secondary">
                            <FaGlobe size={48} className="mb-3 opacity-25" />
                            <p>请配置左上角参数并点击“开始连接”</p>
                        </div>
                    )}
                </div>

                {/* 2. Controls */}
                <div className="bg-white border-top p-2 d-flex justify-content-between align-items-center">
                    <div className="fw-bold small text-secondary">人工审核与反馈</div>
                    <ButtonGroup size="sm">
                        <Button 
                            variant="outline-success" 
                            className="d-flex align-items-center gap-2"
                            onClick={() => handleStepFeedback('pass')}
                            disabled={!isExecuting}
                        >
                            <FaCheck /> Pass
                        </Button>
                        <Button 
                            variant={feedbackType === 'fail' ? 'danger' : 'outline-danger'}
                            className="d-flex align-items-center gap-2"
                            onClick={() => handleStepFeedback('fail')}
                            disabled={!isExecuting && !feedbackType}
                        >
                            <FaTimes /> Fail
                        </Button>
                        <Button 
                            variant={feedbackType === 'skip' ? 'warning' : 'outline-warning'}
                            className="d-flex align-items-center gap-2"
                            onClick={() => handleStepFeedback('skipped')}
                            disabled={!isExecuting && !feedbackType}
                        >
                            <FaForward /> Skip
                        </Button>
                    </ButtonGroup>
                </div>

                {/* 3. Feedback Input (Conditional) */}
                {feedbackType && (
                    <div className="bg-light border-top p-2 animate-slide-up">
                        <Form.Label className="small fw-bold text-secondary mb-1">
                            {feedbackType === 'fail' ? '请填写失败原因 (将存入知识库)' : '请填写跳过原因 (将存入知识库)'}
                        </Form.Label>
                        <InputGroup size="sm">
                            <Form.Control
                                as="textarea"
                                rows={2}
                                value={feedbackText}
                                onChange={(e) => setFeedbackText(e.target.value)}
                                placeholder="详细描述问题..."
                            />
                            <Button variant="primary" onClick={handleSaveFeedback} disabled={savingFeedback}>
                                {savingFeedback ? '保存中...' : <><FaSave /> 保存</>}
                            </Button>
                        </InputGroup>
                    </div>
                )}
                
                {/* 4. Execution Logs */}
                <div className="bg-white border-top p-0 d-flex flex-column" style={{ height: '100px' }}>
                    <div className="p-1 border-bottom bg-light small fw-bold text-secondary ps-2">执行详情</div>
                    <div className="flex-grow-1 p-2 overflow-auto font-monospace small bg-white">
                        {executionLogs.length === 0 ? (
                            <div className="text-muted fst-italic">暂无日志...</div>
                        ) : (
                            executionLogs.map((log, idx) => (
                                <div key={idx} className="mb-1 text-nowrap">{log}</div>
                            ))
                        )}
                    </div>
                </div>
            </Col>

            {/* Right: Test Case Management */}
            <Col md={3} className="d-flex flex-column bg-white border-start h-100">
                {/* Header with Dropdown */}
                <div className="border-bottom bg-light px-2 d-flex justify-content-between align-items-center" style={{ height: '50px' }}>
                     <div className="fw-bold text-secondary small text-nowrap">用例 ({testCases.length})</div>
                     <div className="d-flex gap-1">
                        <Button size="sm" variant="outline-primary" onClick={handleAddStep} className="d-flex align-items-center gap-1">
                            <FaPlus size={10} />
                        </Button>
                        <Dropdown align="end">
                            <Dropdown.Toggle variant="outline-primary" size="sm" className="d-flex align-items-center gap-1">
                                <FaEllipsisV />
                            </Dropdown.Toggle>
                            <Dropdown.Menu>
                                <Dropdown.Header>导入</Dropdown.Header>
                                <Dropdown.Item onClick={() => openImportModal('file')}><FaUpload className="me-2 text-secondary"/>文件导入</Dropdown.Item>
                                <Dropdown.Item onClick={() => openImportModal('text')}><FaPaste className="me-2 text-secondary"/>文本粘贴</Dropdown.Item>
                                <Dropdown.Divider />
                                <Dropdown.Item onClick={handleClearSteps} className="text-danger"><FaTrash className="me-2"/>清空列表</Dropdown.Item>
                            </Dropdown.Menu>
                        </Dropdown>
                     </div>
                </div>

                {requirementSource && (
                    <div className="bg-success bg-opacity-10 border-bottom px-2 py-1 small d-flex justify-content-between align-items-center">
                        <span className="text-success text-truncate" title={requirementSource}><FaBook className="me-1"/>{requirementSource}</span>
                        <Button variant="link" size="sm" className="text-secondary p-0" onClick={() => { setRequirementContext(''); setRequirementSource(''); }}><FaTimes /></Button>
                    </div>
                )}

                <div className="flex-grow-1 overflow-auto p-2 bg-light">
                     {testCases.map((step, idx) => (
                         <Card key={step.id} className={`mb-2 border-0 shadow-sm ${idx === currentStepIndex ? 'border-start border-4 border-primary' : ''}`}>
                             <Card.Body className="p-2">
                                 <div className="d-flex gap-2">
                                     <Badge bg="secondary" className="align-self-start mt-1">#{idx + 1}</Badge>
                                     <div className="flex-grow-1">
                                         <Form.Control 
                                            as="textarea" 
                                            rows={2} 
                                            className="border-0 bg-transparent p-0 small"
                                            value={step.description}
                                            onChange={(e) => handleUpdateStep(step.id, e.target.value)}
                                            style={{ resize: 'none' }}
                                         />
                                     </div>
                                     <div className="d-flex flex-column gap-1">
                                         <Button variant="link" size="sm" className="text-danger p-0" onClick={() => handleDeleteStep(step.id)}>
                                             <FaTrash size={10} />
                                         </Button>
                                     </div>
                                 </div>
                                 <div className="d-flex justify-content-between align-items-center mt-2 border-top pt-1">
                                     <Badge bg={
                                         step.status === 'pass' ? 'success' :
                                         step.status === 'fail' ? 'danger' :
                                         step.status === 'running' ? 'primary' :
                                         step.status === 'skipped' ? 'warning' : 'secondary'
                                     } pill className="fw-normal" style={{fontSize: '0.7em'}}>
                                         {step.status.toUpperCase()}
                                     </Badge>
                                     {step.status === 'running' && <Spinner size="sm" animation="border" className="text-primary" style={{width:'0.8rem', height:'0.8rem'}} />}
                                 </div>
                             </Card.Body>
                         </Card>
                     ))}
                     {testCases.length === 0 && (
                         <div className="text-center text-muted mt-5">
                             <FaRobot size={32} className="mb-2 opacity-25" />
                             <p className="small">暂无测试步骤，请点击右上角导入或添加</p>
                         </div>
                     )}
                </div>
            </Col>
        </Row>

    </div>
    );
  };

  const AppAutomationView = () => {
    return (
    <div className="h-100 d-flex flex-column">
        {/* Main Split Content */}
        <Row className="g-0 flex-grow-1 overflow-hidden h-100">
            {/* Left: Real-time View & Controls & Logs */}
            <Col md={9} className="d-flex flex-column bg-light border-end position-relative h-100">
                {/* 0. Top Bar (App Config) */}
                <div className="bg-light border-bottom px-3 d-flex gap-2 align-items-center py-2" style={{ height: 'auto', minHeight: '50px' }}>
                    <div className="d-flex flex-column flex-grow-1 gap-2">
                         <div className="d-flex gap-2">
                             <Form.Select 
                                size="sm" 
                                style={{ width: '140px' }}
                                value={appConnectionMode}
                                onChange={(e) => setAppConnectionMode(e.target.value as 'simulator' | 'device')}
                                className="border-secondary"
                             >
                                <option value="simulator">模拟器连接</option>
                                <option value="device">手机连接</option>
                             </Form.Select>
                             <InputGroup size="sm" className="flex-grow-1">
                                <InputGroup.Text className="bg-white text-secondary" title="应用信息"><FaMobileAlt /></InputGroup.Text>
                                <Form.Control 
                                    value={appPackageInfo}
                                    onChange={(e) => setAppPackageInfo(e.target.value)}
                                    placeholder="输入应用程序名 或 包名 (例如 com.example.app)..."
                                />
                                <Button variant="outline-secondary" onClick={handleRetrieveAppInfo} title="自动获取当前应用" disabled={isTracking}>
                                    <FaRedo /> 检索
                                </Button>
                             </InputGroup>
                         </div>
                    </div>
                    <Button variant={isTracking ? "danger" : "primary"} size="sm" onClick={handleConnect} disabled={isTracking} className="d-flex align-items-center gap-1 text-nowrap" style={{ height: '31px' }}>
                        {isTracking ? <><FaTimes /> 停止</> : <><FaLink /> 开始连接</>}
                    </Button>
                </div>

                {/* 1. Viewport (Enlarged) */}
                <div className="flex-grow-1 d-flex align-items-center justify-content-center overflow-hidden" style={{ position: 'relative' }}>
                    {isTracking ? (
                        <div className="w-100 h-100 bg-white border rounded shadow-sm position-relative overflow-hidden">
                             {/* Mock Viewport Content based on Mode */}
                             <div className="w-100 h-100 d-flex flex-column bg-dark text-white align-items-center justify-content-center">
                                 <div className="text-center opacity-50">
                                     {appConnectionMode === 'simulator' ? <FaDesktop size={48} className="mb-3"/> : <FaMobileAlt size={48} className="mb-3"/>}
                                     <div className="h5">{appConnectionMode === 'simulator' ? '模拟器运行中' : '真机调试中'}</div>
                                     <div className="font-monospace small mt-2">{appPackageInfo}</div>
                                 </div>
                             </div>

                             {isExecuting && (
                                 <div className="position-absolute bottom-0 start-50 translate-middle-x mb-3 bg-dark text-white px-3 py-1 rounded-pill small opacity-75">
                                     AI 正在执行: 步骤 {currentStepIndex + 1}
                                 </div>
                             )}
                        </div>
                    ) : (
                        <div className="text-center text-secondary">
                            <FaGlobe size={48} className="mb-3 opacity-25" />
                            <p>请配置左上角参数并点击“开始连接”</p>
                        </div>
                    )}
                </div>

                {/* 2. Controls */}
                <div className="bg-white border-top p-2 d-flex justify-content-between align-items-center">
                    <div className="fw-bold small text-secondary">人工审核与反馈</div>
                    <ButtonGroup size="sm">
                        <Button 
                            variant="outline-success" 
                            className="d-flex align-items-center gap-2"
                            onClick={() => handleStepFeedback('pass')}
                            disabled={!isExecuting}
                        >
                            <FaCheck /> Pass
                        </Button>
                        <Button 
                            variant={feedbackType === 'fail' ? 'danger' : 'outline-danger'}
                            className="d-flex align-items-center gap-2"
                            onClick={() => handleStepFeedback('fail')}
                            disabled={!isExecuting && !feedbackType}
                        >
                            <FaTimes /> Fail
                        </Button>
                        <Button 
                            variant={feedbackType === 'skip' ? 'warning' : 'outline-warning'}
                            className="d-flex align-items-center gap-2"
                            onClick={() => handleStepFeedback('skipped')}
                            disabled={!isExecuting && !feedbackType}
                        >
                            <FaForward /> Skip
                        </Button>
                    </ButtonGroup>
                </div>

                {/* 3. Feedback Input (Conditional) */}
                {feedbackType && (
                    <div className="bg-light border-top p-2 animate-slide-up">
                        <Form.Label className="small fw-bold text-secondary mb-1">
                            {feedbackType === 'fail' ? '请填写失败原因 (将存入知识库)' : '请填写跳过原因 (将存入知识库)'}
                        </Form.Label>
                        <InputGroup size="sm">
                            <Form.Control
                                as="textarea"
                                rows={2}
                                value={feedbackText}
                                onChange={(e) => setFeedbackText(e.target.value)}
                                placeholder="详细描述问题..."
                            />
                            <Button variant="primary" onClick={handleSaveFeedback} disabled={savingFeedback}>
                                {savingFeedback ? '保存中...' : <><FaSave /> 保存</>}
                            </Button>
                        </InputGroup>
                    </div>
                )}
                
                {/* 4. Execution Logs */}
                <div className="bg-white border-top p-0 d-flex flex-column" style={{ height: '100px' }}>
                    <div className="p-1 border-bottom bg-light small fw-bold text-secondary ps-2">执行详情</div>
                    <div className="flex-grow-1 p-2 overflow-auto font-monospace small bg-white">
                        {executionLogs.length === 0 ? (
                            <div className="text-muted fst-italic">暂无日志...</div>
                        ) : (
                            executionLogs.map((log, idx) => (
                                <div key={idx} className="mb-1 text-nowrap">{log}</div>
                            ))
                        )}
                    </div>
                </div>
            </Col>

            {/* Right: Test Case Management */}
            <Col md={3} className="d-flex flex-column bg-white border-start h-100">
                {/* Header with Dropdown */}
                <div className="border-bottom bg-light px-2 d-flex justify-content-between align-items-center" style={{ height: '50px' }}>
                     <div className="fw-bold text-secondary small text-nowrap">用例 ({testCases.length})</div>
                     <div className="d-flex gap-1">
                        <Button size="sm" variant="outline-primary" onClick={handleAddStep} className="d-flex align-items-center gap-1">
                            <FaPlus size={10} />
                        </Button>
                        <Dropdown align="end">
                            <Dropdown.Toggle variant="outline-primary" size="sm" className="d-flex align-items-center gap-1">
                                <FaEllipsisV />
                            </Dropdown.Toggle>
                            <Dropdown.Menu>
                                <Dropdown.Header>导入</Dropdown.Header>
                                <Dropdown.Item onClick={() => openImportModal('file')}><FaUpload className="me-2 text-secondary"/>文件导入</Dropdown.Item>
                                <Dropdown.Item onClick={() => openImportModal('text')}><FaPaste className="me-2 text-secondary"/>文本粘贴</Dropdown.Item>
                                <Dropdown.Divider />
                                <Dropdown.Item onClick={handleClearSteps} className="text-danger"><FaTrash className="me-2"/>清空列表</Dropdown.Item>
                            </Dropdown.Menu>
                        </Dropdown>
                     </div>
                </div>

                {requirementSource && (
                    <div className="bg-success bg-opacity-10 border-bottom px-2 py-1 small d-flex justify-content-between align-items-center">
                        <span className="text-success text-truncate" title={requirementSource}><FaBook className="me-1"/>{requirementSource}</span>
                        <Button variant="link" size="sm" className="text-secondary p-0" onClick={() => { setRequirementContext(''); setRequirementSource(''); }}><FaTimes /></Button>
                    </div>
                )}

                <div className="flex-grow-1 overflow-auto p-2 bg-light">
                     {testCases.map((step, idx) => (
                         <Card key={step.id} className={`mb-2 border-0 shadow-sm ${idx === currentStepIndex ? 'border-start border-4 border-primary' : ''}`}>
                             <Card.Body className="p-2">
                                 <div className="d-flex gap-2">
                                     <Badge bg="secondary" className="align-self-start mt-1">#{idx + 1}</Badge>
                                     <div className="flex-grow-1">
                                         <Form.Control 
                                            as="textarea" 
                                            rows={2} 
                                            className="border-0 bg-transparent p-0 small"
                                            value={step.description}
                                            onChange={(e) => handleUpdateStep(step.id, e.target.value)}
                                            style={{ resize: 'none' }}
                                         />
                                     </div>
                                     <div className="d-flex flex-column gap-1">
                                         <Button variant="link" size="sm" className="text-danger p-0" onClick={() => handleDeleteStep(step.id)}>
                                             <FaTrash size={10} />
                                         </Button>
                                     </div>
                                 </div>
                                 <div className="d-flex justify-content-between align-items-center mt-2 border-top pt-1">
                                     <Badge bg={
                                         step.status === 'pass' ? 'success' :
                                         step.status === 'fail' ? 'danger' :
                                         step.status === 'running' ? 'primary' :
                                         step.status === 'skipped' ? 'warning' : 'secondary'
                                     } pill className="fw-normal" style={{fontSize: '0.7em'}}>
                                         {step.status.toUpperCase()}
                                     </Badge>
                                     {step.status === 'running' && <Spinner size="sm" animation="border" className="text-primary" style={{width:'0.8rem', height:'0.8rem'}} />}
                                 </div>
                             </Card.Body>
                         </Card>
                     ))}
                     {testCases.length === 0 && (
                         <div className="text-center text-muted mt-5">
                             <FaRobot size={32} className="mb-2 opacity-25" />
                             <p className="small">暂无测试步骤，请点击右上角导入或添加</p>
                         </div>
                     )}
                </div>
            </Col>
        </Row>

    </div>
    );
  };

  const PlaceholderView = ({ title, icon, children }: { title: string, icon: any, children?: React.ReactNode }) => (
    <div className="h-100 d-flex flex-column align-items-center justify-content-center text-secondary p-5">
        <div className="fs-1 mb-3 opacity-25">{icon}</div>
        <h4 className="mb-3">{title}</h4>
        {children ? children : <p>该模块功能正在开发中...</p>}
    </div>
  );

  return (
    <div className="h-100 w-100 bg-white">
       {view === 'report' && <ReportView />}
       {view === 'web' && <WebAutomationView />}
       {view === 'app' && <AppAutomationView />}
       {view === 'regression' && <PlaceholderView title="回归测试" icon={<FaRedo />} />}

        {/* Unified Import Modal - Shared across views */}
        <Modal show={showImportModal} onHide={() => setShowImportModal(false)} centered>
            <Modal.Header closeButton>
                <Modal.Title className="h5">
                    {(importMode === 'file' || importTarget === 'requirement') ? '文件导入' : '文本导入'}
                </Modal.Title>
            </Modal.Header>
            <Modal.Body>
                {/* Target Selector */}
                <div className="d-flex justify-content-center mb-4">
                    <ButtonGroup>
                        <Button 
                            variant={importTarget === 'testcase' ? 'primary' : 'outline-primary'} 
                            onClick={() => setImportTarget('testcase')}
                            size="sm"
                        >
                            导入测试用例
                        </Button>
                        <Button 
                            variant={importTarget === 'requirement' ? 'primary' : 'outline-primary'} 
                            onClick={() => { setImportTarget('requirement'); setImportMode('file'); }}
                            size="sm"
                        >
                            关联需求文档
                        </Button>
                    </ButtonGroup>
                </div>

                {(importMode === 'file' || importTarget === 'requirement') ? (
                    <div>
                        <Form.Control 
                            type="file" 
                            ref={fileInputRef}
                            onChange={importTarget === 'testcase' ? handleFileUpload : handleReqFileUpload}
                            accept={importTarget === 'testcase' ? ".json,.csv,.txt" : ".txt,.md,.json,.pdf"}
                        />
                        <Form.Text className="text-muted mt-2 d-block">
                            {importTarget === 'testcase' 
                                ? "支持 JSON, CSV, TXT 格式。" 
                                : "支持 TXT, Markdown, JSON, PDF (纯文本) 格式。"}
                        </Form.Text>
                    </div>
                ) : (
                    <div>
                        <Form.Control 
                            as="textarea" 
                            rows={8} 
                            placeholder="在此粘贴测试用例步骤，每行一步..."
                            value={importText}
                            onChange={(e) => setImportText(e.target.value)}
                        />
                    </div>
                )}
            </Modal.Body>
            <Modal.Footer>
                <Button variant="secondary" onClick={() => setShowImportModal(false)}>取消</Button>
                {importMode === 'text' && importTarget === 'testcase' && (
                    <Button variant="primary" onClick={handlePasteImport} disabled={!importText.trim()}>
                        解析并导入
                    </Button>
                )}
            </Modal.Footer>
        </Modal>
    </div>
  );
}

// Helper component for spinner
function Spinner(props: any) {
    return <div className={`spinner-border ${props.className}`} role="status" style={props.style}></div>;
}
