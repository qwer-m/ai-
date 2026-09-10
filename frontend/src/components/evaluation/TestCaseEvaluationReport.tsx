import {
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LineElement,
  LinearScale,
  PointElement,
  Title,
  Tooltip,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import { FaRobot } from 'react-icons/fa';
import type { EvaluationHistoryPoint, QualityReport } from './state/types';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend);

type Props = {
  evalResult: QualityReport;
  history: EvaluationHistoryPoint[];
};

export function TestCaseEvaluationReport({
  evalResult,
  history,
}: Props) {
  const {
    metrics,
    defectAnalysis,
    requirementBaseline,
    summary,
  } = evalResult;
  const generatedCoverageRate = requirementBaseline?.generated_coverage_rate;
  const modifiedCoverageRate = requirementBaseline?.modified_coverage_rate;
  const requirementPoints = requirementBaseline?.requirement_points || [];
  const aiRequirementGaps = requirementBaseline?.ai_requirement_gaps || [];
  const humanRequirementGaps = requirementBaseline?.human_requirement_gaps || [];
  const bothMissingPoints = requirementBaseline?.both_missing_points || [];
  const aiUnanchoredPoints = requirementBaseline?.ai_unanchored_points || [];
  const humanAddedValue = requirementBaseline?.human_added_value || [];
  return (
    <div className="evaluation-report-panel panel-card">
      <h6 className="border-bottom pb-2 mb-3 evaluation-report-title">质量评估报告</h6>

      <div className="mb-4 evaluation-chart-wrap evaluation-report-chart-wrap">
        <Line
          data={{
            labels: history.length > 0 ? history.map((h) => h.created_at) : ['Current'],
            datasets: [
              {
                label: 'Precision',
                data: history.length > 0 ? history.map((h) => h.precision) : [metrics.precision],
                borderColor: '#0d6efd',
                tension: 0.1,
                pointRadius: 3,
                borderWidth: 2,
                hoverBorderWidth: 4,
              },
              {
                label: 'Recall',
                data: history.length > 0 ? history.map((h) => h.recall) : [metrics.recall],
                borderColor: '#198754',
                tension: 0.1,
                pointRadius: 3,
                borderWidth: 2,
                hoverBorderWidth: 4,
              },
              {
                label: 'F1 分数',
                data: history.length > 0 ? history.map((h) => h.f1_score) : [metrics.f1_score],
                borderColor: '#6f42c1',
                tension: 0.1,
                pointRadius: 3,
                borderWidth: 2,
                hoverBorderWidth: 4,
              },
              {
                label: '相似度',
                data: history.length > 0 ? history.map((h) => h.semantic_similarity) : [metrics.semantic_similarity],
                borderColor: '#fd7e14',
                tension: 0.1,
                pointRadius: 3,
                borderWidth: 2,
                hoverBorderWidth: 4,
              },
            ],
          }}
          options={{
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
              mode: 'nearest',
              intersect: true,
              axis: 'x',
            },
            plugins: {
              legend: {
                display: true,
                position: 'top',
                labels: { boxWidth: 10, usePointStyle: true, padding: 10, font: { size: 10 } },
              },
              title: { display: true, text: '质量评估历史趋势' },
              tooltip: {
                mode: 'index',
                intersect: false,
              },
            },
            scales: {
              y: {
                type: 'linear',
                display: true,
                position: 'left',
                title: { display: true, text: '评分 (0-1)' },
                min: 0,
                max: 1,
                beginAtZero: true,
                grid: { drawOnChartArea: true },
              },
            },
          }}
        />
      </div>

      <div className="d-flex gap-2 mb-3 text-center evaluation-report-kpi-grid">
        <div className="p-2 bg-white border rounded flex-fill evaluation-report-kpi">
          <div className="fw-bold text-primary">{typeof metrics.precision === 'number' ? metrics.precision.toFixed(2) : '-'}</div>
          <div className="x-small text-muted">精准率</div>
        </div>
        <div className="p-2 bg-white border rounded flex-fill evaluation-report-kpi">
          <div className="fw-bold text-primary">{typeof metrics.recall === 'number' ? metrics.recall.toFixed(2) : '-'}</div>
          <div className="x-small text-muted">召回率</div>
        </div>
        <div className="p-2 bg-white border rounded flex-fill evaluation-report-kpi">
          <div className="fw-bold text-primary">{typeof metrics.f1_score === 'number' ? metrics.f1_score.toFixed(2) : '-'}</div>
          <div className="x-small text-muted">F1 分数</div>
        </div>
        <div className="p-2 bg-white border rounded flex-fill evaluation-report-kpi">
          <div className="fw-bold text-primary">
            {typeof metrics.semantic_similarity === 'number' ? metrics.semantic_similarity.toFixed(2) : '-'}
          </div>
          <div className="x-small text-muted">语义相似度</div>
        </div>
      </div>

      <div className="mb-2 evaluation-report-defect-head">
        <strong>缺陷归因分析:</strong>
      </div>

      {requirementBaseline ? (
        <div className="mb-3 p-2 border rounded bg-white evaluation-report-requirement-baseline">
          <div className="d-flex align-items-center justify-content-between gap-2 mb-2">
            <div>
              <strong className="small">需求基准评估</strong>
              <div className="x-small text-muted">以需求文档为锚点，同时检查 AI 生成用例和人工最终用例。</div>
            </div>
            <div className="d-flex gap-2 text-center">
              <div className="px-2 py-1 border rounded bg-light">
                <div className="fw-bold text-primary">{typeof generatedCoverageRate === 'number' ? generatedCoverageRate.toFixed(2) : '-'}</div>
                <div className="x-small text-muted">AI覆盖</div>
              </div>
              <div className="px-2 py-1 border rounded bg-light">
                <div className="fw-bold text-primary">{typeof modifiedCoverageRate === 'number' ? modifiedCoverageRate.toFixed(2) : '-'}</div>
                <div className="x-small text-muted">人工覆盖</div>
              </div>
            </div>
          </div>
          {requirementBaseline.summary ? <div className="small text-secondary mb-2">{requirementBaseline.summary}</div> : null}
          {requirementPoints.length ? (
            <div className="small text-muted mb-2">识别需求点：{requirementPoints.length} 个</div>
          ) : null}
          {humanAddedValue.length ? (
            <div className="mb-2 evaluation-report-defect-group">
              <span className="badge bg-success text-white me-2">人工增益</span>
              <ul className="mb-1 ps-3 mt-1 text-muted">
                {humanAddedValue.map((item, index) => <li key={index}>{item}</li>)}
              </ul>
            </div>
          ) : null}
          {aiRequirementGaps.length ? (
            <div className="mb-2 evaluation-report-defect-group">
              <span className="badge bg-warning text-dark me-2">AI需求遗漏</span>
              <ul className="mb-1 ps-3 mt-1 text-muted">
                {aiRequirementGaps.map((item, index) => <li key={index}>{item}</li>)}
              </ul>
            </div>
          ) : null}
          {humanRequirementGaps.length ? (
            <div className="mb-2 evaluation-report-defect-group">
              <span className="badge bg-secondary text-white me-2">人工需复核</span>
              <ul className="mb-1 ps-3 mt-1 text-muted">
                {humanRequirementGaps.map((item, index) => <li key={index}>{item}</li>)}
              </ul>
            </div>
          ) : null}
          {bothMissingPoints.length ? (
            <div className="mb-2 evaluation-report-defect-group">
              <span className="badge bg-dark text-white me-2">双方遗漏</span>
              <ul className="mb-1 ps-3 mt-1 text-muted">
                {bothMissingPoints.map((item, index) => <li key={index}>{item}</li>)}
              </ul>
            </div>
          ) : null}
          {aiUnanchoredPoints.length ? (
            <div className="mb-2 evaluation-report-defect-group">
              <span className="badge bg-danger text-white me-2">AI无需求依据</span>
              <ul className="mb-1 ps-3 mt-1 text-muted">
                {aiUnanchoredPoints.map((item, index) => <li key={index}>{item}</li>)}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}

      {defectAnalysis.missing_points?.length ? (
        <div className="mb-2 evaluation-report-defect-group">
          <span className="badge bg-warning text-dark me-2">遗漏点（召回损失）</span>
          <ul className="mb-1 ps-3 mt-1 text-muted">
            {defectAnalysis.missing_points.map((item, index) => <li key={index}>{item}</li>)}
          </ul>
        </div>
      ) : null}

      {defectAnalysis.hallucinations?.length ? (
        <div className="mb-2 evaluation-report-defect-group">
          <span className="badge bg-danger text-white me-2">幻觉/多余（精度损失）</span>
          <ul className="mb-1 ps-3 mt-1 text-muted">
            {defectAnalysis.hallucinations.map((item, index) => <li key={index}>{item}</li>)}
          </ul>
        </div>
      ) : null}

      {defectAnalysis.modifications?.length ? (
        <div className="mb-2 evaluation-report-defect-group">
          <span className="badge bg-info text-white me-2">逻辑修正</span>
          <ul className="mb-1 ps-3 mt-1 text-muted">
            {defectAnalysis.modifications.map((item, index) => <li key={index}>{item}</li>)}
          </ul>
        </div>
      ) : null}

      <div className="mt-3 pt-2 border-top text-secondary evaluation-report-summary">
        <strong>总结:</strong> {summary}
      </div>
      <div className="mt-2 text-end text-muted x-small">
        <FaRobot className="me-1" /> 缺陷归因分析由 AI 模型生成
      </div>
    </div>
  );
}
