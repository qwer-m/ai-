import { Button } from 'react-bootstrap';
import { FaCheckDouble, FaClipboardCheck, FaDownload, FaRobot } from 'react-icons/fa';
import type { EvaluationRunRecord } from './state/types';

type Props = {
  latestRun: EvaluationRunRecord | null;
  onExportHistory: () => void;
};

const formatTime = (value: string | null | undefined) => (
  value ? new Date(value).toLocaleString() : '-'
);

export function EvaluationOverviewPanel({ latestRun, onExportHistory }: Props) {
  return (
    <>
      <div className="bento-card col-span-12 p-4 d-flex align-items-center justify-content-between glass-panel evaluation-overview-card">
        <h4 className="text-gradient mb-0 d-flex align-items-center gap-2">
          <FaClipboardCheck className="text-primary" />
          质量评估与召回
        </h4>
      </div>

      <div className="bento-card col-span-12 p-0 border-0 bg-transparent evaluation-overview-grid">
        <div className="d-flex flex-column flex-md-row gap-3">
          <div className="bento-card p-4 d-flex flex-column hover-lift flex-fill ui-section-card">
            <div className="d-flex align-items-center gap-2 mb-4 text-secondary">
              <FaRobot />
              <span className="fw-bold">最新 Agent Run</span>
            </div>
            <div className="grid grid-cols-2 gap-3 small">
              <div className="ui-kpi-card"><span className="ui-kpi-title d-block">Run ID</span> {latestRun?.run_id ?? '-'}</div>
              <div className="ui-kpi-card"><span className="ui-kpi-title d-block">状态</span> {latestRun?.status || '-'}</div>
              <div className="ui-kpi-card"><span className="ui-kpi-title d-block">当前节点</span> {latestRun?.current_node_key || '-'}</div>
              <div className="ui-kpi-card"><span className="ui-kpi-title d-block">创建时间</span> {formatTime(latestRun?.created_at)}</div>
              <div className="ui-kpi-card"><span className="ui-kpi-title d-block">开始时间</span> {formatTime(latestRun?.started_at)}</div>
              <div className="ui-kpi-card"><span className="ui-kpi-title d-block">完成时间</span> {formatTime(latestRun?.finished_at)}</div>
            </div>
          </div>

          <div className="bento-card p-4 d-flex flex-column hover-lift flex-fill ui-section-card">
            <div className="d-flex justify-content-between align-items-center mb-4">
              <div className="d-flex align-items-center gap-2 text-secondary">
                <FaCheckDouble />
                <span className="fw-bold">最新测试用例产物</span>
              </div>
              <Button
                variant="link"
                size="sm"
                className="p-0 text-decoration-none d-flex align-items-center gap-1"
                onClick={onExportHistory}
                disabled={!latestRun}
              >
                <FaDownload size={12} /> 导出
              </Button>
            </div>
            <div className="grid grid-cols-2 gap-3 small">
              <div className="ui-kpi-card"><span className="ui-kpi-title d-block">用例数量</span> {latestRun?.case_count ?? '-'}</div>
              <div className="ui-kpi-card"><span className="ui-kpi-title d-block">质量评估</span> {latestRun ? (latestRun.has_evaluation ? '已生成' : '未生成') : '-'}</div>
              <div className="ui-kpi-card"><span className="ui-kpi-title d-block">项目 ID</span> {latestRun?.project_id ?? '-'}</div>
              <div className="ui-kpi-card"><span className="ui-kpi-title d-block">父 Run</span> {latestRun?.parent_run_id ?? '-'}</div>
              <div className="ui-kpi-card grid-column-span-2">
                <span className="ui-kpi-title d-block">需求</span>
                <span>{latestRun?.requirement_text || '-'}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
