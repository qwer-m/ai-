import { Button, Form } from 'react-bootstrap';
import type { AutomationEvaluationReport, LoadingType } from './state/types';

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
  uiEvalOutput: AutomationEvaluationReport | null;
  onEvaluateUi: () => void;
  apiEvalScript: string;
  setApiEvalScript: (v: string) => void;
  apiEvalSpec: string;
  setApiEvalSpec: (v: string) => void;
  apiEvalExec: string;
  setApiEvalExec: (v: string) => void;
  apiEvalOutput: AutomationEvaluationReport | null;
  onEvaluateApi: () => void;
};

const STATUS_LABELS = {
  success: { label: '成功', className: 'text-bg-success' },
  failed: { label: '失败', className: 'text-bg-danger' },
  unknown: { label: '未知', className: 'text-bg-secondary' },
} as const;

function ReportItems({ items, emptyText }: { items: string[]; emptyText: string }) {
  if (items.length === 0) return <div className="text-muted">{emptyText}</div>;
  return (
    <ul className="mb-0 ps-3">
      {items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}
    </ul>
  );
}

function AutomationEvaluationReportView({ report }: { report: AutomationEvaluationReport }) {
  const status = STATUS_LABELS[report.execution_status];
  const coverageRate = report.coverage.rate === null
    ? '无基准'
    : `${(report.coverage.rate * 100).toFixed(1)}%`;

  return (
    <div className="mt-3 alert alert-light border small automation-eval-output">
      <div className="d-flex align-items-center justify-content-between border-bottom pb-2 mb-3">
        <h6 className="mb-0">Agent 评测报告</h6>
        <span className={`badge ${status.className}`}>{status.label}</span>
      </div>

      <div className="row g-0 border-bottom pb-3 mb-3 text-center">
        <div className="col-6 border-end">
          <div className="fw-bold fs-5">{report.overall_score.toFixed(1)}</div>
          <div className="x-small text-muted">总分 / 10</div>
        </div>
        <div className="col-6">
          <div className="fw-bold fs-5">{coverageRate}</div>
          <div className="x-small text-muted">覆盖率</div>
        </div>
      </div>

      <section className="mb-3">
        <div className="fw-semibold mb-1">总结</div>
        <div>{report.summary}</div>
      </section>

      <section className="mb-3">
        <div className="fw-semibold mb-2">评测维度</div>
        <div className="table-responsive">
          <table className="table table-sm align-middle mb-0">
            <thead>
              <tr>
                <th scope="col">维度</th>
                <th scope="col" className="text-nowrap">得分</th>
                <th scope="col">分析</th>
              </tr>
            </thead>
            <tbody>
              {report.criteria.map((criterion, index) => (
                <tr key={`${criterion.key}-${index}`}>
                  <th scope="row" className="text-nowrap">{criterion.name}</th>
                  <td>{criterion.score.toFixed(1)}</td>
                  <td>{criterion.analysis}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="mb-3">
        <div className="fw-semibold mb-1">覆盖分析</div>
        {report.coverage.explanation ? <div className="mb-2">{report.coverage.explanation}</div> : null}
        <div className="row g-3">
          <div className="col-12 col-lg-6">
            <div className="x-small text-muted mb-1">已覆盖</div>
            <ReportItems items={report.coverage.covered_items} emptyText="暂无已覆盖项" />
          </div>
          <div className="col-12 col-lg-6">
            <div className="x-small text-muted mb-1">未覆盖</div>
            <ReportItems items={report.coverage.missing_items} emptyText="暂无未覆盖项" />
          </div>
        </div>
      </section>

      <div className="row g-3">
        <section className="col-12 col-lg-6">
          <div className="fw-semibold mb-1">风险</div>
          <ReportItems items={report.risks} emptyText="暂无已识别风险" />
        </section>
        <section className="col-12 col-lg-6">
          <div className="fw-semibold mb-1">建议</div>
          <ReportItems items={report.recommendations} emptyText="暂无改进建议" />
        </section>
      </div>
    </div>
  );
}

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
        <div className="bento-card col-span-12 md:col-span-6 p-4 d-flex flex-column ui-section-card automation-eval-card panel-card control-grid-lr">
          <Form.Group className="mb-3 control-field">
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
          <Form.Group className="mb-3 control-field">
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
          <Form.Group className="mb-3 control-field">
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
          <Button className="btn-pro-primary w-100 mt-auto panel-card-primary-action" disabled={loading === 'ui'} onClick={onEvaluateUi}>
            {loading === 'ui' ? '评估中...' : '开始评估'}
          </Button>
          {uiEvalOutput ? <AutomationEvaluationReportView report={uiEvalOutput} /> : null}
        </div>
      ) : null}

      {showApi ? (
        <div className="bento-card col-span-12 md:col-span-6 p-4 d-flex flex-column ui-section-card automation-eval-card panel-card control-grid-lr">
          <Form.Group className="mb-3 control-field">
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
          <Form.Group className="mb-3 control-field">
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
          <Form.Group className="mb-3 control-field">
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

          <Button className="btn-pro-primary w-100 mt-auto panel-card-primary-action" disabled={loading === 'api'} onClick={onEvaluateApi}>
            {loading === 'api' ? '多维评估中...' : '开始评估'}
          </Button>
          {apiEvalOutput ? <AutomationEvaluationReportView report={apiEvalOutput} /> : null}
        </div>
      ) : null}
    </>
  );
}
