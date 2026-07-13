// @ts-nocheck
import { useEffect, useState } from 'react';
import { Alert, Badge, Button, Modal, Spinner, Tab, Tabs } from 'react-bootstrap';
import { api } from '../../utils/api';
import { CloudTab } from './ConfigModalCloudTab';
import { LocalTab } from './ConfigModalLocalTab';
import { translateConfigError } from './ConfigModal.utils';
import './ConfigModal.css';

const CLOUD_PROVIDERS = ['dashscope', 'openai', 'deepseek'];
const DEFAULT_OPENAI_BASE_URL = 'https://api.openai.com/v1';
const OFFICIAL_BASE_URL_BY_PROVIDER: Record<string, string> = {
  deepseek: 'https://api.deepseek.com',
};

const resolveCloudBaseUrl = (provider: string, baseUrl: string): string => {
  const official = OFFICIAL_BASE_URL_BY_PROVIDER[String(provider || '').trim().toLowerCase()];
  if (official) {
    return official;
  }
  return String(baseUrl || '').trim();
};

type Props = {
  show: boolean;
  onHide: () => void;
  initialError?: unknown;
};

type MessageState = {
  type: 'success' | 'danger' | 'warning';
  text: string;
} | null;

export function ConfigModal({ show, onHide, initialError }: Props) {
  const [tab, setTab] = useState('cloud');

  const [apiKey, setApiKey] = useState('');
  const [modelName, setModelName] = useState('');
  const [vlModelName, setVlModelName] = useState('');
  const [turboModelName, setTurboModelName] = useState('');
  const [reviewModelName, setReviewModelName] = useState('');

  const [turboProvider, setTurboProvider] = useState('follow_main');
  const [turboApiKey, setTurboApiKey] = useState('');
  const [reviewProvider, setReviewProvider] = useState('follow_main');
  const [reviewApiKey, setReviewApiKey] = useState('');
  const [vlProvider, setVlProvider] = useState('follow_main');
  const [vlApiKey, setVlApiKey] = useState('');
  const [vlBaseUrl, setVlBaseUrl] = useState('');

  const [tesseractPath, setTesseractPath] = useState('');
  const [tesseractManualOverride, setTesseractManualOverride] = useState(false);
  const [detectingOcr, setDetectingOcr] = useState(false);
  const [ocrHint, setOcrHint] = useState<MessageState>(null);

  const [provider, setProvider] = useState('dashscope');
  const [cloudBaseUrl, setCloudBaseUrl] = useState('');
  const [lastOpenaiBaseUrl, setLastOpenaiBaseUrl] = useState(DEFAULT_OPENAI_BASE_URL);
  const [localBaseUrl, setLocalBaseUrl] = useState('http://localhost:11434/v1');
  const [localModelName, setLocalModelName] = useState('');
  const [detectedServices, setDetectedServices] = useState<any[]>([]);
  const [detectingLocal, setDetectingLocal] = useState(false);

  const [message, setMessage] = useState<MessageState>(null);
  const [pending, setPending] = useState(false);
  const [streamOutput, setStreamOutput] = useState('');
  const [streamState, setStreamState] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
  const [dirty, setDirty] = useState(false);
  const effectiveCloudBaseUrl = resolveCloudBaseUrl(provider, cloudBaseUrl);

  useEffect(() => {
    if (message?.type === 'danger') {
      setMessage(null);
      setStreamOutput('');
      setStreamState('idle');
    }
  }, [tab, provider, modelName, turboProvider, reviewProvider, vlProvider, vlBaseUrl, cloudBaseUrl, localBaseUrl, localModelName, tesseractPath, tesseractManualOverride]);


  useEffect(() => {
    document.body.classList.toggle('config-modal-open', show);
    return () => document.body.classList.remove('config-modal-open');
  }, [show]);

  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (!dirty) {
        return;
      }
      event.preventDefault();
      event.returnValue = '';
    };

    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  useEffect(() => {
    if (!show) {
      return;
    }

    let mounted = true;
    api
      .get('/api/config/current')
      .then((res) => {
        if (!mounted || !res?.active) {
          return;
        }

        if (CLOUD_PROVIDERS.includes(res.provider)) {
          setTab('cloud');
          setProvider(res.provider);
          setModelName(res.model_name);
          setVlModelName(res.vl_model_name || '');
          setTurboModelName(res.turbo_model_name || '');
          setReviewModelName(res.review_model_name || '');
          setApiKey(res.has_api_key ? '******' : '');
          const resolvedBaseUrl = resolveCloudBaseUrl(res.provider, res.base_url || '');
          setCloudBaseUrl(resolvedBaseUrl);
          if (res.provider === 'openai') {
            setLastOpenaiBaseUrl(resolvedBaseUrl || DEFAULT_OPENAI_BASE_URL);
          }

          setTurboProvider(res.turbo_follow_main ? 'follow_main' : res.turbo_provider || 'follow_main');
          setTurboApiKey(res.has_turbo_api_key ? '******' : '');
          setReviewProvider(res.review_follow_main ? 'follow_main' : res.review_provider || 'follow_main');
          setReviewApiKey(res.has_review_api_key ? '******' : '');
          setVlProvider(res.vl_follow_main ? 'follow_main' : res.vl_provider || 'follow_main');
          setVlApiKey(res.has_vl_api_key ? '******' : '');
          setVlBaseUrl(res.vl_base_url || '');
        } else {
          setTab('local');
          setLocalBaseUrl(res.base_url || '');
          setLocalModelName(res.model_name);
        }

        setTesseractPath(res.tesseract_path || '');
        setTesseractManualOverride(!!res.tesseract_manual_override);

        if (res.ocr_auto_detected && res.tesseract_path) {
          setOcrHint({ type: 'success', text: `已自动检索到本地 OCR 路径：${res.tesseract_path}` });
        } else if (res.ocr_auto_detect_message) {
          setOcrHint({ type: 'warning', text: res.ocr_auto_detect_message });
        } else {
          setOcrHint(null);
        }
      })
      .catch(console.error);

    if (initialError) {
      void (async () => {
        const text = await translateConfigError(initialError);
        if (mounted) {
          setMessage({ type: 'danger', text });
        }
      })();
    } else {
      setMessage(null);
    }

    return () => {
      mounted = false;
    };
  }, [show, initialError]);

  const markDirty = () => setDirty(true);
  const handleCloudBaseUrlChange = (value: string) => {
    setCloudBaseUrl(value);
    if (provider === 'openai') {
      const next = String(value || '').trim();
      if (next) {
        setLastOpenaiBaseUrl(next);
      }
    }
  };

  const handleCloudProviderChange = (value: string) => {
    const next = String(value || '').trim();
    if (provider === 'openai') {
      const currentOpenaiBase = String(cloudBaseUrl || '').trim();
      if (currentOpenaiBase) {
        setLastOpenaiBaseUrl(currentOpenaiBase);
      }
    }
    setProvider(next);
    if (next === 'openai') {
      setCloudBaseUrl(lastOpenaiBaseUrl || DEFAULT_OPENAI_BASE_URL);
    } else {
      setCloudBaseUrl(resolveCloudBaseUrl(next, cloudBaseUrl));
    }
    markDirty();
  };

  const handleVlProviderChange = (value: string) => {
    const next = String(value || '').trim();
    setVlProvider(next);
    if (next === 'openai' && !String(vlBaseUrl || '').trim()) {
      setVlBaseUrl(effectiveCloudBaseUrl || lastOpenaiBaseUrl || DEFAULT_OPENAI_BASE_URL);
    }
    markDirty();
  };

  const targetApiKeyMissing = (targetProvider: string, targetApiKey: string): boolean => {
    if (targetProvider === 'follow_main') {
      return false;
    }
    if (targetProvider === provider && (apiKey || '').trim()) {
      return false;
    }
    return !(targetApiKey || '').trim();
  };

  const detectLocalServices = async () => {
    setDetectingLocal(true);
    try {
      const candidates = [
        'http://localhost:11434/v1',
        'http://127.0.0.1:11434/v1',
        'http://localhost:8000/v1',
        'http://localhost:1234/v1',
      ];
      const res = await api.post('/api/config/detect', { candidates });
      setDetectedServices(res.services || []);
      if (res.services?.length > 0) {
        const first = res.services[0];
        setLocalBaseUrl(first.url);
        if (first.models?.length > 0) {
          setLocalModelName(first.models[0].id);
        }
      }
    } catch (error) {
      const text = await translateConfigError(error);
      setMessage({ type: 'danger', text });
    } finally {
      setDetectingLocal(false);
    }
  };

  const autoDetectOcr = async () => {
    setDetectingOcr(true);
    try {
      const res = await api.post('/api/config/ocr/auto-detect', {});
      if (res?.found && res?.validated && res?.path) {
        setTesseractPath(res.path);
        setTesseractManualOverride(false);
        setOcrHint({ type: 'success', text: res.message || `已自动检索到路径：${res.path}` });
      } else {
        setOcrHint({
          type: 'warning',
          text: res?.message || '未检测到本地 OCR 引擎，建议使用云端 OCR 或先安装本地 OCR 模块。',
        });
      }
    } catch (error) {
      const text = await translateConfigError(error);
      setOcrHint({ type: 'warning', text });
    } finally {
      setDetectingOcr(false);
    }
  };

  const runTestStream = async (payload: any) => {
    setStreamState('running');
    setStreamOutput('');
    try {
      const params = new URLSearchParams({
        provider: payload.provider,
        model: payload.model_name,
        prompt: 'Hello!',
      });
      if (payload.api_key) {
        params.append('api_key', payload.api_key);
      }
      if (payload.base_url) {
        params.append('base_url', payload.base_url);
      }

      const source = new EventSource(`/api/config/test-stream?${params.toString()}`);
      const appendError = async (raw: unknown) => {
        const text = await translateConfigError(raw);
        setStreamOutput((prev) => `${prev}\n${text}`);
      };

      source.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.token) {
          setStreamOutput((prev) => prev + data.token);
        }
        if (data.error) {
          setStreamState('error');
          void appendError(data.error);
          source.close();
        }
        if (data.done) {
          setStreamState('done');
          source.close();
        }
      };

      source.onerror = () => {
        setStreamState('error');
        setStreamOutput((prev) => `${prev}\n连接测试通道异常，请稍后重试。`);
        source.close();
      };
    } catch (error) {
      setStreamState('error');
      const text = await translateConfigError(error);
      setStreamOutput((prev) => `${prev}\n${text}`);
    }
  };

  const validateConnection = async () => {
    setPending(true);
    setMessage(null);
    setStreamOutput('');
    setStreamState('idle');

    if (tab === 'cloud') {
      if (turboProvider !== 'follow_main' && (turboModelName || '').trim() && !(turboApiKey || '').trim()) {
        setMessage({ type: 'danger', text: '压缩模型已配置独立服务商，请填写压缩模型 API Key。' });
        setPending(false);
        return;
      }
      if (reviewProvider !== 'follow_main' && (reviewModelName || '').trim() && !(reviewApiKey || '').trim()) {
        setMessage({ type: 'danger', text: '审查模型已配置独立服务商，请填写审查模型 API Key。' });
        setPending(false);
        return;
      }
      if ((vlModelName || '').trim() && targetApiKeyMissing(vlProvider, vlApiKey)) {
        setMessage({ type: 'danger', text: '图像模型已配置独立服务商，请填写图像模型 API Key。' });
        setPending(false);
        return;
      }
    }

    const payload =
      tab === 'cloud'
        ? {
            provider,
            api_key: apiKey === '******' ? '' : apiKey,
            base_url: effectiveCloudBaseUrl,
            model_name: modelName,
            vl_model_name: vlModelName,
            turbo_model_name: turboModelName,
            review_model_name: reviewModelName,
            turbo_provider: turboProvider === 'follow_main' ? provider : turboProvider,
            turbo_api_key: turboProvider === 'follow_main' || turboApiKey === '******' ? undefined : turboApiKey,
            turbo_follow_main: turboProvider === 'follow_main',
            review_provider: reviewProvider === 'follow_main' ? provider : reviewProvider,
            review_api_key: reviewProvider === 'follow_main' || reviewApiKey === '******' ? undefined : reviewApiKey,
            review_follow_main: reviewProvider === 'follow_main',
            vl_provider: vlProvider === 'follow_main' ? provider : vlProvider,
            vl_api_key: vlProvider === 'follow_main' || vlApiKey === '******' ? undefined : vlApiKey,
            vl_base_url: vlProvider === 'follow_main' ? undefined : vlBaseUrl.trim(),
            vl_follow_main: vlProvider === 'follow_main',
            tesseract_path: tesseractPath.trim(),
            tesseract_manual_override: tesseractManualOverride,
          }
        : {
            provider: 'local',
            base_url: localBaseUrl,
            model_name: localModelName,
            tesseract_path: tesseractPath.trim(),
            tesseract_manual_override: tesseractManualOverride,
          };

    try {
      const res = await api.post('/api/config/validate', payload);
      const checks = Array.isArray(res?.details?.checks) ? res.details.checks : [];

      if (res?.details?.ocr_auto_detected && res?.details?.ocr_auto_detected_path) {
        setTesseractPath(res.details.ocr_auto_detected_path);
        setTesseractManualOverride(false);
        setOcrHint({
          type: 'success',
          text: `已自动检索并验证 OCR 路径：${res.details.ocr_auto_detected_path}`,
        });
      }

      if (res.valid) {
        const profile = tab === 'cloud' ? `${provider}/${modelName || '(未填写模型)'}` : `local/${localModelName || '(未填写模型)'}`;
        const checksText = checks.length > 0 ? `，通过项：${checks.map((x: any) => `${x.label}:${x.model}`).join(' / ')}` : '';
        setMessage({
          type: 'success',
          text: `验证通过 (${profile})，总耗时 ${res.details?.latency}ms${checksText}`,
        });
        void runTestStream(payload);
      } else {
        const failedChecks = checks.filter((x: any) => !x?.success);
        const suffix =
          failedChecks.length > 0
            ? `（失败项：${failedChecks.map((x: any) => `${x.label}:${x.model}`).join(' / ')}）`
            : '';
        const text = await translateConfigError((res?.error || '验证失败') + suffix);
        setMessage({ type: 'danger', text });
        if (text.includes('本地 OCR') || text.includes('OCR')) {
          setOcrHint({ type: 'warning', text });
        }
      }
    } catch (error) {
      const text = await translateConfigError(error);
      setMessage({ type: 'danger', text });
    } finally {
      setPending(false);
    }
  };

  const saveConfig = async () => {
    setPending(true);

    if (tab === 'cloud') {
      if (turboProvider !== 'follow_main' && (turboModelName || '').trim() && !(turboApiKey || '').trim()) {
        setMessage({ type: 'danger', text: '压缩模型已配置独立服务商，请填写压缩模型 API Key。' });
        setPending(false);
        return;
      }
      if (reviewProvider !== 'follow_main' && (reviewModelName || '').trim() && !(reviewApiKey || '').trim()) {
        setMessage({ type: 'danger', text: '审查模型已配置独立服务商，请填写审查模型 API Key。' });
        setPending(false);
        return;
      }
      if ((vlModelName || '').trim() && targetApiKeyMissing(vlProvider, vlApiKey)) {
        setMessage({ type: 'danger', text: '图像模型已配置独立服务商，请填写图像模型 API Key。' });
        setPending(false);
        return;
      }
    }

    try {
      const payload =
        tab === 'cloud'
          ? {
              provider,
              api_key: apiKey === '******' ? undefined : apiKey,
              base_url: effectiveCloudBaseUrl,
              model_name: modelName,
              vl_model_name: vlModelName,
              turbo_model_name: turboModelName,
              review_model_name: reviewModelName,
              turbo_provider: turboProvider === 'follow_main' ? provider : turboProvider,
              turbo_api_key: turboProvider === 'follow_main' || turboApiKey === '******' ? undefined : turboApiKey,
              turbo_follow_main: turboProvider === 'follow_main',
              review_provider: reviewProvider === 'follow_main' ? provider : reviewProvider,
              review_api_key: reviewProvider === 'follow_main' || reviewApiKey === '******' ? undefined : reviewApiKey,
              review_follow_main: reviewProvider === 'follow_main',
              vl_provider: vlProvider === 'follow_main' ? provider : vlProvider,
              vl_api_key: vlProvider === 'follow_main' || vlApiKey === '******' ? undefined : vlApiKey,
              vl_base_url: vlProvider === 'follow_main' ? undefined : vlBaseUrl.trim(),
              vl_follow_main: vlProvider === 'follow_main',
              tesseract_path: tesseractPath.trim(),
              tesseract_manual_override: tesseractManualOverride,
            }
          : {
              provider: 'local',
              base_url: localBaseUrl,
              model_name: localModelName,
              tesseract_path: tesseractPath.trim(),
              tesseract_manual_override: tesseractManualOverride,
            };

      const res = await api.post('/api/config/save', payload);
      if (res.status === 'success') {
        if (res.tesseract_path) {
          setTesseractPath(res.tesseract_path);
        }
        if (res.ocr_auto_detected) {
          setTesseractManualOverride(false);
          setOcrHint({ type: 'success', text: '已自动检索并持久化本地 OCR 路径。' });
        } else if (res.ocr_warning) {
          setOcrHint({ type: 'warning', text: res.ocr_warning });
        }
        setMessage({ type: 'success', text: '配置已激活' });
        setDirty(false);
        setTimeout(() => {
          onHide();
          setMessage(null);
        }, 1000);
      } else {
        const text = await translateConfigError(res.error || '保存失败');
        setMessage({ type: 'danger', text });
      }
    } catch (error) {
      const text = await translateConfigError(error);
      setMessage({ type: 'danger', text });
    } finally {
      setPending(false);
    }
  };

  const handleClose = () => {
    if (dirty && !confirm('您有未保存的配置，确定要关闭吗？')) {
      return;
    }
    onHide();
  };

  return (
    <Modal show={show} onHide={handleClose} backdrop="static" size="xl" dialogClassName="config-modal-dialog">
      <Modal.Header closeButton className="config-modal-header">
        <Modal.Title>API 配置中心</Modal.Title>
      </Modal.Header>

      <Modal.Body className="config-modal-body">
        {message && <Alert variant={message.type}>{message.text}</Alert>}

        <section className="config-center-panel">
          <Tabs activeKey={tab} onSelect={(key) => setTab(key || 'cloud')} className="config-modal-tabs mb-3">
          <Tab eventKey="cloud" title="云端模型 (Cloud)">
            <CloudTab
              provider={provider}
              apiKey={apiKey}
              baseUrl={effectiveCloudBaseUrl}
              model={modelName}
              vlModel={vlModelName}
              turboModel={turboModelName}
              reviewModel={reviewModelName}
              turboProvider={turboProvider}
              turboApiKey={turboApiKey}
              reviewProvider={reviewProvider}
              reviewApiKey={reviewApiKey}
              vlProvider={vlProvider}
              vlApiKey={vlApiKey}
              vlBaseUrl={vlBaseUrl}
              onProviderChange={handleCloudProviderChange}
              onApiKeyChange={setApiKey}
              onBaseUrlChange={handleCloudBaseUrlChange}
              onModelChange={setModelName}
              onVlModelChange={setVlModelName}
              onTurboModelChange={setTurboModelName}
              onReviewModelChange={setReviewModelName}
              onTurboProviderChange={setTurboProvider}
              onTurboApiKeyChange={setTurboApiKey}
              onReviewProviderChange={setReviewProvider}
              onReviewApiKeyChange={setReviewApiKey}
              onVlProviderChange={handleVlProviderChange}
              onVlApiKeyChange={setVlApiKey}
              onVlBaseUrlChange={setVlBaseUrl}
              onDirty={markDirty}
            />
          </Tab>

          <Tab eventKey="local" title="本地模型 (Local)">
            <LocalTab
              localBaseUrl={localBaseUrl}
              localModel={localModelName}
              detectedServices={detectedServices}
              detecting={detectingLocal}
              onDetect={detectLocalServices}
              onBaseUrlChange={setLocalBaseUrl}
              onModelChange={setLocalModelName}
              onDirty={markDirty}
              onSelectDetectedService={(service) => {
                setLocalBaseUrl(service.url);
                if (service.models?.length) {
                  setLocalModelName(service.models[0].id);
                }
                setDirty(true);
              }}
            />
          </Tab>
          </Tabs>

          <div className="config-bottom-split">
          <section className="config-preview-card config-bottom-split__left">
            <div className="config-preview-card__header d-flex justify-content-between align-items-center mb-2">
              <strong>连接测试预览</strong>
              {streamState === 'running' && <Spinner size="sm" animation="grow" variant="primary" />}
              {streamState === 'done' && <Badge bg="success">完成</Badge>}
              {streamState === 'error' && <Badge bg="danger">错误</Badge>}
            </div>
            <div className="config-preview-card__content config-stream-output font-monospace">
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
                  onClick={autoDetectOcr}
                  disabled={detectingOcr}
                >
                  {detectingOcr ? '检索中...' : '自动检索 OCR'}
                </button>
              </div>
              <input
                className="form-control"
                value={tesseractPath}
                onChange={(event) => {
                  setTesseractPath(event.target.value);
                  setTesseractManualOverride(true);
                  if (ocrHint) {
                    setOcrHint(null);
                  }
                  markDirty();
                }}
                placeholder="留空使用系统 PATH，例如 C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
              />
              <div className="form-text">该路径仅用于图片本地 OCR，配置后会在“验证连接”阶段进行可执行性校验。</div>
              {ocrHint && <div className={`config-ocr-hint config-ocr-hint--${ocrHint.type}`}>{ocrHint.text}</div>}
            </section>
          </aside>
          </div>
        </section>
      </Modal.Body>

      <Modal.Footer className="config-modal-footer">
        <Button variant="secondary" onClick={handleClose} className="config-modal-btn config-modal-btn--secondary">
          取消
        </Button>
        <Button variant="info" onClick={validateConnection} disabled={pending} className="config-modal-btn config-modal-btn--info">
          验证连接
        </Button>
        <Button variant="primary" onClick={saveConfig} disabled={pending} className="config-modal-btn config-modal-btn--primary">
          {pending ? '保存中...' : '应用并保存'}
        </Button>
      </Modal.Footer>
    </Modal>
  );
}
