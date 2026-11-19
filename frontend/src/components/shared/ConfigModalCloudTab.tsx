import { Form, InputGroup } from 'react-bootstrap';
import { ConfigModelInput } from './ConfigModelInput';
import type { ModelRoute, ReasoningCapabilities, ReasoningEffort, ReasoningSelections } from './ConfigModal.types';
import { QuotaRing } from './ConfigModalQuotaRing';

type Props = {
  pending: boolean;
  reasoning: ReasoningCapabilities;
  efforts: ReasoningSelections;
  onEffortChange: (route: ModelRoute, value?: ReasoningEffort) => void;
  provider: string;
  apiKey: string;
  baseUrl: string;
  model: string;
  vlModel: string;
  turboModel: string;
  reviewModel: string;
  turboProvider: string;
  turboApiKey: string;
  turboBaseUrl: string;
  reviewProvider: string;
  reviewApiKey: string;
  reviewBaseUrl: string;
  vlProvider: string;
  vlApiKey: string;
  vlBaseUrl: string;
  onProviderChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onBaseUrlChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onVlModelChange: (value: string) => void;
  onTurboModelChange: (value: string) => void;
  onReviewModelChange: (value: string) => void;
  onTurboProviderChange: (value: string) => void;
  onTurboApiKeyChange: (value: string) => void;
  onTurboBaseUrlChange: (value: string) => void;
  onReviewProviderChange: (value: string) => void;
  onReviewApiKeyChange: (value: string) => void;
  onReviewBaseUrlChange: (value: string) => void;
  onVlProviderChange: (value: string) => void;
  onVlApiKeyChange: (value: string) => void;
  onVlBaseUrlChange: (value: string) => void;
  onDirty: () => void;
};

export function CloudTab({
  pending, reasoning, efforts, onEffortChange,
  provider,
  apiKey,
  baseUrl,
  model,
  vlModel,
  turboModel,
  reviewModel,
  turboProvider,
  turboApiKey,
  turboBaseUrl,
  reviewProvider,
  reviewApiKey,
  reviewBaseUrl,
  vlProvider,
  vlApiKey,
  vlBaseUrl,
  onProviderChange,
  onApiKeyChange,
  onBaseUrlChange,
  onModelChange,
  onVlModelChange,
  onTurboModelChange,
  onReviewModelChange,
  onTurboProviderChange,
  onTurboApiKeyChange,
  onTurboBaseUrlChange,
  onReviewProviderChange,
  onReviewApiKeyChange,
  onReviewBaseUrlChange,
  onVlProviderChange,
  onVlApiKeyChange,
  onVlBaseUrlChange,
  onDirty,
}: Props) {
  const turboFollowMain = turboProvider === 'follow_main';
  const reviewFollowMain = reviewProvider === 'follow_main';
  const vlFollowMain = vlProvider === 'follow_main';
  const showBaseUrlInput = provider === 'openai';

  return (
    <Form className="config-cloud-form" onChange={onDirty}>
      <fieldset disabled={pending} className="config-model-grid">
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
          {showBaseUrlInput && (
            <Form.Group className="config-field">
              <Form.Label>API Base URL</Form.Label>
              <Form.Control
                type="text"
                value={baseUrl}
                onChange={(e) => onBaseUrlChange(e.target.value)}
                placeholder="https://api.openai.com/v1"
              />
            </Form.Group>
          )}
          <ConfigModelInput route="main" label="文本模型" model={model}
            effort={efforts.main} capability={reasoning.main}
            onModelChange={onModelChange} onEffortChange={(value) => onEffortChange('main', value)}>
              <QuotaRing provider={provider} apiKey={apiKey} baseUrl={baseUrl} model={model} />
            </ConfigModelInput>
        </section>

        <section className="config-model-card">
          <div className="config-model-card__header">
            <h6>压缩模型</h6>
            <span>用于上下文压缩</span>
          </div>
          <Form.Group className="config-field">
            <Form.Label>服务商</Form.Label>
            <Form.Select value={turboProvider} onChange={(e) => onTurboProviderChange(e.target.value)}>
              <option value="follow_main">沿用主模型服务商与凭据</option>
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
          <ConfigModelInput route="turbo" label="压缩模型" model={turboModel}
            effort={efforts.turbo} capability={reasoning.turbo}
            onModelChange={onTurboModelChange} onEffortChange={(value) => onEffortChange('turbo', value)} />
          {!turboFollowMain && turboProvider === 'openai' && <Form.Group className="config-field" controlId="config-turbo-endpoint">
            <Form.Label>服务地址</Form.Label>
            <Form.Control type="url" value={turboBaseUrl} onChange={(event) => onTurboBaseUrlChange(event.target.value)} />
          </Form.Group>}
        </section>

        <section className="config-model-card">
          <div className="config-model-card__header">
            <h6>审查模型</h6>
            <span>用于测试用例评审/筛选</span>
          </div>
          <Form.Group className="config-field">
            <Form.Label>服务商</Form.Label>
            <Form.Select value={reviewProvider} onChange={(e) => onReviewProviderChange(e.target.value)}>
              <option value="follow_main">沿用主模型服务商与凭据</option>
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
                value={reviewApiKey}
                onChange={(e) => onReviewApiKeyChange(e.target.value)}
                placeholder={
                  reviewFollowMain
                    ? '跟随主模型，无需单独填写'
                    : reviewApiKey === '******'
                      ? '已加密存储'
                      : 'sk-...'
                }
                disabled={reviewFollowMain}
              />
            </InputGroup>
          </Form.Group>
          <ConfigModelInput route="review" label="审查模型" model={reviewModel}
            effort={efforts.review} capability={reasoning.review}
            onModelChange={onReviewModelChange} onEffortChange={(value) => onEffortChange('review', value)} />
          {!reviewFollowMain && reviewProvider === 'openai' && <Form.Group className="config-field" controlId="config-review-endpoint">
            <Form.Label>服务地址</Form.Label>
            <Form.Control type="url" value={reviewBaseUrl} onChange={(event) => onReviewBaseUrlChange(event.target.value)} />
          </Form.Group>}
        </section>

        <section className="config-model-card">
          <div className="config-model-card__header">
            <h6>图像模型</h6>
            <span>用于图片理解/OCR兜底</span>
          </div>
          <Form.Group className="config-field">
            <Form.Label>服务商</Form.Label>
            <Form.Select value={vlProvider} onChange={(e) => onVlProviderChange(e.target.value)}>
              <option value="follow_main">沿用主模型服务商与凭据</option>
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
                    : vlProvider === provider
                      ? '留空沿用主模型 API Key'
                    : vlApiKey === '******'
                      ? '已加密存储'
                      : 'sk-...'
                }
                disabled={vlFollowMain}
              />
            </InputGroup>
          </Form.Group>
          {!vlFollowMain && vlProvider === 'openai' && (
            <Form.Group className="config-field">
              <Form.Label>API Base URL</Form.Label>
              <Form.Control
                type="text"
                value={vlBaseUrl}
                onChange={(e) => onVlBaseUrlChange(e.target.value)}
                placeholder={provider === 'openai' ? '留空沿用主模型 Base URL' : 'https://api.openai.com/v1'}
              />
              <Form.Text>同一网关仅切换视觉模型时，可沿用主模型 Base URL。</Form.Text>
            </Form.Group>
          )}
          <ConfigModelInput route="vision" label="图像模型" model={vlModel}
            effort={efforts.vision} capability={reasoning.vision}
            onModelChange={onVlModelChange} onEffortChange={(value) => onEffortChange('vision', value)} />
        </section>
      </fieldset>

      <Form.Text className="config-secret-tip">
        密钥将通过强加密存储在数据库中，绝不明文传输。
      </Form.Text>
    </Form>
  );
}
