import { useEffect, useRef, useState } from 'react';
import { Button, Form, InputGroup, Spinner } from 'react-bootstrap';
import {
  FaBars,
  FaCheckCircle,
  FaChevronLeft,
  FaCloudUploadAlt,
  FaExchangeAlt,
  FaExclamationTriangle,
  FaFile,
  FaFolderPlus,
  FaGlobe,
  FaHistory,
  FaMobileAlt,
  FaPlay,
  FaProjectDiagram,
  FaRobot,
  FaSave,
} from 'react-icons/fa';
import { api } from '../../../utils/api';
import { HistoryList, type HistoryListHandle } from '../../UIAutomation/HistoryList';
import { ReportDetail } from '../../UIAutomation/ReportDetail';
import { ImportedTestCasesView, type ImportedUITestCase } from './ImportedTestCasesView';
import './ui-automation.css';

type UIAutomationView = 'web' | 'app' | 'report' | 'regression';

interface Props {
  projectId: number | null;
  projectName?: string;
  onLog: (message: string) => void;
  view?: UIAutomationView;
}

interface NaturalLanguageOperation {
  name: string;
  description: string;
  steps: string[];
}

interface ExportInfo {
  project_id: number;
  project_name: string;
  root_dir: string;
  script_path: string;
  page_paths: string[];
}

interface UITestCaseItem {
  id: number;
  name: string;
  type: 'folder' | 'file';
  script_content?: string;
  requirements?: string;
  description?: string;
  target_config?: string;
  parent_id?: number | null;
  hierarchy?: string[];
}

interface UIExecutionSummary {
  id: number;
  task_description: string;
  status: string;
  created_at: string;
  automation_type: string;
  quality_score?: number;
}

interface ImportedCasesResponse {
  filename: string;
  case_count: number;
  parse_strategy: string;
  cases: ImportedUITestCase[];
}

const emptyOperation: NaturalLanguageOperation = { name: '', description: '', steps: [] };

function parseStoredOperation(item: UITestCaseItem): NaturalLanguageOperation {
  if (item.requirements) {
    try {
      const parsed = JSON.parse(item.requirements);
      if (parsed && typeof parsed === 'object') {
        return {
          name: String(parsed.name || item.name),
          description: String(parsed.description || item.description || ''),
          steps: Array.isArray(parsed.steps) ? parsed.steps.map(String).filter(Boolean) : [],
        };
      }
    } catch {
      const steps = item.requirements.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
      return { name: item.name, description: item.description || item.requirements, steps };
    }
  }
  return { name: item.name, description: item.description || '', steps: [] };
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    idle: '待运行',
    created: '已转为脚本',
    running: '运行中',
    pending: '等待中',
    success: '成功',
    failed: '失败',
  };
  return labels[status] || status;
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function getExecutionFailure(result: any): string {
  const raw = String(result?.error || result?.stderr || result?.stdout || '').trim();
  if (!raw) return '执行失败，未返回具体错误，请查看后端执行记录。';
  const lines = raw.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  return lines[lines.length - 1] || raw;
}

function buildCaseTask(testCase: ImportedUITestCase): string {
  const sections = [
    `测试用例：${testCase.description}`,
    testCase.test_module ? `所属模块：${testCase.test_module}` : '',
    testCase.preconditions.length > 0 ? `前置条件：\n${testCase.preconditions.map((item, index) => `${index + 1}. ${item}`).join('\n')}` : '',
    testCase.steps.length > 0 ? `执行步骤：\n${testCase.steps.map((item, index) => `${index + 1}. ${item}`).join('\n')}` : '',
    testCase.test_input ? `测试数据：${testCase.test_input}` : '',
    testCase.expected_result ? `预期结果：${testCase.expected_result}` : '',
  ];
  return sections.filter(Boolean).join('\n\n');
}

export function UIAutomation({ projectId, projectName = '', onLog, view = 'web' }: Props) {
  const currentView = view === 'regression' ? 'report' : view;
  const automationType = currentView === 'app' ? 'app' : 'web';
  const [script, setScript] = useState('');
  const [operation, setOperation] = useState<NaturalLanguageOperation>(emptyOperation);
  const [exportInfo, setExportInfo] = useState<ExportInfo | null>(null);
  const [webTarget, setWebTarget] = useState('');
  const [appTarget, setAppTarget] = useState('');
  const [status, setStatus] = useState('idle');
  const [executionId, setExecutionId] = useState<number | null>(null);
  const [executionError, setExecutionError] = useState('');
  const [selectedCaseId, setSelectedCaseId] = useState<number | null>(null);
  const [selectedFolder, setSelectedFolder] = useState<{ id: number; name: string } | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(292);
  const [resizing, setResizing] = useState(false);
  const [reportRows, setReportRows] = useState<UIExecutionSummary[]>([]);
  const [selectedExecution, setSelectedExecution] = useState<any>(null);

  const [uploadedFilename, setUploadedFilename] = useState('');
  const [importedCases, setImportedCases] = useState<ImportedUITestCase[]>([]);
  const [selectedImportedKeys, setSelectedImportedKeys] = useState<Set<string>>(new Set());
  const [uploadingCases, setUploadingCases] = useState(false);
  const [convertingCases, setConvertingCases] = useState(false);

  const [naturalName, setNaturalName] = useState('');
  const [naturalDescription, setNaturalDescription] = useState('');
  const [naturalRunning, setNaturalRunning] = useState(false);
  const [naturalConverting, setNaturalConverting] = useState(false);
  const [naturalPreviewScript, setNaturalPreviewScript] = useState('');
  const [naturalRunSucceeded, setNaturalRunSucceeded] = useState(false);
  const [naturalConverted, setNaturalConverted] = useState(false);

  const historyRef = useRef<HistoryListHandle>(null);
  const resizeStart = useRef<{ x: number; width: number } | null>(null);
  const uploadInputRef = useRef<HTMLInputElement>(null);

  const target = automationType === 'app' ? appTarget : webTarget;
  const setTarget = automationType === 'app' ? setAppTarget : setWebTarget;

  useEffect(() => {
    setScript('');
    setOperation(emptyOperation);
    setExportInfo(null);
    setSelectedCaseId(null);
    setSelectedFolder(null);
    setStatus('idle');
    setExecutionId(null);
    setExecutionError('');
    setUploadedFilename('');
    setImportedCases([]);
    setSelectedImportedKeys(new Set());
    setNaturalName('');
    setNaturalDescription('');
    setNaturalPreviewScript('');
    setNaturalRunSucceeded(false);
    setNaturalConverted(false);
  }, [projectId, automationType]);

  useEffect(() => {
    if (!projectId || currentView !== 'report') return;
    void api.get<UIExecutionSummary[]>(`/api/ui-automation/history?project_id=${projectId}`)
      .then(setReportRows)
      .catch((error) => onLog(`加载自动化报告失败：${getErrorMessage(error)}`));
  }, [currentView, projectId, onLog]);

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    if (executionId && (status === 'running' || status === 'pending')) {
      timer = setInterval(() => {
        void api.get<any>(`/api/ui-automation/${executionId}`).then((detail) => {
          setStatus(detail.status || 'failed');
          if (detail.status === 'failed') setExecutionError(getExecutionFailure({ stderr: detail.execution_result }));
        });
      }, 2000);
    }
    return () => {
      if (timer) clearInterval(timer);
    };
  }, [executionId, status]);

  useEffect(() => {
    const onMove = (event: MouseEvent) => {
      if (!resizing || !resizeStart.current) return;
      setSidebarWidth(Math.max(200, Math.min(420, resizeStart.current.width + event.clientX - resizeStart.current.x)));
    };
    const onUp = () => {
      setResizing(false);
      resizeStart.current = null;
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    return () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
  }, [resizing]);

  const ensureTargetReady = async () => {
    if (!target.trim()) throw new Error(automationType === 'app' ? '请先检测并确认 App 包名与 Activity。' : '请先填写目标网址。');
    const result = await api.post<any>('/api/ui-automation/detect', {
      type: automationType,
      target: automationType === 'web' ? target : undefined,
    });
    if (!result.success) throw new Error(result.message);
    return result;
  };

  const detectTarget = async () => {
    try {
      onLog('开始检测目标环境...');
      const result = await api.post<any>('/api/ui-automation/detect', {
        type: automationType,
        target: automationType === 'web' ? target : undefined,
      });
      if (!result.success) throw new Error(result.message);
      if (automationType === 'app' && result.data?.app_id) {
        setAppTarget(`${result.data.app_id}${result.data.activity ? `/${String(result.data.activity).split('/').pop()}` : ''}`);
      } else if (automationType === 'web' && result.data?.validated_url) {
        setWebTarget(result.data.validated_url);
      }
      onLog(`检测成功：${result.message}`);
    } catch (error) {
      const message = getErrorMessage(error);
      onLog(`检测失败：${message}`);
      alert(message);
    }
  };

  const executeOperation = async () => {
    if (!projectId || !script.trim() || !operation.name) {
      alert('请先从左侧选择一个已转化的自动化操作。');
      return;
    }
    setExecutionError('');
    try {
      await ensureTargetReady();
      setStatus('running');
      const form = new FormData();
      form.append('script', script);
      form.append('task', operation.description);
      form.append('operation_name', operation.name);
      form.append('operation_steps', JSON.stringify(operation.steps));
      form.append('url', target);
      form.append('automation_type', automationType);
      form.append('project_id', String(projectId));
      const result = await api.upload<any>('/api/ui-automation/execute', form);
      const nextStatus = result.status || 'failed';
      setStatus(nextStatus);
      setExecutionId(result.execution_id || null);
      setExportInfo(result.export || exportInfo);
      if (nextStatus === 'failed') setExecutionError(getExecutionFailure(result));
      onLog(`自动化操作“${operation.name}”执行完成：${statusLabel(nextStatus)}`);
    } catch (error) {
      const message = getErrorMessage(error);
      setStatus('failed');
      setExecutionError(message);
      onLog(`启动自动化操作失败：${message}`);
    }
  };

  const selectOperation = (item: UITestCaseItem) => {
    if (item.type !== 'file') return;
    const selectedOperation = parseStoredOperation(item);
    setSelectedCaseId(item.id);
    setSelectedFolder(
      item.parent_id
        ? { id: item.parent_id, name: item.hierarchy?.[item.hierarchy.length - 1] || `目录 ${item.parent_id}` }
        : null,
    );
    setScript(item.script_content || '');
    setOperation(selectedOperation);
    setNaturalName(selectedOperation.name);
    setNaturalDescription(selectedOperation.description);
    if (item.target_config) setTarget(item.target_config);
    setExportInfo(null);
    setExecutionId(null);
    setStatus('idle');
    setExecutionError('');
    setNaturalPreviewScript('');
    setNaturalRunSucceeded(false);
    setNaturalConverted(false);
  };

  const startNewOperation = () => {
    setSelectedCaseId(null);
    setScript('');
    setOperation(emptyOperation);
    setExportInfo(null);
    setStatus('idle');
    setExecutionId(null);
    setExecutionError('');
    setNaturalName('');
    setNaturalDescription('');
    setNaturalPreviewScript('');
    setNaturalRunSucceeded(false);
    setNaturalConverted(false);
    onLog('已新建自动化操作，请填写操作名称和自然语言描述。');
  };

  const uploadTestCases = async (file: File) => {
    if (!projectId) return;
    setUploadingCases(true);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('project_id', String(projectId));
      const response = await api.upload<ImportedCasesResponse>('/api/ui-automation/import-test-cases', form);
      setUploadedFilename(response.filename);
      setImportedCases(response.cases.map((item) => ({ ...item, conversion_status: 'idle' })));
      setSelectedImportedKeys(new Set(response.cases.map((item) => item.key)));
      onLog(`已解析测试用例文件“${response.filename}”：${response.case_count} 条`);
    } catch (error) {
      const message = getErrorMessage(error);
      onLog(`测试用例上传失败：${message}`);
      alert(message);
    } finally {
      setUploadingCases(false);
      if (uploadInputRef.current) uploadInputRef.current.value = '';
    }
  };

  const toggleImportedCase = (key: string) => {
    setSelectedImportedKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const toggleAllImportedCases = () => {
    setSelectedImportedKeys((current) => (
      current.size === importedCases.length ? new Set() : new Set(importedCases.map((item) => item.key))
    ));
  };

  const updateImportedCase = (key: string, patch: Partial<ImportedUITestCase>) => {
    setImportedCases((current) => current.map((item) => item.key === key ? { ...item, ...patch } : item));
  };

  const convertSelectedCases = async () => {
    if (!projectId) return;
    const selected = importedCases.filter((item) => selectedImportedKeys.has(item.key));
    if (selected.length === 0) {
      alert('请至少勾选一条测试用例。');
      return;
    }
    if (!target.trim()) {
      alert('请先填写或检测自动化目标。');
      return;
    }
    setConvertingCases(true);
    let successCount = 0;
    try {
      await ensureTargetReady();
      for (const testCase of selected) {
        updateImportedCase(testCase.key, { conversion_status: 'converting', conversion_message: 'AI 正在理解并执行该用例' });
        const operationName = `${testCase.id} ${testCase.description}`.slice(0, 100);
        const caseTask = buildCaseTask(testCase);
        try {
          const preview = await api.post<any>('/api/ui-automation/natural-run', {
            project_id: projectId,
            operation_name: operationName,
            operation_steps: testCase.steps,
            task: caseTask,
            url: target,
            automation_type: automationType,
          });
          const execution = preview.result || {};
          setExecutionId(execution.execution_id || null);
          if (execution.status !== 'success') {
            throw new Error(getExecutionFailure(execution));
          }
          const response = await api.post<any>('/api/ui-automation/convert', {
            project_id: projectId,
            operation_name: operationName,
            operation_steps: testCase.steps,
            task: caseTask,
            url: target,
            automation_type: automationType,
            script: preview.script,
            parent_id: selectedFolder?.id ?? null,
          });
          updateImportedCase(testCase.key, {
            conversion_status: 'converted',
            conversion_message: '画面执行成功，已写入桌面脚本',
          });
          setScript(response.script || '');
          setOperation(response.operation || { name: operationName, description: caseTask, steps: testCase.steps });
          setExportInfo(response.export || null);
          setSelectedCaseId(response.test_case_id || null);
          successCount += 1;
        } catch (error) {
          updateImportedCase(testCase.key, { conversion_status: 'failed', conversion_message: getErrorMessage(error) });
        }
      }
      await historyRef.current?.refresh();
      onLog(`测试用例逐条执行并转化完成：成功 ${successCount} 条，失败 ${selected.length - successCount} 条`);
    } catch (error) {
      const message = getErrorMessage(error);
      setExecutionError(message);
      onLog(`测试用例执行环境检查失败：${message}`);
    } finally {
      setConvertingCases(false);
    }
  };

  const runNaturalLanguage = async () => {
    if (!projectId) return;
    const description = naturalDescription.trim();
    if (!description) {
      alert('请输入要让 AI 在画面上执行的自然语言操作。');
      return;
    }
    const name = naturalName.trim() || description.split(/[。！？\n]/)[0].slice(0, 40) || '自然语言操作';
    setNaturalRunning(true);
    setNaturalRunSucceeded(false);
    setNaturalConverted(false);
    setNaturalPreviewScript('');
    setExecutionError('');
    try {
      await ensureTargetReady();
      setStatus('running');
      const steps = description.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
      const response = await api.post<any>('/api/ui-automation/natural-run', {
        project_id: projectId,
        operation_name: name,
        operation_steps: steps,
        task: description,
        url: target,
        automation_type: automationType,
      });
      const result = response.result || {};
      const nextStatus = result.status || 'failed';
      setStatus(nextStatus);
      setExecutionId(result.execution_id || null);
      setNaturalPreviewScript(response.script || '');
      setOperation(response.operation || { name, description, steps });
      setNaturalName(name);
      if (nextStatus === 'success') {
        setNaturalRunSucceeded(true);
        onLog(`自然语言操作“${name}”画面执行成功，可以转为脚本。`);
      } else {
        const reason = getExecutionFailure(result);
        setExecutionError(reason);
        onLog(`自然语言操作“${name}”执行失败：${reason}`);
      }
    } catch (error) {
      const message = getErrorMessage(error);
      setStatus('failed');
      setExecutionError(message);
      onLog(`自然语言画面执行失败：${message}`);
    } finally {
      setNaturalRunning(false);
    }
  };

  const convertNaturalRun = async () => {
    if (!projectId || !naturalRunSucceeded || !naturalPreviewScript) return;
    setNaturalConverting(true);
    try {
      const steps = naturalDescription.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
      const response = await api.post<any>('/api/ui-automation/convert', {
        project_id: projectId,
        operation_name: naturalName,
        operation_steps: steps,
        task: naturalDescription,
        url: target,
        automation_type: automationType,
        script: naturalPreviewScript,
        parent_id: selectedFolder?.id ?? null,
      });
      setScript(response.script || naturalPreviewScript);
      setOperation(response.operation || operation);
      setExportInfo(response.export || null);
      setSelectedCaseId(response.test_case_id || null);
      setNaturalConverted(true);
      await historyRef.current?.refresh();
      onLog(`自然语言操作“${naturalName}”已转为桌面 Page Object 脚本。`);
    } catch (error) {
      const message = getErrorMessage(error);
      setExecutionError(message);
      onLog(`自然语言操作转为脚本失败：${message}`);
    } finally {
      setNaturalConverting(false);
    }
  };

  if (currentView === 'report') {
    return (
      <div className="ui-automation-shell ui-automation-report d-flex h-100">
        <aside className="ui-automation-report-sidebar border-end overflow-auto">
          <div className="ui-automation-sidebar-head p-3 border-bottom fw-bold"><FaHistory className="me-2" />执行报告</div>
          {reportRows.map((row) => (
            <button
              key={row.id}
              type="button"
              className="ui-automation-report-row w-100 text-start border-0 border-bottom p-3"
              onClick={() => void api.get(`/api/ui-automation/${row.id}`).then(setSelectedExecution)}
            >
              <div className="fw-semibold text-truncate">{row.task_description}</div>
              <div className="small text-muted mt-1">{statusLabel(row.status)} · {new Date(row.created_at).toLocaleString()}</div>
            </button>
          ))}
          {reportRows.length === 0 ? <div className="text-muted small text-center p-4">暂无执行报告</div> : null}
        </aside>
        <main className="ui-automation-report-content flex-grow-1 overflow-hidden">
          {selectedExecution ? <ReportDetail execution={selectedExecution} onReRun={() => undefined} /> : <div className="h-100 d-flex align-items-center justify-content-center text-muted">请选择一条执行报告</div>}
        </main>
      </div>
    );
  }

  return (
    <div className="ui-automation-shell h-100 d-flex overflow-hidden">
      <aside
        className={`ui-automation-sidebar ui-automation-panel-card d-flex flex-column ${sidebarOpen ? '' : 'is-closed'} ${resizing ? 'is-resizing' : ''}`}
        style={{ '--ui-sidebar-width': `${sidebarWidth}px` } as React.CSSProperties}
      >
        <div className="ui-automation-sidebar-head p-2 d-flex align-items-center justify-content-between">
          <span className="small fw-bold"><FaHistory className="me-2" />自动化操作</span>
          <div className="d-flex gap-1">
            <Button
              size="sm"
              variant="link"
              className="p-1 text-secondary"
              title="新建分组"
              onClick={() => historyRef.current?.openCreateFolder()}
            >
              <FaFolderPlus />
            </Button>
            <Button size="sm" variant="outline-primary" className="py-0 px-2" onClick={startNewOperation}>
              <FaFile className="me-1" />新建操作
            </Button>
          </div>
        </div>
        <HistoryList
          ref={historyRef}
          projectId={projectId}
          onSelect={selectOperation}
          onFolderSelect={(folder) => setSelectedFolder(folder ? { id: folder.id, name: folder.name } : null)}
          onHierarchyChange={onLog}
          onNodeMoved={(item, parent) => {
            if (item.id === selectedCaseId) {
              setSelectedFolder(parent ? { id: parent.id, name: parent.name } : null);
            }
          }}
          filterType={automationType}
          selectedId={selectedCaseId}
          selectedFolderId={selectedFolder?.id ?? null}
        />
      </aside>
      {sidebarOpen ? (
        <div
          className="ui-automation-resizer"
          onMouseDown={(event) => {
            resizeStart.current = { x: event.clientX, width: sidebarWidth };
            setResizing(true);
          }}
        />
      ) : null}

      <main className="ui-automation-main flex-grow-1 d-flex flex-column">
        <div className="ui-automation-workspace flex-grow-1 d-flex gap-3 p-3 overflow-hidden">
          <div className="ui-automation-center-column d-flex flex-column gap-3 overflow-hidden">
            <div className="ui-automation-toolbar ui-automation-panel-card p-2 d-flex align-items-center gap-2">
              <Button size="sm" variant="link" className="text-secondary" onClick={() => setSidebarOpen((value) => !value)}>
                {sidebarOpen ? <FaChevronLeft /> : <FaBars />}
              </Button>
              <InputGroup size="sm" className="ui-automation-target-input flex-grow-1">
                <InputGroup.Text>{automationType === 'app' ? <FaMobileAlt /> : <FaGlobe />}</InputGroup.Text>
                <Form.Control
                  value={target}
                  onChange={(event) => setTarget(event.target.value)}
                  placeholder={automationType === 'app' ? 'App 包名 / Activity' : '目标网址'}
                />
                <Button variant="outline-secondary" onClick={() => void detectTarget()}>检测</Button>
              </InputGroup>
            </div>

            <section className="ui-automation-preview-card ui-automation-preview-main ui-automation-panel-card overflow-hidden">
              {importedCases.length > 0 ? (
                <ImportedTestCasesView
                  filename={uploadedFilename}
                  cases={importedCases}
                  selectedKeys={selectedImportedKeys}
                  onToggle={toggleImportedCase}
                  onToggleAll={toggleAllImportedCases}
                />
              ) : (
                <div className="ui-automation-empty-state h-100 d-flex flex-column align-items-center justify-content-center text-center">
                  <span className="ui-automation-empty-icon"><FaCloudUploadAlt /></span>
                  <strong>测试用例工作区</strong>
                  <p>上传测试用例后，可在这里勾选并查看 AI 转化进度。</p>
                </div>
              )}
            </section>
          </div>

          <div className="ui-automation-right-main ui-automation-right-stack d-flex flex-column gap-3 overflow-hidden">
            <section className="ui-automation-operation-card ui-automation-conversion-card ui-automation-panel-card d-flex flex-column overflow-hidden">
              <div className="ui-automation-side-head px-3 py-2 d-flex align-items-center justify-content-between gap-2">
                <span className="small fw-bold"><FaCloudUploadAlt className="me-2 text-primary" />测试用例上传与转化</span>
                <Button
                  size="sm"
                  variant="primary"
                  disabled={convertingCases || selectedImportedKeys.size === 0}
                  onClick={() => void convertSelectedCases()}
                >
                  {convertingCases ? <Spinner animation="border" size="sm" className="me-1" /> : <FaExchangeAlt className="me-1" />}
                  执行并转化{selectedImportedKeys.size > 0 ? ` (${selectedImportedKeys.size})` : ''}
                </Button>
              </div>
              <div className="p-3 flex-grow-1 overflow-auto">
                <input
                  ref={uploadInputRef}
                  type="file"
                  className="d-none"
                  accept=".xlsx,.csv,.json,.txt,.html,.htm"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) void uploadTestCases(file);
                  }}
                />
                <button
                  type="button"
                  className="ui-test-case-upload-zone w-100"
                  disabled={uploadingCases}
                  onClick={() => uploadInputRef.current?.click()}
                >
                  {uploadingCases ? <Spinner animation="border" size="sm" className="me-2" /> : <FaCloudUploadAlt className="me-2" />}
                  {uploadedFilename ? `重新上传（当前：${uploadedFilename}）` : '上传测试用例文件'}
                </button>
                <div className="small text-muted mt-2">支持 XLSX、CSV、JSON、TXT。上传后在中心用例列表勾选，AI 会逐条理解、画面执行，成功后再生成独立脚本。</div>
                {importedCases.length > 0 ? (
                  <div className="ui-case-import-summary mt-3">
                    <span>共 {importedCases.length} 条</span>
                    <span>已选 {selectedImportedKeys.size} 条</span>
                  </div>
                ) : null}
              </div>
            </section>

            <section className="ui-automation-operation-card ui-automation-natural-card ui-automation-panel-card d-flex flex-column overflow-hidden">
              <div className="ui-automation-side-head px-3 py-2 d-flex align-items-center justify-content-between gap-2">
                <span className="small fw-bold"><FaProjectDiagram className="me-2 text-success" />自然语言画面执行</span>
                <Button size="sm" variant="outline-primary" onClick={() => void executeOperation()} disabled={!script || status === 'running' || naturalRunning}>
                  <FaPlay className="me-1" />运行已保存操作
                </Button>
              </div>
              <div className="ui-automation-operation-body flex-grow-1 overflow-auto p-3">
                <div className="ui-automation-operation-title-row mb-3">
                  <div>
                    <div className="small text-muted mb-1">当前操作</div>
                    <div className="fw-bold fs-5">{operation.name || '尚未选择或转化'}</div>
                  </div>
                  <span className={`ui-automation-status is-${status}`}>{statusLabel(status)}</span>
                </div>

                <Form.Group className="mb-2">
                  <Form.Label className="small text-muted mb-1">操作名称</Form.Label>
                  <Form.Control
                    size="sm"
                    value={naturalName}
                    onChange={(event) => {
                      setNaturalName(event.target.value);
                      setNaturalRunSucceeded(false);
                      setNaturalConverted(false);
                    }}
                    placeholder="例如：游客登录"
                  />
                </Form.Group>
                <div className="ui-automation-save-location mb-2">
                  脚本保存位置：<strong>{selectedFolder ? selectedFolder.name : '根目录'}</strong>
                </div>
                <Form.Group className="mb-2">
                  <Form.Label className="small text-muted mb-1">自然语言描述</Form.Label>
                  <Form.Control
                    as="textarea"
                    rows={4}
                    value={naturalDescription}
                    onChange={(event) => {
                      setNaturalDescription(event.target.value);
                      setNaturalRunSucceeded(false);
                      setNaturalConverted(false);
                    }}
                    placeholder={'例如：点击手机登录，在弹出的登录页面点击关闭按钮，验证进入游客内容界面。'}
                  />
                </Form.Group>
                <div className="d-flex gap-2 flex-wrap mb-3">
                  <Button size="sm" variant="primary" disabled={naturalRunning || !naturalDescription.trim()} onClick={() => void runNaturalLanguage()}>
                    {naturalRunning ? <Spinner animation="border" size="sm" className="me-1" /> : <FaRobot className="me-1" />}
                    AI 执行画面
                  </Button>
                  <Button size="sm" variant="success" disabled={!naturalRunSucceeded || naturalConverting || naturalConverted} onClick={() => void convertNaturalRun()}>
                    {naturalConverting ? <Spinner animation="border" size="sm" className="me-1" /> : naturalConverted ? <FaCheckCircle className="me-1" /> : <FaSave className="me-1" />}
                    {naturalConverted ? '已转为脚本' : '执行成功后转为脚本'}
                  </Button>
                </div>

                {naturalRunSucceeded && !naturalConverted ? (
                  <div className="ui-natural-success mb-3"><FaCheckCircle className="me-2" />画面执行成功，确认后可固化到当前项目的桌面 Page Object 工程。</div>
                ) : null}
                {executionError ? (
                  <div className="ui-execution-error mb-3"><FaExclamationTriangle className="me-2 flex-shrink-0" /><span>{executionError}</span></div>
                ) : null}

                {operation.name ? (
                  <>
                    <div className="ui-automation-operation-meta ui-automation-operation-meta-compact">
                      <div><span>所属项目</span><strong>{exportInfo?.project_name || projectName || `项目 ${projectId}`}</strong></div>
                      <div><span>目标</span><strong>{target || '未设置'}</strong></div>
                      <div><span>桌面目录</span><strong>{exportInfo?.root_dir || `桌面/ai ui自动化/${projectName || '当前项目'}`}</strong></div>
                    </div>
                    {operation.steps.length > 0 ? (
                      <>
                        <div className="small text-muted mt-3 mb-2">执行步骤</div>
                        <ol className="ui-automation-step-list ui-automation-step-list-compact mb-0">
                          {operation.steps.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}
                        </ol>
                      </>
                    ) : null}
                  </>
                ) : null}
              </div>
            </section>
          </div>
        </div>
      </main>
    </div>
  );
}
