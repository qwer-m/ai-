import { useState, useEffect, useRef, type MouseEvent, type ReactElement } from 'react';
import { Button, Form, Spinner, Card, Row, Col, Badge, Accordion, Nav, ListGroup, Modal, Dropdown } from 'react-bootstrap';
import { FaCheckCircle, FaBug, FaPlus, FaTrash, FaSave, FaLayerGroup, FaBars, FaChevronLeft, FaFolderPlus, FaRobot, FaEdit, FaGlobe, FaChevronDown, FaCog, FaTimes, FaEllipsisH, FaChevronRight } from 'react-icons/fa';
import { api } from '../utils/api';

type StandardAPITestingProps = {
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
    id: number;
    type: 'request' | 'folder';
    name: string;
    description?: string;
    parentId: number | null;
    isOpen?: boolean; // Frontend only
    
    // Request specific fields
    baseUrl?: string;
    apiPath?: string;
    method?: string;
    requirement?: string;
    mode?: 'natural' | 'structured';
    
    headers?: {key:string, value:string, desc:string}[];
    params?: {key:string, value:string, desc:string}[];
    bodyMode?: string;
    rawType?: string;
    bodyContent?: string;
    
    testTypes?: {
        functional: boolean;
        boundary: boolean;
        security: boolean;
    };
    timestamp?: number;
    testConfig?: any;
};

type EnvConfig = {
    id: string;
    name: string;
    baseUrl: string;
    variables?: Array<{
        key: string;
        value: string;
        enabled: boolean;
    }>;
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

export function StandardAPITesting({ projectId, onLog }: StandardAPITestingProps) {
  const [mode, setMode] = useState<'natural' | 'structured'>('natural');
  const [requirement, setRequirement] = useState('');
  // Merged URL state: apiPath now holds the full URL
  const [apiPath, setApiPath] = useState('');
  const [method, setMethod] = useState('POST');
  const [testTypes, setTestTypes] = useState({
      functional: true,
      boundary: false,
      security: false
  });
  
  const [loading, setLoading] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  
  // Interactive Response State
  const [responseStatus, setResponseStatus] = useState<number | null>(null);
  const [responseTime, setResponseTime] = useState<number | null>(null);
  const [responseBody, setResponseBody] = useState<string | null>(null);
  const [responseHeaders, setResponseHeaders] = useState<any>({});
  const [responseViewMode, setResponseViewMode] = useState<'json' | 'html' | 'headers'>('json');

  // Env Manager State
  const [showEnvModal, setShowEnvModal] = useState(false);
  const [editingEnv, setEditingEnv] = useState<EnvConfig | null>(null);

  // Renaming State
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renamingName, setRenamingName] = useState('');
  
  const processVariables = (text: string, env: EnvConfig | undefined) => {
      if (!text || !env || !env.variables) return text;
      let processed = text;
      env.variables.forEach(v => {
          if (v.enabled && v.key) {
              processed = processed.replaceAll(`{{${v.key}}}`, v.value);
          }
      });
      return processed;
  };

  const handleSendRequest = async () => {
      setLoading(true);
      try {
          // Get Active Env (Best effort match)
          // Priority: 
          // 1. Env with baseUrl matching start of apiPath (Longest match first if we wanted to be fancy, but first found is ok for now)
          // 2. Env with baseUrl equal to {{var}} if apiPath starts with {{var}}
          
          let activeEnv = savedEnvs.find(e => e.baseUrl && apiPath.startsWith(e.baseUrl));
          
          // Special case for {{VAR}} style baseUrls
          if (!activeEnv) {
              const match = apiPath.match(/^(\{\{\s*.+?\s*\}\})/);
              if (match) {
                  const tag = match[1];
                  activeEnv = savedEnvs.find(e => e.baseUrl === tag);
              }
          }

          // Variable Substitution
          const substitute = (str: string) => processVariables(str, activeEnv);

          // Construct URL (Handle absolute URL input vs Base+Path)
          let fullUrl = substitute(apiPath);
          if (!fullUrl.startsWith('http') && !fullUrl.startsWith('{{')) {
              fullUrl = 'http://' + fullUrl;
          }

          const reqHeaders = headers.reduce((acc, curr) => {
              if (curr.key) acc[substitute(curr.key)] = substitute(curr.value);
              return acc;
          }, {} as any);
          const reqParams = queryParams.reduce((acc, curr) => {
              if (curr.key) acc[substitute(curr.key)] = substitute(curr.value);
              return acc;
          }, {} as any);
          
          const finalBody = bodyMode === 'raw' ? substitute(bodyContent) : undefined;

          const res = await api.post<any>('/api/debug/request', {
              method,
              url: fullUrl,
              headers: reqHeaders,
              params: reqParams,
              body: finalBody
          });

          setResponseStatus(res.status);
          setResponseTime(res.time);
          setResponseBody(res.body);
          setResponseHeaders(res.headers);
          
          // Auto-detect view mode
          const contentType = (res.headers['content-type'] || '').toLowerCase();
          if (contentType.includes('text/html')) {
              setResponseViewMode('html');
          } else {
              setResponseViewMode('json');
          }
          onLog(`请求成功: ${res.status} (${res.time}s)`);
      } catch (e) {
          onLog(`请求失败: ${e}`);
          setResponseStatus(0);
          setResponseBody(e instanceof Error ? e.message : String(e));
      } finally {
          setLoading(false);
      }
  };

  const [runSubTab, setRunSubTab] = useState('params'); 
  const [responseTab, setResponseTab] = useState('report'); 

  // Body State
  const [bodyMode, setBodyMode] = useState<'none' | 'form-data' | 'x-www-form-urlencoded' | 'raw' | 'binary' | 'graphql'>('raw');
  const [rawType, setRawType] = useState<'Text' | 'JavaScript' | 'JSON' | 'HTML' | 'XML'>('JSON');
  
  // Request Config State
  const [queryParams, setQueryParams] = useState<{key:string, value:string, desc:string}[]>([{key:'', value:'', desc:''}]);
  
  // Resizer State
  const [requestHeight, setRequestHeight] = useState(300);
  const [isDragging, setIsDragging] = useState(false);
  const mainContentRef = useRef<HTMLDivElement>(null);

  const handleMouseDown = (e: React.MouseEvent) => {
      setIsDragging(true);
      e.preventDefault();
  };

  const handleMouseMove = (e: React.MouseEvent) => {
      if (!isDragging || !mainContentRef.current) return;
      
      // Calculate new height relative to the container top
      // We need to subtract the top offset of the request content container
      // But a simpler way is to use movementY if we tracked start position, 
      // or just use clientY relative to the container.
      
      const containerRect = mainContentRef.current.getBoundingClientRect();
      // The request content starts after Request Bar (approx 50px) and Tabs (approx 40px)
      // Let's approximate offset as 90px, or better, calculate it.
      // Actually, we can just set height based on mouse position relative to container top.
      // But the container includes the top bars. 
      // So height = e.clientY - containerRect.top - (Header Heights)
      
      const headerOffset = 95; // Approximate height of Request Bar + Tabs
      const newHeight = e.clientY - containerRect.top - headerOffset;
      
      if (newHeight > 100 && newHeight < containerRect.height - 100) {
          setRequestHeight(newHeight);
      }
  };

  const handleMouseUp = () => {
      setIsDragging(false);
  };
  const [headers, setHeaders] = useState<{key:string, value:string, desc:string}[]>([{key:'', value:'', desc:''}]);
  const [bodyContent, setBodyContent] = useState('');
  const [showSidebar, setShowSidebar] = useState(true);

    // Saved Envs State
    const [savedEnvs, setSavedEnvs] = useState<EnvConfig[]>(() => {
        try {
            const saved = localStorage.getItem('api_testing_saved_envs_v1');
            if (!saved) return [];
            const parsed = JSON.parse(saved);
            return Array.isArray(parsed) ? parsed : [];
        } catch (e) {
            console.error('Failed to load saved envs:', e);
            return [];
        }
    });

    // Env Var Detection State
    const [activeEnvTag, setActiveEnvTag] = useState<string | null>(null);
    const [inputScrollLeft, setInputScrollLeft] = useState(0);
    const inputRef = useRef<HTMLInputElement>(null);
    const highlighterRef = useRef<HTMLDivElement>(null);
    const popupTimerRef = useRef<NodeJS.Timeout | null>(null);
    const [showPopup, setShowPopup] = useState(false);
    
    useEffect(() => {
        const match = apiPath.match(/^([\s\S]*?)(\{\{\s*(.*?)\s*\}\})/);
        if (match) {
            setActiveEnvTag(match[2]);
        } else {
            setActiveEnvTag(null);
            setShowPopup(false);
        }
    }, [apiPath]);

    const handleInputMouseMove = (e: React.MouseEvent<HTMLInputElement>) => {
        if (!activeEnvTag || !inputRef.current) return;
        
        const input = inputRef.current;
        const rect = input.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        
        const style = window.getComputedStyle(input);
        const font = style.font;
        const paddingLeft = parseFloat(style.paddingLeft) || 0;
        const borderLeft = parseFloat(style.borderLeftWidth) || 0;
        const scrollLeft = input.scrollLeft;
        
        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d');
        if (!context) return;
        context.font = font;
        
        // Calculate precise range handling any prefix (spaces or text)
        const match = apiPath.match(/^([\s\S]*?)(\{\{\s*(.*?)\s*\}\})/);
        let startX = borderLeft + paddingLeft - scrollLeft;
        let endX = startX;

        if (match && match[2] === activeEnvTag) {
             const prefixWidth = context.measureText(match[1]).width;
             const tagWidth = context.measureText(activeEnvTag).width;
             startX += prefixWidth;
             endX = startX + tagWidth;
        } else {
             // Fallback if regex fails but activeEnvTag is set (unlikely)
             const tagWidth = context.measureText(activeEnvTag).width;
             endX = startX + tagWidth;
        }
        
        if (mouseX >= startX && mouseX <= endX) { // Exact match, removed buffer
            if (popupTimerRef.current) {
                clearTimeout(popupTimerRef.current);
                popupTimerRef.current = null;
            }
            setShowPopup(true);
        } else {
            if (showPopup && !popupTimerRef.current) {
                popupTimerRef.current = setTimeout(() => setShowPopup(false), 300);
            }
        }
    };

    const handleInputMouseLeave = () => {
        if (showPopup) {
            if (popupTimerRef.current) clearTimeout(popupTimerRef.current);
            popupTimerRef.current = setTimeout(() => setShowPopup(false), 300);
        }
    };

    const handlePopupMouseEnter = () => {
        if (popupTimerRef.current) {
            clearTimeout(popupTimerRef.current);
            popupTimerRef.current = null;
        }
        setShowPopup(true);
    };

    const handlePopupMouseLeave = () => {
        if (popupTimerRef.current) clearTimeout(popupTimerRef.current);
        popupTimerRef.current = setTimeout(() => setShowPopup(false), 300);
    };

  const getEnvBaseUrlValue = (tag: string) => {
      const env = savedEnvs.find(e => e.baseUrl === tag);
      if (!env) return '';
      const varName = tag.replace(/[\{\}\s]/g, '');
      const v = env.variables?.find(v => v.key === varName);
      return v ? v.value : '';
  };

  const setEnvBaseUrlValue = (tag: string, val: string) => {
      const varName = tag.replace(/[\{\}\s]/g, '');
      setSavedEnvs(prev => {
          const envIndex = prev.findIndex(e => e.baseUrl === tag);
          if (envIndex === -1) {
              // Create new
              return [...prev, {
                  id: Date.now().toString(),
                  name: varName,
                  baseUrl: tag,
                  variables: [{key: varName, value: val, enabled: true}]
              }];
          }
          
          const newEnvs = [...prev];
          const env = { ...newEnvs[envIndex] };
          const vars = env.variables ? [...env.variables] : [];
          const varIndex = vars.findIndex(v => v.key === varName);
          
          if (varIndex === -1) {
              vars.push({key: varName, value: val, enabled: true});
          } else {
              vars[varIndex] = { ...vars[varIndex], value: val };
          }
          
          env.variables = vars;
          newEnvs[envIndex] = env;
          return newEnvs;
      });
  };

  // Persist Envs
    useEffect(() => {
        try {
            localStorage.setItem('api_testing_saved_envs_v1', JSON.stringify(savedEnvs));
        } catch (e) {
            console.error('Failed to save envs:', e);
        }
    }, [savedEnvs]);

    const handleSaveEnv = () => {
        setShowEnvModal(true);
        setEditingEnv(null); // Start with list view
    };

    const handleUpdateEnv = (env: EnvConfig) => {
        setSavedEnvs(prev => {
            const exists = prev.find(e => e.id === env.id);
            if (exists) {
                return prev.map(e => e.id === env.id ? env : e);
            }
            return [...prev, env];
        });
        setEditingEnv(null);
    };

    const handleDeleteEnv = (id: string) => {
        if(!window.confirm('Delete environment?')) return;
        setSavedEnvs(prev => prev.filter(e => e.id !== id));
        if (editingEnv?.id === id) setEditingEnv(null);
    };

    const renderKvEditor = (items: {key:string, value:string, desc:string}[], onChange: (items: any[]) => void) => {
        const handleChange = (index: number, field: string, val: string) => {
            const newItems = [...items];
            newItems[index] = { ...newItems[index], [field]: val };
            if (index === items.length - 1 && (newItems[index].key || newItems[index].value)) {
                newItems.push({ key: '', value: '', desc: '' });
            }
            onChange(newItems);
        };
        const handleDelete = (index: number) => {
            if (items.length <= 1) {
                onChange([{ key: '', value: '', desc: '' }]);
                return;
            }
            const newItems = items.filter((_, i) => i !== index);
            onChange(newItems);
        };
        return (
            <div className="table-responsive">
                <table className="table table-sm table-borderless mb-0 align-middle small">
                    <thead className="text-secondary bg-light">
                        <tr>
                            <th style={{width: '30%', fontWeight: '500'}} className="ps-3">Key</th>
                            <th style={{width: '30%', fontWeight: '500'}}>Value</th>
                            <th style={{width: '30%', fontWeight: '500'}}>Description</th>
                            <th style={{width: '10%'}}></th>
                        </tr>
                    </thead>
                    <tbody>
                        {items.map((item, idx) => (
                            <tr key={idx} className="border-bottom">
                                <td className="ps-2"><Form.Control size="sm" placeholder="Key" value={item.key} onChange={e => handleChange(idx, 'key', e.target.value)} className="border-0 shadow-none bg-transparent" /></td>
                                <td><Form.Control size="sm" placeholder="Value" value={item.value} onChange={e => handleChange(idx, 'value', e.target.value)} className="border-0 shadow-none bg-transparent" /></td>
                                <td><Form.Control size="sm" placeholder="Description" value={item.desc} onChange={e => handleChange(idx, 'desc', e.target.value)} className="border-0 shadow-none bg-transparent" /></td>
                                <td className="text-center">
                                    {items.length > 1 && (
                                        <Button variant="link" className="text-muted p-0 opacity-50 hover-opacity-100" onClick={() => handleDelete(idx)}><FaTrash size={12}/></Button>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        );
    };

    // Saved Interfaces State
  const [savedInterfaces, setSavedInterfaces] = useState<SavedInterface[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [draggedId, setDraggedId] = useState<number | null>(null);
  const [dragOverId, setDragOverId] = useState<number | null>(null); // Folder ID being hovered

  // Drafts State
  const [drafts, setDrafts] = useState<Record<number, any>>({});
  
  // BaseURL Editing State

  // Save Modal State
  const [showSaveModal, setShowSaveModal] = useState(false);
  const [saveForm, setSaveForm] = useState({ name: '', description: '', parentId: null as number | null });
  const [editingTargetId, setEditingTargetId] = useState<number | null>(null);
  const [hoverId, setHoverId] = useState<number | null>(null);

  // Helper for folder options
  const renderFolderOptions = (parentId: number | null = null, depth = 0): ReactElement[] => {
      const items = savedInterfaces.filter(i => i.type === 'folder' && i.parentId === parentId);
      let options: ReactElement[] = [];
      
      items.forEach(item => {
          options.push(
              <option key={item.id} value={item.id}>
                  {'\u00A0'.repeat(depth * 4)}{item.name}
              </option>
          );
          options = [...options, ...renderFolderOptions(item.id, depth + 1)];
      });
      
      return options;
  };

  // Fetch Interfaces
  const fetchInterfaces = async () => {
      // Always fetch user's interfaces, project_id is optional filter
      try {
          const url = projectId ? `/api/standard/interfaces?project_id=${projectId}` : '/api/standard/interfaces';
          const res = await api.get<any[]>(url);
          if (res) {
              const mapped: SavedInterface[] = res.map(i => ({
                  id: i.id,
                  type: i.type,
                  name: i.name,
                  description: i.description,
                  parentId: i.parent_id,
                  isOpen: false,
                  
                  baseUrl: i.base_url,
                  apiPath: i.api_path,
                  method: i.method,
                  
                  headers: i.headers,
                  params: i.params,
                  bodyMode: i.body_mode,
                  rawType: i.raw_type,
                  bodyContent: i.body_content,
                  
                  testConfig: i.test_config,
                  requirement: i.test_config?.requirement,
                  mode: i.test_config?.mode,
                  testTypes: i.test_config?.testTypes
              }));
              setSavedInterfaces(mapped);
          }
      } catch (e) {
          console.error("Failed to fetch interfaces:", e);
      }
  };

  useEffect(() => {
      fetchInterfaces();
  }, [projectId]);

  const handleCreateFolder = async (parentId: number | null = null) => {
      const name = prompt("请输入文件夹名称:", "新建文件夹");
      if (!name) return;
      
      try {
          await api.post('/api/standard/interfaces', {
              name,
              type: 'folder',
              project_id: projectId,
              parent_id: parentId
          });
          fetchInterfaces();
      } catch (e: any) {
          alert('创建文件夹失败: ' + (e.message || String(e)));
      }
  };

  const handleCreateInterface = async (targetParentId?: number | null) => {
      let parentId = targetParentId;
      if (parentId === undefined) {
          parentId = null;
          if (selectedId) {
              const existing = savedInterfaces.find(i => i.id === selectedId);
              if (existing) {
                   if (existing.type === 'folder') {
                       parentId = existing.id;
                   } else {
                       parentId = existing.parentId;
                   }
              }
          }
      }

      try {
          const res = await api.post<any>('/api/standard/interfaces', {
              name: 'New Request',
              type: 'request',
              project_id: projectId,
              parent_id: parentId,
              method: 'GET'
          });
          
          if (res) {
              const newItem: SavedInterface = {
                  id: res.id,
                  type: res.type,
                  name: res.name,
                  description: res.description,
                  parentId: res.parent_id,
                  isOpen: false,
                  baseUrl: res.base_url,
                  apiPath: res.api_path,
                  method: res.method,
                  headers: res.headers,
                  params: res.params,
                  bodyMode: res.body_mode,
                  rawType: res.raw_type,
                  bodyContent: res.body_content,
                  testConfig: res.test_config,
                  requirement: res.test_config?.requirement,
                  mode: res.test_config?.mode,
                  testTypes: res.test_config?.testTypes
              };
              
              setSavedInterfaces(prev => [...prev, newItem]);
              handleLoadInterface(newItem);
              
              // Ensure parent folder is open
              if (parentId) {
                  setSavedInterfaces(prev => prev.map(i => 
                      i.id === parentId ? { ...i, isOpen: true } : i
                  ));
              }
          }
      } catch (e: any) {
          alert('创建接口失败: ' + (e.message || String(e)));
      }
  };

  const handleSaveInterfaceClick = () => {
      setEditingTargetId(null);
      if (!apiPath) return alert('请至少填写接口路径');
      
      let defaultParentId = null;
      if (selectedId) {
          const existing = savedInterfaces.find(i => i.id === selectedId);
          if (existing) {
               if (existing.type === 'folder') {
                   defaultParentId = existing.id;
               } else {
                   defaultParentId = existing.parentId;
               }
          }
      }

      if (selectedId) {
          const existing = savedInterfaces.find(i => i.id === selectedId);
          if (existing && existing.type === 'request') {
              // Pre-fill form
              setSaveForm({ 
                  name: existing.name, 
                  description: existing.description || '',
                  parentId: existing.parentId
              });
          } else {
             setSaveForm({ name: apiPath, description: '', parentId: defaultParentId }); 
          }
      } else {
          setSaveForm({ name: apiPath, description: '', parentId: defaultParentId });
      }
      setShowSaveModal(true);
  };

  const handleConfirmSave = async () => {
      if (!saveForm.name) return alert('请输入名称');

      if (editingTargetId) {
          const target = savedInterfaces.find(i => i.id === editingTargetId);
          if (target) {
              const updates: any = {
                  name: saveForm.name,
                  description: saveForm.description,
                  parent_id: saveForm.parentId,
                  project_id: projectId
              };
              await updateInterface(editingTargetId, updates);
              setShowSaveModal(false);
              setEditingTargetId(null);
              fetchInterfaces();
              return;
          }
      }
      
      const payload = {
          name: saveForm.name,
          description: saveForm.description,
          project_id: projectId,
          type: 'request',
          method,
          base_url: '',
          api_path: apiPath,
          headers,
          params: queryParams,
          body_mode: bodyMode,
          raw_type: rawType,
          body_content: bodyContent,
          test_config: { testTypes, mode, requirement },
          parent_id: saveForm.parentId
      };

      try {
          if (selectedId) {
              // Update? Or Create New if user wants "Save As"?
              // Ideally if selectedId exists and it's a request, update it.
              // But maybe user wants to change name.
              await api.put(`/api/standard/interfaces/${selectedId}`, payload);
              onLog('接口已更新');
          } else {
              // Create
              const res = await api.post<SavedInterface>('/api/standard/interfaces', payload);
              if(res) setSelectedId(res.id);
              onLog('接口已保存');
          }
          setShowSaveModal(false);
          fetchInterfaces();
      } catch (e) {
          alert('保存失败: ' + e);
      }
  };

  const handleLoadInterface = (item: SavedInterface) => {
      if (item.type === 'folder') {
          // Toggle folder
          setSavedInterfaces(prev => prev.map(i => 
              i.id === item.id ? { ...i, isOpen: !i.isOpen } : i
          ));
          return;
      }

      // Save current draft
      if (selectedId) {
          setDrafts(prev => ({
              ...prev,
              [selectedId]: {
                  apiPath,
                  method,
                  requirement,
                  mode,
                  testTypes,
                  headers,
                  params: queryParams,
                  bodyMode,
                  rawType,
                  bodyContent
              }
          }));
      }

      setSelectedId(item.id);
      
      // Load from draft if exists, else from item
      const draft = drafts[item.id];
      
      if (draft) {
          // Merge BaseURL and Path for single input
          setApiPath((draft.baseUrl || '') + (draft.apiPath || ''));
          setMethod(draft.method ?? item.method ?? 'POST');
          setRequirement(draft.requirement ?? item.requirement ?? (item.testConfig?.requirement) ?? '');
          setMode(draft.mode ?? item.mode ?? (item.testConfig?.mode) ?? 'natural');
          setTestTypes(draft.testTypes ?? item.testConfig?.testTypes ?? { functional: true, boundary: false, security: false });
          
          setHeaders(draft.headers ?? item.headers ?? [{key:'', value:'', desc:''}]);
          setQueryParams(draft.params ?? item.params ?? [{key:'', value:'', desc:''}]);
          
          setBodyMode(draft.bodyMode ?? item.bodyMode ?? 'raw');
          setRawType(draft.rawType ?? item.rawType ?? 'JSON');
          setBodyContent(draft.bodyContent ?? item.bodyContent ?? '');
      } else {
          // Merge BaseURL and Path for single input
          setApiPath((item.baseUrl || '') + (item.apiPath || ''));
          setMethod(item.method || 'POST');
          setRequirement(item.requirement || (item.testConfig?.requirement) || '');
          setMode(item.mode || (item.testConfig?.mode) || 'natural');
          setTestTypes(item.testConfig?.testTypes || { functional: true, boundary: false, security: false });
          
          setHeaders(item.headers || [{key:'', value:'', desc:''}]);
          setQueryParams(item.params || [{key:'', value:'', desc:''}]);
          
          setBodyMode((item.bodyMode as any) || 'raw');
          setRawType((item.rawType as any) || 'JSON');
          setBodyContent(item.bodyContent || '');
      }
  };

  const handleEditFolder = (item: SavedInterface) => {
      setEditingTargetId(item.id);
      setSaveForm({
          name: item.name,
          description: item.description || '',
          parentId: item.parentId
      });
      setShowSaveModal(true);
  };



  const handleDeleteInterface = async (id: number, e: MouseEvent) => {
      e.stopPropagation();
      if (!window.confirm('确定要删除吗？')) return;
      try {
          await api.delete(`/api/standard/interfaces/${id}`);
          if (selectedId === id) setSelectedId(null);
          fetchInterfaces();
      } catch (e) {
          alert('删除失败');
      }
  };

  // Drag and Drop Handlers
  const handleDragStart = (e: React.DragEvent, id: number) => {
      e.stopPropagation();
      e.dataTransfer.setData('text/plain', String(id));
      setDraggedId(id);
  };

  const handleDragOver = (e: React.DragEvent, targetId: number, isFolder: boolean) => {
      e.preventDefault();
      e.stopPropagation();
      if (draggedId === targetId) return;
      if (isFolder) {
          setDragOverId(targetId);
      }
  };

  const handleDragLeave = () => {
      setDragOverId(null);
  };

  const handleDrop = async (e: React.DragEvent, targetId: number | null) => {
        e.preventDefault();
        e.stopPropagation();
        setDragOverId(null);
        const idStr = e.dataTransfer.getData('text/plain');
        if (!idStr) return;
        const id = Number(idStr);
        if (id === targetId) return;

        // Prevent dropping folder into its own child
        // Check circular dependency
        let current = targetId;
        while (current) {
            if (current === id) return; // Can't drop parent into child
            const parent = savedInterfaces.find(i => i.id === current)?.parentId;
            current = parent || null;
        }
        
        const updates: any = { parent_id: targetId };
        
        // BaseURL Inheritance Logic
        if (targetId) {
             const targetFolder = savedInterfaces.find(i => i.id === targetId);
             if (targetFolder && targetFolder.baseUrl) {
                 updates.base_url = targetFolder.baseUrl;
             }
        }
        
        updateInterface(id, updates);
        setDraggedId(null);
    };

    // Check if unsaved changes exist
  const isUnsaved = (item: SavedInterface) => {
      if (item.id !== selectedId) return false;
      if (item.type === 'folder') return false;
      
      const normalize = (val: any) => val === null || val === undefined ? '' : String(val);
      
      // Compare concatenated URL
      const savedFullUrl = normalize(item.baseUrl) + normalize(item.apiPath);
      
      return (
          savedFullUrl !== normalize(apiPath) ||
          normalize(item.method) !== normalize(method) ||
          normalize(item.requirement) !== normalize(requirement) ||
          normalize(item.mode) !== normalize(mode) ||
          JSON.stringify(item.testTypes) !== JSON.stringify(testTypes)
      );
  };

  const updateInterface = async (id: number, updates: any) => {
      // Optimistic update
      setSavedInterfaces(prev => prev.map(item => {
          if (item.id === id) {
              const newItem = { ...item };
              if (updates.parent_id !== undefined) newItem.parentId = updates.parent_id;
              if (updates.name !== undefined) newItem.name = updates.name;
              if (updates.base_url !== undefined) newItem.baseUrl = updates.base_url;
              if (updates.method !== undefined) newItem.method = updates.method;
              if (updates.api_path !== undefined) newItem.apiPath = updates.api_path;
              if (updates.description !== undefined) newItem.description = updates.description;
              return newItem;
          }
          return item;
      }));

      try {
          await api.put(`/api/standard/interfaces/${id}`, updates);
      } catch (e) {
          console.error("Update failed", e);
          fetchInterfaces(); // Revert
      }
  };

  const toggleFolder = (id: number) => {
      setSavedInterfaces(prev => prev.map(i => 
          i.id === id ? { ...i, isOpen: !i.isOpen } : i
      ));
  };

  const handleApiPathBlur = () => {
      if (!apiPath || !apiPath.trim()) return;
      
      // Check for {{VAR}} format at start of URL
      const match = apiPath.match(/^(\{\{\s*(.+?)\s*\}\})/);
      if (match) {
          const tag = match[1]; // {{key}}
          const envName = match[2]; // key
          
          const exists = savedEnvs.some(e => e.baseUrl === tag);
          if (!exists) {
              const newEnv: EnvConfig = {
                  id: Date.now().toString(),
                  name: envName,
                  baseUrl: tag,
                  variables: []
              };
              setSavedEnvs(prev => [...prev, newEnv]);
          }
      }
  };

  const handleRenameConfirm = async () => {
      if (renamingId === null) return;
      if (!renamingName.trim()) {
          setRenamingId(null);
          return;
      }

      // Update UI optimistically
      setSavedInterfaces(prev => prev.map(item => 
          item.id === renamingId ? { ...item, name: renamingName } : item
      ));

      try {
          await api.put(`/api/standard/interfaces/${renamingId}`, { name: renamingName });
      } catch (e) {
          console.error("Rename failed", e);
          fetchInterfaces(); // Revert
      } finally {
          setRenamingId(null);
      }
  };

  const renderTree = (parentId: number | null, depth = 0) => {
      const items = savedInterfaces.filter(i => i.parentId === parentId);
      if (items.length === 0) return null;

      return items.map(item => {
          const isFolder = item.type === 'folder';
          const unsaved = isUnsaved(item);
          const isSelected = item.id === selectedId;
          const isOver = dragOverId === item.id;
          const isHovered = hoverId === item.id;

          return (
              <div key={item.id}>
                  <div 
                    draggable={renamingId !== item.id}
                    onMouseEnter={() => setHoverId(item.id)}
                      onMouseLeave={() => setHoverId(null)}
                      onDragStart={(e) => handleDragStart(e, item.id)}
                      onDragOver={(e) => handleDragOver(e, item.id, isFolder)}
                      onDragLeave={handleDragLeave}
                      onDrop={(e) => isFolder ? handleDrop(e, item.id) : undefined}
                      className={`
                          ${isOver ? 'bg-primary-subtle border border-primary' : 'border border-transparent'}
                          ${isSelected ? 'bg-light' : ''}
                          rounded
                      `}
                      style={{transition: 'all 0.2s'}}
                  >
                      <ListGroup.Item 
                          action 
                          active={isSelected}
                          onClick={() => handleLoadInterface(item)}
                          className={`border-0 py-1 px-2 d-flex align-items-center ${depth > 0 ? 'ms-3' : ''}`}
                          style={{ 
                              paddingLeft: `${depth * 12 + 4}px`,
                              backgroundColor: isSelected ? '#e9ecef' : 'transparent',
                              borderLeft: isSelected ? '3px solid #0d6efd' : '3px solid transparent'
                          }}
                      >
                          <div 
                              className="me-1 d-flex align-items-center justify-content-center text-secondary"
                              style={{width: '20px', height: '20px', cursor: 'pointer', visibility: isFolder ? 'visible' : 'hidden'}}
                              onClick={(e) => {
                                  e.stopPropagation();
                                  toggleFolder(item.id);
                              }}
                          >
                              {item.isOpen ? <FaChevronDown size={10} /> : <FaChevronRight size={10} />}
                          </div>

                          <div className="d-flex align-items-center flex-grow-1 overflow-hidden">
                                {isFolder && (
                                  <span className="me-2 text-warning">
                                      <FaLayerGroup size={14}/>
                                  </span>
                              )}
                                {!isFolder && (
                                    <span className={`fw-bold small me-2 ${getMethodColor(item.method || 'GET')}`} style={{fontSize: '0.7rem', width: '30px'}}>
                                        {item.method}
                                    </span>
                                )}
                                
                                {renamingId === item.id ? (
                                    <Form.Control 
                                        size="sm"
                                        value={renamingName}
                                        onChange={e => setRenamingName(e.target.value)}
                                        onBlur={handleRenameConfirm}
                                        onKeyDown={e => e.key === 'Enter' && handleRenameConfirm()}
                                        autoFocus
                                        onClick={e => e.stopPropagation()}
                                        onMouseDown={e => e.stopPropagation()}
                                        onDragStart={e => { e.preventDefault(); e.stopPropagation(); }}
                                        className="p-0 px-1 py-0 h-auto"
                                    />
                                ) : (
                                    <span 
                                        className="text-truncate small fw-medium flex-grow-1" 
                                        title={item.name}
                                        onDoubleClick={(e) => {
                                            e.stopPropagation();
                                            setRenamingId(item.id);
                                            setRenamingName(item.name);
                                        }}
                                    >
                                        {item.name}
                                    </span>
                                )}

                                {/* Status Light */}
                            {!isFolder && (
                                <div 
                                    className="rounded-circle me-2 flex-shrink-0" 
                                    style={{
                                        width: '8px', 
                                        height: '8px', 
                                        backgroundColor: unsaved ? '#ffc107' : '#198754',
                                        cursor: unsaved ? 'pointer' : 'default'
                                    }}
                                    title={unsaved ? "未保存 (点击保存)" : "已保存"}
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        if (unsaved) handleSaveInterfaceClick();
                                    }}
                                />
                            )}
                        </div>
                        
                        <div 
                              className="ms-2 d-flex gap-2 align-items-center flex-shrink-0"
                              style={{ opacity: (isHovered || isSelected) ? 1 : 0, transition: 'opacity 0.2s' }}
                          >
                              <Dropdown onClick={(e) => e.stopPropagation()}>
                                  <Dropdown.Toggle as="div" className="cursor-pointer text-secondary px-1 no-caret">
                                      <FaEllipsisH size={12} />
                                  </Dropdown.Toggle>

                                <Dropdown.Menu align="end" style={{ zIndex: 1050 }}>
                                    <Dropdown.Item onClick={() => isFolder ? handleCreateInterface(item.id) : handleCreateInterface(item.parentId)}>
                                        <FaPlus className="me-2" /> 新增接口
                                    </Dropdown.Item>
                                    {isFolder && (
                                         <Dropdown.Item onClick={() => handleEditFolder(item)}>
                                             <FaEdit className="me-2" /> 编辑详情
                                         </Dropdown.Item>
                                    )}
                                    <Dropdown.Item onClick={() => {
                                         setRenamingId(item.id);
                                         setRenamingName(item.name);
                                    }}>
                                        <FaEdit className="me-2" /> 重命名
                                    </Dropdown.Item>
                                    <Dropdown.Item onClick={(e) => handleDeleteInterface(item.id, e as any)} className="text-danger">
                                        <FaTrash className="me-2" /> 删除
                                    </Dropdown.Item>
                                </Dropdown.Menu>
                            </Dropdown>
                        </div>
                      </ListGroup.Item>
                  </div>
                  
                  {isFolder && item.isOpen && (
                      <div className="border-start ms-2 ps-1">
                          {renderTree(item.id, depth + 1)}
                      </div>
                  )}
              </div>
          );
      });
  };

  const handleRun = async () => {
    if (!projectId) return alert('请先选择项目');
    if (!apiPath) return alert('请输入请求 URL');
    if (!requirement) return alert('请输入 AI 测试意图或接口定义');
    
    setLoading(true);
    setTestResult(null);
    setResponseTab('report'); // Switch to report tab to show progress
    onLog(`开始生成接口测试脚本 (${mode === 'natural' ? '自然语言' : '结构化'}模式)...`);
    
    try {
      const activeTypes = (Object.keys(testTypes) as Array<keyof typeof testTypes>)
        .filter(k => testTypes[k])
        .map(k => k.charAt(0).toUpperCase() + k.slice(1));

      // Construct a richer prompt by combining method/url/requirement and user config
      let richRequirement = `
Method: ${method}
URL: ${apiPath}
Context/Requirement: ${requirement}
      `.trim();

      // Append Params
      const validParams = queryParams.filter(p => p.key);
      if (validParams.length > 0) {
          richRequirement += `\n\nQuery Params:\n${validParams.map(p => `${p.key}: ${p.value} (${p.desc})`).join('\n')}`;
      }

      // Append Headers
      const validHeaders = headers.filter(h => h.key);
      if (validHeaders.length > 0) {
          richRequirement += `\n\nHeaders:\n${validHeaders.map(h => `${h.key}: ${h.value} (${h.desc})`).join('\n')}`;
      }

      // Append Body
      if (bodyMode !== 'none' && bodyContent.trim()) {
          richRequirement += `\n\nRequest Body (${bodyMode}):\n${bodyContent}`;
      }

      const data = await api.post<TestResult>('/api/api-testing', { 
        requirement: richRequirement, 
        project_id: projectId,
        base_url: '', // Deprecated: Full URL is in richRequirement/prompt
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
    } finally {
      setLoading(false);
    }
  };

  const getMethodColor = (m: string) => {
      switch(m) {
          case 'GET': return 'text-primary';
          case 'POST': return 'text-warning';
          case 'PUT': return 'text-info';
          case 'DELETE': return 'text-danger';
          default: return 'text-secondary';
      }
  };

  const renderDashboard = (report: NonNullable<TestResult['structured_report']>) => {
      const passRate = report.total > 0 ? (report.passed / report.total) * 100 : 0;
      
      return (
          <div className="d-flex flex-column gap-3 animate-fade-in p-3">
              <Row className="g-3">
                  <Col md={3} xs={6}>
                      <Card className="text-center h-100 border-success shadow-sm">
                          <Card.Body className="d-flex flex-column justify-content-center py-2">
                              <h3 className="text-success mb-0 fw-bold">{Math.round(passRate)}%</h3>
                              <div className="small text-muted">通过率</div>
                          </Card.Body>
                      </Card>
                  </Col>
                  <Col md={3} xs={6}>
                      <Card className="text-center h-100 border-primary shadow-sm">
                          <Card.Body className="d-flex flex-column justify-content-center py-2">
                              <h3 className="text-primary mb-0 fw-bold">{report.total}</h3>
                              <div className="small text-muted">总用例</div>
                          </Card.Body>
                      </Card>
                  </Col>
                  <Col md={3} xs={6}>
                      <Card className={`text-center h-100 shadow-sm ${report.failed > 0 ? 'border-danger bg-danger bg-opacity-10' : 'border-light'}`}>
                          <Card.Body className="d-flex flex-column justify-content-center py-2">
                              <h3 className={`mb-0 fw-bold ${report.failed > 0 ? 'text-danger' : 'text-secondary'}`}>{report.failed}</h3>
                              <div className="small text-muted">失败</div>
                          </Card.Body>
                      </Card>
                  </Col>
                  <Col md={3} xs={6}>
                      <Card className="text-center h-100 border-light shadow-sm">
                          <Card.Body className="d-flex flex-column justify-content-center py-2">
                              <h5 className="text-secondary mb-0">{report.time.toFixed(2)}s</h5>
                              <div className="small text-muted">耗时</div>
                          </Card.Body>
                      </Card>
                  </Col>
              </Row>
              
              {report.failures.length > 0 ? (
                  <Card className="border-danger shadow-sm mt-2">
                      <Card.Header className="bg-danger text-white d-flex align-items-center gap-2 py-2">
                          <FaBug /> 失败用例透视
                      </Card.Header>
                      <Accordion flush alwaysOpen>
                          {report.failures.map((fail, idx) => (
                              <Accordion.Item eventKey={String(idx)} key={idx}>
                                  <Accordion.Header>
                                      <div className="d-flex align-items-center gap-2">
                                          <Badge bg="danger">Failed</Badge>
                                          <span className="font-monospace text-truncate" style={{maxWidth: '300px'}}>{fail.name}</span>
                                      </div>
                                  </Accordion.Header>
                                  <Accordion.Body className="bg-light p-2">
                                      <div className="mb-2 small">
                                          <strong>错误归因:</strong> <span className="text-danger fw-bold ms-2">{fail.message}</span>
                                      </div>
                                      <ErrorTrace details={fail.details} />
                                  </Accordion.Body>
                              </Accordion.Item>
                          ))}
                      </Accordion>
                  </Card>
              ) : (
                   <div className="alert alert-success d-flex align-items-center mt-2">
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
    <div className="d-flex h-100 w-100 bg-white overflow-hidden">
      {/* Left Sidebar - Interface List */}
      <div className="border-end bg-light d-flex flex-column" style={{ 
          width: showSidebar ? '260px' : '0px', 
          minWidth: showSidebar ? '260px' : '0px',
          overflow: 'hidden',
          transition: 'all 0.3s ease',
          opacity: showSidebar ? 1 : 0
      }}>
        <div className="d-flex justify-content-between align-items-center mb-2 px-3 pt-3">
            <h6 className="fw-bold mb-0 text-secondary">接口列表</h6>
            <div className="d-flex gap-2">
                 <Button variant="link" className="p-0 text-secondary" onClick={() => handleCreateFolder(null)} title="新建文件夹">
                     <FaFolderPlus size={16} />
                 </Button>
                 <Button variant="link" className="p-0 text-secondary" onClick={() => handleCreateInterface(null)} title="新建接口">
                     <FaPlus size={16} />
                 </Button>
            </div>
        </div>
        <div 
            className="flex-grow-1 overflow-auto border-top bg-light position-relative"
            style={{ minHeight: '100px' }}
        >
            <ListGroup variant="flush">
                {renderTree(null)}
            </ListGroup>
            
            {savedInterfaces.length === 0 && (
                <div className="text-center text-muted mt-5 small position-absolute w-100" style={{top: '100px', left: 0, pointerEvents: 'none'}}>
                    暂无接口，点击右上角 + 新建
                </div>
            )}
        </div>
        
        {/* Fixed Drop Zone for Moving to Root */}
        <div 
            className="border-top bg-light d-flex align-items-center justify-content-center text-muted small py-3" 
            style={{
                cursor: 'default',
                transition: 'background-color 0.2s'
            }}
            onDragOver={(e) => { 
                e.preventDefault(); 
                e.currentTarget.style.backgroundColor = '#e9ecef';
            }}
            onDragLeave={(e) => {
                e.currentTarget.style.backgroundColor = '';
            }}
            onDrop={(e) => {
                e.preventDefault();
                e.currentTarget.style.backgroundColor = '';
                const idStr = e.dataTransfer.getData('text/plain');
                if (!idStr) return;
                const id = parseInt(idStr);
                // Move to root
                updateInterface(id, { parent_id: null });
                setDraggedId(null);
            }}
        >
            <div style={{opacity: 0.7}}>拖拽至此移出文件夹</div>
        </div>
      </div>

      {/* Main Content */}
      <div 
        className="flex-grow-1 d-flex flex-column h-100 overflow-hidden bg-white"
        ref={mainContentRef}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        
        {/* 1. Request Bar (Postman Style) */}
        <div className="d-flex align-items-center p-2 border-bottom bg-light gap-2" style={{height: '50px'}}>
            <Button variant="link" className="p-0 text-secondary me-2" onClick={() => setShowSidebar(!showSidebar)} title={showSidebar ? "收起列表" : "展开列表"}>
                <FaBars size={16} />
            </Button>
            
            <div className="d-flex flex-grow-1 bg-white border rounded">
                 <Form.Select 
                    className="border-0 fw-bold text-secondary" 
                    style={{width: '110px', backgroundColor: '#f9f9f9', borderRight: '1px solid #dee2e6'}} 
                    value={method} 
                    onChange={e => setMethod(e.target.value)}
                 >
                    <option value="GET">GET</option>
                    <option value="POST">POST</option>
                    <option value="PUT">PUT</option>
                    <option value="DELETE">DELETE</option>
                 </Form.Select>
                 <div className="d-flex flex-grow-1 align-items-center px-2 border-end bg-white position-relative">
                    <div className="position-relative w-100 h-100 d-flex align-items-center">
                        {/* Highlighter Background */}
                        <div 
                            ref={highlighterRef}
                            className="position-absolute top-0 start-0 w-100 h-100 d-flex align-items-center"
                            style={{
                                whiteSpace: 'pre',
                                overflow: 'hidden',
                                pointerEvents: 'none',
                                font: 'inherit',
                                color: 'black', // Default text color
                                paddingLeft: '0px', // Match input padding
                                paddingRight: '0px'
                            }}
                        >
                            {apiPath.split(/(\{\{.*?\}\})/).map((part, index) => {
                                if (part.startsWith('{{') && part.endsWith('}}')) {
                                    const isEmpty = part.replace(/[\{\}\s]/g, '').length === 0;
                                    return (
                                        <span key={index} style={{ color: isEmpty ? '#ffc107' : '#0d6efd' }}>
                                            {part}
                                        </span>
                                    );
                                }
                                return <span key={index} style={{ color: 'black' }}>{part}</span>;
                            })}
                        </div>

                        {/* Foreground Input */}
                        <Form.Control 
                            ref={inputRef}
                            className="border-0 shadow-none p-0 bg-transparent custom-api-input"
                            placeholder="Enter request URL" 
                            value={apiPath} 
                            onChange={e => setApiPath(e.target.value)} 
                            onMouseMove={handleInputMouseMove}
                            onMouseLeave={handleInputMouseLeave}
                            onScroll={(e) => {
                                if (highlighterRef.current) {
                                    highlighterRef.current.scrollLeft = e.currentTarget.scrollLeft;
                                }
                                setInputScrollLeft(e.currentTarget.scrollLeft);
                            }}
                            style={{
                                color: apiPath ? 'transparent' : undefined, 
                                caretColor: 'black',
                                position: 'relative',
                                zIndex: 1,
                                fontFamily: 'inherit'
                            }}
                        />
                     </div>
                    
                    {activeEnvTag && showPopup && (
                        <div 
                            className="position-absolute start-0 end-0 bg-white border rounded shadow-sm px-3 py-2" 
                            style={{top: '100%', zIndex: 1050, marginTop: '4px'}}
                            onMouseEnter={handlePopupMouseEnter}
                            onMouseLeave={handlePopupMouseLeave}
                        >
                            <div className="d-flex align-items-center bg-white rounded px-2 py-1 border">
                                <span className={`fw-bold small me-2 font-monospace ${activeEnvTag.replace(/[\{\}\s]/g, '').length === 0 ? 'text-warning' : 'text-primary'}`} style={activeEnvTag.replace(/[\{\}\s]/g, '').length === 0 ? {color: '#ffc107'} : {}}>{activeEnvTag}:</span>
                                <Form.Control 
                                    size="sm"
                                    className="border-0 bg-transparent shadow-none p-0 text-muted"
                                    placeholder="Enter Base URL value..."
                                    value={getEnvBaseUrlValue(activeEnvTag)}
                                    onChange={e => setEnvBaseUrlValue(activeEnvTag, e.target.value)}
                                    onFocus={() => {
                                        if (popupTimerRef.current) {
                                            clearTimeout(popupTimerRef.current);
                                            popupTimerRef.current = null;
                                        }
                                        setShowPopup(true);
                                    }}
                                    onBlur={() => {
                                         popupTimerRef.current = setTimeout(() => setShowPopup(false), 300);
                                    }}
                                />
                            </div>
                        </div>
                    )}
                </div>
           </div>
           
           <Button variant="primary" onClick={handleSendRequest} disabled={loading} className="px-4 fw-bold text-white">
                {loading ? <Spinner size="sm" animation="border" /> : "Send"}
            </Button>
            <Button variant="outline-secondary" className="px-3 fw-bold" onClick={handleSaveInterfaceClick} title="保存接口">
                <FaSave className="me-1"/> Save
            </Button>
            <Button variant="light" className="border text-secondary" onClick={handleSaveEnv} title="环境管理">
                <FaCog className="me-1"/> 环境管理
            </Button>
        </div>

        {/* 2. Request Config Tabs */}
        <div className="border-bottom px-3 pt-2 bg-white" style={{height: '45px'}}>
            <Nav variant="underline" activeKey={runSubTab} onSelect={k => setRunSubTab(k || 'params')} className="small">
                <Nav.Item><Nav.Link eventKey="params" className="text-secondary">Params</Nav.Link></Nav.Item>
                <Nav.Item><Nav.Link eventKey="authorization" className="text-secondary">Authorization</Nav.Link></Nav.Item>
                <Nav.Item><Nav.Link eventKey="headers" className="text-secondary">Headers <span className="text-muted ms-1">({headers.filter(h=>h.key).length})</span></Nav.Link></Nav.Item>
                <Nav.Item><Nav.Link eventKey="body" className="text-secondary">Body</Nav.Link></Nav.Item>
                <Nav.Item><Nav.Link eventKey="scripts" className="text-secondary">Scripts</Nav.Link></Nav.Item>
                <Nav.Item><Nav.Link eventKey="settings" className="text-secondary">Settings</Nav.Link></Nav.Item>
                <Nav.Item><Nav.Link eventKey="ai_prompt" className="text-primary fw-bold"><FaRobot className="me-1"/>AI Gen</Nav.Link></Nav.Item>
            </Nav>
        </div>

        {/* 3. Request Config Content */}
        <div className="overflow-auto bg-white d-flex flex-column" style={{height: `${requestHeight}px`, minHeight: '100px'}}>
             {runSubTab === 'params' && renderKvEditor(queryParams, setQueryParams)}
             {runSubTab === 'headers' && renderKvEditor(headers, setHeaders)}
             {runSubTab === 'authorization' && <div className="p-4 text-center text-muted">Authorization settings (Bearer Token, Basic Auth, etc.)</div>}
             {runSubTab === 'scripts' && <div className="p-4 text-center text-muted">Pre-request and Post-response scripts</div>}
             {runSubTab === 'settings' && <div className="p-4 text-center text-muted">Request settings (Timeout, Redirects, etc.)</div>}
             
             {runSubTab === 'body' && (
                <div className="h-100 d-flex flex-column">
                    <div className="d-flex gap-3 px-3 py-2 small border-bottom bg-light">
                        <Form.Check type="radio" label="none" checked={bodyMode==='none'} onChange={()=>setBodyMode('none')} inline id="body-none" className="mb-0"/>
                        <Form.Check type="radio" label="form-data" checked={bodyMode==='form-data'} onChange={()=>setBodyMode('form-data')} inline id="body-form" className="mb-0"/>
                        <Form.Check type="radio" label="x-www-form-urlencoded" checked={bodyMode==='x-www-form-urlencoded'} onChange={()=>setBodyMode('x-www-form-urlencoded')} inline id="body-url" className="mb-0"/>
                        <Form.Check type="radio" label="raw" checked={bodyMode==='raw'} onChange={()=>setBodyMode('raw')} inline id="body-raw" className="mb-0"/>
                        <Form.Check type="radio" label="binary" checked={bodyMode==='binary'} onChange={()=>setBodyMode('binary')} inline id="body-binary" className="mb-0"/>
                        <Form.Check type="radio" label="GraphQL" checked={bodyMode==='graphql'} onChange={()=>setBodyMode('graphql')} inline id="body-graphql" className="mb-0"/>
                        
                        {bodyMode === 'raw' && (
                             <Nav className="ms-auto">
                                 <Dropdown>
                                     <Dropdown.Toggle variant="link" size="sm" className="text-decoration-none p-0 text-primary small">
                                         {rawType} <FaChevronDown size={8}/>
                                     </Dropdown.Toggle>
                                     <Dropdown.Menu align="end">
                                         {['Text', 'JavaScript', 'JSON', 'HTML', 'XML'].map(t => (
                                             <Dropdown.Item key={t} onClick={() => setRawType(t as any)} active={rawType===t}>{t}</Dropdown.Item>
                                         ))}
                                     </Dropdown.Menu>
                                 </Dropdown>
                             </Nav>
                        )}
                    </div>
                    
                    {bodyMode !== 'none' ? (
                        <Form.Control 
                            as="textarea" 
                            className="flex-grow-1 font-monospace small border-0 p-3" 
                            value={bodyContent} 
                            onChange={e => setBodyContent(e.target.value)} 
                            placeholder={bodyMode === 'raw' && rawType === 'JSON' ? '{\n  "key": "value"\n}' : 'Request body content...'}
                            style={{resize: 'none', outline: 'none'}}
                        />
                    ) : (
                        <div className="d-flex align-items-center justify-content-center flex-grow-1 text-muted small bg-light">
                            This request has no body.
                        </div>
                    )}
                </div>
             )}

             {runSubTab === 'ai_prompt' && (
                 <div className="h-100 d-flex flex-column p-3">
                     <div className="d-flex justify-content-between mb-2">
                         <Form.Label className="small text-muted mb-0">AI Test Generation (Natural Language or JSON Definition)</Form.Label>
                         <div className="d-flex gap-2">
                            <Form.Check type="radio" label="Natural" checked={mode==='natural'} onChange={()=>setMode('natural')} inline className="small"/>
                            <Form.Check type="radio" label="JSON" checked={mode==='structured'} onChange={()=>setMode('structured')} inline className="small"/>
                         </div>
                     </div>
                     <Form.Control 
                        as="textarea" 
                        className="flex-grow-1 font-monospace small bg-light" 
                        style={{border: '1px solid #dee2e6'}}
                        value={requirement}
                        onChange={e => setRequirement(e.target.value)}
                        placeholder="Describe your test scenario..."
                     />
                     <div className="mt-2 d-flex justify-content-end">
                        <Button variant="outline-primary" size="sm" onClick={handleRun} disabled={loading}>
                             <FaRobot className="me-1"/> Generate & Run Tests
                        </Button>
                     </div>
                 </div>
             )}
        </div>

        {/* 4. Resizer / Divider */}
        <div 
            className="border-top border-bottom bg-light d-flex align-items-center justify-content-center text-muted" 
            style={{ 
                height: '8px', 
                cursor: 'row-resize', 
                backgroundColor: isDragging ? '#e9ecef' : '#f0f0f0',
                userSelect: 'none' 
            }}
            onMouseDown={handleMouseDown}
        >
             <div style={{ width: '30px', height: '3px', backgroundColor: isDragging ? '#0d6efd' : '#ccc', borderRadius: '2px' }}></div>
        </div>

        {/* 5. Response Section */}
        <div className="d-flex flex-column bg-white flex-grow-1" style={{minHeight: '200px'}}>
             <div className="px-3 py-1 border-bottom bg-white d-flex justify-content-between align-items-center">
                <Nav variant="underline" activeKey={responseTab} onSelect={k => setResponseTab(k || 'body')} className="small">
                     <Nav.Item><Nav.Link eventKey="body" className="text-secondary">Body</Nav.Link></Nav.Item>
                     <Nav.Item><Nav.Link eventKey="cookies" className="text-secondary">Cookies</Nav.Link></Nav.Item>
                     <Nav.Item><Nav.Link eventKey="headers" className="text-secondary">Headers <span className="text-muted">({Object.keys(responseHeaders).length})</span></Nav.Link></Nav.Item>
                     <Nav.Item><Nav.Link eventKey="test_results" className="text-secondary">Test Results</Nav.Link></Nav.Item>
                     <Nav.Item><Nav.Link eventKey="report" className="text-primary"><FaRobot className="me-1"/>AI Report</Nav.Link></Nav.Item>
                </Nav>
                <div className="d-flex gap-3 align-items-center small text-secondary">
                    <span>Status: <span className={responseStatus === 200 ? "text-success fw-bold" : (responseStatus ? "text-danger fw-bold" : "")}>{responseStatus || '---'}</span></span>
                    <span>Time: <span className="fw-bold text-dark">{responseTime ? responseTime + ' ms' : '---'}</span></span>
                    <span>Size: <span className="fw-bold text-dark">{responseBody ? responseBody.length + ' B' : '---'}</span></span>
                </div>
             </div>
             
             <div className="flex-grow-1 overflow-auto p-0 position-relative">
                 {loading && (
                     <div className="position-absolute top-0 start-0 w-100 h-100 bg-white bg-opacity-75 d-flex align-items-center justify-content-center z-1">
                         <Spinner animation="border" variant="primary" />
                     </div>
                 )}
                 
                 {responseTab === 'body' && (
                    <div className="h-100 d-flex flex-column">
                        {responseBody ? (
                            <>
                                <div className="bg-light border-bottom px-2 py-1 d-flex justify-content-between align-items-center">
                                    <div className="small text-muted">
                                        <Button variant="link" size="sm" className={`p-0 me-2 text-decoration-none ${responseViewMode==='json'?'fw-bold text-dark':'text-secondary'}`} onClick={()=>setResponseViewMode('json')}>Pretty</Button>
                                        <Button variant="link" size="sm" className={`p-0 me-2 text-decoration-none ${responseViewMode==='html'?'fw-bold text-dark':'text-secondary'}`} onClick={()=>setResponseViewMode('html')}>Preview</Button>
                                        {/* Add Raw view logic if needed */}
                                    </div>
                                    <div className="d-flex gap-2">
                                         <Form.Select size="sm" style={{width: 'auto', fontSize: '0.75rem', padding: '0 0.5rem', height: '20px'}} value="JSON">
                                             <option>JSON</option>
                                             <option>XML</option>
                                             <option>HTML</option>
                                             <option>Text</option>
                                         </Form.Select>
                                    </div>
                                </div>
                                <div className="flex-grow-1 bg-white position-relative">
                                    {responseViewMode === 'html' ? (
                                        <iframe 
                                           srcDoc={responseBody} 
                                           style={{width: '100%', height: '100%', border: 'none'}} 
                                           title="Response Preview"
                                           sandbox="allow-same-origin"
                                        />
                                    ) : (
                                        <textarea 
                                           className="w-100 h-100 border-0 p-3 font-monospace small" 
                                           style={{resize: 'none', outline: 'none', color: '#333'}}
                                           value={typeof responseBody === 'object' ? JSON.stringify(responseBody, null, 2) : responseBody}
                                           readOnly
                                        />
                                    )}
                                </div>
                            </>
                        ) : (
                             <div className="d-flex flex-column align-items-center justify-content-center h-100 text-muted opacity-50">
                                 <FaGlobe size={48} className="mb-3"/>
                                 <div>Enter the URL and click Send to get a response</div>
                             </div>
                        )}
                    </div>
                 )}
                 
                 {responseTab === 'headers' && (
                   <div className="h-100 overflow-auto">
                       {Object.keys(responseHeaders).length > 0 ? (
                           <table className="table table-sm table-hover mb-0 small">
                               <thead className="bg-light sticky-top"><tr><th className="ps-3 border-0">Key</th><th className="border-0">Value</th></tr></thead>
                               <tbody>
                                   {Object.entries(responseHeaders).map(([k, v]) => (
                                       <tr key={k}>
                                           <td className="ps-3 fw-bold text-secondary">{k}</td>
                                           <td className="font-monospace text-break text-dark">{String(v)}</td>
                                       </tr>
                                   ))}
                               </tbody>
                           </table>
                       ) : (
                           <div className="d-flex align-items-center justify-content-center h-100 text-muted small">No headers</div>
                       )}
                   </div>
                 )}

                 {responseTab === 'report' && (
                     testResult?.structured_report ? (
                        renderDashboard(testResult.structured_report)
                     ) : (
                        <div className="d-flex flex-column align-items-center justify-content-center h-100 text-muted opacity-50">
                            <FaRobot size={48} className="mb-3"/>
                            <div>Run an AI test generation to see the report</div>
                        </div>
                     )
                 )}
                 
                 {responseTab === 'test_results' && (
                     <div className="d-flex align-items-center justify-content-center h-100 text-muted opacity-50">
                         <div>No standard tests run yet</div>
                     </div>
                 )}
                 
                 {responseTab === 'cookies' && (
                     <div className="d-flex align-items-center justify-content-center h-100 text-muted opacity-50">
                         <div>No cookies</div>
                     </div>
                 )}
             </div>
        </div>
      </div>
      
      <Modal show={showSaveModal} onHide={() => setShowSaveModal(false)} centered>
        <Modal.Header closeButton>
            <Modal.Title>Save Request</Modal.Title>
        </Modal.Header>
        <Modal.Body>
            <Form>
                <Form.Group className="mb-3">
                    <Form.Label>Request Name</Form.Label>
                    <Form.Control 
                        type="text" 
                        value={saveForm.name} 
                        onChange={e => setSaveForm({...saveForm, name: e.target.value})}
                        autoFocus
                    />
                </Form.Group>
                <Form.Group className="mb-3">
                    <Form.Label>Description</Form.Label>
                    <Form.Control 
                        as="textarea" 
                        rows={3} 
                        value={saveForm.description} 
                        onChange={e => setSaveForm({...saveForm, description: e.target.value})}
                    />
                </Form.Group>
                <Form.Group className="mb-3">
                    <Form.Label>Save to Folder</Form.Label>
                    <Form.Select 
                        value={saveForm.parentId || ''} 
                        onChange={e => setSaveForm({...saveForm, parentId: e.target.value ? Number(e.target.value) : null})}
                    >
                        <option value="">(Root)</option>
                        {renderFolderOptions(null)}
                    </Form.Select>
                </Form.Group>
            </Form>
        </Modal.Body>
        <Modal.Footer>
            <Button variant="secondary" onClick={() => setShowSaveModal(false)}>Cancel</Button>
            <Button variant="primary" onClick={handleConfirmSave}>Save</Button>
         </Modal.Footer>
       </Modal>

       {/* Env Manager Modal */}
       <Modal show={showEnvModal} onHide={() => setShowEnvModal(false)} size="lg" centered>
         <Modal.Header closeButton>
             <Modal.Title>环境管理</Modal.Title>
         </Modal.Header>
         <Modal.Body style={{minHeight: '400px'}}>
             {editingEnv ? (
                 <div className="d-flex flex-column h-100">
                     <div className="d-flex justify-content-between align-items-center mb-3">
                        <Button variant="link" className="p-0 text-decoration-none" onClick={() => setEditingEnv(null)}>
                            <FaChevronLeft className="me-1"/> Back to List
                        </Button>
                        <h5 className="mb-0 text-primary">{editingEnv.id === 'new' ? 'New Environment' : 'Edit Environment'}</h5>
                     </div>
                     
                     <Form.Group className="mb-3">
                        <Form.Label>Environment Name</Form.Label>
                        <Form.Control value={editingEnv.name} onChange={e => setEditingEnv({...editingEnv, name: e.target.value})} placeholder="e.g. Production"/>
                     </Form.Group>
                     <Form.Group className="mb-3">
                        <Form.Label>Base URL</Form.Label>
                        <Form.Control value={editingEnv.baseUrl} onChange={e => setEditingEnv({...editingEnv, baseUrl: e.target.value})} placeholder="https://api.example.com"/>
                     </Form.Group>
                     
                     <div className="flex-grow-1 d-flex flex-column">
                        <div className="d-flex justify-content-between align-items-center mb-2">
                            <label className="form-label mb-0">Variables</label>
                            <Button size="sm" variant="outline-secondary" onClick={() => {
                                const newVars = [...(editingEnv.variables || []), {key: '', value: '', enabled: true}];
                                setEditingEnv({...editingEnv, variables: newVars});
                            }}>
                                <FaPlus size={12}/> Add Var
                            </Button>
                        </div>
                        <div className="border rounded flex-grow-1 overflow-auto bg-light p-2" style={{maxHeight: '300px'}}>
                            {(!editingEnv.variables || editingEnv.variables.length === 0) && <div className="text-center text-muted small mt-4">No variables defined.</div>}
                            {editingEnv.variables?.map((v, idx) => (
                                <div key={idx} className="d-flex gap-2 mb-2 align-items-center">
                                    <Form.Check 
                                        checked={v.enabled} 
                                        onChange={e => {
                                            const newVars = [...editingEnv.variables!];
                                            newVars[idx].enabled = e.target.checked;
                                            setEditingEnv({...editingEnv, variables: newVars});
                                        }}
                                    />
                                    <Form.Control size="sm" placeholder="Variable" value={v.key} onChange={e => {
                                        const newVars = [...editingEnv.variables!];
                                        newVars[idx].key = e.target.value;
                                        setEditingEnv({...editingEnv, variables: newVars});
                                    }}/>
                                    <Form.Control size="sm" placeholder="Value" value={v.value} onChange={e => {
                                        const newVars = [...editingEnv.variables!];
                                        newVars[idx].value = e.target.value;
                                        setEditingEnv({...editingEnv, variables: newVars});
                                    }}/>
                                    <Button variant="link" className="text-danger p-0" onClick={() => {
                                        const newVars = editingEnv.variables!.filter((_, i) => i !== idx);
                                        setEditingEnv({...editingEnv, variables: newVars});
                                    }}><FaTimes/></Button>
                                </div>
                            ))}
                        </div>
                     </div>
                 </div>
             ) : (
                 <div className="d-flex flex-column h-100">
                     <div className="text-end mb-3">
                         <Button variant="success" size="sm" onClick={() => setEditingEnv({id: Date.now().toString(), name: 'New Env', baseUrl: '', variables: []})}>
                             <FaPlus className="me-1"/> Create New
                         </Button>
                     </div>
                     <ListGroup variant="flush">
                        {savedEnvs.length === 0 && <div className="text-center text-muted my-5">No environments found.</div>}
                        {savedEnvs.map(env => (
                            <ListGroup.Item key={env.id} className="d-flex justify-content-between align-items-center">
                                <div>
                                    <div className="fw-bold">{env.name}</div>
                                    <div className="small text-muted font-monospace">{env.baseUrl}</div>
                                    <div className="small text-secondary">{env.variables?.length || 0} variables</div>
                                </div>
                                <div className="d-flex gap-2">
                                    <Button variant="outline-primary" size="sm" onClick={() => setEditingEnv(env)}><FaEdit/></Button>
                                    <Button variant="outline-danger" size="sm" onClick={() => handleDeleteEnv(env.id)}><FaTrash/></Button>
                                </div>
                            </ListGroup.Item>
                        ))}
                     </ListGroup>
                 </div>
             )}
         </Modal.Body>
         {editingEnv && (
             <Modal.Footer>
                 <Button variant="secondary" onClick={() => setEditingEnv(null)}>Cancel</Button>
                 <Button variant="primary" onClick={() => handleUpdateEnv(editingEnv)}>Save Environment</Button>
             </Modal.Footer>
         )}
       </Modal>
    </div>
  );
}
