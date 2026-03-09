import React, { useState, useEffect, useRef } from 'react';
import { Button, Form, InputGroup, Modal, Spinner } from 'react-bootstrap';
import { FaPlay, FaHistory, FaGlobe, FaMobileAlt, FaBug, FaMagic, FaBars, FaChevronLeft, FaFolderPlus, FaFile } from 'react-icons/fa';
import { api } from '../utils/api';
import { ScriptEditor } from './UIAutomation/ScriptEditor';
import { LivePreview } from './UIAutomation/LivePreview';
import { ReportDetail } from './UIAutomation/ReportDetail';
import { HistoryList, type HistoryListHandle } from './UIAutomation/HistoryList';

// Types
interface UIExecution {
    id: number;
    automation_type: string;
    status: string;
    generated_script?: string;
    execution_result?: string;
    screenshot_paths: string[];
    quality_score?: number;
    evaluation_result?: any;
    created_at: string;
    task_description?: string;
}

interface UITestCase {
    id: number;
    type: 'folder' | 'file';
    script_content?: string;
    requirements?: string;
}

interface UIAutomationProps {
    projectId: number | null;
    onLog: (msg: string) => void;
    view?: string; // 'web' | 'app' | 'report'
}

export const UIAutomation: React.FC<UIAutomationProps> = ({ projectId, onLog, view = 'web' }) => {
    // -- State --
    // Map 'regression' to 'report' view logic internally
    const effectiveView = view === 'regression' ? 'report' : view;
    
    const [currentScript, setCurrentScript] = useState('');
    const [targetUrl, setTargetUrl] = useState('');
    const [appConfig, setAppConfig] = useState('');
    const [isGenerating, setIsGenerating] = useState(false);
    const [executionId, setExecutionId] = useState<number | null>(null);
    const [executionStatus, setExecutionStatus] = useState<string>('idle');
    const [logs, setLogs] = useState('');
    const [screenshots, setScreenshots] = useState<string[]>([]);
    const [showImportModal, setShowImportModal] = useState(false);
    const [importText, setImportText] = useState('');
    const [selectedReport, setSelectedReport] = useState<UIExecution | null>(null);
    const [selectedCaseId, setSelectedCaseId] = useState<number | null>(null);

    // -- Layout State --
    const [showSidebar, setShowSidebar] = useState(true);
    const [sidebarWidth, setSidebarWidth] = useState(250);
    const [isResizingSidebar, setIsResizingSidebar] = useState(false);
    const sidebarResizeStartRef = useRef<{ x: number; width: number } | null>(null);
    const historyListRef = useRef<HistoryListHandle>(null);

    // -- Polling for Execution Status --
    useEffect(() => {
        let interval: ReturnType<typeof setInterval> | undefined;
        if (executionId && (executionStatus === 'running' || executionStatus === 'pending')) {
            interval = setInterval(async () => {
                try {
                    const data = await api.get<UIExecution>(`/api/ui-automation/${executionId}`);
                    setExecutionStatus(data.status);
                    setLogs(data.execution_result || '');
                    setScreenshots(data.screenshot_paths || []);
                    
                    if (data.status === 'success' || data.status === 'failed') {
                        if (effectiveView === 'report' && selectedReport?.id === executionId) {
                            setSelectedReport(data);
                        }
                    }
                } catch (e) {
                    console.error("Poll failed", e);
                }
            }, 2000);
        }
        return () => clearInterval(interval);
    }, [executionId, executionStatus, effectiveView, selectedReport]);

    // -- Sidebar Resize Logic --
    useEffect(() => {
        const handleGlobalMouseMove = (e: globalThis.MouseEvent) => {
            if (!isResizingSidebar || !sidebarResizeStartRef.current) return;
            const dx = e.clientX - sidebarResizeStartRef.current.x;
            const next = Math.max(150, Math.min(500, sidebarResizeStartRef.current.width + dx));
            setSidebarWidth(next);
        };

        const handleGlobalMouseUp = () => {
            setIsResizingSidebar(false);
            sidebarResizeStartRef.current = null;
        };

        if (isResizingSidebar) {
            document.addEventListener('mousemove', handleGlobalMouseMove);
            document.addEventListener('mouseup', handleGlobalMouseUp);
        }

        return () => {
            document.removeEventListener('mousemove', handleGlobalMouseMove);
            document.removeEventListener('mouseup', handleGlobalMouseUp);
        };
    }, [isResizingSidebar]);

    // -- Handlers --

    const [requirements, setRequirements] = useState('');

    const handleGenerate = async () => {
        if (!projectId) return;
        setIsGenerating(true);
        try {
            // Try to get test cases from localStorage if empty
            let cases = importText;
            if (!cases) {
                // ... (auto fetch logic)
                 try {
                     // Try to fetch latest test generation from backend
                     const gens = await api.get<any[]>(`/api/test-generations?project_id=${projectId}`);
                     if (gens && gens.length > 0) {
                         const latest = gens[0];
                         const detail = await api.get<any>(`/api/test-generations/${latest.id}`);
                         // Prefer generated result (JSON/Text), fallback to requirement
                         cases = detail.generated_result || detail.raw || detail.requirement_text;
                         onLog(`Auto-imported test cases from generation #${latest.id}`);
                     }
                 } catch (e) {
                     console.warn("Failed to fetch latest test generation, falling back to empty", e);
                 }
            }
            
            setRequirements(cases); // Update requirements view

            const payload = {
                project_id: projectId,
                task: cases || "Perform a general smoke test",
                url: effectiveView === 'web' ? targetUrl : appConfig,
                automation_type: effectiveView === 'app' ? 'app' : 'web'
            };
            
            const res = await api.post<{ script: string }>('/api/ui-automation/generate', payload);
            setCurrentScript(res.script || '');
            setExecutionId(null);
            setExecutionStatus('created');
            setLogs('');
            setScreenshots([]);
            onLog('Script generated.');
        } catch (e) {
            onLog(`Generation failed: ${e instanceof Error ? e.message : String(e)}`);
            alert('Failed to generate script. Please check backend logs.');
        } finally {
            setIsGenerating(false);
            setShowImportModal(false);
        }
    };

    const handleRun = async () => {
        if (!projectId || !currentScript.trim()) {
            alert('No script loaded. Please generate or select a script first.');
            return;
        }
        setExecutionStatus('running');
        try {
            const form = new FormData();
            form.append('script', currentScript);
            form.append('task', requirements || 'Perform a general smoke test');
            form.append('url', effectiveView === 'web' ? targetUrl : appConfig);
            form.append('automation_type', effectiveView === 'app' ? 'app' : 'web');
            form.append('project_id', String(projectId));
            if (selectedCaseId) {
                form.append('test_case_id', String(selectedCaseId));
            }

            const res = await api.upload<any>('/api/ui-automation/execute', form);
            setExecutionStatus(res.status || 'failed');
            setLogs(`STDOUT:\n${res.stdout || ''}\n\nSTDERR:\n${res.stderr || ''}`);
            setScreenshots(Array.isArray(res.screenshot_paths) ? res.screenshot_paths : []);

            if (res.execution_id) {
                setExecutionId(res.execution_id);
                try {
                    const detail = await api.get<UIExecution>(`/api/ui-automation/${res.execution_id}`);
                    setSelectedReport(detail);
                } catch (e) {
                    console.warn('Failed to fetch execution detail', e);
                }
            }

            onLog(`Execution finished: ${res.status || 'unknown'}`);
        } catch (e) {
            onLog(`Execution failed to start: ${e instanceof Error ? e.message : String(e)}`);
            setExecutionStatus('failed');
        }
    };

    const handleHistorySelect = (item: UITestCase) => {
        if (item.type !== 'file') {
            return;
        }
        setSelectedCaseId(item.id);
        setCurrentScript(item.script_content || '');
        setRequirements(item.requirements || '');
        setExecutionId(null);
        setExecutionStatus('idle');
        setLogs('');
        setScreenshots([]);
        if (effectiveView === 'report') {
            setSelectedReport(null);
        }
    };

    // -- Render Helpers --

    const renderAutomationView = (type: 'web' | 'app') => (
        <div className="h-100 d-flex overflow-hidden">
            {/* Left: History Sidebar (Resizable) */}
            <div 
                className="border-end bg-light d-flex flex-column position-relative flex-shrink-0"
                style={{ 
                    width: showSidebar ? `${sidebarWidth}px` : '0px', 
                    transition: isResizingSidebar ? 'none' : 'width 0.2s ease',
                    overflow: 'hidden'
                }}
            >
                <div className="d-flex justify-content-between align-items-center p-2 border-bottom">
                    <div className="d-flex align-items-center gap-2">
                        <span className="fw-bold small text-secondary"><FaHistory className="me-1"/>历史记录</span>
                        <FaFolderPlus className="text-secondary cursor-pointer" title="新建根文件夹" onClick={() => historyListRef.current?.openCreateModal('folder')} size={14} />
                        <FaFile className="text-secondary cursor-pointer" title="新建根脚本" onClick={() => historyListRef.current?.openCreateModal('file')} size={12} />
                    </div>
                    <Button variant="link" size="sm" className="p-0 text-secondary" onClick={() => setShowSidebar(false)}>
                        <FaChevronLeft size={12}/>
                    </Button>
                </div>
                <div className="flex-grow-1 overflow-hidden">
                    <HistoryList 
                        ref={historyListRef}
                        projectId={projectId} 
                        onSelect={handleHistorySelect} 
                        filterType={type}
                    />
                </div>
                {/* Resize Handle */}
                {showSidebar && (
                    <div
                        className="position-absolute top-0 end-0 h-100"
                        style={{ width: '5px', cursor: 'col-resize', zIndex: 10 }}
                        onMouseDown={(e) => {
                            setIsResizingSidebar(true);
                            sidebarResizeStartRef.current = { x: e.clientX, width: sidebarWidth };
                            e.preventDefault();
                        }}
                    />
                )}
            </div>

            {/* Main Content */}
            <div className="flex-grow-1 d-flex flex-column" style={{ minWidth: 0 }}>
                {/* Split Pane: Live Preview (Left) + Requirements & Script (Right) */}
                <div className="flex-grow-1 d-flex overflow-hidden bg-light p-2 gap-2">
                    {/* Panel 1: Live Preview (2/3 width) */}
                    <div className="h-100 bg-white border rounded overflow-hidden d-flex flex-column shadow-sm" style={{flex: 2}}>
                        <div className="p-2 border-bottom bg-light d-flex align-items-center gap-2">
                            {!showSidebar && (
                                <Button variant="light" size="sm" onClick={() => setShowSidebar(true)} className="p-0 border-0 bg-transparent me-1" title="展开侧边栏">
                                    <FaBars className="text-secondary"/>
                                </Button>
                            )}
                            <InputGroup size="sm" style={{maxWidth: '500px'}}>
                                <InputGroup.Text className="bg-white border-end-0">
                                    {type === 'web' ? <FaGlobe className="text-primary"/> : <FaMobileAlt className="text-primary"/>}
                                </InputGroup.Text>
                                <Form.Control 
                                    className="border-start-0 ps-1"
                                    placeholder={type === 'web' ? "目标 URL (如 https://google.com)" : "App 包名 / ID"}
                                    value={type === 'web' ? targetUrl : appConfig}
                                    onChange={e => type === 'web' ? setTargetUrl(e.target.value) : setAppConfig(e.target.value)}
                                />
                                <Button variant="outline-secondary" onClick={async () => {
                                    try {
                                        onLog('开始检索环境...');
                                        const res = await api.post<{success: boolean, message: string, data?: any}>('/api/ui-automation/detect', {
                                            type,
                                            target: type === 'web' ? targetUrl : undefined
                                        });
                                        
                                        if (res.success) {
                                            onLog(`检索成功: ${res.message}`);
                                            if (type === 'app' && res.data?.app_id) {
                                                setAppConfig(`${res.data.app_id}${res.data.activity ? '/' + res.data.activity.split('/').pop() : ''}`);
                                            } else if (type === 'web' && res.data?.validated_url) {
                                                setTargetUrl(res.data.validated_url);
                                            }
                                        } else {
                                            onLog(`检索失败: ${res.message}`);
                                            alert(res.message);
                                        }
                                    } catch (e) {
                                        console.error(e);
                                        onLog('检索请求出错');
                                    }
                                }}>
                                    开始检索
                                </Button>
                            </InputGroup>
                        </div>
                        <div className="flex-grow-1">
                            <LivePreview 
                                executionId={executionId}
                                status={executionStatus}
                                logs={logs}
                                screenshotPaths={screenshots}
                                isPolling={executionStatus === 'running'}
                            />
                        </div>
                    </div>

                    {/* Panel 2: Requirements & Script (Merged, 1/3 width) */}
                    <div className="h-100 d-flex flex-column gap-2" style={{flex: 1}}>
                        {/* Sub-panel 1: Requirements */}
                        <div className="bg-white border rounded overflow-hidden d-flex flex-column shadow-sm" style={{flex: 1}}>
                            <div className="p-2 border-bottom bg-light d-flex align-items-center justify-content-between">
                                <div className="fw-bold small text-secondary d-flex align-items-center">
                                    <FaMagic className="me-2 text-primary"/>
                                    <span>测试用例</span>
                                </div>
                                <Button 
                                    size="sm" 
                                    variant="outline-primary" 
                                    onClick={() => setShowImportModal(true)}
                                    disabled={isGenerating}
                                    style={{fontSize: '0.8em', padding: '2px 8px'}}
                                >
                                    {isGenerating ? <Spinner size="sm" animation="border"/> : "自动导入 & 生成脚本"}
                                </Button>
                            </div>
                            <div className="flex-grow-1 p-0 h-100">
                                <Form.Control
                                    as="textarea"
                                    className="h-100 w-100 border-0 p-3"
                                    style={{resize: 'none', fontSize: '0.9em', fontFamily: 'monospace', backgroundColor: '#fdfdfd'}}
                                    value={requirements}
                                    readOnly
                                    placeholder="点击上方“自动导入 & 生成脚本”后，AI生成的测试用例将显示在这里..."
                                />
                            </div>
                        </div>

                        {/* Sub-panel 2: Script */}
                        <div className="bg-white border rounded overflow-hidden d-flex flex-column shadow-sm" style={{flex: 1}}>
                            <div className="p-2 border-bottom bg-light d-flex align-items-center justify-content-between">
                                <div className="fw-bold small text-secondary d-flex align-items-center">
                                    <FaBug className="me-2 text-success"/>
                                    <span>测试脚本</span>
                                </div>
                                <Button 
                                    size="sm" 
                                    variant="primary" 
                                    onClick={handleRun}
                                    disabled={!currentScript || executionStatus === 'running'}
                                    style={{fontSize: '0.8em', padding: '2px 12px'}}
                                >
                                    {executionStatus === 'running' ? <Spinner size="sm" animation="border"/> : <><FaPlay className="me-1"/> 运行脚本</>}
                                </Button>
                            </div>
                            <div className="flex-grow-1 overflow-hidden h-100">
                                <ScriptEditor 
                                    script={currentScript} 
                                    onChange={setCurrentScript} 
                                    readOnly={executionStatus === 'running'}
                                />
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );

    const renderReportView = () => (
        <div style={{height: '100%'}} className="d-flex">
            <div className="w-25 border-end h-100">
                 <HistoryList 
                    projectId={projectId} 
                    onSelect={handleHistorySelect}
                />
            </div>
            <div className="w-75 h-100">
                {selectedReport ? (
                    <ReportDetail 
                        execution={selectedReport} 
                        onReRun={() => {
                            // Temporary fallback: alert user to switch view
                            // Ideally, we should lift state up to Dashboard to switch tabs
                            alert(`请切换到 ${selectedReport.automation_type.includes('app') ? 'App' : 'Web'} 自动化界面以重新运行此脚本`);
                        }} 
                    />
                ) : (
                    <div className="h-100 d-flex align-items-center justify-content-center text-muted">
                        <div className="text-center">
                            <FaHistory size={48} className="mb-3 opacity-25"/>
                            <p>请从左侧列表选择一个报告查看详情</p>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );

    return (
        <div className="h-100 d-flex flex-column bg-white">
            <div className="flex-grow-1" style={{height: '100%'}}>
                {effectiveView === 'web' && renderAutomationView('web')}
                {effectiveView === 'app' && renderAutomationView('app')}
                {effectiveView === 'report' && renderReportView()}
            </div>

            {/* Import Modal */}
            <Modal show={showImportModal} onHide={() => setShowImportModal(false)}>
                <Modal.Header closeButton>
                    <Modal.Title>导入测试用例</Modal.Title>
                </Modal.Header>
                <Modal.Body>
                    <Form.Group>
                        <Form.Label>粘贴测试用例或需求描述</Form.Label>
                        <Form.Control 
                            as="textarea" 
                            rows={6} 
                            value={importText}
                            onChange={e => setImportText(e.target.value)}
                            placeholder="1. 打开 Google&#10;2. 搜索 'Trae'&#10;3. 验证结果..."
                        />
                        <Form.Text className="text-muted">
                            留空将尝试自动加载最新的测试生成结果。
                        </Form.Text>
                    </Form.Group>
                </Modal.Body>
                <Modal.Footer>
                    <Button variant="secondary" onClick={() => setShowImportModal(false)}>取消</Button>
                    <Button variant="primary" onClick={handleGenerate} disabled={isGenerating}>
                        {isGenerating ? '生成中...' : '生成脚本'}
                    </Button>
                </Modal.Footer>
            </Modal>
        </div>
    );
};
