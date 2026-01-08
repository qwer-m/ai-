import { useState, useEffect, useRef, useMemo } from 'react';
import { Nav, Button, Form, Spinner, Alert, ProgressBar, Modal, Badge, Toast } from 'react-bootstrap';
import { FaFileUpload, FaFileAlt, FaFileImage, FaPlay, FaDownload, FaTrash, FaLightbulb, FaCheckCircle, FaExclamationCircle, FaFileCode, FaCog, FaChartBar } from 'react-icons/fa';
import { saveFileToDB, getFileFromDB } from '../utils/storage';
import { api, getAuthHeaders } from '../utils/api';
import classNames from 'classnames';

// --- Helper Functions ---
function cleanStreamingContent(content: string) {
    const codeBlockRegex = /```(?:json)?\s*([\s\S]*?)\s*```/;
    const match = content.match(codeBlockRegex);
    return match ? match[1] : content;
}

// --- AIHintBubble Component (Inline) ---
function AIHintBubble({ onClose }: { onClose: () => void }) {
    const [show, setShow] = useState(false);

    useEffect(() => {
        // Smart Trigger: Check if new user
        const lastVisit = window.localStorage.getItem('tg_last_visit');
        const isNewUser = !lastVisit || (Date.now() - Number(lastVisit) > 3 * 24 * 60 * 60 * 1000);
        
        if (isNewUser) {
            setShow(true);
        }
        window.localStorage.setItem('tg_last_visit', String(Date.now()));

        // Keyboard close
        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onClose();
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [onClose]);

    if (!show) return null;

    return (
        <div 
            className="position-absolute bg-white shadow-lg border rounded-3 p-3"
            style={{ 
                top: '-15px', 
                left: '-15px', 
                width: '280px', 
                zIndex: 100,
                borderLeft: '4px solid #0d6efd' 
            }}
        >
            <div className="d-flex justify-content-between align-items-start mb-2">
                <strong className="text-primary d-flex align-items-center gap-2">
                    <FaLightbulb /> AI 助手建议
                </strong>
                <button 
                    onClick={onClose} 
                    className="btn-close btn-close-sm" 
                    aria-label="Close"
                />
            </div>
            <p className="small text-secondary mb-0" style={{ lineHeight: '1.5' }}>
                上传详细的需求文档或原型图，并明确预期用例数量，可以获得更覆盖全面的测试用例。
            </p>
            {/* CSS Triangle */}
            <div style={{
                position: 'absolute',
                bottom: '-8px',
                left: '20px',
                width: 0,
                height: 0,
                borderLeft: '8px solid transparent',
                borderRight: '8px solid transparent',
                borderTop: '8px solid #fff',
                filter: 'drop-shadow(0 2px 1px rgba(0,0,0,0.05))'
            }} />
        </div>
    );
}

type Props = {
  projectId: number | null;
  onLog: (msg: string) => void;
  onGenerated: (data: any) => void;
  onError?: (msg: string) => void;
};

const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50MB
const ALLOWED_TYPES = [
    'application/pdf', 
    'application/msword', 
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document', // .docx
    'text/plain', 
    'text/markdown',
    'image/png', 
    'image/jpeg',
    'image/gif'
];

export function TestGeneration({ projectId, onLog, onGenerated, onError }: Props) {
  // Refs for hidden file inputs
  const fileInputRef = useRef<HTMLInputElement>(null);
  const protoInputRef = useRef<HTMLInputElement>(null);
  const uploadZoneRef = useRef<HTMLDivElement>(null);

  // Load initial state from localStorage if available
  const [mode, setMode] = useState<'text' | 'file'>(() => 
    (window.localStorage.getItem('tg_mode') as 'text' | 'file') || 'text'
  );
  
  // Text Mode State
  const [requirement, setRequirement] = useState(() => {
    const key = projectId ? `tg_requirement_${projectId}` : 'tg_requirement';
    return window.localStorage.getItem(key) || '';
  });
  
  // File Mode State
  const [file, setFile] = useState<File | null>(null);
  const [docType, setDocType] = useState(() => 
    window.localStorage.getItem('tg_docType') || 'requirement'
  );
  const [protoFile, setProtoFile] = useState<File | null>(null);
  const [force, setForce] = useState(false);
  const [isDragActive, setIsDragActive] = useState(false);

  // Common State
  const [compress, setCompress] = useState(() => 
    window.localStorage.getItem('tg_compress') === 'true'
  );
  const [expectedCount, setExpectedCount] = useState(() => 
    Number(window.localStorage.getItem('tg_expectedCount')) || 20
  );
  const [loading, setLoading] = useState(false);
  const [pollStatus, setPollStatus] = useState<string>('');
  
  // Results State
  const [textResult, setTextResult] = useState<any>(() => {
    try {
      const key = projectId ? `tg_text_result_${projectId}` : 'tg_text_result';
      const saved = window.localStorage.getItem(key);
      return saved ? JSON.parse(saved) : null;
    } catch { return null; }
  });
  const [textStreamingContent, setTextStreamingContent] = useState(() => {
    const key = projectId ? `tg_text_streaming_content_${projectId}` : 'tg_text_streaming_content';
    return window.localStorage.getItem(key) || '';
  });

  const [fileResult, setFileResult] = useState<any>(() => {
    try {
      const key = projectId ? `tg_file_result_${projectId}` : 'tg_file_result';
      const saved = window.localStorage.getItem(key);
      return saved ? JSON.parse(saved) : null;
    } catch { return null; }
  });
  const [fileStreamingContent, setFileStreamingContent] = useState(() => {
    const key = projectId ? `tg_file_streaming_content_${projectId}` : 'tg_file_streaming_content';
    return window.localStorage.getItem(key) || '';
  });
  
  const [savedFileName, setSavedFileName] = useState(() => {
    const key = projectId ? `tg_savedFileName_${projectId}` : 'tg_savedFileName';
    return window.localStorage.getItem(key) || '';
  });

  // Derived state
  const result = mode === 'text' ? textResult : fileResult;
  const streamingContent = mode === 'text' ? textStreamingContent : fileStreamingContent;
  
  const setResult = (data: any) => {
      if (mode === 'text') setTextResult(data);
      else setFileResult(data);
  };
  const setStreamingContent = (data: string) => {
      if (mode === 'text') setTextStreamingContent(data);
      else setFileStreamingContent(data);
  };

  const [error, setError] = useState<string | null>(null);
  const [showDuplicateModal, setShowDuplicateModal] = useState(false);
  const [duplicateData, setDuplicateData] = useState<any>(null);
  const [showHint, setShowHint] = useState(true);
  
  // New: Toast for file errors
  const [toastMsg, setToastMsg] = useState<string | null>(null);

  // Effects (Persistence & DB)
  useEffect(() => {
    window.localStorage.removeItem('tg_result');
    window.localStorage.removeItem('tg_streamingContent');
  }, []);

  useEffect(() => {
    const key = projectId ? `tg_savedFileName_${projectId}` : 'tg_savedFileName';
    window.localStorage.setItem(key, savedFileName);
  }, [savedFileName, projectId]);

  useEffect(() => {
    setFile(null);
    setProtoFile(null);
    if (projectId) {
      let active = true;
      getFileFromDB(`tg_file_${projectId}`)
        .then(f => { if (active) setFile(f || null); })
        .catch(err => console.error("Failed to load file from DB:", err));
      getFileFromDB(`tg_protoFile_${projectId}`)
        .then(f => { if (active) setProtoFile(f || null); })
        .catch(err => console.error("Failed to load proto file from DB:", err));
      return () => { active = false; };
    }
  }, [projectId]);

  useEffect(() => { window.localStorage.setItem('tg_mode', mode); }, [mode]);
  useEffect(() => { 
    const key = projectId ? `tg_requirement_${projectId}` : 'tg_requirement';
    window.localStorage.setItem(key, requirement); 
  }, [requirement, projectId]);
  useEffect(() => { window.localStorage.setItem('tg_docType', docType); }, [docType]);
  useEffect(() => { window.localStorage.setItem('tg_compress', String(compress)); }, [compress]);
  useEffect(() => { window.localStorage.setItem('tg_expectedCount', String(expectedCount)); }, [expectedCount]);
  
  useEffect(() => { 
    const key = projectId ? `tg_text_result_${projectId}` : 'tg_text_result';
    if (textResult) window.localStorage.setItem(key, JSON.stringify(textResult)); 
    else window.localStorage.removeItem(key);
  }, [textResult, projectId]);
  useEffect(() => { 
    const key = projectId ? `tg_text_streaming_content_${projectId}` : 'tg_text_streaming_content';
    window.localStorage.setItem(key, textStreamingContent); 
  }, [textStreamingContent, projectId]);

  useEffect(() => { 
    const key = projectId ? `tg_file_result_${projectId}` : 'tg_file_result';
    if (fileResult) window.localStorage.setItem(key, JSON.stringify(fileResult)); 
    else window.localStorage.removeItem(key);
  }, [fileResult, projectId]);
  useEffect(() => { 
    const key = projectId ? `tg_file_streaming_content_${projectId}` : 'tg_file_streaming_content';
    window.localStorage.setItem(key, fileStreamingContent); 
  }, [fileStreamingContent, projectId]);

  // Network status listener
  useEffect(() => {
      const handleOnline = () => { /* maybe auto-retry or clear error */ };
      const handleOffline = () => setToastMsg("网络连接已断开，请检查网络设置");
      window.addEventListener('online', handleOnline);
      window.addEventListener('offline', handleOffline);
      return () => {
          window.removeEventListener('online', handleOnline);
          window.removeEventListener('offline', handleOffline);
      };
  }, []);

  // AI Hint Hover Trigger
  useEffect(() => {
      let timer: any;
      const zone = uploadZoneRef.current;
      if (!zone) return;

      const handleMouseEnter = () => {
          if (!file && !showHint) { // Only if not already showing
               // Wait 5s
               timer = setTimeout(() => {
                   if (!file) setShowHint(true);
               }, 5000);
          }
      };
      const handleMouseLeave = () => {
          if (timer) clearTimeout(timer);
      };

      zone.addEventListener('mouseenter', handleMouseEnter);
      zone.addEventListener('mouseleave', handleMouseLeave);
      return () => {
          zone.removeEventListener('mouseenter', handleMouseEnter);
          zone.removeEventListener('mouseleave', handleMouseLeave);
          if (timer) clearTimeout(timer);
      };
  }, [file, showHint]);


  // File Handlers
  const validateAndSetFile = (f: File | null) => {
      if (!f) {
          setFile(null);
          return;
      }
      
      // 1. Size Check
      if (f.size > MAX_FILE_SIZE) {
          setToastMsg(`文件大小超过限制 (Max 50MB)`);
          return;
      }

      // 2. Type Check (Loose check based on extension/mime if possible, but mime can be empty)
      // For now, rely on accept attribute in input, but manual check for drop
      // Simplified: Just check if it looks like a document or image
      
      setFile(f);
      if (f) setSavedFileName(f.name);
      if (projectId) {
          saveFileToDB(`tg_file_${projectId}`, f).catch(e => console.error("Save failed:", e));
      }
  };

  const handleFileChange = (f: File | null) => validateAndSetFile(f);

  const handleProtoFileChange = (f: File | null) => {
    setProtoFile(f);
    if (projectId) {
        saveFileToDB(`tg_protoFile_${projectId}`, f).catch(e => console.error("Save proto failed:", e));
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragActive(true);
  };
  const handleDragLeave = () => setIsDragActive(false);
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      validateAndSetFile(e.dataTransfer.files[0]);
    }
  };

  // Generation Logic
  const handleGenerateStream = async (isText: boolean, forceOverride?: boolean) => {
    if (!navigator.onLine) return alert('网络已断开，无法生成');
    if (!projectId) return alert('请先选择项目');
    if (isText && !requirement.trim()) return alert('请输入需求内容');
    if (!isText && !file) return alert('请选择文件');

    const setCurrentResult = isText ? setTextResult : setFileResult;
    const setCurrentStreamingContent = isText ? setTextStreamingContent : setFileStreamingContent;

    setLoading(true);
    setError(null);
    setCurrentResult(null);
    setCurrentStreamingContent('');
    setPollStatus('正在实时生成...');
    onLog(isText ? '开始实时生成测试用例 (文本模式) - 已启用等价类/边界值分析...' : `开始实时生成测试用例 (文件模式: ${file?.name}) - 已启用等价类/边界值分析...`);

    const formData = new FormData();
    formData.append('project_id', String(projectId));
    formData.append('doc_type', isText ? 'requirement' : docType);
    formData.append('compress', String(compress));
    formData.append('expected_count', String(expectedCount));
    formData.append('force', String(forceOverride !== undefined ? forceOverride : force));
    
    if (isText) {
        formData.append('requirement_text', requirement);
    } else if (file) {
        formData.append('file', file);
        if (docType === 'incomplete' && protoFile) {
            formData.append('prototype_file', protoFile);
        }
    }

    try {
        const resp = await fetch('/api/generate-tests-stream', {
            method: 'POST',
            headers: { ...getAuthHeaders() },
            body: formData,
        });
        
        if (!resp.ok) {
            const errData = await resp.json().catch(() => ({}));
            throw new Error(errData.error || `HTTP ${resp.status}`);
        }

        const reader = resp.body?.getReader();
        if (!reader) throw new Error("No response body");

        const decoder = new TextDecoder();
        let fullText = '';
        let duplicateDetected = false;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value, { stream: true });
            
            if (!duplicateDetected && chunk.includes('@@DUPLICATE@@')) {
                duplicateDetected = true;
                const parts = (fullText + chunk).split('@@DUPLICATE@@');
                try {
                    let jsonStr = parts[1];
                    if (jsonStr.startsWith(':')) jsonStr = jsonStr.substring(1);
                    const prevData = JSON.parse(jsonStr);
                    setDuplicateData(prevData);
                    setShowDuplicateModal(true);
                    onLog("检测到重复文档，等待用户确认...");
                    reader.cancel();
                    return; 
                } catch (e) {
                    console.error("Failed to parse duplicate data", e);
                }
            }

            fullText += chunk;
            setCurrentStreamingContent(fullText);
        }
        
        try {
            let clean = fullText;
            if (fullText.includes("```json")) clean = fullText.split("```json")[1];
            if (clean.includes("```")) clean = clean.split("```")[0];
            clean = clean.trim();
            const json = JSON.parse(clean);
            setCurrentResult(json);
            onGenerated(json);
        } catch (e) {
            onLog("生成完成 (解析JSON失败，显示原始内容)");
        }
        
        onLog("生成完成");
        
    } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setError(msg);
        onLog(`生成失败: ${msg}`);
        if (onError && (msg.includes('401') || msg.includes('QUOTA') || msg.includes('API Key not set'))) {
            onError(msg);
        }
    } finally {
        setLoading(false);
        setPollStatus('');
    }
  };

  const handleDuplicateConfirm = () => {
    setShowDuplicateModal(false);
    if (mode === 'text') handleGenerateStream(true, true);
    else handleGenerateStream(false, true);
  };

  const handleDuplicateCancel = async () => {
    if (duplicateData && duplicateData.id) {
        try {
            setLoading(true);
            const data = await api.get<any>(`/api/test-generations/${duplicateData.id}`);
            setResult(data);
            onGenerated(data);
            setStreamingContent(JSON.stringify(data, null, 2));
            onLog("已加载历史生成结果");
        } catch (e) {
            onLog(`加载历史失败: ${e}`);
        } finally {
            setLoading(false);
        }
    }
    setShowDuplicateModal(false);
  };

  const handleExportExcel = async () => {
    let exportData: any[] = [];
    if (Array.isArray(result) && result.length > 0) {
        exportData = [...result];
    } else if (streamingContent) {
        const content = streamingContent;
        const foundItems: any[] = [];
        let cursor = 0;
        while (cursor < content.length) {
            const start = content.indexOf('[', cursor);
            if (start === -1) break;
            let balance = 0;
            let end = -1;
            let inString = false;
            let escape = false;
            for (let i = start; i < content.length; i++) {
                const char = content[i];
                if (escape) { escape = false; continue; }
                if (char === '\\') { escape = true; continue; }
                if (char === '"') { inString = !inString; continue; }
                if (!inString) {
                    if (char === '[') balance++;
                    else if (char === ']') {
                        balance--;
                        if (balance === 0) { end = i; break; }
                    }
                }
            }
            if (end !== -1) {
                const jsonStr = content.substring(start, end + 1);
                try {
                    const parsed = JSON.parse(jsonStr);
                    if (Array.isArray(parsed)) foundItems.push(...parsed);
                } catch (e) {}
                cursor = end + 1;
            } else {
                cursor = start + 1;
            }
        }
        exportData = foundItems;
    }

    if (exportData.length > 0) {
        exportData = exportData.filter(item => item && (item.id !== undefined || item.ID !== undefined));
        exportData.sort((a, b) => {
            const getId = (item: any) => {
                const idVal = item.id !== undefined ? item.id : item.ID;
                if (typeof idVal === 'number') return idVal;
                if (typeof idVal === 'string') {
                    const match = idVal.match(/(\d+)/);
                    return match ? parseInt(match[1]) : 999999;
                }
                return 999999;
            };
            return getId(a) - getId(b);
        });
    } else if (streamingContent) {
        exportData = [{ raw_content: streamingContent }];
    }

    if (!exportData || exportData.length === 0) return;

    try {
      const resp = await fetch('/api/export-tests-excel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify(exportData),
      });
      if (!resp.ok) throw new Error('Export failed');
      const blob = await resp.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      let filename = 'test_cases.xlsx';
      if (mode === 'file') {
          const nameToUse = file ? file.name : savedFileName;
          if (nameToUse) {
            const name = nameToUse.substring(0, nameToUse.lastIndexOf('.')) || nameToUse;
            filename = `${name}_测试用例.xlsx`;
          }
      }
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      onLog('导出 Excel 成功');
    } catch (e) {
      onLog(`导出失败: ${e}`);
    }
  };

  const stats = useMemo(() => {
    let count = 0;
    if (Array.isArray(result)) count = result.length;
    return { count };
  }, [result]);

  return (
    <div className="bento-grid h-100 align-content-start position-relative">
      
      {/* Toast */}
      <div className="position-fixed top-0 end-0 p-3" style={{ zIndex: 1100 }}>
          <Toast show={!!toastMsg} onClose={() => setToastMsg(null)} delay={3000} autohide bg="danger">
              <Toast.Header><strong className="me-auto text-danger">错误</strong></Toast.Header>
              <Toast.Body className="text-white">{toastMsg}</Toast.Body>
          </Toast>
      </div>

      {/* Header Section */}
      <div className="bento-card col-span-12 p-4 d-flex align-items-center justify-content-between glass-panel">
         <div>
            <h4 className="text-gradient mb-1 d-flex align-items-center gap-2">
                <FaPlay className="text-primary" size={20} />
                测试用例生成中心
            </h4>
            <p className="text-secondary small mb-0">AI 驱动的智能测试设计引擎，支持文本描述与文件分析</p>
         </div>
         <div className="d-flex gap-3">
             <Badge bg="white" text="primary" className="border shadow-sm p-2 px-3 d-flex align-items-center gap-2">
                 <FaChartBar />
                 已生成: <span className="fw-bold">{stats.count}</span>
             </Badge>
         </div>
      </div>

      {/* Main Input Section */}
      <div className="bento-card col-span-12 md:col-span-8 p-4 d-flex flex-column position-relative">
         {/* AI Hint Bubble */}
         {showHint && mode === 'file' && !file && (
             <AIHintBubble onClose={() => setShowHint(false)} />
         )}

         <div className="d-flex justify-content-between align-items-center mb-4">
             <Nav variant="pills" className="bg-light p-1 rounded-pill" activeKey={mode} onSelect={(k) => setMode(k as 'text' | 'file')}>
                <Nav.Item>
                    <Nav.Link eventKey="text" className="rounded-pill px-3 py-1 small fw-bold">
                        <FaFileAlt className="me-2"/>文本
                    </Nav.Link>
                </Nav.Item>
                <Nav.Item>
                    <Nav.Link eventKey="file" className="rounded-pill px-3 py-1 small fw-bold">
                        <FaFileUpload className="me-2"/>文件
                    </Nav.Link>
                </Nav.Item>
             </Nav>
             {mode === 'file' && file && (
                 <Button variant="link" className="text-danger p-0 text-decoration-none small" onClick={() => validateAndSetFile(null)}>
                     <FaTrash className="me-1"/>移除文件
                 </Button>
             )}
         </div>

         <div className="flex-grow-1">
            {mode === 'text' ? (
                <Form.Control 
                    as="textarea" 
                    className="input-pro h-100 border-0 bg-light"
                    style={{ resize: 'none', minHeight: '300px' }}
                    placeholder="请输入详细的需求描述，例如：登录功能，用户输入账号密码..." 
                    value={requirement}
                    onChange={e => setRequirement(e.target.value)}
                />
            ) : (
                !file ? (
                    <div 
                        ref={uploadZoneRef}
                        className={classNames("h-100 rounded-3 d-flex flex-column align-items-center justify-content-center text-center transition-all p-5", { 
                            "bg-primary-subtle border-primary": isDragActive, 
                            "bg-light border-secondary-subtle": !isDragActive,
                            "opacity-50": loading 
                        })}
                        style={{ 
                            borderStyle: 'dashed', 
                            borderWidth: '2px',
                            minHeight: '300px',
                            cursor: loading ? 'not-allowed' : 'pointer'
                        }}
                        onDragOver={handleDragOver}
                        onDragLeave={handleDragLeave}
                        onDrop={handleDrop}
                        onClick={() => !loading && fileInputRef.current?.click()}
                    >
                        <input type="file" ref={fileInputRef} onChange={(e) => handleFileChange(e.target.files?.[0] || null)} style={{ display: 'none' }} accept={ALLOWED_TYPES.join(',')} />
                        <div className="mb-3 text-primary"><FaFileUpload size={48} /></div>
                        <h6 className="fw-bold mb-2">点击或拖拽上传文档</h6>
                        <div className="text-muted small">支持 PDF, Word, TXT, MD, 图片 (Max 50MB)</div>
                    </div>
                ) : (
                    <div className="h-100 d-flex flex-column align-items-center justify-content-center bg-light rounded-3 p-5">
                        <FaFileAlt size={64} className="text-primary mb-3" />
                        <h5 className="fw-bold text-dark">{file.name}</h5>
                        <p className="text-secondary">{(file.size / 1024).toFixed(1)} KB</p>
                    </div>
                )
            )}
         </div>
      </div>

      {/* Config Panel */}
      <div className="bento-card col-span-12 md:col-span-4 p-4 d-flex flex-column gap-3 bg-white">
         <h6 className="fw-bold d-flex align-items-center gap-2 mb-3">
            <FaCog className="text-primary" /> 配置面板
         </h6>

         {mode === 'file' && (
             <div className="p-3 bg-light rounded-3 mb-2">
                 <Form.Group className="mb-3">
                    <Form.Label className="small fw-bold text-secondary">文档类型</Form.Label>
                    <Form.Select className="input-pro form-select-sm" value={docType} onChange={e => setDocType(e.target.value)}>
                        <option value="requirement">需求文档</option>
                        <option value="incomplete">残缺文档 (需补充原型图)</option>
                        <option value="product_requirement">产品需求</option>
                    </Form.Select>
                 </Form.Group>
                 
                 {docType === 'incomplete' && (
                    <Form.Group>
                        <Form.Label className="small fw-bold text-secondary">补充原型图</Form.Label>
                        <div className="d-flex gap-2">
                            <Button variant="outline-secondary" size="sm" className="w-100 text-start d-flex align-items-center justify-content-between input-pro" onClick={() => protoInputRef.current?.click()}>
                                <span className="text-truncate">{protoFile ? protoFile.name : '选择图片...'}</span>
                                <FaFileImage />
                            </Button>
                            {protoFile && <Button variant="outline-danger" size="sm" onClick={() => handleProtoFileChange(null)}><FaTrash /></Button>}
                        </div>
                        <input type="file" ref={protoInputRef} onChange={e => handleProtoFileChange(e.target.files?.[0] || null)} style={{ display: 'none' }} accept="image/*" />
                    </Form.Group>
                 )}
             </div>
         )}

         <div className="p-3 bg-light rounded-3 flex-grow-1">
            <Form.Check 
                type="switch"
                id="compress-switch"
                label="启用上下文压缩" 
                checked={compress} 
                onChange={e => setCompress(e.target.checked)} 
                className="fw-medium mb-3"
            />
            
            <Form.Group className="mb-3">
                <Form.Label className="small fw-bold text-secondary">预期用例数</Form.Label>
                <Form.Control 
                    type="number" 
                    className="input-pro"
                    value={expectedCount} 
                    onChange={e => setExpectedCount(Number(e.target.value))} 
                />
            </Form.Group>

            {mode === 'file' && (
                <Form.Check 
                    type="checkbox" 
                    id="force-gen"
                    label="强制重新生成" 
                    checked={force} 
                    onChange={e => setForce(e.target.checked)} 
                    className="text-secondary small mt-3"
                />
            )}
         </div>

         <div className="mt-auto d-flex flex-column gap-2">
            <Button 
                className="btn-pro-primary w-100 py-2 fw-bold shadow-sm d-flex align-items-center justify-content-center"
                disabled={loading || !projectId}
                onClick={() => mode === 'text' ? handleGenerateStream(true) : handleGenerateStream(false)}
            >
                {loading ? <><Spinner size="sm" animation="border" className="me-2" /> 生成中...</> : <><FaPlay className="me-2" /> 开始生成</>}
            </Button>

            {(result || streamingContent) && (
                <div className="d-flex gap-2">
                    <Button variant="outline-success" className="flex-grow-1 input-pro border-0" onClick={handleExportExcel}>
                        <FaDownload className="me-1" /> 导出
                    </Button>
                    <Button variant="outline-danger" className="flex-grow-1 input-pro border-0" onClick={() => {
                        if (mode === 'text') { setTextResult(null); setTextStreamingContent(''); }
                        else { setFileResult(null); setFileStreamingContent(''); }
                        onLog('已清除生成结果');
                    }}>
                        <FaTrash className="me-1" /> 清除
                    </Button>
                </div>
            )}
         </div>
      </div>

      {/* Progress Bar (Col-Span-12) */}
      {loading && (
        <div className="col-span-12 animate-pulse">
            <ProgressBar animated now={100} label={pollStatus} variant="info" style={{ height: '6px', borderRadius: '3px' }} />
            <div className="text-center mt-2 text-muted small">AI 正在深度分析需求文档，请稍候...</div>
        </div>
      )}

      {/* Error Alert */}
      {error && (
          <div className="col-span-12">
            <Alert variant="danger" dismissible onClose={() => setError(null)} className="shadow-sm border-0 mb-0">
                <FaExclamationCircle className="me-2" /> {error}
            </Alert>
          </div>
      )}

      {/* Results Section */}
      <div className="bento-card col-span-12 p-0 overflow-hidden d-flex flex-column" style={{ minHeight: '500px' }}>
         <div className="bg-light border-bottom d-flex justify-content-between align-items-center px-4 py-3">
            <h6 className="mb-0 fw-bold d-flex align-items-center gap-2">
                <FaCheckCircle className={result ? "text-success" : "text-muted"} /> 生成结果
            </h6>
            {result && (
                <Badge bg="success" className="d-flex align-items-center gap-1">
                    已生成 {stats.count} 条用例
                </Badge>
            )}
         </div>
         <div className="flex-grow-1 bg-white p-0 overflow-hidden position-relative">
            <div className="position-absolute top-0 start-0 w-100 h-100 overflow-auto p-4 font-monospace">
                {mode === 'text' ? (
                    textResult ? JSON.stringify(textResult, null, 2) : (textStreamingContent ? cleanStreamingContent(textStreamingContent) : <div className="text-center text-muted mt-5 py-5"><div className="mb-3 opacity-25"><FaFileCode size={48} /></div>暂无生成结果</div>)
                ) : (
                    fileResult ? JSON.stringify(fileResult, null, 2) : (fileStreamingContent ? cleanStreamingContent(fileStreamingContent) : <div className="text-center text-muted mt-5 py-5"><div className="mb-3 opacity-25"><FaFileCode size={48} /></div>暂无生成结果</div>)
                )}
            </div>
         </div>
      </div>

      {/* Duplicate Modal */}
      <Modal show={showDuplicateModal} onHide={() => setShowDuplicateModal(false)} centered backdrop="static">
        <Modal.Header closeButton className="border-0 pb-0">
          <Modal.Title className="fw-bold text-warning"><FaExclamationTriangle className="me-2" />文档内容重复</Modal.Title>
        </Modal.Header>
        <Modal.Body className="pt-2">
          <p className="mb-3">检测到该文档内容未发生变化。系统已为您找到历史生成结果。</p>
          <div className="bg-light p-3 rounded small text-secondary">
            <ul className="mb-0 ps-3">
              <li className="mb-1"><strong>加载历史：</strong> 直接使用上次生成的结果（推荐，无需等待）。</li>
              <li><strong>强制生成：</strong> 忽略重复，强制AI重新分析并生成（耗时且消耗Token）。</li>
            </ul>
          </div>
        </Modal.Body>
        <Modal.Footer className="border-0 pt-0">
          <Button variant="secondary" onClick={handleDuplicateCancel}>加载历史结果</Button>
          <Button variant="primary" onClick={handleDuplicateConfirm}>强制重新生成</Button>
        </Modal.Footer>
      </Modal>

    </div>
  );
}

// Helper icon
function FaExclamationTriangle(props: any) {
    return <svg stroke="currentColor" fill="currentColor" strokeWidth="0" viewBox="0 0 576 512" height="1em" width="1em" xmlns="http://www.w3.org/2000/svg" {...props}><path d="M569.517 440.013C587.975 472.007 564.806 512 527.94 512H48.054c-36.937 0-59.999-40.055-41.577-71.987L246.423 23.985c18.467-32.009 64.72-31.951 83.154 0l239.94 416.028zM288 354c-25.405 0-46 20.595-46 46s20.595 46 46 46 46-20.595 46-46-20.595-46-46-46zm-43.673-165.346l7.418 136c.347 6.364 5.609 11.346 11.982 11.346h48.546c6.373 0 11.635-4.982 11.982-11.346l7.418-136c.375-6.874-5.098-12.654-11.982-12.654h-63.383c-6.884 0-12.356 5.78-11.981 12.654z"></path></svg>;
}
