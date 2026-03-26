import { Form, InputGroup } from 'react-bootstrap';
import { QuotaRing } from './ConfigModalQuotaRing';

type Props = {
  provider: string;
  apiKey: string;
  model: string;
  vlModel: string;
  turboModel: string;
  turboProvider: string;
  turboApiKey: string;
  vlProvider: string;
  vlApiKey: string;
  onProviderChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onVlModelChange: (value: string) => void;
  onTurboModelChange: (value: string) => void;
  onTurboProviderChange: (value: string) => void;
  onTurboApiKeyChange: (value: string) => void;
  onVlProviderChange: (value: string) => void;
  onVlApiKeyChange: (value: string) => void;
  onDirty: () => void;
};

export function CloudTab({
  provider,
  apiKey,
  model,
  vlModel,
  turboModel,
  turboProvider,
  turboApiKey,
  vlProvider,
  vlApiKey,
  onProviderChange,
  onApiKeyChange,
  onModelChange,
  onVlModelChange,
  onTurboModelChange,
  onTurboProviderChange,
  onTurboApiKeyChange,
  onVlProviderChange,
  onVlApiKeyChange,
  onDirty,
}: Props) {
  const turboFollowMain = turboProvider === 'follow_main';
  const vlFollowMain = vlProvider === 'follow_main';

  return (
    <Form className="config-cloud-form" onChange={onDirty}>
      <div className="config-model-grid">
        <section className="config-model-card">
          <div className="config-model-card__header">
            <h6>文本模型</h6>
            <span>主模型配置</span>
          </div>
          <Form.Group className="config-field">
            <Form.Label>服务商</Form.Label>
            <Form.Select value={provider} onChange={(e) => onProviderChange(e.target.value)}>
              <option value="dashscope">DashScope (阿里云灵积)</option>
              <option value="deepseek">DeepSeek</option>
              <option value="openai">OpenAI (兼容服务)</option>
            </Form.Select>
          </Form.Group>
          <Form.Group className="config-field">
            <Form.Label>API Key</Form.Label>
            <InputGroup>
              <Form.Control
                type="password"
                value={apiKey}
                onChange={(e) => onApiKeyChange(e.target.value)}
                placeholder={apiKey === '******' ? '已加密存储' : 'sk-...'}
              />
            </InputGroup>
          </Form.Group>
          <Form.Group className="config-field config-field--model">
            <Form.Label>模型名称</Form.Label>
            <div style={{ position: 'relative' }}>
              <Form.Control
                type="text"
                value={model}
                onChange={(e) => onModelChange(e.target.value)}
                list="cloud-models"
                style={{ paddingRight: '35px' }}
              />
              <QuotaRing provider={provider} apiKey={apiKey} baseUrl="" model={model} />
            </div>
            <datalist id="cloud-models" />
          </Form.Group>
        </section>

        <section className="config-model-card">
          <div className="config-model-card__header">
            <h6>压缩模型</h6>
            <span>用于上下文压缩</span>
          </div>
          <Form.Group className="config-field">
            <Form.Label>服务商</Form.Label>
            <Form.Select value={turboProvider} onChange={(e) => onTurboProviderChange(e.target.value)}>
              <option value="follow_main">跟随主模型（文本模型）</option>
              <option value="dashscope">DashScope (阿里云灵积)</option>
              <option value="deepseek">DeepSeek</option>
              <option value="openai">OpenAI (兼容服务)</option>
            </Form.Select>
          </Form.Group>
          <Form.Group className="config-field">
            <Form.Label>API Key</Form.Label>
            <InputGroup>
              <Form.Control
                type="password"
                value={turboApiKey}
                onChange={(e) => onTurboApiKeyChange(e.target.value)}
                placeholder={
                  turboFollowMain
                    ? '跟随主模型，无需单独填写'
                    : turboApiKey === '******'
                      ? '已加密存储'
                      : 'sk-...'
                }
                disabled={turboFollowMain}
              />
            </InputGroup>
          </Form.Group>
          <Form.Group className="config-field config-field--model">
            <Form.Label>模型名称</Form.Label>
            <Form.Control
              type="text"
              value={turboModel}
              onChange={(e) => onTurboModelChange(e.target.value)}
              placeholder="e.g. qwen-turbo"
              list="turbo-models"
            />
            <datalist id="turbo-models" />
          </Form.Group>
        </section>

        <section className="config-model-card">
          <div className="config-model-card__header">
            <h6>图像模型</h6>
            <span>用于图片理解/OCR兜底</span>
          </div>
          <Form.Group className="config-field">
            <Form.Label>服务商</Form.Label>
            <Form.Select value={vlProvider} onChange={(e) => onVlProviderChange(e.target.value)}>
              <option value="follow_main">跟随主模型（文本模型）</option>
              <option value="dashscope">DashScope (阿里云灵积)</option>
              <option value="deepseek">DeepSeek</option>
              <option value="openai">OpenAI (兼容服务)</option>
            </Form.Select>
          </Form.Group>
          <Form.Group className="config-field">
            <Form.Label>API Key</Form.Label>
            <InputGroup>
              <Form.Control
                type="password"
                value={vlApiKey}
                onChange={(e) => onVlApiKeyChange(e.target.value)}
                placeholder={
                  vlFollowMain
                    ? '跟随主模型，无需单独填写'
                    : vlApiKey === '******'
                      ? '已加密存储'
                      : 'sk-...'
                }
                disabled={vlFollowMain}
              />
            </InputGroup>
          </Form.Group>
          <Form.Group className="config-field config-field--model">
            <Form.Label>模型名称</Form.Label>
            <Form.Control
              type="text"
              value={vlModel}
              onChange={(e) => onVlModelChange(e.target.value)}
              placeholder="e.g. qwen-vl-plus"
              list="vl-models"
            />
            <datalist id="vl-models" />
          </Form.Group>
        </section>
      </div>

      <Form.Text className="config-secret-tip">
        密钥将通过强加密存储在数据库中，绝不明文传输。
      </Form.Text>
    </Form>
  );
}
