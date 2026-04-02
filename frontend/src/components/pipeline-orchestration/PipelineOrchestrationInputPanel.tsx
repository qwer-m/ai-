import { Card, Form } from 'react-bootstrap';
import { testTypeLabel, testTypeOptions } from './model';
import type { PipelineOrchestrationController } from './usePipelineOrchestrationController';

type Props = {
  controller: PipelineOrchestrationController;
};

export function PipelineOrchestrationInputPanel({ controller }: Props) {
  return (
    <Card className="border-0 shadow-sm h-100 panel-card pipeline-input-card">
      <Card.Body className="d-flex flex-column gap-3 pipeline-input-body">
        <h6 className="mb-0">流水线输入</h6>

        <div>
          <Form.Label>需求描述</Form.Label>
          <Form.Control
            as="textarea"
            rows={4}
            value={controller.requirement}
            onChange={(e) => controller.setRequirement(e.target.value)}
            placeholder="请描述本次运行的端到端需求。"
          />
        </div>

        <div className="row g-3">
          <div className="col-md-4">
            <Form.Label>期望用例数</Form.Label>
            <Form.Control
              type="number"
              min={1}
              value={controller.expectedCount}
              onChange={(e) => controller.setExpectedCount(Math.max(1, Number(e.target.value) || 1))}
            />
          </div>
          <div className="col-md-8 d-flex align-items-end">
            <Form.Check
              type="switch"
              id="pipeline-compress"
              label="在用例生成阶段启用上下文压缩"
              checked={controller.compress}
              onChange={(e) => controller.setCompress(e.target.checked)}
            />
          </div>
        </div>

        <hr className="my-1" />
        <h6 className="mb-0">UI 自动化</h6>
        <div className="row g-3">
          <div className="col-md-6">
            <Form.Label>目标地址</Form.Label>
            <Form.Control value={controller.uiTarget} onChange={(e) => controller.setUiTarget(e.target.value)} />
          </div>
          <div className="col-md-3">
            <Form.Label>类型</Form.Label>
            <Form.Select
              value={controller.uiAutomationType}
              onChange={(e) => controller.setUiAutomationType(e.target.value as 'web' | 'app')}
            >
              <option value="web">网页（Web）</option>
              <option value="app">应用（App）</option>
            </Form.Select>
          </div>
          <div className="col-md-3">
            <Form.Label>任务（可选）</Form.Label>
            <Form.Control
              value={controller.uiTask}
              onChange={(e) => controller.setUiTask(e.target.value)}
              placeholder="默认使用全局需求"
            />
          </div>
        </div>

        <hr className="my-1" />
        <h6 className="mb-0">接口自动化</h6>
        <div className="row g-3">
          <div className="col-md-4">
            <Form.Label>基础 URL</Form.Label>
            <Form.Control value={controller.apiBaseUrl} onChange={(e) => controller.setApiBaseUrl(e.target.value)} />
          </div>
          <div className="col-md-4">
            <Form.Label>接口路径</Form.Label>
            <Form.Control value={controller.apiPath} onChange={(e) => controller.setApiPath(e.target.value)} />
          </div>
          <div className="col-md-4">
            <Form.Label>模式</Form.Label>
            <Form.Select
              value={controller.apiMode}
              onChange={(e) => controller.setApiMode(e.target.value as 'structured' | 'natural')}
            >
              <option value="structured">结构化</option>
              <option value="natural">自然语言</option>
            </Form.Select>
          </div>
        </div>
        <Form.Group>
          <Form.Label>接口需求（可选）</Form.Label>
          <Form.Control
            as="textarea"
            rows={2}
            value={controller.apiRequirement}
            onChange={(e) => controller.setApiRequirement(e.target.value)}
            placeholder="默认使用全局需求"
          />
        </Form.Group>
        <div className="d-flex gap-3 flex-wrap">
          {testTypeOptions.map((item) => (
            <Form.Check
              key={item}
              inline
              id={`pipeline-api-type-${item}`}
              type="checkbox"
              label={testTypeLabel[item] || item}
              checked={controller.apiTestTypes.includes(item)}
              onChange={() => controller.toggleApiType(item)}
            />
          ))}
        </div>

        <hr className="my-1" />
        <h6 className="mb-0">评估配置</h6>
        <div className="d-flex gap-4 flex-wrap">
          <Form.Check
            type="switch"
            id="pipeline-eval-testcase"
            label="测试用例评估"
            checked={controller.runTestcaseEval}
            onChange={(e) => controller.setRunTestcaseEval(e.target.checked)}
          />
          <Form.Check
            type="switch"
            id="pipeline-eval-ui"
            label="UI 自动化评估"
            checked={controller.runUiEval}
            onChange={(e) => controller.setRunUiEval(e.target.checked)}
          />
          <Form.Check
            type="switch"
            id="pipeline-eval-api"
            label="接口评估"
            checked={controller.runApiEval}
            onChange={(e) => controller.setRunApiEval(e.target.checked)}
          />
        </div>
        {controller.runTestcaseEval && (
          <Form.Group>
            <Form.Label>基线测试用例（用于对比）</Form.Label>
            <Form.Control
              as="textarea"
              rows={3}
              value={controller.baselineTestCases}
              onChange={(e) => controller.setBaselineTestCases(e.target.value)}
              placeholder="请粘贴人工修订后的基线测试用例。"
            />
          </Form.Group>
        )}

        <hr className="my-1" />
        <div className="d-flex justify-content-between align-items-center">
          <h6 className="mb-0">智能体循环</h6>
          <span className="small text-muted">
            {controller.agentDefaultsState === 'loading' && '正在加载项目默认配置...'}
            {controller.agentDefaultsState === 'saving' && '正在保存项目默认配置...'}
            {controller.agentDefaultsState === 'ready' && '项目默认配置已同步'}
          </span>
        </div>

        {/*
          智能体配置直接映射到后端持久化字段。
          拆分后保持原有开关和数值约束不变，避免运行参数与历史记录不一致。
        */}
        <div className="d-flex gap-4 flex-wrap">
          <Form.Check
            type="switch"
            id="pipeline-agent-enabled"
            label="启用智能体循环"
            checked={controller.agentEnabled}
            onChange={(e) => controller.setAgentEnabled(e.target.checked)}
          />
          <Form.Check
            type="switch"
            id="pipeline-agent-planner"
            label="规划 LLM"
            checked={controller.agentPlannerLLM}
            disabled={!controller.agentEnabled}
            onChange={(e) => controller.setAgentPlannerLLM(e.target.checked)}
          />
          <Form.Check
            type="switch"
            id="pipeline-agent-reviewer"
            label="评审 LLM"
            checked={controller.agentReviewerLLM}
            disabled={!controller.agentEnabled}
            onChange={(e) => controller.setAgentReviewerLLM(e.target.checked)}
          />
          <Form.Check
            type="switch"
            id="pipeline-agent-executor-parallel"
            label="执行器并行"
            checked={controller.agentExecutorParallel}
            disabled={!controller.agentEnabled}
            onChange={(e) => controller.setAgentExecutorParallel(e.target.checked)}
          />
          <Form.Check
            type="switch"
            id="pipeline-agent-auto-retry"
            label="评审自动重试"
            checked={controller.agentAutoRetryEnabled}
            disabled={!controller.agentEnabled}
            onChange={(e) => controller.setAgentAutoRetryEnabled(e.target.checked)}
          />
        </div>

        <div className="row g-3">
          <div className="col-md-4">
            <Form.Label>智能体上下文字符数</Form.Label>
            <Form.Control
              type="number"
              min={800}
              max={12000}
              value={controller.agentMaxContextChars}
              disabled={!controller.agentEnabled}
              onChange={(e) =>
                controller.setAgentMaxContextChars(Math.max(800, Math.min(12000, Number(e.target.value) || 800)))
              }
            />
          </div>
          <div className="col-md-4">
            <Form.Label>执行器工作线程数</Form.Label>
            <Form.Control
              type="number"
              min={1}
              max={8}
              value={controller.agentExecutorWorkers}
              disabled={!controller.agentEnabled || !controller.agentExecutorParallel}
              onChange={(e) =>
                controller.setAgentExecutorWorkers(Math.max(1, Math.min(8, Number(e.target.value) || 1)))
              }
            />
          </div>
          <div className="col-md-4">
            <Form.Label>最大自动重试次数</Form.Label>
            <Form.Control
              type="number"
              min={0}
              max={3}
              value={controller.agentMaxAutoRetries}
              disabled={!controller.agentEnabled || !controller.agentAutoRetryEnabled}
              onChange={(e) =>
                controller.setAgentMaxAutoRetries(Math.max(0, Math.min(3, Number(e.target.value) || 0)))
              }
            />
          </div>
          <div className="col-md-4">
            <Form.Label>重试策略</Form.Label>
            <Form.Select
              value={controller.agentRetryPolicy}
              disabled={!controller.agentEnabled || !controller.agentAutoRetryEnabled}
              onChange={(e) =>
                controller.setAgentRetryPolicy(e.target.value as 'conservative' | 'balanced' | 'aggressive')
              }
            >
              <option value="conservative">保守</option>
              <option value="balanced">均衡</option>
              <option value="aggressive">激进</option>
            </Form.Select>
          </div>
        </div>
      </Card.Body>
    </Card>
  );
}
