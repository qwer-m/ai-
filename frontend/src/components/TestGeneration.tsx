import { useState, useEffect, useRef, useMemo } from 'react';
import { Nav, Button, Form, Spinner, Alert, ProgressBar, Modal, Badge, Toast, InputGroup } from 'react-bootstrap';
import { FaFileUpload, FaFileAlt, FaFileImage, FaPlay, FaDownload, FaTrash, FaLightbulb, FaCheckCircle, FaExclamationCircle, FaFileCode, FaCog, FaChartBar, FaCopy } from 'react-icons/fa';
import { saveFileToDB, getFileFromDB } from '../utils/storage';
import { api, getAuthHeaders } from '../utils/api';
import classNames from 'classnames';

// --- Helper Functions ---
function cleanStreamingContent(content: string) {
    if (!content) return '';
    // Strip markdown code blocks (```json ... ``` or just ``` ...)
    let cleaned = content.replace(/```json\s*/g, '').replace(/```\s*/g, '');
    return cleaned;
}

function parseMultipleJsonArrays(text: string): any[] {
    const clean = cleanStreamingContent(text).trim();
    if (!clean) return [];

    const foundItems: any[] = [];
    
    // Robust streaming parser: extracts complete objects {...} from potential arrays [...]
    let cursor = 0;
    while (cursor < clean.length) {
        // Find start of an array
        const startArray = clean.indexOf('[', cursor);
        if (startArray === -1) break; // No more arrays
        
        cursor = startArray + 1;
        
        // Scan for objects inside this array
        while (cursor < clean.length) {
            // Skip whitespace and commas
            while (cursor < clean.length && /[\s,]/.test(clean[cursor])) {
                cursor++;
            }
            
            if (cursor >= clean.length) break;
            
            // If we hit closing array, we are done with this array
            if (clean[cursor] === ']') {
                cursor++;
                break;
            }
            
            // We expect an object '{'
            if (clean[cursor] === '{') {
                const startObj = cursor;
                let balance = 0;
                let endObj = -1;
                let inString = false;
                let escape = false;
                
                for (let i = startObj; i < clean.length; i++) {
                    const char = clean[i];
                    if (escape) { escape = false; continue; }
                    if (char === '\\') { escape = true; continue; }
                    if (char === '"') { inString = !inString; continue; }
                    
                    if (!inString) {
                        if (char === '{') balance++;
                        else if (char === '}') {
                            balance--;
                            if (balance === 0) {
                                endObj = i;
                                break;
                            }
                        }
                    }
                }
                
                if (endObj !== -1) {
                    // Try parse this object
                    const jsonStr = clean.substring(startObj, endObj + 1);
                    try {
                        const obj = JSON.parse(jsonStr);
                        if (obj && typeof obj === 'object') {
                             foundItems.push(obj);
                        }
                    } catch (e) {
                        // ignore malformed objects
                    }
                    cursor = endObj + 1;
                } else {
                    // Object not closed yet (streaming), stop parsing this array
                    cursor = clean.length; // exit loop
                }
            } else {
                // Unexpected char, skip
                cursor++;
            }
        }
    }

    // Fallback: If standard parsing works (e.g. for simple arrays), use it if our custom parser failed or found nothing
    if (foundItems.length === 0) {
        try {
            const parsed = JSON.parse(clean);
            if (Array.isArray(parsed)) return parsed;
        } catch {}
    }

    return foundItems;
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
  const [appendCount, setAppendCount] = useState(() => 
    Number(window.localStorage.getItem('tg_appendCount')) || 10
  );
  
  // Auto-calculate recommended count
  const [isManualCount, setIsManualCount] = useState(false);
  const [isEstimating, setIsEstimating] = useState(false);

  useEffect(() => {
      setIsManualCount(false);
  }, [mode]);

  useEffect(() => {
      if (isManualCount) return;

      const estimate = async () => {
        setIsEstimating(true);
        try {
            const formData = new FormData();
            formData.append('project_id', String(projectId || 0));
            formData.append('doc_type', 'requirement');
            
            if (mode === 'text') {
                if (!requirement || requirement.trim().length === 0) {
                    setExpectedCount(20);
                    setIsEstimating(false);
                    return;
                }
                formData.append('requirement', requirement);
            } else {
                if (!file) {
                    setExpectedCount(20);
                    setIsEstimating(false);
                    return;
                }
                formData.append('file', file);
            }

            const res = await api.upload<{count: number}>('/api/estimate-test-count', formData);
            
            if (res && typeof res.count === 'number') {
                setExpectedCount(res.count);
            }
        } catch (e) {
            console.error("Estimation failed", e);
            setToastMsg(`智能估算失败，已使用默认值。错误: ${e instanceof Error ? e.message : String(e)}`);
            // Fallback logic removed as per user request - rely on default value (20) or user manual input
        } finally {
            setIsEstimating(false);
        }
      };

      const timer = setTimeout(estimate, mode === 'text' ? 800 : 600);
      return () => clearTimeout(timer);
  }, [requirement, file, mode, isManualCount, projectId]);

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
  useEffect(() => { 
      const key = projectId ? `tg_expectedCount_${projectId}` : 'tg_expectedCount';
      window.localStorage.setItem(key, String(expectedCount)); 
  }, [expectedCount, projectId]);
  useEffect(() => { 
      const key = projectId ? `tg_appendCount_${projectId}` : 'tg_appendCount';
      window.localStorage.setItem(key, String(appendCount)); 
  }, [appendCount, projectId]);
  
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

  // Prevent accidental navigation/close during generation
  useEffect(() => {
      const handleBeforeUnload = (e: BeforeUnloadEvent) => {
          if (loading) {
              e.preventDefault();
              e.returnValue = ''; 
              return '';
          }
      };
      window.addEventListener('beforeunload', handleBeforeUnload);
      return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [loading]);

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
          setFileResult(null);
          setFileStreamingContent('');
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
      setFileResult(null);
      setFileStreamingContent('');
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
  const extractFirstJsonArray = (content: string): any[] | null => {
    if (!content) return null;
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
            } catch {}
            cursor = end + 1;
        } else {
            cursor = start + 1;
        }
        if (foundItems.length > 0) return foundItems;
    }
    return null;
  };

  const normalizeTestCaseId = (n: number) => `TC-${String(n).padStart(3, '0')}`;

  const normalizeStringList = (v: unknown, fallback?: string) => {
    if (Array.isArray(v)) return v.map(x => String(x)).map(s => s.trim()).filter(Boolean);
    if (typeof v === 'string') {
      const s = v.trim();
      if (!s) return [];
      return s.split('\n').map(x => x.trim()).filter(Boolean);
    }
    return fallback ? [fallback] : [];
  };

  const normalizePriority = (v: unknown) => {
    const s = String(v ?? '').trim().toUpperCase();
    if (s === 'P0' || s === 'P1' || s === 'P2') return s;
    if (s === '高' || s === 'HIGH') return 'P0';
    if (s === '中' || s === 'MEDIUM') return 'P1';
    if (s === '低' || s === 'LOW') return 'P2';
    return 'P1';
  };

  const normalizeId = (v: unknown, fallbackIndex: number) => {
    const raw = String(v ?? '').trim();
    if (/^TC-\d{3,}$/.test(raw)) return raw;
    if (/^\d+$/.test(raw)) return normalizeTestCaseId(Number(raw));
    return normalizeTestCaseId(fallbackIndex + 1);
  };

  const pickField = (item: any, keys: string[]) => {
    for (const k of keys) {
      const v = item?.[k];
      if (v === undefined || v === null) continue;
      if (typeof v === 'string') {
        const s = v.trim();
        if (s) return s;
        continue;
      }
      if (Array.isArray(v) && v.length > 0) return v;
      if (typeof v === 'number' || typeof v === 'boolean') return v;
      if (typeof v === 'object') return v;
    }
    return undefined;
  };

  const normalizeStandardCases = (items: any[]) => {
    const out: any[] = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (!item || typeof item !== 'object' || Array.isArray(item)) continue;
      const id = normalizeId(
        pickField(item, ['id', 'ID', 'test_case_id', 'case_id', '用例编号', '用例ID', '编号', 'ID号']),
        i
      );
      const description = String(pickField(item, ['description', 'desc', 'name', '描述', '用例描述', '场景']) ?? '').trim();
      const test_module = String(pickField(item, ['test_module', 'module', '模块', '测试模块']) ?? '').trim();
      const preconditions = normalizeStringList(pickField(item, ['preconditions', 'precondition', '前置条件']) ?? (item as any).preconditions);
      const steps = normalizeStringList(pickField(item, ['steps', 'step', '步骤', '测试步骤']) ?? (item as any).steps);
      const test_input = String(pickField(item, ['test_input', 'input', '输入', '测试输入']) ?? '').trim();
      const expected_result = String(pickField(item, ['expected_result', 'expected', 'expect', '预期', '预期结果']) ?? '').trim();
      const priority = normalizePriority(pickField(item, ['priority', 'p', '优先级']) ?? (item as any).priority);
      out.push({ id, description, test_module, preconditions, steps, test_input, expected_result, priority });
    }
    return out;
  };

  const validateStandardCases = (items: any[]) => {
    if (!Array.isArray(items)) return { ok: false as const, error: '结果不是 JSON 数组' };
    if (items.length === 0) return { ok: false as const, error: '结果为空数组，请重试生成' };
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (!it || typeof it !== 'object' || Array.isArray(it)) return { ok: false as const, error: `第 ${i + 1} 条不是对象` };
      const keys = Object.keys(it);
      const required = ['id','description','test_module','preconditions','steps','test_input','expected_result','priority'];
      for (const k of required) {
        if (!(k in it)) return { ok: false as const, error: `第 ${i + 1} 条缺少字段: ${k}` };
      }
      for (const k of keys) {
        if (!required.includes(k)) return { ok: false as const, error: `第 ${i + 1} 条包含多余字段: ${k}` };
      }
    }
    return { ok: true as const };
  };

  const getExistingCases = (isText: boolean) => {
    const existing = isText ? textResult : fileResult;
    if (Array.isArray(existing)) return existing;
    const stream = isText ? textStreamingContent : fileStreamingContent;
    const parsed = extractFirstJsonArray(cleanStreamingContent(stream));
    return parsed ?? [];
  };

  const hasJsonInResultBox = useMemo(() => {
    if (Array.isArray(result) && result.length > 0) return true;
    if (result && typeof result === 'object') return true;
    const parsed = extractFirstJsonArray(cleanStreamingContent(streamingContent));
    return Array.isArray(parsed) && parsed.length > 0;
  }, [result, streamingContent]);

  const handleGenerateStream = async (isText: boolean, forceOverride?: boolean, appendMode?: boolean) => {
    if (!navigator.onLine) return alert('网络已断开，无法生成');
    if (!projectId) return alert('请先选择项目');
    if (isText && !requirement.trim()) return alert('请输入需求内容');
    if (!isText && !file) return alert('请选择文件');

    const setCurrentResult = isText ? setTextResult : setFileResult;
    const setCurrentStreamingContent = isText ? setTextStreamingContent : setFileStreamingContent;
    const existingCases = appendMode ? getExistingCases(isText) : [];
    
    let targetVal = expectedCount;
    if (appendMode) {
         // Logic: If existing < expected, try to bridge the gap in batches of 25.
         // If existing >= expected, use appendCount.
         const currentCount = existingCases.length || 0;
         if (currentCount < expectedCount) {
             const remaining = expectedCount - currentCount;
             if (remaining > 25) {
                 targetVal = currentCount + 25;
             } else {
                 targetVal = currentCount + remaining; // Finish it
             }
         } else {
             // Already met expectation, user wants more
             const toAdd = appendCount;
             if (toAdd > 25) {
                 targetVal = currentCount + 25;
             } else {
                 targetVal = currentCount + toAdd;
             }
         }
    }

    const safeExpectedCount = Math.max(1, Math.floor(Number(targetVal) || 1));
    
    // Only update state if not appending (to keep base config stable)
    if (!appendMode && safeExpectedCount !== expectedCount) {
        setExpectedCount(safeExpectedCount);
    }

    setLoading(true);
    setError(null);
    if (!appendMode) setCurrentResult(null);
    setCurrentStreamingContent('');
    setPollStatus('正在实时生成...');
    onLog(isText ? '开始实时生成测试用例 (文本模式) - 已启用等价类/边界值分析...' : `开始实时生成测试用例 (文件模式: ${file?.name}) - 已启用等价类/边界值分析...`);

    const formData = new FormData();
    formData.append('project_id', String(projectId));
    formData.append('doc_type', isText ? 'requirement' : docType);
    formData.append('compress', String(compress));
    formData.append('expected_count', String(safeExpectedCount));
    formData.append('force', String(forceOverride !== undefined ? forceOverride : force));
    if (appendMode) formData.append('append', 'true');
    
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
        let rawText = '';
        let duplicateDetected = false;
        let buffer = '';
        let pendingDuplicateJson: string | null = null;
        let lastParseTime = 0; // Throttle parsing

        // Safety timeout to detect if component unmounted or stuck?
        // Actually, we can't easily detect unmount inside this async function.
        // But we can check a ref if we passed one. 
        // For now, rely on reader.read() throwing or returning done if cancelled.

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value, { stream: true });
            
            buffer += chunk;

            if (pendingDuplicateJson !== null) {
                pendingDuplicateJson += buffer;
                buffer = '';
                try {
                    let jsonStr = pendingDuplicateJson;
                    if (jsonStr.startsWith(':')) jsonStr = jsonStr.substring(1);
                    const prevData = JSON.parse(jsonStr);
                    setDuplicateData(prevData);
                    setShowDuplicateModal(true);
                    onLog("检测到重复文档，等待用户确认...");
                reader.cancel();
                    return;
                } catch {
                    continue;
                }
            }

            while (true) {
                const statusMatch = buffer.match(/@@STATUS@@:(.*?)(?:\r?\n)/);
                if (statusMatch) {
                    const statusMsg = statusMatch[1].trim();
                    setPollStatus(statusMsg);
                    onLog(statusMsg);
                    buffer = buffer.replace(statusMatch[0], '');
                    continue;
                }

                const diagMatch = buffer.match(/GEN_DIAG:({.*?})(?:\r?\n)/);
                if (diagMatch) {
                    try {
                        const diagJson = JSON.parse(diagMatch[1]);
                        onLog(`GEN_DIAG:${JSON.stringify(diagJson)}`);
                    } catch {}
                    buffer = buffer.replace(diagMatch[0], '');
                    continue;
                }

                const qmMatch = buffer.match(/GEN_QM:({.*?})(?:\r?\n)/);
                if (qmMatch) {
                    try {
                        const qmJson = JSON.parse(qmMatch[1]);
                        onLog(`GEN_QM:${JSON.stringify(qmJson)}`);
                    } catch {}
                    buffer = buffer.replace(qmMatch[0], '');
                    continue;
                }

                const errLineMatch = buffer.match(/(?:^|\r?\n)Error:(.*?)(?:\r?\n|$)/);
                if (errLineMatch) {
                    const errorMsg = errLineMatch[1].trim();
                    throw new Error(errorMsg);
                }

                break;
            }

            if (!duplicateDetected && buffer.includes('@@DUPLICATE@@')) {
                duplicateDetected = true;
                const idx = buffer.indexOf('@@DUPLICATE@@');
                const before = buffer.slice(0, idx);
                const after = buffer.slice(idx + '@@DUPLICATE@@'.length);
                buffer = before;
                pendingDuplicateJson = after;
                
                // Try to parse immediately
                try {
                    let jsonStr = pendingDuplicateJson;
                    if (jsonStr.startsWith(':')) jsonStr = jsonStr.substring(1);
                        const prevData = JSON.parse(jsonStr);
                        setDuplicateData(prevData);
                        setShowDuplicateModal(true);
                        onLog("检测到重复文档，等待用户确认...");
                        reader.cancel();
                        return;
                } catch {
                    // Wait for more data
                }
            }

            // Check if buffer ends with a partial tag to avoid splitting tags across chunks
            const potentialTags = ['@@STATUS@@:', 'GEN_DIAG:', 'GEN_QM:', '@@DUPLICATE@@', 'Error:'];
            let safeEndIndex = buffer.length;
            
            // Look for partial tags at the end of the buffer (up to 20 chars back)
            // We iterate backwards to find the earliest start of a potential tag
            const searchLimit = Math.max(0, buffer.length - 20);
            for (let i = buffer.length - 1; i >= searchLimit; i--) {
                const suffix = buffer.slice(i);
                // Check if this suffix is a prefix of any potential tag
                const isPrefix = potentialTags.some(tag => tag.startsWith(suffix));
                if (isPrefix) {
                    safeEndIndex = i;
                }
            }

            let flushText = buffer.slice(0, safeEndIndex);
            buffer = buffer.slice(safeEndIndex);

            if (flushText) {
                rawText += flushText;
                setCurrentStreamingContent(rawText);
            }

            // Real-time parsing attempt to show progress in table
            // Throttle: Only parse every 500ms to avoid blocking UI/Timer
            const now = Date.now();
            if (now - lastParseTime > 500) {
                lastParseTime = now;
                try {
                    // Try simple parse first
                    const parsed = parseMultipleJsonArrays(rawText);
                    if (parsed.length > 0) {
                        const normalizedNew = normalizeStandardCases(parsed);
                        if (normalizedNew.length > 0) {
                            if (appendMode) {
                                const normalizedExisting = normalizeStandardCases(existingCases);
                                setCurrentResult([...normalizedExisting, ...normalizedNew]);
                            } else {
                                setCurrentResult(normalizedNew);
                            }
                        }
                    }
                } catch (e) {
                    // If full parse fails, try heuristic for incomplete JSON
                    try {
                        let clean = rawText;
                        if (rawText.includes("```json")) clean = rawText.split("```json")[1];
                        if (clean.includes("```")) clean = clean.split("```")[0];
                        clean = clean.trim();
                        
                        // If we have multiple blocks, try to take the last one or all valid ones
                        // The parseMultipleJsonArrays might fail on incomplete last block
                        
                        // Try to close the string if it looks like an incomplete array
                        const start = clean.lastIndexOf('[');
                        if (start !== -1) {
                            let candidate = clean.substring(start);
                            if (!candidate.endsWith(']')) {
                                const lastBrace = candidate.lastIndexOf('}');
                                if (lastBrace !== -1) {
                                    candidate = candidate.substring(0, lastBrace + 1) + ']';
                                    const parsed = JSON.parse(candidate);
                                    if (Array.isArray(parsed)) {
                                        // This gives us the LATEST chunk. 
                                        // To get ALL, we need to parse previous chunks too.
                                        // This is getting complicated for real-time.
                                        // Let's rely on parseMultipleJsonArrays for completed chunks
                                        // and just try to show what we have.
                                    }
                                }
                            }
                        }
                    } catch (err) {
                        // ignore
                    }
                }
            }
        }

        if (pendingDuplicateJson !== null) {
            duplicateDetected = true;
            try {
                let jsonStr = pendingDuplicateJson;
                if (jsonStr.startsWith(':')) jsonStr = jsonStr.substring(1);
                const prevData = JSON.parse(jsonStr);
                setDuplicateData(prevData);
                setShowDuplicateModal(true);
                onLog("检测到重复文档，等待用户确认...");
                return;
            } catch {
                // If we still can't parse it, show modal with fallback
                onLog("Duplicate detected but failed to parse details.");
                setDuplicateData({ id: null });
                setShowDuplicateModal(true);
                return;
            }
        }

        if (buffer) {
            const tail = buffer.replace(/@@STATUS@@:.*$/g, '').replace(/GEN_DIAG:.*$/g, '').replace(/GEN_QM:.*$/g, '');
            rawText += tail;
            setCurrentStreamingContent(rawText);
            buffer = '';
        }
        
        try {
            // Late detection check: If @@DUPLICATE@@ was split across chunks and missed by buffer check,
            // it will be in rawText. We catch it here to prevent "Empty Result" error.
            if (!duplicateDetected && rawText.includes('@@DUPLICATE@@')) {
                duplicateDetected = true;
                try {
                    const idx = rawText.indexOf('@@DUPLICATE@@');
                    let jsonStr = rawText.slice(idx + '@@DUPLICATE@@'.length).trim();
                    if (jsonStr.startsWith(':')) jsonStr = jsonStr.substring(1).trim();
                    
                    // Try to extract just the JSON object if there's trailing text
                    const braceStart = jsonStr.indexOf('{');
                    const braceEnd = jsonStr.lastIndexOf('}');
                    if (braceStart !== -1 && braceEnd !== -1 && braceEnd > braceStart) {
                        jsonStr = jsonStr.substring(braceStart, braceEnd + 1);
                    }

                    const prevData = JSON.parse(jsonStr);
                    setDuplicateData(prevData);
                    setShowDuplicateModal(true);
                    onLog("检测到重复文档，等待用户确认...");
                    return;
                } catch {
                    // Fallback if parsing fails
                    onLog("检测到重复文档，但解析详情失败。");
                    setDuplicateData({ id: null });
                    setShowDuplicateModal(true);
                    return;
                }
            }

            if (duplicateDetected) return;

            const skipNormalize = typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('skipNormalize') === '1';
            const cleaned = cleanStreamingContent(rawText).trim();
            if (!cleaned) throw new Error('生成结果为空（模型未返回内容或被流式解析丢弃），请检查模型配置/额度/网络后重试');
            const errMatches = Array.from(cleaned.matchAll(/(?:^|\r?\n)Error:\s*([^\r\n]+)/g));
            let json: any[] = [];
            try {
                json = parseMultipleJsonArrays(rawText);
            } catch (e) {
                console.warn("Standard parse failed, trying heuristic recovery...", e);
                let recovered = false;
                try {
                    const clean = cleanStreamingContent(rawText).trim();
                    const start = clean.indexOf('[');
                    if (start !== -1) {
                        // Try to salvage valid objects by finding the last closing brace
                        // Backtrack from end to find the valid JSON structure
                        let lastBrace = clean.lastIndexOf('}');
                        while (lastBrace !== -1 && lastBrace > start) {
                            try {
                                let candidate = clean.substring(start, lastBrace + 1);
                                // Remove potential trailing comma if present inside the candidate (at the end)
                                // Actually, candidate ends with '}'. 
                                // We need to check if we need to append ']'
                                
                                // Check if the candidate itself is a valid array (unlikely if we just cut at '}')
                                // Usually we need to append ']'
                                const candidateWithBracket = candidate + ']';
                                
                                // Try parsing with bracket appended
                                const parsed = JSON.parse(candidateWithBracket);
                                if (Array.isArray(parsed)) {
                                    json = parsed;
                                    recovered = true;
                                    onLog("警告: 生成内容可能不完整，已尝试自动修复并提取有效测试用例");
                                    break;
                                }
                            } catch {
                                // Ignore
                            }
                            
                            // Move to previous brace
                            lastBrace = clean.lastIndexOf('}', lastBrace - 1);
                        }
                    }
                } catch (err) {
                    console.warn("Heuristic recovery failed", err);
                }
                
                if (!recovered) throw e;
            }

            if (skipNormalize) {
                const merged = appendMode ? [...(Array.isArray(existingCases) ? existingCases : []), ...json] : json;
                setCurrentResult(merged);
                onGenerated(merged);
            } else {
                const normalizedNew = normalizeStandardCases(json);
                if (normalizedNew.length === 0) {
                    if (errMatches.length > 0) {
                        const lastErr = errMatches[errMatches.length - 1]?.[1] || '';
                        throw new Error(lastErr ? `生成失败: ${lastErr}` : '生成失败: 后端返回错误');
                    }
                    if (Array.isArray(json) && json.length === 0) {
                        throw new Error('生成结果为空数组：模型可能拒绝生成/输出被截断/内容解析失败，请检查模型配置与提示词后重试');
                    }
                    const first = Array.isArray(json) && json.length > 0 ? json[0] : undefined;
                    const kind = Array.isArray(first) ? 'array' : typeof first;
                    throw new Error(`生成结果不是“用例对象数组”（数组元素类型=${kind}），请让模型返回由对象组成的 JSON 数组`);
                }
                const validNew = validateStandardCases(normalizedNew);
                if (!validNew.ok) throw new Error(`生成结果不符合标准JSON结构: ${validNew.error}`);
                if (appendMode) {
                    const normalizedExisting = normalizeStandardCases(existingCases);
                    const merged = normalizeStandardCases([...normalizedExisting, ...normalizedNew]);
                    const validMerged = validateStandardCases(merged);
                    if (!validMerged.ok) throw new Error(`合并后结果不符合标准JSON结构: ${validMerged.error}`);
                    setCurrentResult(merged);
                    onGenerated(merged);
                } else {
                    setCurrentResult(normalizedNew);
                    onGenerated(normalizedNew);
                }
            }
        } catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            setError(msg);
            onLog(`生成完成但结果不符合标准JSON结构: ${msg}`);
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
      <div className="bento-card col-span-6 p-4 d-flex flex-column position-relative">
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
      <div className="bento-card col-span-6 p-4 d-flex flex-column gap-3 bg-white">
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
                <div className="d-flex gap-2">
                    <div className="flex-grow-1">
                        <Form.Label className="small fw-bold text-secondary">推荐生成用例数</Form.Label>
                        <InputGroup>
                            <Form.Control 
                                type="number" 
                                className="input-pro"
                                value={expectedCount} 
                                min={1}
                                step={1}
                                onChange={e => {
                                    setExpectedCount(Math.max(1, Math.floor(Number(e.target.value) || 1)));
                                    setIsManualCount(true);
                                }} 
                                style={{ borderRight: isEstimating ? '0' : undefined }}
                            />
                            {isEstimating && (
                                <InputGroup.Text className="bg-white border-start-0 ps-0">
                                    <Spinner animation="border" size="sm" variant="primary" />
                                </InputGroup.Text>
                            )}
                        </InputGroup>
                    </div>
                    <div className="flex-grow-1">
                        <Form.Label className="small fw-bold text-secondary">追加用例数</Form.Label>
                        <Form.Control 
                            type="number" 
                            className="input-pro"
                            value={appendCount} 
                            min={1}
                            step={1}
                            onChange={e => {
                                const newAppend = Math.max(1, Math.floor(Number(e.target.value) || 1));
                                setAppendCount(newAppend);
                                
                                // Auto-update expected count based on previous results if changing append count
                                const currentTotal = hasJsonInResultBox ? (mode === 'text' ? (textResult?.length || 0) : (fileResult?.length || 0)) : 0;
                                if (currentTotal > expectedCount) {
                                    setExpectedCount(currentTotal);
                                }
                            }} 
                        />
                    </div>
                </div>
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
            {(() => {
                const currentTotal = hasJsonInResultBox ? (mode === 'text' ? (textResult?.length || 0) : (fileResult?.length || 0)) : 0;
                const targetTotal = expectedCount + appendCount;
                const isLimitReached = hasJsonInResultBox && currentTotal >= targetTotal;
                
                return (
                    <Button 
                        className="btn-pro-primary w-100 py-2 fw-bold shadow-sm d-flex align-items-center justify-content-center"
                        disabled={loading || !projectId || isLimitReached}
                        onClick={() => mode === 'text' ? handleGenerateStream(true, undefined, hasJsonInResultBox) : handleGenerateStream(false, undefined, hasJsonInResultBox)}
                    >
                        {loading ? <><Spinner size="sm" animation="border" className="me-2" /> 生成中...</> : 
                         (isLimitReached ? <><FaPlay className="me-2" /> 开始生成</> : 
                          <><FaPlay className="me-2" /> {hasJsonInResultBox ? '继续生成' : '开始生成'}</>)
                        }
                    </Button>
                );
            })()}

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
            <ProgressBar 
                animated 
                now={100} 
                label={<div style={{ whiteSpace: 'normal', wordBreak: 'break-all', fontSize: '0.85rem', lineHeight: '1.2' }}>{pollStatus}</div>} 
                variant="info" 
                style={{ height: 'auto', minHeight: '30px', borderRadius: '3px' }} 
            />
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

      <div className="bento-card col-span-12 p-0 overflow-hidden d-flex flex-column" style={{ minHeight: '600px' }}>
        <div className="bg-light border-bottom d-flex justify-content-between align-items-center px-4 py-3">
          <h6 className="mb-0 fw-bold d-flex align-items-center gap-2">
            <FaCheckCircle className={result ? "text-success" : "text-muted"} /> 生成结果
          </h6>
          <div className="d-flex align-items-center gap-2">
            {result && (
                <Badge bg="success" className="d-flex align-items-center gap-1">
                总计 {stats.count} 条
                </Badge>
            )}
            {streamingContent && (
                <Badge bg="primary" className="d-flex align-items-center gap-1">
                    {loading ? '生成中...' : '最新批次'}
                </Badge>
            )}
          </div>
        </div>
        
        <div className="flex-grow-1 d-flex flex-column md:flex-row h-100 position-relative">
            {/* Left Panel: Main/Historical Result */}
            <div className={classNames("h-100 position-relative transition-all", { 
                "col-12": !streamingContent, 
                "col-12 md:col-6 border-end": streamingContent 
            })}>
                <div 
                    className="position-absolute top-0 start-0 w-100 px-4 py-2 border-bottom small fw-bold text-secondary" 
                    style={{ 
                        zIndex: 10, 
                        backgroundColor: '#f8f9fa',
                        opacity: 1
                    }}
                >
                    {streamingContent ? '合并后结果 / 历史结果' : '生成结果'}
                </div>
                <div className="position-absolute top-0 start-0 w-100 h-100 overflow-auto p-4 pt-5 font-monospace" style={{ whiteSpace: 'pre-wrap' }}>
                    {mode === 'text' ? (
                    textResult
                        ? JSON.stringify(textResult, null, 2)
                        : <div className="text-center text-muted mt-5 py-5"><div className="mb-3 opacity-25"><FaFileCode size={48} /></div>暂无历史结果</div>
                    ) : (
                    fileResult
                        ? JSON.stringify(fileResult, null, 2)
                        : <div className="text-center text-muted mt-5 py-5"><div className="mb-3 opacity-25"><FaFileCode size={48} /></div>暂无历史结果</div>
                    )}
                </div>
            </div>

            {/* Right Panel: Streaming Content */}
            {streamingContent && (
                <div className="col-12 md:col-6 h-100 position-relative bg-white">
                    <div className="position-absolute top-0 start-0 w-100 px-4 py-2 bg-primary-subtle border-bottom small fw-bold text-primary z-10 d-flex justify-content-between align-items-center">
                        <span><FaPlay size={10} className="me-1"/> 新增批次流式输出</span>
                        <div className="d-flex align-items-center gap-2">
                            <Button 
                                variant="link" 
                                size="sm" 
                                className="p-0 text-decoration-none d-flex align-items-center gap-1"
                                onClick={() => {
                                    navigator.clipboard.writeText(cleanStreamingContent(streamingContent));
                                    // Optional: Add toast notification here
                                }}
                                title="复制内容"
                            >
                                <FaCopy /> 复制
                            </Button>
                            {loading && <Spinner size="sm" animation="grow" variant="primary" />}
                        </div>
                    </div>
                    <div className="position-absolute top-0 start-0 w-100 h-100 overflow-auto p-4 pt-5 font-monospace bg-light bg-opacity-10" style={{ whiteSpace: 'pre-wrap', userSelect: 'text' }}>
                        {cleanStreamingContent(streamingContent)}
                    </div>
                </div>
            )}
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
