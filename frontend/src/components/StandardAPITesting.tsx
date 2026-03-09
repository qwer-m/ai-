import { useState, useEffect, useRef } from 'react';
import { Button, Form, Spinner, Nav, ListGroup, Dropdown } from 'react-bootstrap';
import { FaPlus, FaMinus, FaTrash, FaSave, FaBars, FaFolderPlus, FaRobot, FaChevronDown, FaCog, FaCookie, FaFile } from 'react-icons/fa';
import { api } from '../utils/api';
import { highlightJson } from './standard-api-testing/jsonHighlight';
import { SaveRequestModal } from './standard-api-testing/SaveRequestModal';
import { CookieManagerModal } from './standard-api-testing/CookieManagerModal';
import { EnvManagerModal } from './standard-api-testing/EnvManagerModal';
import { ResponsePanel } from './standard-api-testing/ResponsePanel';
import { StructuredReportDashboard } from './standard-api-testing/StructuredReportDashboard';
import { InterfaceTree } from './standard-api-testing/InterfaceTree';
import { useInterfaceEditor } from './standard-api-testing/useInterfaceEditor';
import { useInterfaceCrud } from './standard-api-testing/useInterfaceCrud';
import {
  buildPostmanFolderItems,
  importFilesFromCollections,
  importInterfaceItemsToBackend,
} from './standard-api-testing/importExport';
import {
  computeDragOverPosition,
  planInterfaceDrop,
  type DragOverPosition,
} from './standard-api-testing/dragTree';
import {
  buildDebugRequestPayload,
  createRuntimeContext,
  executePostResponseScript,
  executePreRequestScript,
  resolveActiveEnv,
  type ScriptTest,
} from './standard-api-testing/requestExecution';
import type { EnvConfig, ResponseTab, SavedInterface, StandardAPITestingProps, TestResult } from './standard-api-testing/types';

export function StandardAPITesting({ projectId, onLog }: StandardAPITestingProps) {
  const [mode, setMode] = useState<'natural' | 'structured'>('natural');
  const [requirement, setRequirement] = useState('');
  // 合并后的 URL 状态：apiPath 现在保存完整 URL
  const [apiPath, setApiPath] = useState('');
  const [method, setMethod] = useState('POST');
  const [testTypes, setTestTypes] = useState({
      functional: true,
      boundary: false,
      security: false
  });
  
  const [loading, setLoading] = useState(false);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  
  // 交互式响应状态
  const [responseStatus, setResponseStatus] = useState<number | null>(null);
  const [responseTime, setResponseTime] = useState<number | null>(null);
  const [responseBody, setResponseBody] = useState<string | null>(null);
  const [responseHeaders, setResponseHeaders] = useState<any>({});
  const [responseCookies, setResponseCookies] = useState<any>({});
  const [responseDetailedCookies, setResponseDetailedCookies] = useState<any>({});
  const [sentHeaders, setSentHeaders] = useState<any>({});
  const [sentCookies, setSentCookies] = useState<any>({});
  const [sentBody, setSentBody] = useState<string | null>(null);
  const [scriptTests, setScriptTests] = useState<ScriptTest[]>([]); // Postman 风格测试
  const [responseViewMode, setResponseViewMode] = useState<'json' | 'html' | 'headers'>('json');
  const [responseFormat, setResponseFormat] = useState<'JSON' | 'XML' | 'HTML' | 'JavaScript' | 'Raw' | 'Hex' | 'Base64'>('JSON');
  const [aiAnalysis, setAiAnalysis] = useState<string | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);

  // 环境管理状态
  const [showEnvModal, setShowEnvModal] = useState(false);
    const [showCookieModal, setShowCookieModal] = useState(false);
    const [cookieJar, setCookieJar] = useState<Record<string, string>>({}); // 自动管理的 Cookies
  const [editingEnv, setEditingEnv] = useState<EnvConfig | null>(null);

  // 重命名状态
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renamingName, setRenamingName] = useState('');
  
  // 拖拽状态
  const [isDragOver, setIsDragOver] = useState(false);

  // 中文注释：错误管理前置处理与统一中文翻译
  const getErrorText = (error: any) => {
    if (!error) return '';
    if (typeof error === 'string') return error;
    if (error?.data?.error) return String(error.data.error);
    if (error?.data?.detail) return String(error.data.detail);
    if (error?.data?.message) return String(error.data.message);
    if (error?.message) return String(error.message);
    try {
      return JSON.stringify(error);
    } catch {
      return String(error);
    }
  };

  const translateError = async (error: any) => {
    const raw = getErrorText(error);
    try {
      const res = await api.post<any>('/api/error/translate', { error: raw });
      return res?.message ? String(res.message) : raw;
    } catch {
      return raw;
    }
  };

  // 核心请求执行器：
  // 1) 解析环境变量与脚本
  // 2) 组装请求（鉴权、参数、Body、Cookie、超时等）
  // 3) 写入响应状态并执行后置脚本断言
  const handleSendRequest = async () => {
    setLoading(true);
    setScriptTests([]); // 清空上一次脚本断言结果
    setAiAnalysis(null); // 清空上一次 AI 分析结果
    try {
      const activeEnv = resolveActiveEnv(savedEnvs, apiPath);
      const runtimeContext = createRuntimeContext(activeEnv, getEnvBaseUrlValue);
      const { envInterface, substitute, getRuntimeVariables } = runtimeContext;

      if (preRequestScript && preRequestScript.trim()) {
        try {
          executePreRequestScript(preRequestScript, envInterface);
        } catch (error) {
          const msg = await translateError(error);
          onLog(`前置脚本错误: ${msg}`);
        }
      }

      const { fullUrl, reqHeaders, reqParams, finalBody, isBase64 } = buildDebugRequestPayload({
        apiPath,
        headers,
        queryParams,
        authType,
        authToken,
        authBasic,
        authApiKey,
        bodyMode,
        bodyContent,
        xWwwFormUrlencodedParams,
        formDataParams,
        binaryFile,
        graphqlQuery,
        graphqlVariables,
        substitute,
      });

      const res = await api.post<any>('/api/debug/request', {
        method,
        url: fullUrl,
        headers: reqHeaders,
        params: reqParams,
        cookies: cookieJar,
        body: finalBody,
        is_base64_body: isBase64,
        timeout: requestSettings.timeout / 1000,
        verify_ssl: requestSettings.verifySSL,
        follow_redirects: requestSettings.followRedirects,
        max_redirects: requestSettings.maxRedirects,
        http_version: requestSettings.httpVersion,
      });

      setResponseStatus(res.status);
      setResponseTime(res.time);
      setResponseBody(res.body);
      setResponseHeaders(res.headers);
      setSentHeaders(reqHeaders);
      setSentCookies(cookieJar);
      setSentBody(finalBody || null);
      setResponseCookies(res.cookies || {});
      if (res.detailed_cookies) {
        setResponseDetailedCookies(res.detailed_cookies);
      } else {
        setResponseDetailedCookies({});
      }

      if (res.cookies) {
        setCookieJar((prev) => ({ ...prev, ...res.cookies }));
      }

      if (postResponseScript && postResponseScript.trim()) {
        try {
          const tests = executePostResponseScript({
            script: postResponseScript,
            envInterface,
            response: {
              body: res.body,
              headers: res.headers || {},
              status: res.status,
              time: res.time,
            },
          });
          setScriptTests(tests);
          if (tests.length > 0) {
            setResponseTab('test_results');
          }
        } catch (error) {
          const msg = await translateError(error);
          onLog(`后置脚本错误: ${msg}`);
        }
      }

      if (activeEnv) {
        setSavedEnvs((prev) =>
          prev.map((item) =>
            item.id === activeEnv.id ? { ...item, variables: getRuntimeVariables() } : item,
          ),
        );
      }

      onLog(`请求成功: ${res.status} (${res.time}s)`);
    } catch (error) {
      const msg = await translateError(error);
      onLog(`请求失败: ${msg}`);
      setResponseStatus(0);
      setResponseBody(msg);
    } finally {
      setLoading(false);
    }
  };

  const [runSubTab, setRunSubTab] = useState('params'); 
  const [responseTab, setResponseTab] = useState<ResponseTab>('report');

  // 请求体状态
  const [bodyMode, setBodyMode] = useState<'none' | 'form-data' | 'x-www-form-urlencoded' | 'raw' | 'binary' | 'graphql'>('raw');
  const [rawType, setRawType] = useState<'Text' | 'JavaScript' | 'JSON' | 'HTML' | 'XML'>('JSON');
  const [formDataParams, setFormDataParams] = useState<{key:string, value:string, desc:string, type:'text'|'file', src?: string}[]>([{key:'', value:'', desc:'', type:'text'}]);
  const [xWwwFormUrlencodedParams, setXWwwFormUrlencodedParams] = useState<{key:string, value:string, desc:string}[]>([{key:'', value:'', desc:''}]);
  const [binaryFile, setBinaryFile] = useState<{name: string, data: string} | null>(null);
  const [graphqlQuery, setGraphqlQuery] = useState('');
  const [graphqlVariables, setGraphqlVariables] = useState('');
  
  // 请求配置状态
    const [queryParams, setQueryParams] = useState<{key:string, value:string, desc:string}[]>([{key:'', value:'', desc:''}]);
    
    // 认证状态
    const [authType, setAuthType] = useState<'none' | 'bearer' | 'basic' | 'apikey'>('none');
    const [authToken, setAuthToken] = useState('');
    const [authBasic, setAuthBasic] = useState({username: '', password: ''});
    const [authApiKey, setAuthApiKey] = useState({key: '', value: '', addTo: 'header' as 'header'|'query'});

    // 脚本状态
    const [activeScriptTab, setActiveScriptTab] = useState<'pre' | 'post'>('pre');
    const [preRequestScript, setPreRequestScript] = useState('');
    const [postResponseScript, setPostResponseScript] = useState('');

    // 请求设置状态
    const [requestSettings, setRequestSettings] = useState({
        timeout: 0, // 毫秒，0 表示不限时
        followRedirects: true,
        verifySSL: false,
        httpVersion: 'HTTP/1.x',
        followOriginalHttpMethod: false,
        followAuthorizationHeader: false,
        removeRefererHeader: false,
        strictHttpParser: false,
        encodeUrl: true,
        disableCookieJar: false,
        useServerCipherSuite: false,
        maxRedirects: 10,
        disabledSSLProtocols: '',
        cipherSuites: ''
    });
  
  // 根据 Body 模式自动管理 Content-Type
    useEffect(() => {
        const updateHeaders = () => {
            let targetType = '';
            if (bodyMode === 'raw') {
                if (rawType === 'JSON') targetType = 'application/json';
                else if (rawType === 'HTML') targetType = 'text/html';
                else if (rawType === 'XML') targetType = 'application/xml';
                else if (rawType === 'JavaScript') targetType = 'application/javascript';
                else if (rawType === 'Text') targetType = 'text/plain';
            } else if (bodyMode === 'x-www-form-urlencoded') {
                targetType = 'application/x-www-form-urlencoded';
            }

            if (!targetType) return;

            setHeaders(prev => {
                const newHeaders = [...prev];
                const index = newHeaders.findIndex(h => h.key.toLowerCase() === 'content-type');
                
                if (index >= 0) {
                    // 仅更新“自动生成”的 Content-Type，避免覆盖用户手工输入。
                    if (newHeaders[index].value !== targetType && newHeaders[index].desc === 'Auto-generated') {
                        newHeaders[index] = { ...newHeaders[index], value: targetType };
                        return newHeaders;
                    }
                    // 用户手动设置时不覆盖，尊重用户配置。
                    return prev;
                } else {
                    // 若不存在 Content-Type，则补充一条自动生成项
                    const emptyIndex = newHeaders.findIndex(h => !h.key && !h.value);
                    if (emptyIndex !== -1 && newHeaders.length === 1) {
                        newHeaders[emptyIndex] = { key: 'Content-Type', value: targetType, desc: 'Auto-generated' };
                    } else {
                        newHeaders.push({ key: 'Content-Type', value: targetType, desc: 'Auto-generated' });
                    }
                    return newHeaders;
                }
            });
        };
        updateHeaders();
    }, [bodyMode, rawType]);

    // 请求区高度拖拽状态
  const [requestHeight, setRequestHeight] = useState(300);
  const [isDragging, setIsDragging] = useState(false);
  const mainContentRef = useRef<HTMLDivElement>(null);

  const handleMouseDown = (e: React.MouseEvent) => {
      setIsDragging(true);
      e.preventDefault();
  };

  useEffect(() => {
       // 修复：使用全局事件监听提升拖动条灵敏度，避免鼠标移出组件后拖动失效
       const handleGlobalMouseMove = (e: globalThis.MouseEvent) => {
          if (!isDragging || !mainContentRef.current) return;
          
          const containerRect = mainContentRef.current.getBoundingClientRect();
          const headerOffset = 95; // 请求栏 + 标签栏的近似高度
          const resizerHeight = 8;
          const minResponseHeight = 200;
          
          const newHeight = e.clientY - containerRect.top - headerOffset;
          const maxHeight = containerRect.height - headerOffset - resizerHeight - minResponseHeight;
          
          if (newHeight > 100 && newHeight < maxHeight) {
              setRequestHeight(newHeight);
          }
      };

      const handleGlobalMouseUp = () => {
          setIsDragging(false);
      };

      if (isDragging) {
          document.addEventListener('mousemove', handleGlobalMouseMove);
          document.addEventListener('mouseup', handleGlobalMouseUp);
      }

      return () => {
          document.removeEventListener('mousemove', handleGlobalMouseMove);
          document.removeEventListener('mouseup', handleGlobalMouseUp);
      };
  }, [isDragging]);
  const [headers, setHeaders] = useState<{key:string, value:string, desc:string}[]>([{key:'', value:'', desc:''}]);
  const [bodyContent, setBodyContent] = useState('');
  const [showSidebar, setShowSidebar] = useState(true);
  // 侧边栏：支持拖拽调宽，避免内容溢出导致的横向滚动条
  const [sidebarWidth, setSidebarWidth] = useState(260);
  const [isResizingSidebar, setIsResizingSidebar] = useState(false);
  const sidebarResizeStartRef = useRef<{ x: number; width: number } | null>(null);
  // 文件夹导入：通过隐藏 input 触发选择文件，并将目标文件夹 id 暂存
  const folderImportInputRef = useRef<HTMLInputElement>(null);
  const [folderImportTargetId, setFolderImportTargetId] = useState<number | null>(null);

  useEffect(() => {
      // 侧边栏拖拽调宽：使用全局事件，避免鼠标移出后拖动失效
      const handleGlobalMouseMove = (e: globalThis.MouseEvent) => {
          if (!isResizingSidebar || !sidebarResizeStartRef.current) return;
          const dx = e.clientX - sidebarResizeStartRef.current.x;
          const next = Math.max(220, Math.min(560, sidebarResizeStartRef.current.width + dx));
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

    // 已保存环境状态
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

    // URL 中 {{变量}} 检测状态
    const [activeEnvTag, setActiveEnvTag] = useState<string | null>(null);
    const inputRef = useRef<HTMLInputElement>(null);
    const highlighterRef = useRef<HTMLDivElement>(null);
    const bodyHighlighterRef = useRef<HTMLDivElement>(null);
    
    const handleBodyScroll = (e: React.UIEvent<HTMLTextAreaElement>) => {
        if (bodyHighlighterRef.current) {
            bodyHighlighterRef.current.scrollTop = e.currentTarget.scrollTop;
            bodyHighlighterRef.current.scrollLeft = e.currentTarget.scrollLeft;
        }
    };

    const popupTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
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
        
        // 计算 {{变量}} 在输入框中的精确高亮区间
        const match = apiPath.match(/^([\s\S]*?)(\{\{\s*(.*?)\s*\}\})/);
        let startX = borderLeft + paddingLeft - scrollLeft;
        let endX = startX;

        if (match && match[2] === activeEnvTag) {
             const prefixWidth = context.measureText(match[1]).width;
             const tagWidth = context.measureText(activeEnvTag).width;
             startX += prefixWidth;
             endX = startX + tagWidth;
        } else {
             // 正则未命中时的兜底分支（理论上很少进入）
             const tagWidth = context.measureText(activeEnvTag).width;
             endX = startX + tagWidth;
        }
        
        if (mouseX >= startX && mouseX <= endX) { // 精确命中，不再额外扩展缓冲区
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
              // 创建新环境
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

  // 持久化环境配置到 localStorage
    useEffect(() => {
        try {
            localStorage.setItem('api_testing_saved_envs_v1', JSON.stringify(savedEnvs));
        } catch (e) {
            console.error('Failed to save envs:', e);
        }
    }, [savedEnvs]);

    const handleSaveEnv = () => {
        setShowEnvModal(true);
        setEditingEnv(null); // 默认回到环境列表页
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

    const renderKvEditor = (
        items: {key:string, value:string, desc:string}[], 
        onChange: (items: any[]) => void,
        isBulk: boolean,
        onToggleBulk: () => void,
        bulkText: string,
        onBulkChange: (val: string) => void
    ) => {
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

        if (isBulk) {
            return (
                <div className="w-100 h-100 d-flex flex-column">
                    <div className="d-flex justify-content-end bg-light border-bottom px-2 py-1">
                        <Button variant="link" size="sm" className="text-decoration-none" onClick={onToggleBulk}>Key-Value Edit</Button>
                    </div>
                    <Form.Control 
                        as="textarea" 
                        className="flex-grow-1 border-0 p-3 font-monospace small bg-transparent"
                        style={{resize: 'none', outline: 'none'}}
                        value={bulkText}
                        onChange={e => onBulkChange(e.target.value)}
                        placeholder="key:value"
                        spellCheck={false}
                    />
                </div>
            );
        }

        return (
            <div className="w-100 overflow-hidden d-flex flex-column">
                <div className="d-flex justify-content-end bg-light border-bottom px-2 py-1">
                    <Button variant="link" size="sm" className="text-decoration-none" onClick={onToggleBulk}>Bulk Edit</Button>
                </div>
                <table className="table table-sm table-bordered border-start-0 border-end-0 mb-0 align-middle small w-100" style={{tableLayout: 'fixed'}}>
                    <thead className="text-secondary bg-light">
                        <tr>
                            <th style={{width: '30%', fontWeight: '500'}} className="ps-3 border-start-0">键 (Key)</th>
                            <th style={{width: '30%', fontWeight: '500'}} className="ps-2">值 (Value)</th>
                            <th style={{width: '40%', fontWeight: '500'}} className="ps-2 border-end-0">描述 (Description)</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items.map((item, idx) => (
                            <tr key={idx} className="border-bottom">
                                <td className="ps-3 border-start-0"><Form.Control size="sm" placeholder="Key" value={item.key} onChange={e => handleChange(idx, 'key', e.target.value)} className="border-0 shadow-none bg-transparent px-0" /></td>
                                <td className="ps-2"><Form.Control size="sm" placeholder="Value" value={item.value} onChange={e => handleChange(idx, 'value', e.target.value)} className="border-0 shadow-none bg-transparent px-0" /></td>
                                <td className="ps-2 position-relative border-end-0">
                                    <Form.Control size="sm" placeholder="Description" value={item.desc} onChange={e => handleChange(idx, 'desc', e.target.value)} className="border-0 shadow-none bg-transparent px-0" style={{paddingRight: '24px'}} />
                                    {items.length > 1 && (
                                        <Button 
                                            variant="link" 
                                            className="position-absolute top-50 end-0 translate-middle-y text-muted p-0 pe-2 opacity-50 hover-opacity-100" 
                                            style={{zIndex: 5}}
                                            onClick={() => handleDelete(idx)}
                                        >
                                            <FaTrash size={12}/>
                                        </Button>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        );
    };

    const renderFormDataEditor = (
        items: {key:string, value:string, desc:string, type: 'text'|'file', src?: string}[], 
        onChange: (items: any[]) => void,
        isBulk: boolean,
        onToggleBulk: () => void,
        bulkText: string,
        onBulkChange: (val: string) => void
    ) => {
        const handleChange = (index: number, field: string, val: string) => {
            const newItems = [...items];
            // @ts-ignore
            newItems[index] = { ...newItems[index], [field]: val };
            if (index === items.length - 1 && (newItems[index].key || newItems[index].value)) {
                newItems.push({ key: '', value: '', desc: '', type: 'text' });
            }
            onChange(newItems);
        };
        
        const handleFileChange = (index: number, file: File | null) => {
            const newItems = [...items];
            if (file) {
                newItems[index] = { ...newItems[index], src: file.name };
                // 以 Data URL（Base64）方式读取文件内容
                const reader = new FileReader();
                reader.onload = (e) => {
                    const res = e.target?.result as string;
                    newItems[index].value = res;
                    onChange(newItems);
                };
                reader.readAsDataURL(file);
            } else {
                newItems[index] = { ...newItems[index], src: '', value: '' };
                onChange(newItems);
            }
        };

        const handleDelete = (index: number) => {
            if (items.length <= 1) {
                onChange([{ key: '', value: '', desc: '', type: 'text' }]);
                return;
            }
            const newItems = items.filter((_, i) => i !== index);
            onChange(newItems);
        };

        if (isBulk) {
            return (
                <div className="w-100 h-100 d-flex flex-column">
                    <div className="d-flex justify-content-end bg-light border-bottom px-2 py-1">
                        <Button variant="link" size="sm" className="text-decoration-none" onClick={onToggleBulk}>Key-Value Edit</Button>
                    </div>
                    <Form.Control 
                        as="textarea" 
                        className="flex-grow-1 border-0 p-3 font-monospace small bg-transparent"
                        style={{resize: 'none', outline: 'none'}}
                        value={bulkText}
                        onChange={e => onBulkChange(e.target.value)}
                        placeholder="key:value"
                        spellCheck={false}
                    />
                </div>
            );
        }

        return (
            <div className="w-100 overflow-hidden">
                <div className="d-flex justify-content-end bg-light border-bottom px-2 py-1">
                    <Button variant="link" size="sm" className="text-decoration-none" onClick={onToggleBulk}>Bulk Edit</Button>
                </div>
                <table className="table table-sm table-bordered border-start-0 border-end-0 mb-0 align-middle small w-100" style={{tableLayout: 'fixed'}}>
                    <thead className="text-secondary bg-light">
                        <tr>
                            <th style={{width: '25%', fontWeight: '500'}} className="ps-3 border-start-0">键 (Key)</th>
                            <th style={{width: '10%', fontWeight: '500'}} className="ps-2">类型</th>
                            <th style={{width: '30%', fontWeight: '500'}} className="ps-2">值 (Value)</th>
                            <th style={{width: '35%', fontWeight: '500'}} className="ps-2 border-end-0">描述 (Description)</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items.map((item, idx) => (
                            <tr key={idx} className="border-bottom">
                                <td className="ps-3 border-start-0">
                                    <Form.Control size="sm" placeholder="Key" value={item.key} onChange={e => handleChange(idx, 'key', e.target.value)} className="border-0 shadow-none bg-transparent px-0" />
                                </td>
                                <td className="ps-2">
                                    <Form.Select size="sm" value={item.type} onChange={e => handleChange(idx, 'type', e.target.value)} className="border-0 shadow-none bg-transparent px-0 text-secondary" style={{fontSize: '0.875rem'}}>
                                        <option value="text">Text</option>
                                        <option value="file">File</option>
                                    </Form.Select>
                                </td>
                                <td className="ps-2">
                                    {item.type === 'file' ? (
                                        <div className="d-flex align-items-center">
                                            <Form.Control 
                                                type="file" 
                                                size="sm" 
                                                className="border-0 shadow-none bg-transparent px-0" 
                                                onChange={(e) => {
                                                    const target = e.target as HTMLInputElement;
                                                    handleFileChange(idx, target.files?.[0] || null);
                                                }}
                                            />
                                            {item.src && <span className="small text-muted ms-2 text-truncate" style={{maxWidth: '100px'}} title={item.src}>{item.src}</span>}
                                        </div>
                                    ) : (
                                        <Form.Control size="sm" placeholder="Value" value={item.value} onChange={e => handleChange(idx, 'value', e.target.value)} className="border-0 shadow-none bg-transparent px-0" />
                                    )}
                                </td>
                                <td className="ps-2 position-relative border-end-0">
                                    <Form.Control size="sm" placeholder="Description" value={item.desc} onChange={e => handleChange(idx, 'desc', e.target.value)} className="border-0 shadow-none bg-transparent px-0" style={{paddingRight: '24px'}} />
                                    {items.length > 1 && (
                                        <Button 
                                            variant="link" 
                                            className="position-absolute top-50 end-0 translate-middle-y text-muted p-0 pe-2 opacity-50 hover-opacity-100" 
                                            style={{zIndex: 5}}
                                            onClick={() => handleDelete(idx)}
                                        >
                                            <FaTrash size={12}/>
                                        </Button>
                                    )}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        );
    };

    // 已保存接口树状态
  const [savedInterfaces, setSavedInterfaces] = useState<SavedInterface[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [draggedId, setDraggedId] = useState<number | null>(null);
  const [dragOverId, setDragOverId] = useState<number | null>(null); // 当前悬停的文件夹 ID
  const [dragOverPosition, setDragOverPosition] = useState<DragOverPosition | null>(null);
  const [hoverId, setHoverId] = useState<number | null>(null);
  const [bulkDeleteMode, setBulkDeleteMode] = useState(false);
  const [bulkSelected, setBulkSelected] = useState<Record<number, boolean>>({});

  // 批量编辑状态
  const [isBulkEditParams, setIsBulkEditParams] = useState(false);
  const [paramsBulkText, setParamsBulkText] = useState('');
  
  const [isBulkEditHeaders, setIsBulkEditHeaders] = useState(false);
  const [headersBulkText, setHeadersBulkText] = useState('');
  
  const [isBulkEditBody, setIsBulkEditBody] = useState(false);
  const [bodyBulkText, setBodyBulkText] = useState('');

  const [isBulkEditFormData, setIsBulkEditFormData] = useState(false);
  const [formDataBulkText, setFormDataBulkText] = useState('');

  const parseBulkText = (text: string) => {
      return text.split('\n').map(line => {
          const index = line.indexOf(':');
          if (index === -1) return { key: line.trim(), value: '', desc: '' };
          return { key: line.substring(0, index).trim(), value: line.substring(index + 1).trim(), desc: '' };
      }).filter(i => i.key || i.value);
  };

  const stringifyBulkItems = (items: any[]) => {
      return items.filter(i => i.key || i.value).map(i => `${i.key}:${i.value}`).join('\n');
  };

    const handleFormDataBulkChange = (val: string) => {
        setFormDataBulkText(val);
        const parsed = parseBulkText(val);
        // 将批量文本映射为 form-data 结构，默认按文本字段处理
        const newItems = parsed.map(p => ({
            ...p,
            type: 'text' as const,
            src: ''
        }));
        if (newItems.length === 0) newItems.push({key:'', value:'', desc:'', type:'text', src:''});
        setFormDataParams(newItems);
    };

    const toggleFormDataBulk = () => {
        if (isBulkEditFormData) {
            setIsBulkEditFormData(false);
        } else {
            setFormDataBulkText(stringifyBulkItems(formDataParams));
            setIsBulkEditFormData(true);
        }
    };

  // CRUD Hook：统一管理接口树的读取、创建和更新逻辑。
  const {
      fetchInterfaces,
      createFolder,
      createInterface,
      updateInterface,
  } = useInterfaceCrud({
      projectId,
      selectedId,
      savedInterfaces,
      setSavedInterfaces,
      apiPath,
      method,
      queryParams,
      headers,
      bodyMode,
      rawType,
      bodyContent,
      translateError,
  });

  const {
      showSaveModal,
      setShowSaveModal,
      saveForm,
      setSaveForm,
      handleEditFolder,
      handleSaveInterfaceClick,
      handleConfirmSave,
      handleLoadInterface,
      renderFolderOptions,
  } = useInterfaceEditor({
      projectId,
      savedInterfaces,
      setSavedInterfaces,
      selectedId,
      setSelectedId,
      apiPath,
      setApiPath,
      method,
      setMethod,
      requirement,
      setRequirement,
      mode,
      setMode,
      testTypes,
      setTestTypes,
      headers,
      setHeaders,
      queryParams,
      setQueryParams,
      bodyMode,
      setBodyMode,
      rawType,
      setRawType,
      bodyContent,
      setBodyContent,
      preRequestScript,
      setPreRequestScript,
      postResponseScript,
      setPostResponseScript,
      setResponseStatus,
      setResponseTime,
      setResponseBody,
      setResponseHeaders,
      setResponseCookies,
      setTestResult,
      updateInterface,
      fetchInterfaces,
      translateError,
      onLog,
  });

  useEffect(() => {
      fetchInterfaces();
  }, [fetchInterfaces]);

  const handleCreateFolder = async (parentId: number | null = null) => {
      await createFolder(parentId);
  };

  // 新建接口后，延续原行为：自动加载到编辑区。
  const handleCreateInterface = async (targetParentId?: number | null) => {
      const created = await createInterface(targetParentId);
      if (created) handleLoadInterface(created);
  };

  // 自动管理 Content-Type（与 Postman 一致：仅在 Body 有内容时生成/更新）
    useEffect(() => {
        const updateHeaders = () => {
            let hasBodyContent = false;
            let targetType = '';

            if (bodyMode === 'raw') {
                hasBodyContent = !!(bodyContent && bodyContent.trim());
                if (rawType === 'JSON') targetType = 'application/json';
                else if (rawType === 'HTML') targetType = 'text/html';
                else if (rawType === 'XML') targetType = 'application/xml';
                else if (rawType === 'JavaScript') targetType = 'application/javascript';
                else if (rawType === 'Text') targetType = 'text/plain';
            } else if (bodyMode === 'x-www-form-urlencoded') {
                hasBodyContent = xWwwFormUrlencodedParams.some(p => p.key || p.value);
                targetType = 'application/x-www-form-urlencoded';
            } else if (bodyMode === 'form-data') {
                hasBodyContent = formDataParams.some(p => p.key || p.value);
                // form-data 的 boundary 由客户端自动生成。
                // 手工写死 Content-Type 往往会导致 boundary 不匹配，所以这里不自动写入。
                targetType = ''; 
            }

            // 与 Postman 对齐：Body 为空时不自动生成 Content-Type
            if (!hasBodyContent) targetType = '';

            setHeaders(prev => {
                const newHeaders = [...prev];
                const index = newHeaders.findIndex(h => h.key.toLowerCase() === 'content-type');
                
                if (targetType) {
                    // 更新已有自动头，或补充新的自动头
                    if (index >= 0) {
                        if (newHeaders[index].value !== targetType && newHeaders[index].desc === 'Auto-generated') {
                            newHeaders[index] = { ...newHeaders[index], value: targetType };
                            return newHeaders;
                        }
                        return prev;
                    } else {
                        // 新增自动生成的 Content-Type
                        const emptyIndex = newHeaders.findIndex(h => !h.key && !h.value);
                        if (emptyIndex !== -1 && newHeaders.length === 1) {
                            newHeaders[emptyIndex] = { key: 'Content-Type', value: targetType, desc: 'Auto-generated' };
                        } else {
                            newHeaders.push({ key: 'Content-Type', value: targetType, desc: 'Auto-generated' });
                        }
                        return newHeaders;
                    }
                } else {
                    // 若请求体为空，移除自动生成的 Content-Type
                    if (index >= 0 && newHeaders[index].desc === 'Auto-generated') {
                        newHeaders.splice(index, 1);
                        // 保证编辑器中至少保留一行空项
                        if (newHeaders.length === 0) {
                            newHeaders.push({ key: '', value: '', desc: '' });
                        }
                        return newHeaders;
                    }
                    return prev;
                }
            });
        };
        updateHeaders();
    }, [bodyMode, rawType, bodyContent, formDataParams, xWwwFormUrlencodedParams]);
  const handleDeleteInterface = async (id: number, e: React.MouseEvent) => {
      e.stopPropagation();
      if (!window.confirm('确定要删除吗？')) return;
      try {
          await api.delete(`/api/standard/interfaces/${id}`);
          if (selectedId === id) setSelectedId(null);
          fetchInterfaces();
      } catch (e) {
          // 中文注释：删除失败统一中文错误提示
          const msg = await translateError(e);
          alert('删除失败: ' + msg);
      }
  };

  const toggleBulkSelected = (id: number) => {
      setBulkSelected(prev => {
          const next = { ...prev };
          if (next[id]) {
              delete next[id];
          } else {
              next[id] = true;
          }
          return next;
      });
  };

  const buildRemoveSet = (rootIds: number[]) => {
      const childrenByParent = new Map<number | null, number[]>();
      for (const item of savedInterfaces) {
          const pid = item.parentId ?? null;
          const arr = childrenByParent.get(pid);
          if (arr) arr.push(item.id);
          else childrenByParent.set(pid, [item.id]);
      }

      const remove = new Set<number>();
      const stack = [...rootIds];
      while (stack.length) {
          const id = stack.pop() as number;
          if (remove.has(id)) continue;
          remove.add(id);
          const children = childrenByParent.get(id);
          if (children && children.length) {
              for (const childId of children) stack.push(childId);
          }
      }
      return remove;
  };

  const handleBulkDeleteToggleOrConfirm = async () => {
      if (!bulkDeleteMode) {
          setBulkDeleteMode(true);
          setBulkSelected({});
          return;
      }

      const selectedIds = Object.keys(bulkSelected).map(Number);
      if (selectedIds.length === 0) {
          setBulkDeleteMode(false);
          return;
      }

      const removeSet = buildRemoveSet(selectedIds);
      const hint = removeSet.size !== selectedIds.length ? `（包含子项，共 ${removeSet.size} 项）` : '';
      if (!window.confirm(`确定要删除选中的 ${selectedIds.length} 项吗？${hint}`)) return;

      try {
          for (const id of selectedIds) {
              await api.delete(`/api/standard/interfaces/${id}`);
          }

          if (selectedId !== null && removeSet.has(selectedId)) {
              setSelectedId(null);
          }

          setBulkDeleteMode(false);
          setBulkSelected({});
          fetchInterfaces();
      } catch (e) {
          alert('批量删除失败');
      }
  };

  // 拖拽排序与拖入目录处理
  const handleDragStart = (e: React.DragEvent, id: number) => {
      e.stopPropagation();
      e.dataTransfer.setData('text/plain', String(id));
      setDraggedId(id);
  };

  const handleDragOver = (e: React.DragEvent, targetId: number, isFolder: boolean) => {
      e.preventDefault();
      e.stopPropagation();
      if (draggedId === targetId) return;
      
      const rect = e.currentTarget.getBoundingClientRect();
      const y = e.clientY - rect.top;
      const height = rect.height;
      const position = computeDragOverPosition(isFolder, y, height);

      setDragOverId(targetId);
      setDragOverPosition(position);
  };

  const handleDragLeave = () => {
      setDragOverId(null);
      setDragOverPosition(null);
  };

  const handleDrop = async (e: React.DragEvent, targetId: number | null) => {
        e.preventDefault();
        e.stopPropagation();
        setDragOverId(null);
        setDragOverPosition(null);
        
        const idStr = e.dataTransfer.getData('text/plain');
        if (!idStr) return;
        const id = Number(idStr);
        if (id === targetId) return;

        const position = dragOverPosition || 'middle';
        const dropPlan = planInterfaceDrop(savedInterfaces, id, targetId, position);
        if (!dropPlan) return;

        setSavedInterfaces(dropPlan.nextItems);
        
        const updates: any = { parent_id: dropPlan.newParentId };
        if (dropPlan.newParentId) {
             const parentFolder = savedInterfaces.find(i => i.id === dropPlan.newParentId);
             if (parentFolder && parentFolder.baseUrl) {
                 updates.base_url = parentFolder.baseUrl;
             }
        }
        
        updateInterface(dropPlan.draggedId, updates);
        setDraggedId(null);
    };

    // 预留：未保存变更检测（当前状态灯已移除）
  // const isUnsaved = ...
  const toggleFolder = (id: number) => {
      setSavedInterfaces(prev => prev.map(i => 
          i.id === id ? { ...i, isOpen: !i.isOpen } : i
      ));
  };

  const handleApiPathBlur = () => {
      if (!apiPath || !apiPath.trim()) return;
      
      // 检测 URL 开头是否为 {{变量}}
      const match = apiPath.match(/^(\{\{\s*(.+?)\s*\}\})/);
      if (match) {
          const tag = match[1]; // {{key}}
          const envName = match[2]; // 变量名
          
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

      // 先更新 UI，提升交互响应
      setSavedInterfaces(prev => prev.map(item => 
          item.id === renamingId ? { ...item, name: renamingName } : item
      ));

      try {
          await api.put(`/api/standard/interfaces/${renamingId}`, { name: renamingName });
      } catch (e) {
          console.error("Rename failed", e);
          fetchInterfaces(); // 失败时回滚
      } finally {
          setRenamingId(null);
      }
  };

  // AI 生成并执行接口测试入口。
  // 会结合当前 method/url/body/headers/params 组织 richer prompt。
  const handleRun = async () => {
    if (!projectId) return alert('请先选择项目');
    if (!apiPath) return alert('请输入请求 URL');
    if (!requirement) return alert('请输入 AI 测试意图或接口定义');
    
    setLoading(true);
    setTestResult(null);
    setResponseTab('report'); // 切到报告页，展示生成进度
    onLog(`开始生成接口测试脚本 (${mode === 'natural' ? '自然语言' : '结构化'}模式)...`);
    
    try {
      const activeTypes = (Object.keys(testTypes) as Array<keyof typeof testTypes>)
        .filter(k => testTypes[k])
        .map(k => k.charAt(0).toUpperCase() + k.slice(1));

      // 组合 method/url/requirement 与用户配置，构建更完整的提示词
      let richRequirement = `
Method: ${method}
URL: ${apiPath}
Context/Requirement: ${requirement}
      `.trim();

      // 追加 Query 参数说明
      const validParams = queryParams.filter(p => p.key);
      if (validParams.length > 0) {
          richRequirement += `\n\nQuery Params:\n${validParams.map(p => `${p.key}: ${p.value} (${p.desc})`).join('\n')}`;
      }

      // 追加 Header 说明
      const validHeaders = headers.filter(h => h.key);
      if (validHeaders.length > 0) {
          richRequirement += `\n\nHeaders:\n${validHeaders.map(h => `${h.key}: ${h.value} (${h.desc})`).join('\n')}`;
      }

      // 追加请求体说明
      if (bodyMode !== 'none' && bodyContent.trim()) {
          richRequirement += `\n\nRequest Body (${bodyMode}):\n${bodyContent}`;
      }

      const data = await api.post<TestResult>('/api/api-testing', { 
        requirement: richRequirement, 
        project_id: projectId,
        base_url: '', // 兼容字段：完整 URL 已写入 richRequirement/prompt
        test_types: activeTypes,
        mode
      });
      
      setTestResult(data);
      onLog('接口测试执行完成');
      
      if (data.structured_report && data.structured_report.failed > 0) {
          onLog(`测试发现 ${data.structured_report.failed} 个问题`);
      }
    } catch (e) {
      // 中文注释：接口测试失败统一中文错误提示
      const msg = await translateError(e);
      onLog(`接口测试失败: ${msg}`);
    } finally {
      setLoading(false);
    }
  };

  const getMethodColor = (m: string) => {
      switch(m) {
          case 'GET': return '#198754'; // 绿色
          case 'POST': return '#8B4513'; // 棕色
          case 'PUT': return '#6f42c1'; // 紫色
          case 'DELETE': return '#b02a37'; // 深红
          default: return '#6c757d'; // 灰色
      }
  };

  const importInterfaceItems = async (items: SavedInterface[], rootParentId: number | null) => {
      // 修复导入丢失：导入内容必须写入后端数据库，避免后续 fetch 覆盖前端临时数据
      return importInterfaceItemsToBackend({
          items,
          rootParentId,
          projectId,
          createInterface: (payload) => api.post<SavedInterface>('/api/standard/interfaces', payload)
      });
  };

  const importFiles = async (files: File[], rootParentId: number | null) => {
      return importFilesFromCollections({
          files,
          rootParentId,
          importParsedItems: importInterfaceItems,
          onUnsupportedFormat: (fileName) => {
              onLog(`文件 ${fileName} 不符合导入格式（支持 Postman v2.1 / Apifox 导出）`);
          },
          onParseError: (fileName, message) => {
              onLog(`文件 ${fileName} 解析失败: ${message}`);
          }
      });
  };

  // 点击目录“导入”入口：记录目标目录并触发隐藏文件选择器。
  const handleFolderImport = (folderId: number) => {
      setFolderImportTargetId(folderId);
      folderImportInputRef.current?.click();
  };

  // 处理导入文件并写入数据库，避免刷新后前端临时态被覆盖。
  const handleFolderImportFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files || []);
      e.target.value = '';
      if (files.length === 0) return;

      const count = await importFiles(files, folderImportTargetId);
      if (count > 0) {
          onLog(`成功导入 ${count} 个项目（已写入数据库）`);
          fetchInterfaces();
          if (folderImportTargetId) {
              setSavedInterfaces(prev => prev.map(i => i.id === folderImportTargetId ? { ...i, isOpen: true } : i));
          }
      } else {
          onLog('未找到可导入的接口数据 (支持 Postman v2.1 / Apifox 导出)');
      }

      setFolderImportTargetId(null);
  };

  // 以 Postman v2.1 结构导出单个目录，便于再次导入或共享。
  const handleFolderExport = (folderId: number) => {
      const folder = savedInterfaces.find(i => i.id === folderId && i.type === 'folder');
      if (!folder) return;

      // 导出默认 JSON：按 Postman Collection v2.1 结构生成，便于再次导入
      const collection = {
          info: {
              name: folder.name,
              schema: 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json'
          },
          item: buildPostmanFolderItems(savedInterfaces, folderId)
      };

      const blob = new Blob([JSON.stringify(collection, null, 2)], { type: 'application/json;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${folder.name || 'collection'}.json`;
      a.click();
      URL.revokeObjectURL(url);
  };

  const handleFileDrop = async (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragOver(false);
      
      const files = Array.from(e.dataTransfer.files);
      if (files.length === 0) return;

      const importCount = await importFiles(files, null);

      if (importCount > 0) {
          onLog(`成功导入 ${importCount} 个项目（已写入数据库）`);
          fetchInterfaces();
      } else {
          onLog('未找到可导入的接口数据 (支持 Postman v2.1 / Apifox 导出)');
      }
  };

  // AI 响应分析入口：把当前响应体发送到后端分析接口，结果展示在“报告”标签。
  const handleAnalyzeResponse = async () => {
      if (!responseBody) return;
      
      setIsAnalyzing(true);
      try {
          const res = await api.post<any>('/standard/analyze_response', {
              method: method,
              url: apiPath, // 当前 apiPath 已保存完整 URL
              headers: sentHeaders,
              body: sentBody,
              response_status: responseStatus,
              response_headers: responseHeaders,
              response_body: typeof responseBody === 'string' ? responseBody : JSON.stringify(responseBody),
              error: null
          });
          setAiAnalysis(res.analysis);
      } catch (e) {
      // 中文注释：AI分析失败统一中文错误提示
      const msg = await translateError(e);
      onLog(`AI分析失败: ${msg}`);
      setAiAnalysis(`分析失败: ${msg}`);
      } finally {
          setIsAnalyzing(false);
      }
  };

  return (
    <div className="d-flex h-100 w-100 bg-white overflow-hidden postman-theme">
      <style>{`
          .no-caret::after { display: none !important; }
          /* 三点菜单：统一圆角（页面内全局应用） */
          .dropdown-menu { border-radius: 10px !important; }
          /* 接口列表：选中时使用淡灰覆盖层，不改变原文字/图标颜色 */
          .api-tree-item { position: relative; }
          /* 修复：flex 子项默认 min-width:auto 会导致横向滚动条，统一允许内容收缩并截断 */
          .api-tree-item { min-width: 0; }
          .api-tree-item .d-flex { min-width: 0; }
          .api-tree-item .text-truncate { min-width: 0; }
          .list-group { overflow-x: hidden; }
          .list-group-item { overflow-x: hidden; }
          .api-tree-item.api-tree-item-selected::after {
              content: '';
              position: absolute;
              inset: 2px;
              background: rgba(0, 0, 0, 0.06);
              border: 1px solid rgba(0, 0, 0, 0.12);
              border-radius: 8px;
              pointer-events: none;
          }
          /* 优化：方法列固定宽度且强制居中，并增加列间距 */
          .api-tree-method-col { width: 56px; flex: 0 0 56px; text-align: center; margin-right: 14px; }
          /* 接口列表：预留图标槽位，保证“接口/文件夹”文本起始一致 */
          .api-tree-icon-slot { width: 14px; flex: 0 0 14px; margin-right: 4px; display: inline-flex; align-items: center; justify-content: center; }
          /* 请求方法颜色：按需求自定义配色 */
          .api-method-get { color: #198754 !important; }
          .api-method-post { color: #8b5a2b !important; }
          .api-method-put { color: #6f42c1 !important; }
          .api-method-delete { color: #a61e2b !important; }
          .api-method-other { color: #6c757d !important; }
          .api-sidebar-resizer {
              /* 优化3：拖拽手柄加宽并可见，避免“看不到/拖不动” */
              width: 10px;
              cursor: col-resize;
              background: rgba(0,0,0,0.02);
              border-left: 1px solid #dee2e6;
          }
          .api-sidebar-resizer:hover {
              background: rgba(13, 110, 253, 0.12);
          }
          /* 优化：输入框 Placeholder */
          .custom-api-input::placeholder {
              color: #dee2e6 !important;
              font-size: 12px;
          }
          /* 优化：Tabs 样式自定义 (去除蓝色背景，改为下划线) */
          .custom-nav-tabs .nav-link {
              border: none;
              color: #6c757d;
              background: transparent !important;
              display: inline-flex;
              align-items: center;
              padding-bottom: 8px;
              border-bottom: 2px solid transparent;
              border-radius: 0;
              font-weight: 500; /* Always 500 to prevent jitter */
          }
          .custom-nav-tabs .nav-link:focus,
          .custom-nav-tabs .nav-link:focus-visible {
              outline: none;
              box-shadow: none;
          }
          .custom-nav-tabs .nav-link:hover {
              color: #495057;
          }
          .custom-nav-tabs .nav-link.active {
              color: #0d6efd !important;
              background: transparent !important;
              border-bottom: 2px solid #0d6efd;
              font-weight: 500 !important;
          }
          /* 修复：允许右侧主内容在 flex 布局下收缩，避免子元素过宽导致整体左右“扭动” */
          .api-main-content { min-width: 0; }
          /* 修复：请求配置内容禁止横向撑开，避免切换 Tab 时出现/消失横向溢出引发整体抖动 */
          .api-request-config-content { overflow-x: hidden; }
          /* 优化：美化滚动条 (瘦身、浅色) */
          ::-webkit-scrollbar {
              width: 8px;
              height: 8px;
          }
          ::-webkit-scrollbar-track {
              background: #f1f1f1;
          }
          ::-webkit-scrollbar-thumb {
              background: #ccc;
              border-radius: 4px;
          }
          ::-webkit-scrollbar-thumb:hover {
              background: #b3b3b3;
          }
      `}</style>
      {/* Left Sidebar - Interface List */}
      <div 
          className="border-end bg-light d-flex flex-column position-relative"
          style={{ 
              width: showSidebar ? `${sidebarWidth}px` : '0px', 
              minWidth: showSidebar ? `${sidebarWidth}px` : '0px',
              overflow: 'hidden',
              // 修复：拖拽调宽时禁用过渡，避免宽度变化被动画“吃掉”导致看似不生效
              transition: isResizingSidebar ? 'none' : 'width 0.2s ease, min-width 0.2s ease, opacity 0.2s ease',
              opacity: showSidebar ? 1 : 0
          }}
          onDragOver={(e) => { e.preventDefault(); setIsDragOver(true); }}
          onDragLeave={() => setIsDragOver(false)}
          onDrop={handleFileDrop}
      >
        <input
            ref={folderImportInputRef}
            type="file"
            accept=".json,application/json"
            multiple
            onChange={handleFolderImportFiles}
            style={{ display: 'none' }}
        />
        {isDragOver && (
            <div 
                className="position-absolute top-0 start-0 w-100 h-100 d-flex align-items-center justify-content-center bg-primary bg-opacity-10"
                style={{ zIndex: 100, pointerEvents: 'none', border: '2px dashed #0d6efd' }}
            >
                <div className="text-primary bg-white px-3 py-2 rounded shadow-sm" style={{fontWeight: 600}}>
                    <FaFolderPlus className="me-2"/> 释放以导入
                </div>
            </div>
        )}
        <div className="d-flex justify-content-between align-items-center mb-2 px-3 pt-3">
            <h6 className="mb-0 text-secondary" style={{ fontWeight: 600 }}>接口列表</h6>
            <div className="d-flex gap-2">
                 <Button variant="link" className="p-0 text-secondary" onClick={() => handleCreateFolder(null)} title="新建文件夹">
                     <FaFolderPlus size={16} />
                 </Button>
                 <Button variant="link" className="p-0 text-secondary" onClick={() => handleCreateInterface(null)} title="新建接口">
                     <FaPlus size={16} />
                 </Button>
                 <Button
                     variant="link"
                     className={`p-0 ${bulkDeleteMode ? 'text-danger' : 'text-secondary'}`}
                     onClick={handleBulkDeleteToggleOrConfirm}
                     title={bulkDeleteMode ? '删除选中（再次点击执行；不选则退出）' : '批量删除'}
                 >
                     <FaMinus size={16} />
                 </Button>
            </div>
        </div>
        <div 
            className="flex-grow-1 overflow-auto border-top bg-light position-relative"
            style={{ minHeight: '100px', overflowY: 'auto', overflowX: 'hidden' }}
        >
            <ListGroup variant="flush">
                <InterfaceTree
                    savedInterfaces={savedInterfaces}
                    selectedId={selectedId}
                    dragOverId={dragOverId}
                    dragOverPosition={dragOverPosition}
                    hoverId={hoverId}
                    bulkDeleteMode={bulkDeleteMode}
                    bulkSelected={bulkSelected}
                    renamingId={renamingId}
                    renamingName={renamingName}
                    setHoverId={setHoverId}
                    onDragStart={handleDragStart}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                    onToggleBulkSelected={toggleBulkSelected}
                    onLoadInterface={handleLoadInterface}
                    getMethodColor={getMethodColor}
                    onToggleFolder={toggleFolder}
                    onSetRenamingId={setRenamingId}
                    onSetRenamingName={setRenamingName}
                    onRenameConfirm={handleRenameConfirm}
                    onCreateInterface={handleCreateInterface}
                    onEditFolder={handleEditFolder}
                    onFolderImport={handleFolderImport}
                    onFolderExport={handleFolderExport}
                    onDeleteInterface={handleDeleteInterface}
                />
            </ListGroup>
            
            {savedInterfaces.length === 0 && (
                <div className="text-center text-muted mt-5 small position-absolute w-100" style={{top: '100px', left: 0, pointerEvents: 'none'}}>
                    暂无接口，点击右上角 + 新建
                </div>
            )}
        </div>
        

      </div>
      {showSidebar && (
          <div
              className="api-sidebar-resizer"
              onMouseDown={(e) => {
                  // 侧边栏拖拽调宽：记录起点，交给全局 mousemove 处理
                  setIsResizingSidebar(true);
                  sidebarResizeStartRef.current = { x: e.clientX, width: sidebarWidth };
                  e.preventDefault();
              }}
          />
      )}

      {/* Main Content */}
      <div 
        className="flex-grow-1 d-flex flex-column h-100 overflow-hidden bg-white api-main-content"
        ref={mainContentRef}
      >
        
        {/* 1. Request Bar (Postman Style) */}
        <div className="d-flex align-items-center p-2 border-bottom bg-light gap-2 flex-shrink-0" style={{height: '50px'}}>
            <Button variant="link" className="p-0 text-secondary me-2" onClick={() => setShowSidebar(!showSidebar)} title={showSidebar ? "收起列表" : "展开列表"}>
                <FaBars size={16} />
            </Button>
            
            <div className="d-flex flex-grow-1 bg-white border rounded">
                 <Form.Select 
                    className="border-0 shadow-none"
                    style={{
                        width: '110px', 
                        backgroundColor: '#f9f9f9', 
                        borderRight: '1px solid #dee2e6',
                        fontWeight: 600,
                        color: getMethodColor(method)
                    }} 
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
                                color: 'black', // 默认文本色
                                paddingLeft: '0px', // 与输入框内边距对齐
                                paddingRight: '0px'
                            }}
                        >
                            {apiPath.split(/(\{\{.*?\}\})/).map((part, index) => {
                                if (part.startsWith('{{') && part.endsWith('}}')) {
                                    const isEmpty = part.replace(/[\{\}\s]/g, '').length === 0;
                                    // 规则：{{}} 未配置对应 BaseURL 值时，仅边框标红提示；已配置则渲染为浅灰方块弱化拼接
                                    const envValue = getEnvBaseUrlValue(part);
                                    // 增强判断：排除 "null", "undefined" 字符串干扰
                                    const isMissingBaseUrl = !isEmpty && (!envValue || !envValue.trim() || envValue === 'null' || envValue === 'undefined');
                                    const chipStyle: React.CSSProperties = isEmpty
                                        ? { background: 'transparent', border: '1px solid #ffecb5', borderRadius: '4px', color: '#856404', padding: '0 2px', margin: '0 1px', fontSize: '1em', lineHeight: 1.6 }
                                        : isMissingBaseUrl
                                            // 错误状态：淡红框 + 淡红填充 + 深红文字 + 加粗 (弱化边框视觉)
                                            ? { background: 'rgba(220, 53, 69, 0.1)', border: '1px solid rgba(220, 53, 69, 0.3)', borderRadius: '4px', color: '#dc3545', fontWeight: 600, padding: '0 2px', margin: '0 1px', fontSize: '1em', lineHeight: 1.6 }
                                            // 正常状态：透明底 + 浅灰框 + 蓝色文字 (表示已解析)
                                            : { background: 'transparent', border: '1px solid #dee2e6', borderRadius: '4px', color: '#0d6efd', padding: '0 2px', margin: '0 1px', fontSize: '1em', lineHeight: 1.6 };
                                    return (
                                        <span key={index} style={chipStyle}>
                                            {part}
                                        </span>
                                    );
                                }
                                // 非 {{}} 部分：正常显示
                                return <span key={index} style={{ color: '#212529' }}>{part}</span>;
                            })}
                        </div>

                        {/* Foreground Input */}
                        <Form.Control 
                            ref={inputRef}
                            className="border-0 shadow-none p-0 bg-transparent custom-api-input"
                            placeholder="Enter request URL" 
                            value={apiPath} 
                            onChange={e => setApiPath(e.target.value)} 
                            onBlur={handleApiPathBlur}
                            onMouseMove={handleInputMouseMove}
                            onMouseLeave={handleInputMouseLeave}
                            onScroll={(e) => {
                                if (highlighterRef.current) {
                                    highlighterRef.current.scrollLeft = e.currentTarget.scrollLeft;
                                }
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
                                {(() => {
                                    const isEmpty = activeEnvTag.replace(/[\{\}\s]/g, '').length === 0;
                                    const envValue = getEnvBaseUrlValue(activeEnvTag);
                                    // 规则：{{}} 未配置对应 BaseURL 值时，标签整体标红提示
                                    const isMissingBaseUrl = !isEmpty && (!envValue || !envValue.trim());
                                    const cls = isEmpty ? 'text-warning' : (isMissingBaseUrl ? 'text-danger' : 'text-primary');
                                    const style = isEmpty ? { color: '#ffc107', fontWeight: 500 } : (isMissingBaseUrl ? { color: '#dc3545', fontWeight: 500 } : { fontWeight: 500 });
                                    return <span className={`small me-2 font-monospace ${cls}`} style={style}>{activeEnvTag}:</span>;
                                })()}
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
           
           <Button variant="primary" onClick={handleSendRequest} disabled={loading} className="px-4 text-white rounded-0" style={{ fontWeight: 500, backgroundColor: '#0d6efd', borderColor: '#0d6efd' }}>
                {loading ? <Spinner size="sm" animation="border" /> : "发送"}
            </Button>
            <Button variant="outline-secondary" className="px-3" style={{ fontWeight: 500 }} onClick={handleSaveInterfaceClick} title="保存接口">
                <FaSave className="me-2"/> 保存
            </Button>
            <Button variant="light" className="border text-secondary" onClick={handleSaveEnv} title="环境管理">
                <FaCog className="me-2"/> 环境管理
            </Button>
        </div>

        {/* 2. Request Config Tabs */}
        <div className="border-bottom px-3 bg-white flex-shrink-0 d-flex justify-content-between align-items-end" style={{height: '45px'}}>
            {/* 修复：Tab 使用 button 渲染，避免 a 标签默认行为引发页面滚动/抖动；禁用 Focus 防止浏览器自动滚动视图 */}
            <Nav activeKey={runSubTab} onSelect={k => setRunSubTab(k || 'params')} className="small custom-nav-tabs">
                <Nav.Item><Nav.Link as="button" type="button" eventKey="params" className="text-secondary" onMouseDown={e=>e.preventDefault()}>Params</Nav.Link></Nav.Item>
                <Nav.Item><Nav.Link as="button" type="button" eventKey="authorization" className="text-secondary" onMouseDown={e=>e.preventDefault()}>Authorization</Nav.Link></Nav.Item>
                <Nav.Item><Nav.Link as="button" type="button" eventKey="headers" className="text-secondary" onMouseDown={e=>e.preventDefault()}>Headers <span className="text-muted ms-1">({headers.filter(h=>h.key).length})</span></Nav.Link></Nav.Item>
                <Nav.Item><Nav.Link as="button" type="button" eventKey="body" className="text-secondary" onMouseDown={e=>e.preventDefault()}>Body</Nav.Link></Nav.Item>
                <Nav.Item><Nav.Link as="button" type="button" eventKey="scripts" className="text-secondary" onMouseDown={e=>e.preventDefault()}>Scripts</Nav.Link></Nav.Item>
                <Nav.Item><Nav.Link as="button" type="button" eventKey="settings" className="text-secondary" onMouseDown={e=>e.preventDefault()}>Settings</Nav.Link></Nav.Item>
                <Nav.Item><Nav.Link as="button" type="button" eventKey="ai_prompt" className="text-primary" style={{fontWeight: 500}} onMouseDown={e=>e.preventDefault()}><FaRobot className="me-1"/>AI Gen</Nav.Link></Nav.Item>
            </Nav>
            <Button variant="link" className="text-secondary text-decoration-none pb-2 mb-1" onClick={() => setShowCookieModal(true)} size="sm" title="Cookies 管理">
                <FaCookie className="me-1"/> Cookies
            </Button>
        </div>

        {/* 3. Request Config Content */}
        {/* 修复：父容器溢出隐藏，将滚动条下放至各 Tab 内部，确保每个 Tab 独立拥有稳定的滚动条轨道，彻底消除切换时的布局抖动 */}
        <div className="bg-white d-flex flex-column flex-shrink-0 api-request-config-content position-relative" style={{height: `${requestHeight}px`, minHeight: '100px', overflow: 'hidden'}}>
             {/* Params Tab */}
             <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white" 
                  style={{
                      visibility: runSubTab === 'params' ? 'visible' : 'hidden', 
                      zIndex: runSubTab === 'params' ? 10 : 0,
                      overflowX: 'hidden', 
                      overflowY: 'scroll'
                  }}>
                 {renderKvEditor(
                     queryParams, 
                     setQueryParams,
                     isBulkEditParams,
                     () => {
                         if (!isBulkEditParams) {
                             setParamsBulkText(stringifyBulkItems(queryParams));
                         } else {
                             setQueryParams(parseBulkText(paramsBulkText));
                         }
                         setIsBulkEditParams(!isBulkEditParams);
                     },
                     paramsBulkText,
                     setParamsBulkText
                 )}
             </div>
             {/* Headers Tab */}
             <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white" 
                  style={{
                      visibility: runSubTab === 'headers' ? 'visible' : 'hidden', 
                      zIndex: runSubTab === 'headers' ? 10 : 0,
                      overflowX: 'hidden', 
                      overflowY: 'scroll'
                  }}>
                 {renderKvEditor(
                     headers, 
                     setHeaders,
                     isBulkEditHeaders,
                     () => {
                         if (!isBulkEditHeaders) {
                             setHeadersBulkText(stringifyBulkItems(headers));
                         } else {
                             setHeaders(parseBulkText(headersBulkText));
                         }
                         setIsBulkEditHeaders(!isBulkEditHeaders);
                     },
                     headersBulkText,
                     setHeadersBulkText
                 )}
             </div>
             {/* Authorization Tab */}
             <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white" 
                  style={{
                      visibility: runSubTab === 'authorization' ? 'visible' : 'hidden', 
                      zIndex: runSubTab === 'authorization' ? 10 : 0,
                      overflowX: 'hidden', 
                      overflowY: 'scroll'
                  }}>
                 <div className="d-flex h-100">
                     <div className="border-end bg-light p-2" style={{width: '200px', minWidth: '200px'}}>
                        <div className="small text-muted mb-2 ps-2">类型</div>
                        <div className="d-flex flex-column gap-1">
                             {['none', 'bearer', 'basic', 'apikey'].map(t => (
                                <div 
                                    key={t}
                                    className={`px-3 py-2 small rounded cursor-pointer ${authType === t ? 'bg-primary text-white' : 'text-secondary hover-bg-gray'}`}
                                    onClick={() => setAuthType(t as any)}
                                    style={{cursor: 'pointer'}}
                                >
                                    {t === 'none' ? '无认证 (No Auth)' : t === 'bearer' ? 'Bearer 令牌' : t === 'basic' ? '基础认证 (Basic Auth)' : 'API 密钥 (API Key)'}
                                </div>
                            ))}
                         </div>
                     </div>
                     <div className="flex-grow-1 p-3">
                         {authType === 'none' && (
                            <div className="text-muted small">此请求不使用任何认证。</div>
                        )}
                         {authType === 'bearer' && (
                            <div className="d-flex flex-column gap-2" style={{maxWidth: '500px'}}>
                                <Form.Label className="small mb-0">Token</Form.Label>
                                <Form.Control 
                                    size="sm" 
                                    placeholder="输入 Token" 
                                    value={authToken} 
                                    onChange={e => setAuthToken(e.target.value)}
                                />
                            </div>
                        )}
                         {authType === 'basic' && (
                            <div className="d-flex flex-column gap-2" style={{maxWidth: '500px'}}>
                                <div className="d-flex gap-3">
                                    <div className="flex-grow-1">
                                        <Form.Label className="small mb-0">用户名</Form.Label>
                                        <Form.Control 
                                            size="sm" 
                                            placeholder="用户名" 
                                            value={authBasic.username} 
                                            onChange={e => setAuthBasic({...authBasic, username: e.target.value})}
                                        />
                                    </div>
                                    <div className="flex-grow-1">
                                        <Form.Label className="small mb-0">密码</Form.Label>
                                        <Form.Control 
                                            size="sm" 
                                            type="password"
                                            placeholder="密码" 
                                            value={authBasic.password} 
                                            onChange={e => setAuthBasic({...authBasic, password: e.target.value})}
                                        />
                                    </div>
                                </div>
                            </div>
                        )}
                         {authType === 'apikey' && (
                            <div className="d-flex flex-column gap-3" style={{maxWidth: '500px'}}>
                                <div className="d-flex gap-3">
                                    <div className="flex-grow-1">
                                        <Form.Label className="small mb-0">Key</Form.Label>
                                        <Form.Control 
                                            size="sm" 
                                            placeholder="Key" 
                                            value={authApiKey.key} 
                                            onChange={e => setAuthApiKey({...authApiKey, key: e.target.value})}
                                        />
                                    </div>
                                    <div className="flex-grow-1">
                                        <Form.Label className="small mb-0">Value</Form.Label>
                                        <Form.Control 
                                            size="sm" 
                                            placeholder="Value" 
                                            value={authApiKey.value} 
                                            onChange={e => setAuthApiKey({...authApiKey, value: e.target.value})}
                                        />
                                    </div>
                                </div>
                                <div>
                                    <Form.Label className="small mb-0">添加到</Form.Label>
                                    <Form.Select 
                                        size="sm" 
                                        value={authApiKey.addTo} 
                                        onChange={e => setAuthApiKey({...authApiKey, addTo: e.target.value as any})}
                                    >
                                        <option value="header">Header</option>
                                        <option value="query">Query Params</option>
                                    </Form.Select>
                                </div>
                            </div>
                        )}
                     </div>
                 </div>
             </div>

             {/* Scripts Tab */}
             <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white" 
                  style={{
                      visibility: runSubTab === 'scripts' ? 'visible' : 'hidden', 
                      zIndex: runSubTab === 'scripts' ? 10 : 0,
                      overflowX: 'hidden', 
                      overflowY: 'scroll'
                  }}>
                 <div className="d-flex h-100">
                     <div className="border-end bg-light p-2" style={{width: '200px', minWidth: '200px'}}>
                         <div className="d-flex flex-column gap-1">
                             <div 
                                className={`px-3 py-2 small rounded cursor-pointer ${activeScriptTab === 'pre' ? 'bg-primary text-white' : 'text-secondary hover-bg-gray'}`}
                                onClick={() => setActiveScriptTab('pre')}
                                style={{cursor: 'pointer'}}
                            >
                                Pre-request (前置脚本)
                            </div>
                            <div 
                                className={`px-3 py-2 small rounded cursor-pointer ${activeScriptTab === 'post' ? 'bg-primary text-white' : 'text-secondary hover-bg-gray'}`}
                                onClick={() => setActiveScriptTab('post')}
                                style={{cursor: 'pointer'}}
                            >
                                Post-response (后置脚本)
                            </div>
                         </div>
                     </div>
                     <div className="flex-grow-1 p-0 d-flex flex-column">
                         {activeScriptTab === 'pre' ? (
                           <Form.Control 
                               as="textarea" 
                               className="flex-grow-1 border-0 p-3 font-monospace small bg-transparent"
                               style={{resize: 'none', outline: 'none'}}
                               placeholder="// 在此编写前置脚本 (Pre-request scripts)..."
                               value={preRequestScript}
                               onChange={e => setPreRequestScript(e.target.value)}
                               spellCheck={false}
                           />
                        ) : (
                           <Form.Control 
                               as="textarea" 
                               className="flex-grow-1 border-0 p-3 font-monospace small bg-transparent"
                               style={{resize: 'none', outline: 'none'}}
                               placeholder="// 在此编写后置脚本 (Post-response scripts)..."
                               value={postResponseScript}
                               onChange={e => setPostResponseScript(e.target.value)}
                               spellCheck={false}
                           />
                        )}
                     </div>
                 </div>
             </div>

             {/* Settings Tab */}
             <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white" 
                  style={{
                      visibility: runSubTab === 'settings' ? 'visible' : 'hidden', 
                      zIndex: runSubTab === 'settings' ? 10 : 0,
                      overflowX: 'hidden', 
                      overflowY: 'scroll'
                  }}>
                 <div className="p-4" style={{maxWidth: '800px'}}>
                    <h6 className="mb-3 text-secondary">General (常规)</h6>
                    
                    <div className="d-flex align-items-center justify-content-between mb-3 pb-3 border-bottom">
                        <div>
                            <div className="small fw-bold">HTTP Version (HTTP 版本)</div>
                            <div className="text-muted" style={{fontSize: '0.75rem'}}>选择发送请求时使用的 HTTP 版本。</div>
                        </div>
                         <div style={{width: '150px'}}>
                             <Form.Select 
                                 size="sm" 
                                 value={requestSettings.httpVersion} 
                                 onChange={e => setRequestSettings({...requestSettings, httpVersion: e.target.value})}
                             >
                                 <option value="HTTP/1.x">HTTP/1.x</option>
                                 <option value="HTTP/2">HTTP/2</option>
                             </Form.Select>
                         </div>
                     </div>

                     <div className="d-flex align-items-center justify-content-between mb-3 pb-3 border-bottom">
                        <div>
                            <div className="small fw-bold">Enable SSL certificate verification (启用 SSL 证书验证)</div>
                            <div className="text-muted" style={{fontSize: '0.75rem'}}>发送请求时验证 SSL 证书。验证失败将导致请求中止。</div>
                        </div>
                        <Form.Check 
                            type="switch" 
                            checked={requestSettings.verifySSL}
                            onChange={e => setRequestSettings({...requestSettings, verifySSL: e.target.checked})}
                        />
                    </div>
                     
                     <div className="d-flex align-items-center justify-content-between mb-3 pb-3 border-bottom">
                       <div>
                           <div className="small fw-bold">Automatically follow redirects (自动跟随重定向)</div>
                           <div className="text-muted" style={{fontSize: '0.75rem'}}>将 HTTP 3xx 响应作为重定向处理。</div>
                       </div>
                       <Form.Check 
                           type="switch" 
                           checked={requestSettings.followRedirects}
                           onChange={e => setRequestSettings({...requestSettings, followRedirects: e.target.checked})}
                       />
                   </div>

                   {requestSettings.followRedirects && (
                       <div className="d-flex align-items-center justify-content-between mb-3 pb-3 border-bottom ps-4">
                           <div>
                               <div className="small fw-bold">Maximum number of redirects (最大重定向次数)</div>
                               <div className="text-muted" style={{fontSize: '0.75rem'}}>设置跟随重定向的最大次数限制。</div>
                           </div>
                           <div style={{width: '100px'}}>
                               <Form.Control 
                                   size="sm" 
                                   type="number" 
                                   value={requestSettings.maxRedirects} 
                                   onChange={e => setRequestSettings({...requestSettings, maxRedirects: parseInt(e.target.value)||0})} 
                               />
                           </div>
                       </div>
                   )}

                     <div className="d-flex align-items-center justify-content-between mb-3 pb-3 border-bottom">
                        <div>
                            <div className="small fw-bold">Follow original HTTP Method (保持原 HTTP 方法)</div>
                            <div className="text-muted" style={{fontSize: '0.75rem'}}>重定向时保持原 HTTP 方法，而不是默认的 GET 方法。</div>
                        </div>
                        <Form.Check 
                            type="switch" 
                            checked={requestSettings.followOriginalHttpMethod}
                            onChange={e => setRequestSettings({...requestSettings, followOriginalHttpMethod: e.target.checked})}
                        />
                    </div>

                     <div className="d-flex align-items-center justify-content-between mb-3 pb-3 border-bottom">
                       <div>
                           <div className="small fw-bold">Follow Authorization header (保持 Authorization 头)</div>
                           <div className="text-muted" style={{fontSize: '0.75rem'}}>重定向时保留 Authorization 头。</div>
                       </div>
                       <Form.Check 
                           type="switch" 
                           checked={requestSettings.followAuthorizationHeader}
                           onChange={e => setRequestSettings({...requestSettings, followAuthorizationHeader: e.target.checked})}
                       />
                   </div>

                   <div className="d-flex align-items-center justify-content-between mb-3 pb-3 border-bottom">
                       <div>
                           <div className="small fw-bold">Remove referer header on redirect (重定向时移除 Referer 头)</div>
                           <div className="text-muted" style={{fontSize: '0.75rem'}}>发生重定向时移除 Referer 头。</div>
                       </div>
                       <Form.Check 
                           type="switch" 
                           checked={requestSettings.removeRefererHeader}
                           onChange={e => setRequestSettings({...requestSettings, removeRefererHeader: e.target.checked})}
                       />
                   </div>

                   <div className="d-flex align-items-center justify-content-between mb-3 pb-3 border-bottom">
                       <div>
                           <div className="small fw-bold">Enable strict HTTP parser (启用严格 HTTP 解析)</div>
                           <div className="text-muted" style={{fontSize: '0.75rem'}}>限制包含无效 HTTP 头的响应。</div>
                       </div>
                       <Form.Check 
                           type="switch" 
                           checked={requestSettings.strictHttpParser}
                           onChange={e => setRequestSettings({...requestSettings, strictHttpParser: e.target.checked})}
                       />
                   </div>

                   <div className="d-flex align-items-center justify-content-between mb-3 pb-3 border-bottom">
                       <div>
                           <div className="small fw-bold">Encode URL automatically (自动编码 URL)</div>
                           <div className="text-muted" style={{fontSize: '0.75rem'}}>自动编码 URL 路径、查询参数和认证字段。</div>
                       </div>
                       <Form.Check 
                           type="switch" 
                           checked={requestSettings.encodeUrl}
                           onChange={e => setRequestSettings({...requestSettings, encodeUrl: e.target.checked})}
                       />
                   </div>

                   <div className="d-flex align-items-center justify-content-between mb-3 pb-3 border-bottom">
                       <div>
                           <div className="small fw-bold">Disable cookie jar (禁用 Cookie Jar)</div>
                           <div className="text-muted" style={{fontSize: '0.75rem'}}>防止此请求使用的 Cookie 被存储到 Cookie Jar 中。</div>
                       </div>
                       <Form.Check 
                           type="switch" 
                           checked={requestSettings.disableCookieJar}
                           onChange={e => setRequestSettings({...requestSettings, disableCookieJar: e.target.checked})}
                       />
                   </div>

                     <div className="d-flex align-items-center justify-content-between mb-3 pb-3 border-bottom">
                        <div>
                            <div className="small fw-bold">Request Timeout (请求超时)</div>
                            <div className="text-muted" style={{fontSize: '0.75rem'}}>设置请求超时时间（毫秒，0 表示无限）</div>
                        </div>
                        <div style={{width: '100px'}}>
                            <Form.Control 
                                size="sm" 
                                type="number" 
                                value={requestSettings.timeout} 
                                onChange={e => setRequestSettings({...requestSettings, timeout: parseInt(e.target.value)||0})} 
                            />
                        </div>
                    </div>

                    <h6 className="mb-3 mt-4 text-secondary">Advanced (高级)</h6>

                    <div className="d-flex align-items-center justify-content-between mb-3 pb-3 border-bottom">
                        <div>
                            <div className="small fw-bold">Use server cipher suite during handshake (握手时使用服务器加密套件)</div>
                            <div className="text-muted" style={{fontSize: '0.75rem'}}>在握手过程中使用服务器的加密套件顺序，而不是客户端的。</div>
                        </div>
                        <Form.Check 
                            type="switch" 
                            checked={requestSettings.useServerCipherSuite}
                            onChange={e => setRequestSettings({...requestSettings, useServerCipherSuite: e.target.checked})}
                        />
                    </div>

                    <div className="mb-3 pb-3 border-bottom">
                        <div className="mb-2">
                            <div className="small fw-bold">TLS/SSL protocols disabled during handshake (握手期间禁用的 TLS/SSL 协议)</div>
                            <div className="text-muted" style={{fontSize: '0.75rem'}}>指定在握手期间禁用的 SSL 和 TLS 协议版本。所有其他协议将被启用。</div>
                        </div>
                        <Form.Control 
                            size="sm" 
                            placeholder="" 
                            value={requestSettings.disabledSSLProtocols} 
                            onChange={e => setRequestSettings({...requestSettings, disabledSSLProtocols: e.target.value})} 
                        />
                    </div>

                    <div className="mb-3 pb-3 border-bottom">
                        <div className="mb-2">
                            <div className="small fw-bold">Cipher suite selection (加密套件选择)</div>
                            <div className="text-muted" style={{fontSize: '0.75rem'}}>SSL 服务器配置文件用于建立安全连接的加密套件顺序。</div>
                        </div>
                        <Form.Control 
                            as="textarea"
                            size="sm" 
                            placeholder="Enter cipher suites" 
                            value={requestSettings.cipherSuites} 
                            onChange={e => setRequestSettings({...requestSettings, cipherSuites: e.target.value})} 
                        />
                    </div>
                 </div>
             </div>
             
            {/* Body Tab */}
            <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white" 
                 style={{
                     visibility: runSubTab === 'body' ? 'visible' : 'hidden', 
                     zIndex: runSubTab === 'body' ? 10 : 0,
                     overflowX: 'hidden', 
                     overflowY: 'scroll'
                 }}>
                    <div className="w-100 d-flex flex-column" style={{minWidth: 0}}>
                    {/* 修复：Body 顶部选项栏允许换行，避免内容过宽导致 flex 最小宽度撑开页面 */}
                    <div className="d-flex flex-wrap gap-3 px-3 py-2 small border-bottom bg-light" style={{minWidth: 0}}>
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
                        <div className="position-relative d-flex flex-column h-100">
                             {/* Backdrop Highlighter */}
                             {bodyMode === 'raw' && rawType === 'JSON' && (
                                 <div
                                     ref={bodyHighlighterRef}
                                     className="position-absolute top-0 start-0 w-100 h-100 font-monospace small p-3"
                                     style={{
                                         whiteSpace: 'pre-wrap',
                                         wordWrap: 'break-word',
                                         overflow: 'hidden',
                                         color: 'transparent',
                                         pointerEvents: 'none',
                                         backgroundColor: 'transparent',
                                         zIndex: 0
                                     }}
                                     dangerouslySetInnerHTML={highlightJson(bodyContent)}
                                 />
                             )}
                             
                            {bodyMode === 'raw' ? (
                                <Form.Control 
                                    as="textarea" 
                                    className="w-100 font-monospace small border-0 p-3 bg-transparent flex-grow-1" 
                                    value={bodyContent} 
                                    onChange={e => {
                                        setBodyContent(e.target.value);
                                        e.target.style.height = 'auto';
                                        e.target.style.height = e.target.scrollHeight + 'px';
                                    }}
                                    onScroll={handleBodyScroll}
                                    placeholder={bodyMode === 'raw' && rawType === 'JSON' ? '{\n  "key": "value"\n}' : '请求体内容...'}
                                    style={{
                                        resize: 'vertical', 
                                        outline: 'none', 
                                        color: (bodyMode === 'raw' && rawType === 'JSON') ? 'transparent' : 'inherit',
                                        caretColor: 'black',
                                        zIndex: 1,
                                        position: 'relative',
                                        minHeight: '300px',
                                        overflow: 'hidden'
                                    }}
                                    spellCheck={false}
                                />
                            ) : bodyMode === 'form-data' ? (
                                renderFormDataEditor(
                                    formDataParams, 
                                    setFormDataParams,
                                    isBulkEditFormData,
                                    toggleFormDataBulk,
                                    formDataBulkText,
                                    handleFormDataBulkChange
                                )
                            ) : bodyMode === 'x-www-form-urlencoded' ? (
                                renderKvEditor(
                                    xWwwFormUrlencodedParams, 
                                    setXWwwFormUrlencodedParams,
                                    isBulkEditBody,
                                    () => {
                                        if (!isBulkEditBody) {
                                            setBodyBulkText(stringifyBulkItems(xWwwFormUrlencodedParams));
                                        } else {
                                            setXWwwFormUrlencodedParams(parseBulkText(bodyBulkText));
                                        }
                                        setIsBulkEditBody(!isBulkEditBody);
                                    },
                                    bodyBulkText,
                                    setBodyBulkText
                                )
                            ) : bodyMode === 'binary' ? (
                                <div className="p-4 d-flex flex-column align-items-center justify-content-center h-100 bg-light text-secondary">
                                    <div className="mb-3">
                                        <FaFile className="me-2" size={24}/>
                                        <span>{binaryFile ? binaryFile.name : '选择要上传的文件'}</span>
                                    </div>
                                    <div className="position-relative">
                                        <Button variant="outline-primary" size="sm" onClick={() => document.getElementById('binary-file-input')?.click()}>
                                            {binaryFile ? '更换文件' : '选择文件'}
                                        </Button>
                                        <Form.Control 
                                            id="binary-file-input"
                                            type="file" 
                                            className="d-none"
                                            onChange={(e: any) => {
                                                const file = e.target.files?.[0];
                                                if (file) {
                                                    const reader = new FileReader();
                                                    reader.onload = (ev) => {
                                                        const res = ev.target?.result as string;
                                                        setBinaryFile({ name: file.name, data: res });
                                                    };
                                                    reader.readAsDataURL(file);
                                                }
                                            }}
                                        />
                                    </div>
                                    {binaryFile && (
                                        <div className="small text-muted mt-2">
                                            已选择: {binaryFile.name} 
                                            <Button variant="link" className="text-danger p-0 ms-2 small text-decoration-none" onClick={()=>setBinaryFile(null)}>清除</Button>
                                        </div>
                                    )}
                                    <div className="small text-muted mt-2">文件内容将作为请求体发送 (Base64)</div>
                                </div>
                            ) : bodyMode === 'graphql' ? (
                                <div className="d-flex h-100 w-100">
                                    <div className="w-50 h-100 d-flex flex-column border-end">
                                        <div className="px-3 py-1 bg-light border-bottom small fw-bold text-secondary">查询 (QUERY)</div>
                                        <Form.Control 
                                            as="textarea" 
                                            className="flex-grow-1 border-0 p-3 font-monospace small bg-transparent"
                                            style={{resize: 'none', outline: 'none'}}
                                            value={graphqlQuery}
                                            onChange={e => setGraphqlQuery(e.target.value)}
                                            placeholder="query { ... }"
                                            spellCheck={false}
                                        />
                                    </div>
                                    <div className="w-50 h-100 d-flex flex-column">
                                        <div className="px-3 py-1 bg-light border-bottom small fw-bold text-secondary">变量 (GRAPHQL VARIABLES)</div>
                                        <Form.Control 
                                            as="textarea" 
                                            className="flex-grow-1 border-0 p-3 font-monospace small bg-transparent"
                                            style={{resize: 'none', outline: 'none'}}
                                            value={graphqlVariables}
                                            onChange={e => setGraphqlVariables(e.target.value)}
                                            placeholder="{ ... }"
                                            spellCheck={false}
                                        />
                                    </div>
                                </div>
                            ) : (
                                <div className="p-3 text-muted small">不支持的 Body 类型: {bodyMode}</div>
                            )}
                        </div>
                    ) : (
                        <div className="d-flex align-items-center justify-content-center flex-grow-1 text-muted small bg-light">
                            This request has no body.
                        </div>
                    )}
                </div>
             </div>
             {/* End Body Tab */}

            {/* AI Prompt Tab */}
            <div className="custom-scrollbar position-absolute top-0 start-0 w-100 h-100 bg-white d-flex flex-column p-3" 
                 style={{
                     visibility: runSubTab === 'ai_prompt' ? 'visible' : 'hidden', 
                     zIndex: runSubTab === 'ai_prompt' ? 10 : 0,
                     overflowX: 'hidden', 
                     overflowY: 'scroll'
                 }}>
                     <div className="d-flex justify-content-between mb-2">
                         <Form.Label className="small text-muted mb-0">AI 测试生成 (自然语言或 JSON 定义)</Form.Label>
                         <div className="d-flex gap-2">
                            <Form.Check type="radio" label="自然语言" checked={mode==='natural'} onChange={()=>setMode('natural')} inline className="small"/>
                            <Form.Check type="radio" label="结构化" checked={mode==='structured'} onChange={()=>setMode('structured')} inline className="small"/>
                         </div>
                     </div>
                     <Form.Control 
                        as="textarea" 
                        className="flex-grow-1 font-monospace small bg-light" 
                        style={{border: '1px solid #dee2e6'}}
                        value={requirement}
                        onChange={e => setRequirement(e.target.value)}
                        placeholder="描述您的测试场景..."
                     />
                     <div className="mt-2 d-flex justify-content-end">
                        <Button variant="outline-primary" size="sm" onClick={handleRun} disabled={loading}>
                             <FaRobot className="me-1"/> 生成并运行测试
                        </Button>
                     </div>
                 </div>
             {/* End AI Prompt Tab */}
        </div>

        {/* 4. Resizer / Divider */}
        <div 
            className="border-top bg-light d-flex align-items-center justify-content-center text-muted flex-shrink-0" 
            style={{ 
                height: '6px', 
                cursor: 'row-resize', 
                backgroundColor: isDragging ? '#e9ecef' : '#f8f9fa',
                userSelect: 'none' 
            }}
            onMouseDown={handleMouseDown}
        >
             {/* Handle hidden for cleaner look */}
        </div>

        {/* 5. Response Section */}
        <ResponsePanel
          loading={loading}
          responseTab={responseTab}
          setResponseTab={(tab) => setResponseTab(tab)}
          responseDetailedCookies={responseDetailedCookies}
          responseCookies={responseCookies}
          responseHeaders={responseHeaders}
          sentHeaders={sentHeaders}
          sentCookies={sentCookies}
          responseStatus={responseStatus}
          responseTime={responseTime}
          responseBody={responseBody}
          responseFormat={responseFormat}
          setResponseFormat={setResponseFormat}
          responseViewMode={responseViewMode}
          setResponseViewMode={setResponseViewMode}
          aiAnalysis={aiAnalysis}
          testResult={testResult}
          renderDashboard={(report) => <StructuredReportDashboard report={report} />}
          handleAnalyzeResponse={handleAnalyzeResponse}
          isAnalyzing={isAnalyzing}
          scriptTests={scriptTests}
        />
      </div>
      
      <SaveRequestModal
        show={showSaveModal}
        onHide={() => setShowSaveModal(false)}
        saveForm={saveForm}
        setSaveForm={setSaveForm}
        renderFolderOptions={renderFolderOptions}
        onConfirmSave={handleConfirmSave}
      />

      <CookieManagerModal
        show={showCookieModal}
        onHide={() => setShowCookieModal(false)}
        cookieJar={cookieJar}
        setCookieJar={setCookieJar}
      />

      <EnvManagerModal
        show={showEnvModal}
        onHide={() => setShowEnvModal(false)}
        editingEnv={editingEnv}
        setEditingEnv={setEditingEnv}
        savedEnvs={savedEnvs}
        onDeleteEnv={handleDeleteEnv}
        onUpdateEnv={handleUpdateEnv}
      />
    </div>
  );
}
