import { Button, Form } from 'react-bootstrap';
import { FaNetworkWired, FaRobot } from 'react-icons/fa';
import { parseApiReport } from './state/evaluationService';
import type { LoadingType } from './state/types';

type Props = {
  showUi: boolean;
  showApi: boolean;
  loading: LoadingType;
  uiEvalScript: string;
  setUiEvalScript: (v: string) => void;
  uiEvalJourney: string;
  setUiEvalJourney: (v: string) => void;
  uiEvalExec: string;
  setUiEvalExec: (v: string) => void;
  uiEvalOutput: string | null;
  onEvaluateUi: () => void;
  apiEvalScript: string;
  setApiEvalScript: (v: string) => void;
  apiEvalSpec: string;
  setApiEvalSpec: (v: string) => void;
  apiEvalExec: string;
  setApiEvalExec: (v: string) => void;
  apiEvalOutput: string | null;
  onEvaluateApi: () => void;
};

export function AutomationEvaluationPanels({
  showUi,
  showApi,
  loading,
  uiEvalScript,
  setUiEvalScript,
  uiEvalJourney,
  setUiEvalJourney,
  uiEvalExec,
  setUiEvalExec,
  uiEvalOutput,
  onEvaluateUi,
  apiEvalScript,
  setApiEvalScript,
  apiEvalSpec,
  setApiEvalSpec,
  apiEvalExec,
  setApiEvalExec,
  apiEvalOutput,
  onEvaluateApi,
}: Props) {
  return (
    <>
      {showUi ? (
        <div className="bento-card col-span-12 md:col-span-6 p-4 d-flex flex-column ui-section-card automation-eval-card">
          <div className="d-flex align-items-center gap-2 mb-3 text-secondary">
            <FaRobot />
            <span className="fw-bold">UI 自动化评估</span>
          </div>
          <Form.Group className="mb-3">
            <Form.Label className="small text-muted">UI 自动化脚本</Form.Label>
            <Form.Control
              as="textarea"
              rows={3}
              className="input-pro bg-light"
              value={uiEvalScript}
              onChange={(e) => setUiEvalScript(e.target.value)}
              placeholder="Python Playwright/Selenium 脚本..."
            />
          </Form.Group>
          <Form.Group className="mb-3">
            <Form.Label className="small text-muted">用户旅程图 (JSON) - 黄金标准</Form.Label>
            <Form.Control
              as="textarea"
              rows={3}
              className="input-pro bg-light"
              value={uiEvalJourney}
              onChange={(e) => setUiEvalJourney(e.target.value)}
              placeholder={`{"user_journey": [{"step": "Login", "action": "click('#login')"}]}`}
            />
            <Form.Text className="text-muted x-small">
              评估 AI 生成脚本是否覆盖关键用户旅程（如登录、支付）。
            </Form.Text>
          </Form.Group>
          <Form.Group className="mb-3">
            <Form.Label className="small text-muted">执行结果</Form.Label>
            <Form.Control
              as="textarea"
              rows={3}
              className="input-pro bg-light"
              value={uiEvalExec}
              onChange={(e) => setUiEvalExec(e.target.value)}
              placeholder="执行日志或输出..."
            />
          </Form.Group>
          <Button className="btn-pro-primary w-100 mt-auto" disabled={loading === 'ui'} onClick={onEvaluateUi}>
            {loading === 'ui' ? '评估中...' : '开始评估'}
          </Button>
          {uiEvalOutput ? (
            <div className="mt-3 alert alert-light border small automation-eval-output automation-eval-prewrap">
              {uiEvalOutput}
            </div>
          ) : null}
        </div>
      ) : null}

      {showApi ? (
        <div className="bento-card col-span-12 md:col-span-6 p-4 d-flex flex-column ui-section-card automation-eval-card">
          <div className="d-flex align-items-center gap-2 mb-3 text-secondary">
            <FaNetworkWired />
            <span className="fw-bold">接口自动化评估（AI 响应评估）</span>
          </div>
          <Form.Group className="mb-3">
            <Form.Label className="small text-muted">API 测试脚本</Form.Label>
            <Form.Control
              as="textarea"
              rows={3}
              className="input-pro bg-light"
              value={apiEvalScript}
              onChange={(e) => setApiEvalScript(e.target.value)}
              placeholder="Pytest 脚本..."
            />
          </Form.Group>
          <Form.Group className="mb-3">
            <Form.Label className="small text-muted">OpenAPI 规范（Swagger）- 黄金标准</Form.Label>
            <Form.Control
              as="textarea"
              rows={3}
              className="input-pro bg-light"
              value={apiEvalSpec}
              onChange={(e) => setApiEvalSpec(e.target.value)}
              placeholder="请输入 OpenAPI/Swagger JSON 或 YAML 内容..."
            />
            <Form.Text className="text-muted x-small">
              用于评估 AI 脚本的接口覆盖率与参数正确性。
            </Form.Text>
          </Form.Group>
          <Form.Group className="mb-3">
            <Form.Label className="small text-muted">执行结果</Form.Label>
            <Form.Control
              as="textarea"
              rows={3}
              className="input-pro bg-light"
              value={apiEvalExec}
              onChange={(e) => setApiEvalExec(e.target.value)}
              placeholder="执行日志..."
            />
          </Form.Group>

          <div className="d-flex gap-2 mb-3">
            <div className="form-check form-switch">
              <input className="form-check-input" type="checkbox" id="checkSimilarity" defaultChecked />
              <label className="form-check-label small" htmlFor="checkSimilarity">语义相似度</label>
            </div>
            <div className="form-check form-switch">
              <input className="form-check-input" type="checkbox" id="checkLLMJudge" defaultChecked />
              <label className="form-check-label small" htmlFor="checkLLMJudge">LLM 评审打分</label>
            </div>
            <div className="form-check form-switch">
              <input className="form-check-input" type="checkbox" id="checkCost" />
              <label className="form-check-label small" htmlFor="checkCost">成本/性能分析</label>
            </div>
          </div>

          <Button className="btn-pro-primary w-100 mt-auto" disabled={loading === 'api'} onClick={onEvaluateApi}>
            {loading === 'api' ? '多维评估中...' : '开始评估'}
          </Button>
          {apiEvalOutput ? (
            <div className="mt-3 alert alert-light border small automation-eval-output">
              {(() => {
                const report = parseApiReport(apiEvalOutput);
                if (!report) return <div className="automation-eval-prewrap">{apiEvalOutput}</div>;
                return (
                  <div>
                    <h6 className="border-bottom pb-2 mb-2">评估报告</h6>
                    <div className="row g-2 mb-3">
                      <div className="col-4">
                        <div className="p-2 bg-white border rounded text-center">
                          <div className="fw-bold text-primary">{report.similarity ?? '-'}</div>
                          <div className="x-small text-muted">语义相似度</div>
                        </div>
                      </div>
                      <div className="col-4">
                        <div className="p-2 bg-white border rounded text-center">
                          <div className="fw-bold text-success">{report.score ?? '-'}</div>
                          <div className="x-small text-muted">LLM 评分</div>
                        </div>
                      </div>
                      <div className="col-4">
                        <div className="p-2 bg-white border rounded text-center">
                          <div className="fw-bold text-info">{report.coverage ?? '-'}%</div>
                          <div className="x-small text-muted">API 覆盖率</div>
                        </div>
                      </div>
                    </div>
                    <div><strong>分析:</strong> {report.analysis}</div>
                  </div>
                );
              })()}
            </div>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
