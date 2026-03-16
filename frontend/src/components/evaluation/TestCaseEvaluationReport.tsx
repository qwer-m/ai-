import type { ChangeEvent, ClipboardEvent } from 'react';
import { Button, OverlayTrigger } from 'react-bootstrap';
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
import { FaPlus, FaRobot } from 'react-icons/fa';
import { parseQualityReport } from './evaluationService';
import { SupplementKnowledgePopover } from './SupplementKnowledgePopover';
import type { DefectAnalysis } from './types';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend);

type Props = {
  evalResult: string;
  history: any[];
  showSupplement: boolean;
  setShowSupplement: (next: boolean) => void;
  supplementText: string;
  setSupplementText: (v: string) => void;
  supplementImages: File[];
  setSupplementImages: (files: File[]) => void;
  savedDocId: number | null;
  lastSavedContent: string;
  handleSupplementPaste: (e: ClipboardEvent<HTMLTextAreaElement>) => void;
  handleSupplementFilesChange: (e: ChangeEvent<HTMLInputElement>) => void;
  handleSaveKnowledge: (defectAnalysis: DefectAnalysis) => Promise<void>;
};

export function TestCaseEvaluationReport({
  evalResult,
  history,
  showSupplement,
  setShowSupplement,
  supplementText,
  setSupplementText,
  supplementImages,
  setSupplementImages,
  savedDocId,
  lastSavedContent,
  handleSupplementPaste,
  handleSupplementFilesChange,
  handleSaveKnowledge,
}: Props) {
  const report = parseQualityReport(evalResult);
  if (!report) {
    return <div style={{ whiteSpace: 'pre-wrap' }}>{evalResult}</div>;
  }

  const { metrics, defectAnalysis, summary } = report;

  return (
    <div>
      <h6 className="border-bottom pb-2 mb-3">质量评估报告</h6>

      <div className="mb-4" style={{ height: '300px' }}>
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

      <div className="d-flex gap-2 mb-3 text-center">
        <div className="p-2 bg-white border rounded flex-fill">
          <div className="fw-bold text-primary">{typeof metrics.precision === 'number' ? metrics.precision.toFixed(2) : '-'}</div>
          <div className="x-small text-muted">精准率</div>
        </div>
        <div className="p-2 bg-white border rounded flex-fill">
          <div className="fw-bold text-primary">{typeof metrics.recall === 'number' ? metrics.recall.toFixed(2) : '-'}</div>
          <div className="x-small text-muted">召回率</div>
        </div>
        <div className="p-2 bg-white border rounded flex-fill">
          <div className="fw-bold text-primary">{typeof metrics.f1_score === 'number' ? metrics.f1_score.toFixed(2) : '-'}</div>
          <div className="x-small text-muted">F1 分数</div>
        </div>
        <div className="p-2 bg-white border rounded flex-fill">
          <div className="fw-bold text-primary">
            {typeof metrics.semantic_similarity === 'number' ? metrics.semantic_similarity.toFixed(2) : '-'}
          </div>
          <div className="x-small text-muted">语义相似度</div>
        </div>
      </div>

      <div className="d-flex align-items-center justify-content-between mb-2">
        <strong>缺陷归因分析:</strong>
        <OverlayTrigger
          trigger="click"
          placement="left"
          show={showSupplement}
          onToggle={(next) => setShowSupplement(Boolean(next))}
          rootClose
          overlay={
            <SupplementKnowledgePopover
              supplementText={supplementText}
              setSupplementText={setSupplementText}
              supplementImages={supplementImages}
              onPaste={handleSupplementPaste}
              onFilesChange={handleSupplementFilesChange}
              onCancel={() => {
                setShowSupplement(false);
                setSupplementText('');
                setSupplementImages([]);
              }}
              onConfirm={() => {
                void handleSaveKnowledge(defectAnalysis);
              }}
              disableConfirm={
                (!supplementText.trim() && supplementImages.length === 0)
                || (savedDocId !== null && supplementText === lastSavedContent && supplementImages.length === 0)
              }
            />
          }
        >
          <Button variant="outline-secondary" size="sm" className="py-0 px-2" onClick={() => setShowSupplement(!showSupplement)}>
            <FaPlus className="me-1" /> 用户补充描述
          </Button>
        </OverlayTrigger>
      </div>

      {defectAnalysis.missing_points?.length ? (
        <div className="mb-2">
          <span className="badge bg-warning text-dark me-2">遗漏点（召回损失）</span>
          <ul className="mb-1 ps-3 mt-1 text-muted">
            {defectAnalysis.missing_points.map((item, index) => <li key={index}>{item}</li>)}
          </ul>
        </div>
      ) : null}

      {defectAnalysis.hallucinations?.length ? (
        <div className="mb-2">
          <span className="badge bg-danger text-white me-2">幻觉/多余（精度损失）</span>
          <ul className="mb-1 ps-3 mt-1 text-muted">
            {defectAnalysis.hallucinations.map((item, index) => <li key={index}>{item}</li>)}
          </ul>
        </div>
      ) : null}

      {defectAnalysis.modifications?.length ? (
        <div className="mb-2">
          <span className="badge bg-info text-white me-2">逻辑修正</span>
          <ul className="mb-1 ps-3 mt-1 text-muted">
            {defectAnalysis.modifications.map((item, index) => <li key={index}>{item}</li>)}
          </ul>
        </div>
      ) : null}

      <div className="mt-3 pt-2 border-top text-secondary">
        <strong>总结:</strong> {summary}
      </div>
      <div className="mt-2 text-end text-muted x-small">
        <FaRobot className="me-1" /> 缺陷归因分析由 AI 模型生成
      </div>
    </div>
  );
}
