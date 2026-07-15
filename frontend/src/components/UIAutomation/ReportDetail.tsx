import React from 'react';
import { Card, Badge, Button, Row, Col } from 'react-bootstrap';
import { FaCheckCircle, FaBug, FaRobot, FaClock } from 'react-icons/fa';

interface ReportDetailProps {
    execution: any;
    onReRun: () => void;
}

export const ReportDetail: React.FC<ReportDetailProps> = ({ execution, onReRun }) => {
    if (!execution) {
        return null;
    }

    const { status, screenshot_paths = [], quality_score, evaluation_result, created_at, task_description } = execution;

    let evalData: { raw?: string; [key: string]: unknown } = {};
    try {
        if (typeof evaluation_result === 'string') {
            const jsonMatch = evaluation_result.match(/```json\n([\s\S]*?)\n```/);
            if (jsonMatch) {
                evalData = JSON.parse(jsonMatch[1]);
            } else {
                evalData = { raw: evaluation_result };
            }
        } else {
            evalData = evaluation_result || {};
        }
    } catch {
        evalData = { raw: evaluation_result };
    }

    return (
        <div className="ui-automation-report-detail h-100 overflow-auto p-3">
            <Card className="ui-automation-report-summary border-0 mb-4">
                <Card.Body>
                    <div className="d-flex justify-content-between align-items-start gap-3">
                        <div>
                            <h5 className="mb-1">执行报告 #{execution.id}</h5>
                            <p className="text-muted small mb-2">{task_description}</p>
                            <div className="d-flex gap-3 align-items-center small">
                                <span className="text-muted">
                                    <FaClock className="me-1" />
                                    {new Date(created_at).toLocaleString()}
                                </span>
                                <Badge bg={status === 'success' ? 'success' : 'danger'}>{String(status).toUpperCase()}</Badge>
                            </div>
                        </div>
                        <div className="text-end">
                            <div className="display-6 fw-bold text-primary mb-0">{quality_score ? quality_score.toFixed(1) : 'N/A'}</div>
                            <div className="small text-muted">质量评分</div>
                            <Button size="sm" variant="outline-primary" className="mt-2" onClick={onReRun}>
                                重新运行脚本
                            </Button>
                        </div>
                    </div>
                </Card.Body>
            </Card>

            <h6 className="mb-3 border-bottom pb-2">截图时间线</h6>
            <div className="d-flex overflow-auto pb-3 mb-4 custom-scrollbar gap-3">
                {screenshot_paths.map((path: string, idx: number) => (
                    <Card key={idx} className="ui-automation-report-shot border-0 flex-shrink-0">
                        <div className="position-relative ui-automation-report-shot-image-wrap">
                            <img
                                src={`/api/ui-automation/screenshots/${execution.id}/${path.split(/[/\\]/).pop()}`}
                                className="w-100 h-100 ui-automation-report-shot-image"
                                alt={`步骤 ${idx + 1}`}
                            />
                            <div className="position-absolute top-0 start-0 m-2">
                                <Badge bg="dark" className="opacity-75">
                                    步骤 {idx + 1}
                                </Badge>
                            </div>
                        </div>
                    </Card>
                ))}
                {screenshot_paths.length === 0 ? <div className="text-muted small p-4 w-100 text-center ui-automation-report-empty">未捕获到截图</div> : null}
            </div>

            <Row>
                <Col md={6}>
                    <Card className="ui-automation-report-analysis border-0 h-100">
                        <Card.Header className="bg-white fw-bold small py-2">
                            <FaRobot className="me-2 text-info" /> AI 评估
                        </Card.Header>
                        <Card.Body className="small">
                            {evalData.raw ? <div className="ui-automation-prewrap">{evalData.raw}</div> : <pre className="mb-0">{JSON.stringify(evalData, null, 2)}</pre>}
                        </Card.Body>
                    </Card>
                </Col>
                <Col md={6}>
                    <Card className="ui-automation-report-analysis border-0 h-100">
                        <Card.Header className="bg-white fw-bold small py-2">
                            <FaBug className="me-2 text-danger" /> 缺陷分析
                        </Card.Header>
                        <Card.Body className="d-flex align-items-center justify-content-center text-muted small">
                            {status === 'failed' ? (
                                <p className="mb-0">请查看执行日志定位失败原因。</p>
                            ) : (
                                <div className="text-center">
                                    <FaCheckCircle className="text-success fs-1 mb-2 opacity-50" />
                                    <p className="mb-0">未发现明显缺陷。</p>
                                </div>
                            )}
                        </Card.Body>
                    </Card>
                </Col>
            </Row>
        </div>
    );
};
