import type { ReactNode } from 'react';
import { Form } from 'react-bootstrap';
import { REASONING_OPTIONS } from './ConfigModal.types';
import type { ModelRoute, ReasoningCapability, ReasoningEffort } from './ConfigModal.types';

type Props = {
  route: ModelRoute;
  label: string;
  model: string;
  effort?: ReasoningEffort;
  capability?: ReasoningCapability;
  onModelChange: (value: string) => void;
  onEffortChange: (value?: ReasoningEffort) => void;
  children?: ReactNode;
};

export function ConfigModelInput({ route, label, model, effort, capability,
  onModelChange, onEffortChange, children }: Props) {
  const supported = capability?.supported_efforts ?? [];
  const declaredOptions = supported.length > 0;
  const hintId = `config-${route}-reasoning-hint`;
  return (
    <Form.Group className="config-field config-field--model">
      <Form.Label htmlFor={`config-${route}-model`}>模型名称 / 思考级别</Form.Label>
      <div className="config-model-reasoning-row">
        <div className="config-model-input-wrap">
          <Form.Control id={`config-${route}-model`} type="text" value={model} className={children ? "config-model-input" : undefined}
            aria-label={`${label}名称`} onChange={(event) => onModelChange(event.target.value)} />
          {children}
        </div>
        <Form.Select value={effort ?? ''} aria-label={`${label}思考级别`}
          aria-describedby={hintId} className="config-reasoning-select"
          onChange={(event) => onEffortChange(event.target.value ? event.target.value as ReasoningEffort : undefined)}>
          <option value="">模型默认</option>
          {REASONING_OPTIONS.map(({ value, label: text }) => (
            <option key={value} value={value} disabled={declaredOptions && !supported.includes(value)}>
              {text}{declaredOptions && !supported.includes(value) ? '（不可用）' : ''}
            </option>
          ))}
        </Form.Select>
      </div>
      <Form.Text id={hintId} className="config-reasoning-hint" aria-live="polite">
        {!capability ? '选择后验证本次请求；兼容参数可能映射到相同强度。' : declaredOptions
          ? `可用：${REASONING_OPTIONS.filter(({ value }) => supported.includes(value)).map(({ label: text }) => text).join('、')}；灰色选项不可用`
          : capability.message}
      </Form.Text>
    </Form.Group>
  );
}
