import { Button } from 'react-bootstrap';
import { FaBug, FaCheckDouble, FaClipboardCheck, FaDownload } from 'react-icons/fa';

type Props = {
  diag: any;
  qm: any;
  onExportHistory: () => void;
};

export function EvaluationOverviewPanel({ diag, qm, onExportHistory }: Props) {
  return (
    <>
      <div className="bento-card col-span-12 p-4 d-flex align-items-center justify-content-between glass-panel">
        <h4 className="text-gradient mb-0 d-flex align-items-center gap-2">
          <FaClipboardCheck className="text-primary" />
          质量评估与召回
        </h4>
      </div>

      <div className="bento-card col-span-12 p-0 border-0 bg-transparent">
        <div className="d-flex flex-column flex-md-row gap-3">
          <div className="bento-card p-4 d-flex flex-column hover-lift flex-fill">
            <div className="d-flex align-items-center gap-2 mb-4 text-secondary">
              <FaBug />
              <span className="fw-bold">最新生成诊断</span>
            </div>
            <div className="grid grid-cols-2 gap-3 small">
              <div className="p-2 bg-light rounded"><span className="text-muted d-block">模式</span> {String(diag?.mode ?? '-')}</div>
              <div className="p-2 bg-light rounded"><span className="text-muted d-block">类型</span> {String(diag?.doc_type ?? '-')}</div>
              <div className="p-2 bg-light rounded"><span className="text-muted d-block">压缩</span> {String(diag?.compress ?? '-')}</div>
              <div className="p-2 bg-light rounded"><span className="text-muted d-block">预期数量</span> {String(diag?.expected_count ?? '-')}</div>
              <div className="p-2 bg-light rounded"><span className="text-muted d-block">生成数量</span> {String(diag?.generated_count ?? '-')}</div>
              <div className="p-2 bg-light rounded"><span className="text-muted d-block">模型</span> {String(diag?.model ?? '-')}</div>
            </div>
          </div>

          <div className="bento-card p-4 d-flex flex-column hover-lift flex-fill">
            <div className="d-flex justify-content-between align-items-center mb-4">
              <div className="d-flex align-items-center gap-2 text-secondary">
                <FaCheckDouble />
                <span className="fw-bold">最新质量指标</span>
              </div>
              <Button
                variant="link"
                size="sm"
                className="p-0 text-decoration-none d-flex align-items-center gap-1"
                onClick={onExportHistory}
              >
                <FaDownload size={12} /> 导出
              </Button>
            </div>
            <div className="grid grid-cols-3 gap-3 small">
              <div className="p-2 bg-success bg-opacity-10 text-success rounded text-center">
                <div className="fw-bold fs-5">{String(qm?.positive ?? '-')}</div>
                <div className="small opacity-75">正向</div>
              </div>
              <div className="p-2 bg-danger bg-opacity-10 text-danger rounded text-center">
                <div className="fw-bold fs-5">{String(qm?.negative ?? '-')}</div>
                <div className="small opacity-75">负向</div>
              </div>
              <div className="p-2 bg-warning bg-opacity-10 text-warning rounded text-center">
                <div className="fw-bold fs-5">{String(qm?.edge ?? '-')}</div>
                <div className="small opacity-75">边界</div>
              </div>
              <div className="p-2 bg-info bg-opacity-10 text-info rounded text-center">
                <div className="fw-bold fs-5">{String(qm?.avg_steps ?? '-')}</div>
                <div className="small opacity-75">平均步骤</div>
              </div>
              <div className="p-2 bg-secondary bg-opacity-10 text-secondary rounded text-center">
                <div className="fw-bold fs-5">{String(qm?.pending ?? '-')}</div>
                <div className="small opacity-75">待确认</div>
              </div>
              <div className="p-2 bg-primary bg-opacity-10 text-primary rounded text-center">
                <div className="fw-bold fs-5">{String(qm?.generated_count ?? '-')}</div>
                <div className="small opacity-75">生成总数</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
