import { useEffect, useState } from 'react';
import { Alert, Badge, Button, Modal, Spinner, Tab, Tabs } from 'react-bootstrap';
import { api } from '../../utils/api';
import { CloudTab } from './ConfigModalCloudTab';
import type { DetectedService } from './ConfigModal.types';
import { LocalTab } from './ConfigModalLocalTab';
import { translateConfigError } from './ConfigModal.utils';
import './ConfigModal.css';

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
  const [turboProvider, setTurboProvider] = useState('follow_main');
  const [turboApiKey, setTurboApiKey] = useState('');
  const [vlProvider, setVlProvider] = useState('follow_main');
  const [vlApiKey, setVlApiKey] = useState('');
  const [tesseractPath, setTesseractPath] = useState('');
  const [tesseractManualOverride, setTesseractManualOverride] = useState(false);
  const [tesseractDetecting, setTesseractDetecting] = useState(false);
  const [tesseractHint, setTesseractHint] = useState<{ type: 'success' | 'warning'; text: string } | null>(null);
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
  }, [activeTab, provider, model, turboProvider, vlProvider, localBaseUrl, localModel, tesseractPath, tesseractManualOverride]);

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
              setTurboProvider(data.turbo_follow_main ? 'follow_main' : (data.turbo_provider || 'follow_main'));
              setTurboApiKey(data.has_turbo_api_key ? '******' : '');
              setVlProvider(data.vl_follow_main ? 'follow_main' : (data.vl_provider || 'follow_main'));
              setVlApiKey(data.has_vl_api_key ? '******' : '');
              setTesseractPath(data.tesseract_path || '');
              setTesseractManualOverride(Boolean(data.tesseract_manual_override));
            } else {
              setActiveTab('local');
              setLocalBaseUrl(data.base_url || '');
              setLocalModel(data.model_name);
              setTesseractPath(data.tesseract_path || '');
              setTesseractManualOverride(Boolean(data.tesseract_manual_override));
            }
            if (data.ocr_auto_detected && data.tesseract_path) {
              setTesseractHint({ type: 'success', text: `已自动检索到本地 OCR 路径：${data.tesseract_path}` });
            } else if (data.ocr_auto_detect_message) {
              setTesseractHint({ type: 'warning', text: data.ocr_auto_detect_message });
            } else {
              setTesseractHint(null);
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

  const handleAutoDetectOcr = async () => {
    setTesseractDetecting(true);
    try {
      const data = await api.post<any>('/api/config/ocr/auto-detect', {});
      if (data?.found && data?.validated && data?.path) {
        setTesseractPath(data.path);
        setTesseractManualOverride(false);
        setTesseractHint({ type: 'success', text: data.message || `已自动检索到路径：${data.path}` });
      } else {
        setTesseractHint({
          type: 'warning',
          text: data?.message || '未检测到本地 OCR 引擎，建议使用云端 OCR 或先安装本地 OCR 模块。',
        });
      }
    } catch (e) {
      const translated = await translateConfigError(e);
      setTesseractHint({ type: 'warning', text: translated });
    } finally {
      setTesseractDetecting(false);
    }
  };

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

    if (activeTab === 'cloud') {
      if (turboProvider !== 'follow_main' && (turboModel || '').trim() && !(turboApiKey || '').trim()) {
        setMsg({ type: 'danger', text: '压缩模型已配置独立服务商，请填写压缩模型 API Key。' });
        setLoading(false);
        return;
      }
      if (vlProvider !== 'follow_main' && (vlModel || '').trim() && !(vlApiKey || '').trim()) {
        setMsg({ type: 'danger', text: '图像模型已配置独立服务商，请填写图像模型 API Key。' });
        setLoading(false);
        return;
      }
    }

    const payload =
      activeTab === 'cloud'
        ? {
            provider,
            api_key: apiKey === '******' ? '' : apiKey,
            model_name: model,
            vl_model_name: vlModel,
            turbo_model_name: turboModel,
            turbo_provider: turboProvider === 'follow_main' ? provider : turboProvider,
            turbo_api_key: turboProvider === 'follow_main' ? undefined : (turboApiKey === '******' ? undefined : turboApiKey),
            turbo_follow_main: turboProvider === 'follow_main',
            vl_provider: vlProvider === 'follow_main' ? provider : vlProvider,
            vl_api_key: vlProvider === 'follow_main' ? undefined : (vlApiKey === '******' ? undefined : vlApiKey),
            vl_follow_main: vlProvider === 'follow_main',
            tesseract_path: tesseractPath.trim(),
            tesseract_manual_override: tesseractManualOverride,
          }
        : {
            provider: 'local',
            base_url: localBaseUrl,
            model_name: localModel,
            tesseract_path: tesseractPath.trim(),
            tesseract_manual_override: tesseractManualOverride,
          };

    try {
      const data = await api.post<any>('/api/config/validate', payload);
      const checks = Array.isArray(data?.details?.checks) ? data.details.checks : [];
      if (data?.details?.ocr_auto_detected && data?.details?.ocr_auto_detected_path) {
        setTesseractPath(data.details.ocr_auto_detected_path);
        setTesseractManualOverride(false);
        setTesseractHint({ type: 'success', text: `已自动检索并验证 OCR 路径：${data.details.ocr_auto_detected_path}` });
      }
      if (data.valid) {
        const label =
          activeTab === 'cloud'
            ? `${provider}/${model || '(未填写模型)'}`
            : `local/${localModel || '(未填写模型)'}`;
        const checkSummary = checks.length > 0
          ? `，通过项：${checks.map((c: any) => `${c.label}:${c.model}`).join(' / ')}`
          : '';
        setMsg({ type: 'success', text: `验证通过 (${label})，总耗时 ${data.details?.latency}ms${checkSummary}` });
        startStreamTest(payload);
      } else {
        const failed = checks.filter((c: any) => !c?.success);
        const failedText = failed.length > 0
          ? `（失败项：${failed.map((c: any) => `${c.label}:${c.model}`).join(' / ')}）`
          : '';
        const errorText = await translateConfigError((data?.error || '验证失败') + failedText);
        setMsg({ type: 'danger', text: errorText });
        if (errorText.includes('本地 OCR') || errorText.includes('OCR')) {
          setTesseractHint({ type: 'warning', text: errorText });
        }
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

    if (activeTab === 'cloud') {
      if (turboProvider !== 'follow_main' && (turboModel || '').trim() && !(turboApiKey || '').trim()) {
        setMsg({ type: 'danger', text: '压缩模型已配置独立服务商，请填写压缩模型 API Key。' });
        setLoading(false);
        return;
      }
      if (vlProvider !== 'follow_main' && (vlModel || '').trim() && !(vlApiKey || '').trim()) {
        setMsg({ type: 'danger', text: '图像模型已配置独立服务商，请填写图像模型 API Key。' });
        setLoading(false);
        return;
      }
    }

    try {
      const payload =
        activeTab === 'cloud'
          ? {
              provider,
              api_key: apiKey === '******' ? undefined : apiKey,
              model_name: model,
              vl_model_name: vlModel,
              turbo_model_name: turboModel,
              turbo_provider: turboProvider === 'follow_main' ? provider : turboProvider,
              turbo_api_key: turboProvider === 'follow_main' ? undefined : (turboApiKey === '******' ? undefined : turboApiKey),
              turbo_follow_main: turboProvider === 'follow_main',
              vl_provider: vlProvider === 'follow_main' ? provider : vlProvider,
              vl_api_key: vlProvider === 'follow_main' ? undefined : (vlApiKey === '******' ? undefined : vlApiKey),
              vl_follow_main: vlProvider === 'follow_main',
              tesseract_path: tesseractPath.trim(),
              tesseract_manual_override: tesseractManualOverride,
            }
          : {
              provider: 'local',
              base_url: localBaseUrl,
              model_name: localModel,
              tesseract_path: tesseractPath.trim(),
              tesseract_manual_override: tesseractManualOverride,
            };

      const data = await api.post<any>('/api/config/save', payload);

      if (data.status === 'success') {
        if (data.tesseract_path) {
          setTesseractPath(data.tesseract_path);
        }
        if (data.ocr_auto_detected) {
          setTesseractManualOverride(false);
          setTesseractHint({ type: 'success', text: '已自动检索并持久化本地 OCR 路径。' });
        } else if (data.ocr_warning) {
          setTesseractHint({ type: 'warning', text: data.ocr_warning });
        }
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
    <Modal show={show} onHide={handleClose} backdrop="static" size="xl" dialogClassName="config-modal-dialog">
      <Modal.Header closeButton className="config-modal-header">
        <Modal.Title>API 配置中心</Modal.Title>
      </Modal.Header>
      <Modal.Body className="config-modal-body">
        {msg && <Alert variant={msg.type}>{msg.text}</Alert>}

        <Tabs
          activeKey={activeTab}
          onSelect={(k) => setActiveTab((k as 'cloud' | 'local') || 'cloud')}
          className="config-modal-tabs mb-3"
        >
          <Tab eventKey="cloud" title="云端模型 (Cloud)">
            <CloudTab
              provider={provider}
              apiKey={apiKey}
              model={model}
              vlModel={vlModel}
              turboModel={turboModel}
              turboProvider={turboProvider}
              turboApiKey={turboApiKey}
              vlProvider={vlProvider}
              vlApiKey={vlApiKey}
              onProviderChange={setProvider}
              onApiKeyChange={setApiKey}
              onModelChange={setModel}
              onVlModelChange={setVlModel}
              onTurboModelChange={setTurboModel}
              onTurboProviderChange={setTurboProvider}
              onTurboApiKeyChange={setTurboApiKey}
              onVlProviderChange={setVlProvider}
              onVlApiKeyChange={setVlApiKey}
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

        <div className="config-bottom-split">
          <section className="config-preview-card config-bottom-split__left">
            <div className="config-preview-card__header d-flex justify-content-between align-items-center mb-2">
              <strong>连接测试预览</strong>
              {streamStatus === 'running' && <Spinner size="sm" animation="grow" variant="primary" />}
              {streamStatus === 'done' && <Badge bg="success">完成</Badge>}
              {streamStatus === 'error' && <Badge bg="danger">错误</Badge>}
            </div>
            <div
              className="config-preview-card__content font-monospace"
              style={{ minHeight: '140px', maxHeight: '220px', overflowY: 'auto', whiteSpace: 'pre-wrap', fontSize: '0.9em' }}
            >
              {streamOutput || <span className="text-muted fst-italic">点击“验证连接”开始测试...</span>}
            </div>
          </section>

          <aside className="config-bottom-split__right">
            <section className="config-ocr-card config-ocr-card--pane">
              <div className="config-ocr-card__head">
                <div className="config-ocr-card__title">本地 OCR 引擎路径（可选）</div>
                <button
                  type="button"
                  className="btn btn-sm btn-outline-primary config-auto-ocr-btn"
                  onClick={handleAutoDetectOcr}
                  disabled={tesseractDetecting}
                >
                  {tesseractDetecting ? '检索中...' : '自动检索 OCR'}
                </button>
              </div>
              <input
                className="form-control"
                value={tesseractPath}
                onChange={(e) => {
                  setTesseractPath(e.target.value);
                  setTesseractManualOverride(true);
                  if (tesseractHint) setTesseractHint(null);
                  markDirty();
                }}
                placeholder="留空使用系统 PATH，例如 C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
              />
              <div className="form-text">
                该路径仅用于图片本地 OCR，配置后会在“验证连接”阶段进行可执行性校验。
              </div>
              {tesseractHint && (
                <div className={`config-ocr-hint config-ocr-hint--${tesseractHint.type}`}>
                  {tesseractHint.text}
                </div>
              )}
            </section>
          </aside>
        </div>
      </Modal.Body>
      <Modal.Footer className="config-modal-footer">
        <Button variant="secondary" onClick={handleClose} className="config-modal-btn config-modal-btn--secondary">
          取消
        </Button>
        <Button variant="info" onClick={handleValidate} disabled={loading} className="config-modal-btn config-modal-btn--info">
          验证连接
        </Button>
        <Button variant="primary" onClick={handleSave} disabled={loading} className="config-modal-btn config-modal-btn--primary">
          {loading ? '保存中...' : '应用并保存'}
        </Button>
      </Modal.Footer>
    </Modal>
  );
}
