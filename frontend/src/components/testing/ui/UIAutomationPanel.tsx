import React, { useState, useEffect, useRef } from 'react';
import { Button, Form, InputGroup, Modal, Spinner } from 'react-bootstrap';
import {
    FaPlay,
    FaHistory,
    FaGlobe,
    FaMobileAlt,
    FaBug,
    FaMagic,
    FaBars,
    FaChevronLeft,
    FaFolderPlus,
    FaFile,
} from 'react-icons/fa';
import { api } from '../../../utils/api';
import { ScriptEditor } from '../../UIAutomation/ScriptEditor';
import { LivePreview } from '../../UIAutomation/LivePreview';
import { ReportDetail } from '../../UIAutomation/ReportDetail';
import { HistoryList, type HistoryListHandle } from '../../UIAutomation/HistoryList';
import './ui-automation.css';

interface UIExecution {
    id: number;
    automation_type: string;
    status: string;
    generated_script?: string;
    execution_result?: string;
    screenshot_paths: string[];
    quality_score?: number;
    evaluation_result?: unknown;
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
    view?: string;
}

export const UIAutomation: React.FC<UIAutomationProps> = ({ projectId, onLog, view = 'web' }) => {
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
    const [requirements, setRequirements] = useState('');

    const [showSidebar, setShowSidebar] = useState(true);
    const [sidebarWidth, setSidebarWidth] = useState(260);
    const [isResizingSidebar, setIsResizingSidebar] = useState(false);
    const sidebarResizeStartRef = useRef<{ x: number; width: number } | null>(null);
    const historyListRef = useRef<HistoryListHandle>(null);

    useEffect(() => {
        let interval: ReturnType<typeof setInterval> | undefined;
        if (executionId && (executionStatus === 'running' || executionStatus === 'pending')) {
            interval = setInterval(async () => {
                try {
                    const data = await api.get<UIExecution>(`/api/ui-automation/${executionId}`);
                    setExecutionStatus(data.status);
                    setLogs(data.execution_result || '');
                    setScreenshots(data.screenshot_paths || []);
                    if ((data.status === 'success' || data.status === 'failed') && effectiveView === 'report' && selectedReport?.id === executionId) {
                        setSelectedReport(data);
                    }
                } catch (e) {
                    console.error('Poll failed', e);
                }
            }, 2000);
        }
        return () => clearInterval(interval);
    }, [executionId, executionStatus, effectiveView, selectedReport]);

    useEffect(() => {
        const handleGlobalMouseMove = (e: globalThis.MouseEvent) => {
            if (!isResizingSidebar || !sidebarResizeStartRef.current) {
                return;
            }
            const dx = e.clientX - sidebarResizeStartRef.current.x;
            const next = Math.max(180, Math.min(420, sidebarResizeStartRef.current.width + dx));
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

    const handleGenerate = async () => {
        if (!projectId) {
            return;
        }
        setIsGenerating(true);
        try {
            let cases = importText;
            if (!cases) {
                try {
                    const gens = await api.get<any[]>(`/api/test-generations?project_id=${projectId}`);
                    if (gens && gens.length > 0) {
                        const latest = gens[0];
                        const detail = await api.get<any>(`/api/test-generations/${latest.id}`);
                        if (typeof detail === 'string') {
                            cases = detail;
                        } else if (Array.isArray(detail)) {
                            cases = JSON.stringify(detail, null, 2);
                        } else if (detail && typeof detail === 'object') {
                            if (typeof detail.generated_result === 'string') {
                                cases = detail.generated_result;
                            } else if (Array.isArray(detail.generated_result) || typeof detail.generated_result === 'object') {
                                cases = JSON.stringify(detail.generated_result, null, 2);
                            } else if (typeof detail.raw === 'string') {
                                cases = detail.raw;
                            } else if (typeof detail.requirement_text === 'string') {
                                cases = detail.requirement_text;
                            }
                        }
                        onLog(`已自动导入最近一次用例生成结果 #${latest.id}`);
                    }
                } catch (e) {
                    console.warn('Failed to fetch latest test generation, falling back to empty', e);
                }
            }

            setRequirements(cases);

            const payload = {
                project_id: projectId,
                task: cases || 'Perform a general smoke test',
                url: effectiveView === 'web' ? targetUrl : appConfig,
                automation_type: effectiveView === 'app' ? 'app' : 'web',
            };

            const res = await api.post<{ script: string }>('/api/ui-automation/generate', payload);
            setCurrentScript(res.script || '');
            setExecutionId(null);
            setExecutionStatus('created');
            setLogs('');
            setScreenshots([]);
            onLog('脚本生成成功');
        } catch (e) {
            onLog(`脚本生成失败: ${e instanceof Error ? e.message : String(e)}`);
            alert('脚本生成失败，请检查后端日志。');
        } finally {
            setIsGenerating(false);
            setShowImportModal(false);
        }
    };

    const handleRun = async () => {
        if (!projectId || !currentScript.trim()) {
            alert('脚本为空，请先生成或选择脚本。');
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

            onLog(`执行完成: ${res.status || 'unknown'}`);
        } catch (e) {
            onLog(`执行启动失败: ${e instanceof Error ? e.message : String(e)}`);
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

    const handleDetect = async (type: 'web' | 'app') => {
        try {
            onLog('开始检测目标环境...');
            const res = await api.post<{ success: boolean; message: string; data?: any }>('/api/ui-automation/detect', {
                type,
                target: type === 'web' ? targetUrl : undefined,
            });

            if (!res.success) {
                onLog(`检测失败: ${res.message}`);
                alert(res.message);
                return;
            }

            onLog(`检测成功: ${res.message}`);
            if (type === 'app' && res.data?.app_id) {
                setAppConfig(`${res.data.app_id}${res.data.activity ? `/${res.data.activity.split('/').pop()}` : ''}`);
            } else if (type === 'web' && res.data?.validated_url) {
                setTargetUrl(res.data.validated_url);
            }
        } catch (e) {
            console.error(e);
            onLog('检测请求失败');
        }
    };

    const renderAutomationView = (type: 'web' | 'app') => (
        <div className="ui-automation-body h-100 d-flex overflow-hidden">
            <div
                className={`ui-automation-sidebar border-end d-flex flex-column position-relative flex-shrink-0 ${showSidebar ? 'is-open' : 'is-closed'} ${isResizingSidebar ? 'is-resizing' : ''}`}
                style={{ '--ui-sidebar-width': `${sidebarWidth}px` } as React.CSSProperties}
            >
                <div className="ui-automation-sidebar-head d-flex justify-content-between align-items-center px-3 py-2 border-bottom">
                    <div className="d-flex align-items-center gap-2 text-secondary small fw-bold">
                        <FaHistory className="me-1" />
                        <span>测试脚本历史</span>
                        <FaFolderPlus className="cursor-pointer" title="新建根目录" onClick={() => historyListRef.current?.openCreateModal('folder')} size={14} />
                        <FaFile className="cursor-pointer" title="新建根脚本" onClick={() => historyListRef.current?.openCreateModal('file')} size={12} />
                    </div>
                    <Button variant="link" size="sm" className="p-0 text-secondary" onClick={() => setShowSidebar(false)}>
                        <FaChevronLeft size={12} />
                    </Button>
                </div>
                <div className="flex-grow-1 overflow-hidden">
                    <HistoryList
                        ref={historyListRef}
                        projectId={projectId}
                        onSelect={handleHistorySelect}
                        filterType={type}
                        selectedId={selectedCaseId}
                    />
                </div>
                {showSidebar ? (
                    <div
                        className="ui-automation-resizer position-absolute top-0 end-0 h-100"
                        onMouseDown={(e) => {
                            setIsResizingSidebar(true);
                            sidebarResizeStartRef.current = { x: e.clientX, width: sidebarWidth };
                            e.preventDefault();
                        }}
                    />
                ) : null}
            </div>

            <div className="ui-automation-main ui-automation-main-min flex-grow-1 d-flex flex-column">
                <div className="ui-automation-workspace flex-grow-1 d-flex overflow-hidden p-3 gap-3">
                    <div className="ui-automation-preview-card ui-automation-preview-main h-100 overflow-hidden d-flex flex-column">
                        <div className="ui-automation-toolbar p-2 border-bottom d-flex align-items-center gap-2">
                            {!showSidebar ? (
                                <Button
                                    variant="light"
                                    size="sm"
                                    onClick={() => setShowSidebar(true)}
                                    className="p-0 border-0 bg-transparent me-1"
                                    title="展开侧栏"
                                >
                                    <FaBars className="text-secondary" />
                                </Button>
                            ) : null}

                            <InputGroup size="sm" className="ui-automation-target-input">
                                <InputGroup.Text className="border-end-0">
                                    {type === 'web' ? <FaGlobe className="text-primary" /> : <FaMobileAlt className="text-primary" />}
                                </InputGroup.Text>
                                <Form.Control
                                    className="border-start-0 ps-1"
                                    placeholder={type === 'web' ? '目标 URL（例如 https://google.com）' : 'App 包名 / Activity'}
                                    value={type === 'web' ? targetUrl : appConfig}
                                    onChange={(e) => (type === 'web' ? setTargetUrl(e.target.value) : setAppConfig(e.target.value))}
                                />
                                <Button variant="outline-secondary" onClick={() => void handleDetect(type)}>
                                    检测
                                </Button>
                            </InputGroup>
                        </div>
                        <div className="flex-grow-1 ui-automation-min-h-0">
                            <LivePreview
                                executionId={executionId}
                                status={executionStatus}
                                logs={logs}
                                screenshotPaths={screenshots}
                                isPolling={executionStatus === 'running'}
                            />
                        </div>
                    </div>

                    <div className="ui-automation-right-column ui-automation-right-main h-100 d-flex flex-column gap-3">
                        <div className="ui-automation-side-card ui-automation-side-fill overflow-hidden d-flex flex-column">
                            <div className="ui-automation-side-head p-2 border-bottom d-flex align-items-center justify-content-between">
                                <div className="fw-bold small text-secondary d-flex align-items-center">
                                    <FaMagic className="me-2 text-primary" />
                                    <span>测试用例</span>
                                </div>
                                <Button
                                    size="sm"
                                    variant="outline-primary"
                                    onClick={() => setShowImportModal(true)}
                                    disabled={isGenerating}
                                    className="ui-automation-small-btn"
                                >
                                    {isGenerating ? <Spinner size="sm" animation="border" /> : '自动导入并生成脚本'}
                                </Button>
                            </div>
                            <div className="flex-grow-1 p-0 h-100">
                                <Form.Control
                                    as="textarea"
                                    className="h-100 w-100 border-0 p-3 ui-automation-readonly-area"
                                    value={requirements}
                                    readOnly
                                    placeholder="点击上方按钮后，AI 生成的测试用例将展示在这里。"
                                />
                            </div>
                        </div>

                        <div className="ui-automation-side-card ui-automation-side-fill overflow-hidden d-flex flex-column">
                            <div className="ui-automation-side-head p-2 border-bottom d-flex align-items-center justify-content-between">
                                <div className="fw-bold small text-secondary d-flex align-items-center">
                                    <FaBug className="me-2 text-success" />
                                    <span>测试脚本</span>
                                </div>
                                <Button
                                    size="sm"
                                    variant="primary"
                                    onClick={handleRun}
                                    disabled={!currentScript || executionStatus === 'running'}
                                    className="ui-automation-small-btn"
                                >
                                    {executionStatus === 'running' ? <Spinner size="sm" animation="border" /> : <><FaPlay className="me-1" />运行脚本</>}
                                </Button>
                            </div>
                            <div className="flex-grow-1 overflow-hidden h-100">
                                <ScriptEditor script={currentScript} onChange={setCurrentScript} readOnly={executionStatus === 'running'} />
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );

    const renderReportView = () => (
        <div className="ui-automation-report h-100 d-flex">
            <div className="ui-automation-report-sidebar border-end h-100">
                <HistoryList projectId={projectId} onSelect={handleHistorySelect} selectedId={selectedCaseId} />
            </div>
            <div className="ui-automation-report-content h-100">
                {selectedReport ? (
                    <ReportDetail
                        execution={selectedReport}
                        onReRun={() => {
                            alert(`请切换到 ${selectedReport.automation_type.includes('app') ? 'App' : 'Web'} 自动化页签后重新运行该脚本。`);
                        }}
                    />
                ) : (
                    <div className="h-100 d-flex align-items-center justify-content-center text-muted">
                        <div className="text-center">
                            <FaHistory size={48} className="mb-3 opacity-25" />
                            <p className="mb-0">请先从左侧选择一个脚本或报告。</p>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );

    return (
        <div className="ui-automation-shell h-100 d-flex flex-column">
            <div className="flex-grow-1 ui-automation-fill-height">
                {effectiveView === 'web' ? renderAutomationView('web') : null}
                {effectiveView === 'app' ? renderAutomationView('app') : null}
                {effectiveView === 'report' ? renderReportView() : null}
            </div>

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
                            onChange={(e) => setImportText(e.target.value)}
                            placeholder={'1. 打开 Google\n2. 搜索 "Trae"\n3. 校验搜索结果'}
                        />
                        <Form.Text className="text-muted">留空时会尝试自动导入最近一次测试生成结果。</Form.Text>
                    </Form.Group>
                </Modal.Body>
                <Modal.Footer>
                    <Button variant="secondary" onClick={() => setShowImportModal(false)}>
                        取消
                    </Button>
                    <Button variant="primary" onClick={handleGenerate} disabled={isGenerating}>
                        {isGenerating ? '生成中...' : '生成脚本'}
                    </Button>
                </Modal.Footer>
            </Modal>
        </div>
    );
};
