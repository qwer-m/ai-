import { useEffect, useState } from 'react';
import { Alert, Badge, Button, Modal, Spinner, Tab, Tabs } from 'react-bootstrap';
import { api } from '../../utils/api';
import { CloudTab } from './ConfigModalCloudTab';
import type { DetectedService } from './ConfigModal.types';
import { LocalTab } from './ConfigModalLocalTab';
import { translateConfigError } from './ConfigModal.utils';

type Props = {
  show: boolean;
  onHide: () => void;
  initialError?: string | null;
};

export function ConfigModal({ show, onHide, initialError }: Props) {
  const [activeTab, setActiveTab] = useState<'cloud' | 'local'>('cloud');
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [vlModel, setVlModel] = useState('');
  const [turboModel, setTurboModel] = useState('');
  const [provider, setProvider] = useState('dashscope');
  const [localBaseUrl, setLocalBaseUrl] = useState('http://localhost:11434/v1');
  const [localModel, setLocalModel] = useState('');
  const [detectedServices, setDetectedServices] = useState<DetectedService[]>([]);
  const [detecting, setDetecting] = useState(false);
  const [msg, setMsg] = useState<{ type: 'danger' | 'success'; text: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [streamOutput, setStreamOutput] = useState('');
  const [streamStatus, setStreamStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
  const [isDirty, setIsDirty] = useState(false);

  useEffect(() => {
    if (msg?.type === 'danger') {
      setMsg(null);
      setStreamOutput('');
      setStreamStatus('idle');
    }
  }, [activeTab, provider, model, localBaseUrl, localModel]);

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
      api
        .get<any>('/api/config/current')
        .then((data) => {
          if (data.active) {
            if (['dashscope', 'openai'].includes(data.provider)) {
              setActiveTab('cloud');
              setProvider(data.provider);
              setModel(data.model_name);
              setVlModel(data.vl_model_name || '');
              setTurboModel(data.turbo_model_name || '');
              setApiKey(data.has_api_key ? '******' : '');
            } else {
              setActiveTab('local');
              setLocalBaseUrl(data.base_url || '');
              setLocalModel(data.model_name);
            }
          }
        })
        .catch(console.error);

      let alive = true;
      if (initialError) {
        void (async () => {
          const translated = await translateConfigError(initialError);
          if (alive) setMsg({ type: 'danger', text: translated });
        })();
      } else {
        setMsg(null);
      }
      return () => {
        alive = false;
      };
    }
  }, [show, initialError]);

  const handleDetect = async () => {
    setDetecting(true);
    try {
      const candidates = [
        'http://localhost:11434/v1',
        'http://127.0.0.1:11434/v1',
        'http://localhost:8000/v1',
        'http://localhost:1234/v1',
      ];
      const data = await api.post<any>('/api/config/detect', { candidates });
      setDetectedServices(data.services || []);

      if (data.services && data.services.length > 0) {
        const s = data.services[0];
        setLocalBaseUrl(s.url);
        if (s.models && s.models.length > 0) {
          setLocalModel(s.models[0].id);
        }
      }
    } catch (e) {
      const translated = await translateConfigError(e);
      setMsg({ type: 'danger', text: translated });
    } finally {
      setDetecting(false);
    }
  };

  const markDirty = () => setIsDirty(true);

  const startStreamTest = async (payload: any) => {
    setStreamStatus('running');
    setStreamOutput('');
    try {
      const query = new URLSearchParams({
        provider: payload.provider,
        model: payload.model_name,
        prompt: 'Hello!',
      });
      if (payload.api_key) query.append('api_key', payload.api_key);
      if (payload.base_url) query.append('base_url', payload.base_url);

      const eventSource = new EventSource(`/api/config/test-stream?${query.toString()}`);

      const appendTranslatedStreamError = async (rawError: unknown) => {
        const translated = await translateConfigError(rawError);
        setStreamOutput((prev) => prev + `\n${translated}`);
      };

      eventSource.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.token) {
          setStreamOutput((prev) => prev + data.token);
        }
        if (data.error) {
          setStreamStatus('error');
          void appendTranslatedStreamError(data.error);
          eventSource.close();
        }
        if (data.done) {
          setStreamStatus('done');
          eventSource.close();
        }
      };

      eventSource.onerror = () => {
        setStreamStatus('error');
        setStreamOutput((prev) => prev + '\n连接测试通道异常，请稍后重试。');
        eventSource.close();
      };
    } catch (e) {
      setStreamStatus('error');
      const translated = await translateConfigError(e);
      setStreamOutput((prev) => prev + `\n${translated}`);
    }
  };

  const handleValidate = async () => {
    setLoading(true);
    setMsg(null);
    setStreamOutput('');
    setStreamStatus('idle');

    const payload =
      activeTab === 'cloud'
        ? {
            provider,
            api_key: apiKey === '******' ? '' : apiKey,
            model_name: model,
            vl_model_name: vlModel,
            turbo_model_name: turboModel,
          }
        : { provider: 'local', base_url: localBaseUrl, model_name: localModel };

    try {
      const data = await api.post<any>('/api/config/validate', payload);
      if (data.valid) {
        const label =
          activeTab === 'cloud'
            ? `${provider}/${model || '(未填写模型)'}`
            : `local/${localModel || '(未填写模型)'}`;
        setMsg({ type: 'success', text: `验证通过 (${label})，延迟 ${data.details?.latency}ms` });
        startStreamTest(payload);
      } else {
        const errorText = await translateConfigError(data?.error || '验证失败');
        setMsg({ type: 'danger', text: errorText });
      }
    } catch (e) {
      const errorText = await translateConfigError(e);
      setMsg({ type: 'danger', text: errorText });
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setLoading(true);
    try {
      const payload =
        activeTab === 'cloud'
          ? {
              provider,
              api_key: apiKey === '******' ? undefined : apiKey,
              model_name: model,
              vl_model_name: vlModel,
              turbo_model_name: turboModel,
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
        const translated = await translateConfigError(data.error || '保存失败');
        setMsg({ type: 'danger', text: translated });
      }
    } catch (e) {
      const translated = await translateConfigError(e);
      setMsg({ type: 'danger', text: translated });
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

        <Tabs activeKey={activeTab} onSelect={(k) => setActiveTab((k as 'cloud' | 'local') || 'cloud')} className="mb-3">
          <Tab eventKey="cloud" title="云端模型 (Cloud)">
            <CloudTab
              provider={provider}
              apiKey={apiKey}
              model={model}
              vlModel={vlModel}
              turboModel={turboModel}
              onProviderChange={setProvider}
              onApiKeyChange={setApiKey}
              onModelChange={setModel}
              onVlModelChange={setVlModel}
              onTurboModelChange={setTurboModel}
              onDirty={markDirty}
            />
          </Tab>

          <Tab eventKey="local" title="本地模型 (Local)">
            <LocalTab
              localBaseUrl={localBaseUrl}
              localModel={localModel}
              detectedServices={detectedServices}
              detecting={detecting}
              onDetect={handleDetect}
              onBaseUrlChange={setLocalBaseUrl}
              onModelChange={setLocalModel}
              onDirty={markDirty}
              onSelectDetectedService={(service) => {
                setLocalBaseUrl(service.url);
                if (service.models && service.models.length) setLocalModel(service.models[0].id);
                setIsDirty(true);
              }}
            />
          </Tab>
        </Tabs>

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
            {streamOutput || <span className="text-muted fst-italic">点击“验证连接”开始测试...</span>}
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
