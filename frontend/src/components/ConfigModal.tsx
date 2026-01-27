import { Modal, Button, Form, Alert, Tabs, Tab, Spinner, Badge, InputGroup, OverlayTrigger, Tooltip } from 'react-bootstrap';
import { useState, useEffect } from 'react';
import { api } from '../utils/api';

const QuotaRing = ({ provider, apiKey, baseUrl, model }: { provider: string, apiKey: string, baseUrl: string, model: string }) => {
  const [quota, setQuota] = useState<{ total: number; remaining: number; supported: boolean; loading: boolean }>({
    total: 0, remaining: 0, supported: false, loading: true
  });
  
  const fetchData = async () => {
      if (!apiKey && provider !== 'local') return;
      
      try {
          const res = await api.post<any>('/api/config/quota', {
              provider, 
              api_key: apiKey,
              base_url: baseUrl,
              model_name: model
          });
          
          if (res.supported) {
              setQuota({
                  total: parseFloat(res.total),
                  remaining: parseFloat(res.remaining),
                  supported: true,
                  loading: false
              });
          } else {
              setQuota(prev => ({ ...prev, supported: false, loading: false }));
          }
      } catch (e) {
          setQuota(prev => ({ ...prev, loading: false }));
      }
  };

  useEffect(() => {
      fetchData();
      const interval = setInterval(fetchData, 10000);
      return () => clearInterval(interval);
  }, [provider, apiKey, baseUrl]);

  if (!quota.supported) return null;

  const percent = quota.total > 0 ? (quota.remaining / quota.total) * 100 : 0;
  const color = percent < 20 ? '#dc3545' : percent < 50 ? '#ffc107' : '#28a745'; 

  return (
    <OverlayTrigger
      placement="top"
      overlay={<Tooltip>余额: ${quota.remaining.toFixed(2)} / ${quota.total.toFixed(2)}</Tooltip>}
    >
        <div style={{ position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)', width: '20px', height: '20px', cursor: 'help', zIndex: 5 }}>
            <svg viewBox="0 0 36 36">
                <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="#eee" strokeWidth="4" />
                <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke={color} strokeWidth="4" strokeDasharray={`${percent}, 100`} />
            </svg>
        </div>
    </OverlayTrigger>
  );
};

type Props = {
  show: boolean;
  onHide: () => void;
  initialError?: string | null;
};

type DetectedService = {
  url: string;
  success: boolean;
  latency?: number;
  models?: Array<{ id: string; object: string }>;
};

export function ConfigModal({ show, onHide, initialError }: Props) {
  const [activeTab, setActiveTab] = useState<'cloud' | 'local'>('cloud');
  
  // Cloud Config
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('qwen-plus');
  const [vlModel, setVlModel] = useState('qwen3-vl-plus-2025-12-19');
  const [turboModel, setTurboModel] = useState('qwen-turbo');
  const [provider, setProvider] = useState('dashscope');
  
  // Local Config
  const [localBaseUrl, setLocalBaseUrl] = useState('http://localhost:11434/v1');
  const [localModel, setLocalModel] = useState('qwen:7b');
  const [detectedServices, setDetectedServices] = useState<DetectedService[]>([]);
  const [detecting, setDetecting] = useState(false);

  // Status
  const [msg, setMsg] = useState<{ type: 'danger' | 'success'; text: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [streamOutput, setStreamOutput] = useState('');
  const [streamStatus, setStreamStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
  const [isDirty, setIsDirty] = useState(false);

  // Prevent accidental close
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (isDirty) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [isDirty]);

  useEffect(() => {
    if (show) {
      // Load current config
      api.get<any>('/api/config/current')
        .then(data => {
          if (data.active) {
            if (['dashscope', 'openai'].includes(data.provider)) {
              setActiveTab('cloud');
              setProvider(data.provider);
              setModel(data.model_name);
              setVlModel(data.vl_model_name || 'qwen3-vl-plus-2025-12-19');
              setTurboModel(data.turbo_model_name || 'qwen-turbo');
              setApiKey(data.has_api_key ? '******' : '');
            } else {
              setActiveTab('local');
              setLocalBaseUrl(data.base_url || '');
              setLocalModel(data.model_name);
            }
          }
        })
        .catch(console.error);

      if (initialError) {
        setMsg({ type: 'danger', text: initialError });
      } else {
        setMsg(null);
      }
    }
  }, [show, initialError]);

  const handleDetect = async () => {
    setDetecting(true);
    try {
      const candidates = [
        'http://localhost:11434/v1',
        'http://127.0.0.1:11434/v1',
        'http://localhost:8000/v1',
        'http://localhost:1234/v1'
      ];
      const data = await api.post<any>('/api/config/detect', { candidates });
      setDetectedServices(data.services || []);
      
      // Auto-select first found
      if (data.services && data.services.length > 0) {
        const s = data.services[0];
        setLocalBaseUrl(s.url);
        if (s.models && s.models.length > 0) {
          setLocalModel(s.models[0].id);
        }
      }
    } catch (e) {
      console.error(e);
    } finally {
      setDetecting(false);
    }
  };

  const handleValidate = async () => {
    setLoading(true);
    setMsg(null);
    setStreamOutput('');
    setStreamStatus('idle');
    
    const payload = activeTab === 'cloud' 
      ? { 
          provider, 
          api_key: apiKey === '******' ? '' : apiKey, 
          model_name: model,
          vl_model_name: vlModel,
          turbo_model_name: turboModel
        }
      : { provider: 'local', base_url: localBaseUrl, model_name: localModel };

    try {
      const data = await api.post<any>('/api/config/validate', payload);
      if (data.valid) {
        setMsg({ type: 'success', text: `验证通过! 延迟: ${data.details?.latency}ms` });
        // Start streaming test
        startStreamTest(payload);
      } else {
        setMsg({ type: 'danger', text: `验证失败: ${data.error}` });
      }
    } catch (e) {
      setMsg({ type: 'danger', text: String(e) });
    } finally {
      setLoading(false);
    }
  };

  const startStreamTest = async (payload: any) => {
    setStreamStatus('running');
    setStreamOutput('');
    try {
      const query = new URLSearchParams({
        provider: payload.provider,
        model: payload.model_name,
        prompt: "Hello!"
      });
      if (payload.api_key) query.append('api_key', payload.api_key);
      if (payload.base_url) query.append('base_url', payload.base_url);

      const eventSource = new EventSource(`/api/config/test-stream?${query.toString()}`);
      
      eventSource.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.token) {
          setStreamOutput(prev => prev + data.token);
        }
        if (data.error) {
          setStreamStatus('error');
          setStreamOutput(prev => prev + `\nError: ${data.error}`);
          eventSource.close();
        }
        if (data.done) {
          setStreamStatus('done');
          eventSource.close();
        }
      };
      
      eventSource.onerror = () => {
        setStreamStatus('error');
        eventSource.close();
      };
    } catch (e) {
      setStreamStatus('error');
    }
  };

  const handleSave = async () => {
    setLoading(true);
    try {
      const payload = activeTab === 'cloud' 
        ? { 
            provider, 
            api_key: apiKey === '******' ? undefined : apiKey, 
            model_name: model,
            vl_model_name: vlModel,
            turbo_model_name: turboModel
          }
        : { provider: 'local', base_url: localBaseUrl, model_name: localModel };

      const data = await api.post<any>('/api/config/save', payload);
      
      if (data.status === 'success') {
        setMsg({ type: 'success', text: '配置已激活' });
        setIsDirty(false);
        setTimeout(() => {
          onHide();
          setMsg(null);
        }, 1000);
      } else {
        setMsg({ type: 'danger', text: data.error || '保存失败' });
      }
    } catch (e) {
      setMsg({ type: 'danger', text: String(e) });
    } finally {
      setLoading(false);
    }
  };

  const handleClose = () => {
    if (isDirty) {
      if (!confirm('您有未保存的配置，确定要关闭吗？')) return;
    }
    onHide();
  };

  return (
    <Modal show={show} onHide={handleClose} backdrop="static" size="lg">
      <Modal.Header closeButton className="bg-light">
        <Modal.Title>API 配置中心</Modal.Title>
      </Modal.Header>
      <Modal.Body>
        {msg && <Alert variant={msg.type}>{msg.text}</Alert>}
        
        <Tabs activeKey={activeTab} onSelect={(k) => setActiveTab(k as any)} className="mb-3">
          <Tab eventKey="cloud" title="云端模型 (Cloud)">
            <Form onChange={() => setIsDirty(true)}>
              <Form.Group className="mb-3">
                <Form.Label>服务商</Form.Label>
                <Form.Select value={provider} onChange={(e) => setProvider(e.target.value)}>
                  <option value="dashscope">DashScope (阿里云灵积)</option>
                  <option value="openai">OpenAI (及兼容服务)</option>
                </Form.Select>
              </Form.Group>
              <Form.Group className="mb-3">
                <Form.Label>API Key</Form.Label>
                <InputGroup>
                  <Form.Control
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder={apiKey === '******' ? '已加密存储' : 'sk-...'}
                  />
                </InputGroup>
                <Form.Text className="text-muted">
                  密钥将通过强加密存储在数据库中，绝不明文传输。
                </Form.Text>
              </Form.Group>
              <div className="row">
                <div className="col-md-4">
                  <Form.Group className="mb-3">
                    <Form.Label>文本模型</Form.Label>
                    <div style={{ position: 'relative' }}>
                        <Form.Control 
                          type="text" 
                          value={model} 
                          onChange={(e) => setModel(e.target.value)} 
                          list="cloud-models"
                          style={{ paddingRight: '35px' }}
                        />
                        <QuotaRing provider={provider} apiKey={apiKey} baseUrl="" model={model} />
                    </div>
                    <datalist id="cloud-models">
                      <option value="qwen-plus" />
                      <option value="qwen-max" />
                      <option value="gpt-4o" />
                    </datalist>
                  </Form.Group>
                </div>
                <div className="col-md-4">
                  <Form.Group className="mb-3">
                    <Form.Label>上下文压缩模型</Form.Label>
                    <Form.Control 
                      type="text" 
                      value={turboModel} 
                      onChange={(e) => setTurboModel(e.target.value)} 
                      placeholder="e.g. qwen-turbo"
                    />
                  </Form.Group>
                </div>
                <div className="col-md-4">
                  <Form.Group className="mb-3">
                    <Form.Label>图像模型</Form.Label>
                    <Form.Control 
                      type="text" 
                      value={vlModel} 
                      onChange={(e) => setVlModel(e.target.value)} 
                      placeholder="e.g. qwen-vl-plus"
                    />
                  </Form.Group>
                </div>
              </div>
            </Form>
          </Tab>
          
          <Tab eventKey="local" title="本地模型 (Local)">
            <div className="mb-3 d-flex justify-content-end">
              <Button variant="outline-primary" size="sm" onClick={handleDetect} disabled={detecting}>
                {detecting ? <Spinner size="sm" animation="border" /> : '🔍 自动探测本地服务'}
              </Button>
            </div>
            
            {detectedServices.length > 0 && (
              <Alert variant="success" className="py-2">
                发现 {detectedServices.length} 个本地服务：
                <div className="d-flex gap-2 flex-wrap mt-1">
                  {detectedServices.map((s, i) => (
                    <Badge 
                      key={i} 
                      bg="light" 
                      text="dark" 
                      className="border cursor-pointer"
                      onClick={() => {
                        setLocalBaseUrl(s.url);
                        if(s.models && s.models.length) setLocalModel(s.models[0].id);
                        setIsDirty(true);
                      }}
                      style={{cursor: 'pointer'}}
                    >
                      {s.url} {s.models ? `(${s.models.length} models)` : ''}
                    </Badge>
                  ))}
                </div>
              </Alert>
            )}

            <Form onChange={() => setIsDirty(true)}>
              <Form.Group className="mb-3">
                <Form.Label>API Base URL</Form.Label>
                <Form.Control
                  type="text"
                  value={localBaseUrl}
                  onChange={(e) => setLocalBaseUrl(e.target.value)}
                  placeholder="http://localhost:11434/v1"
                />
              </Form.Group>
              <Form.Group className="mb-3">
                <Form.Label>模型名称</Form.Label>
                <Form.Control 
                  type="text" 
                  value={localModel} 
                  onChange={(e) => setLocalModel(e.target.value)}
                  placeholder="qwen:7b"
                />
              </Form.Group>
            </Form>
          </Tab>
        </Tabs>

        {/* Streaming Test Area */}
        <div className="mt-4 p-3 bg-light rounded border">
          <div className="d-flex justify-content-between align-items-center mb-2">
            <strong>连接测试预览</strong>
            {streamStatus === 'running' && <Spinner size="sm" animation="grow" variant="primary" />}
            {streamStatus === 'done' && <Badge bg="success">完成</Badge>}
            {streamStatus === 'error' && <Badge bg="danger">错误</Badge>}
          </div>
          <div 
            className="font-monospace bg-white p-2 border rounded" 
            style={{ minHeight: '60px', maxHeight: '150px', overflowY: 'auto', whiteSpace: 'pre-wrap', fontSize: '0.9em' }}
          >
            {streamOutput || <span className="text-muted fst-italic">点击"验证连接"开始测试...</span>}
          </div>
        </div>

      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={handleClose}>
          取消
        </Button>
        <Button variant="info" onClick={handleValidate} disabled={loading}>
          验证连接
        </Button>
        <Button variant="primary" onClick={handleSave} disabled={loading}>
          {loading ? '保存中...' : '应用并保存'}
        </Button>
      </Modal.Footer>
    </Modal>
  );
}
