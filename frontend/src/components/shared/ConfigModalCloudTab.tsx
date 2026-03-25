import { Form, InputGroup } from 'react-bootstrap';
import { QuotaRing } from './ConfigModalQuotaRing';

type Props = {
  provider: string;
  apiKey: string;
  model: string;
  vlModel: string;
  turboModel: string;
  onProviderChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onVlModelChange: (value: string) => void;
  onTurboModelChange: (value: string) => void;
  onDirty: () => void;
};

export function CloudTab({
  provider,
  apiKey,
  model,
  vlModel,
  turboModel,
  onProviderChange,
  onApiKeyChange,
  onModelChange,
  onVlModelChange,
  onTurboModelChange,
  onDirty,
}: Props) {
  return (
    <Form onChange={onDirty}>
      <Form.Group className="mb-3">
        <Form.Label>服务商</Form.Label>
        <Form.Select value={provider} onChange={(e) => onProviderChange(e.target.value)}>
          <option value="dashscope">DashScope (阿里云灵积)</option>
          <option value="openai">OpenAI (兼容服务)</option>
        </Form.Select>
      </Form.Group>

      <Form.Group className="mb-3">
        <Form.Label>API Key</Form.Label>
        <InputGroup>
          <Form.Control
            type="password"
            value={apiKey}
            onChange={(e) => onApiKeyChange(e.target.value)}
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
                onChange={(e) => onModelChange(e.target.value)}
                list="cloud-models"
                style={{ paddingRight: '35px' }}
              />
              <QuotaRing provider={provider} apiKey={apiKey} baseUrl="" model={model} />
            </div>
            <datalist id="cloud-models" />
          </Form.Group>
        </div>

        <div className="col-md-4">
          <Form.Group className="mb-3">
            <Form.Label>上下文压缩模型</Form.Label>
            <Form.Control
              type="text"
              value={turboModel}
              onChange={(e) => onTurboModelChange(e.target.value)}
              placeholder="e.g. qwen-turbo"
              list="turbo-models"
            />
            <datalist id="turbo-models" />
          </Form.Group>
        </div>

        <div className="col-md-4">
          <Form.Group className="mb-3">
            <Form.Label>图像模型</Form.Label>
            <Form.Control
              type="text"
              value={vlModel}
              onChange={(e) => onVlModelChange(e.target.value)}
              placeholder="e.g. qwen-vl-plus"
              list="vl-models"
            />
            <datalist id="vl-models" />
          </Form.Group>
        </div>
      </div>
    </Form>
  );
}
