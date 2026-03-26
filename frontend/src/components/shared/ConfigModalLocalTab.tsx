import { Alert, Badge, Button, Form, Spinner } from 'react-bootstrap';
import type { DetectedService } from './ConfigModal.types';

type Props = {
  localBaseUrl: string;
  localModel: string;
  detectedServices: DetectedService[];
  detecting: boolean;
  onDetect: () => void;
  onBaseUrlChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onDirty: () => void;
  onSelectDetectedService: (service: DetectedService) => void;
};

export function LocalTab({
  localBaseUrl,
  localModel,
  detectedServices,
  detecting,
  onDetect,
  onBaseUrlChange,
  onModelChange,
  onDirty,
  onSelectDetectedService,
}: Props) {
  return (
    <div className="config-local-form">
      <div className="mb-3 d-flex justify-content-end">
        <Button variant="outline-primary" size="sm" onClick={onDetect} disabled={detecting} className="config-detect-btn">
          {detecting ? <Spinner size="sm" animation="border" /> : '自动探测本地服务'}
        </Button>
      </div>

      {detectedServices.length > 0 && (
        <Alert variant="success" className="py-2 config-local-alert">
          发现 {detectedServices.length} 个本地服务：
          <div className="d-flex gap-2 flex-wrap mt-1">
            {detectedServices.map((service, index) => (
              <Badge
                key={index}
                bg="light"
                text="dark"
                className="border cursor-pointer"
                onClick={() => onSelectDetectedService(service)}
                style={{ cursor: 'pointer' }}
              >
                {service.url} {service.models ? `(${service.models.length} models)` : ''}
              </Badge>
            ))}
          </div>
        </Alert>
      )}

      <Form onChange={onDirty} className="config-local-card">
        <Form.Group className="config-field">
          <Form.Label>API Base URL</Form.Label>
          <Form.Control
            type="text"
            value={localBaseUrl}
            onChange={(e) => onBaseUrlChange(e.target.value)}
            placeholder="http://localhost:11434/v1"
          />
        </Form.Group>

        <Form.Group className="config-field">
          <Form.Label>模型名称</Form.Label>
          <Form.Control
            type="text"
            value={localModel}
            onChange={(e) => onModelChange(e.target.value)}
            placeholder="qwen:7b"
          />
        </Form.Group>
      </Form>
    </div>
  );
}
