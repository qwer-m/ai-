import React from 'react';
import { Card, Badge, Button, Row, Col } from 'react-bootstrap';
import { FaCheckCircle, FaBug, FaRobot, FaClock } from 'react-icons/fa';

interface ReportDetailProps {
    execution: any;
    onReRun: () => void;
}

export const ReportDetail: React.FC<ReportDetailProps> = ({ execution, onReRun }) => {
    if (!execution) return null;

    const { 
        status, 
        screenshot_paths = [], 
        quality_score, 
        evaluation_result, 
        created_at,
        task_description
    } = execution;

    // Parse evaluation result if string
    let evalData: { raw?: string; [key: string]: unknown } = {};
    try {
        if (typeof evaluation_result === 'string') {
            // Try to extract JSON if embedded in markdown
            const jsonMatch = evaluation_result.match(/```json\n([\s\S]*?)\n```/);
            if (jsonMatch) {
                evalData = JSON.parse(jsonMatch[1]);
            } else {
                // Heuristic parsing
                evalData = { raw: evaluation_result };
            }
        } else {
            evalData = evaluation_result || {};
        }
    } catch (e) {
        evalData = { raw: evaluation_result };
    }

    return (
        <div className="h-100 overflow-auto p-3">
            {/* Header Summary */}
            <Card className="border-0 shadow-sm mb-4">
                <Card.Body>
                    <div className="d-flex justify-content-between align-items-start">
                        <div>
                            <h5 className="mb-1">执行报告 #{execution.id}</h5>
                            <p className="text-muted small mb-2">{task_description}</p>
                            <div className="d-flex gap-3 align-items-center small">
                                <span className="text-muted"><FaClock className="me-1"/>{new Date(created_at).toLocaleString()}</span>
                                <Badge bg={status === 'success' ? 'success' : 'danger'}>
                                    {status.toUpperCase()}
                                </Badge>
                            </div>
                        </div>
                        <div className="text-end">
                            <div className="display-6 fw-bold text-primary mb-0">
                                {quality_score ? quality_score.toFixed(1) : 'N/A'}
                            </div>
                            <div className="small text-muted">质量评分</div>
                            <Button size="sm" variant="outline-primary" className="mt-2" onClick={onReRun}>
                                重新运行脚本
                            </Button>
                        </div>
                    </div>
                </Card.Body>
            </Card>

            {/* Timeline Gallery */}
            <h6 className="mb-3 border-bottom pb-2">步骤时间轴</h6>
            <div className="d-flex overflow-auto pb-3 mb-4 custom-scrollbar gap-3">
                {screenshot_paths.map((path: string, idx: number) => (
                    <Card key={idx} className="border-0 shadow-sm flex-shrink-0" style={{width: '240px'}}>
                        <div className="position-relative" style={{height: '135px', overflow: 'hidden'}}>
                            <img 
                                src={`/api/screenshots/${execution.id}/${path.split(/[/\\]/).pop()}`} 
                                className="w-100 h-100" 
                                style={{objectFit: 'cover', cursor: 'pointer'}}
                                alt={`步骤 ${idx + 1}`}
                            />
                            <div className="position-absolute top-0 start-0 m-2">
                                <Badge bg="dark" className="opacity-75">步骤 {idx + 1}</Badge>
                            </div>
                        </div>
                    </Card>
                ))}
                {screenshot_paths.length === 0 && (
                    <div className="text-muted small p-4 w-100 text-center bg-light rounded">未捕获到截图</div>
                )}
            </div>

            {/* Analysis */}
            <Row>
                <Col md={6}>
                    <Card className="border-0 shadow-sm h-100">
                        <Card.Header className="bg-white fw-bold small py-2">
                            <FaRobot className="me-2 text-info"/> AI 评估
                        </Card.Header>
                        <Card.Body className="small">
                            {evalData.raw ? (
                                <div style={{whiteSpace: 'pre-wrap'}}>{evalData.raw}</div>
                            ) : (
                                <pre>{JSON.stringify(evalData, null, 2)}</pre>
                            )}
                        </Card.Body>
                    </Card>
                </Col>
                <Col md={6}>
                    <Card className="border-0 shadow-sm h-100">
                        <Card.Header className="bg-white fw-bold small py-2">
                            <FaBug className="me-2 text-danger"/> Defect Analysis
                        </Card.Header>
                        <Card.Body className="d-flex align-items-center justify-content-center text-muted small">
                            {status === 'failed' ? (
                                <p>Check logs for detailed error trace.</p>
                            ) : (
                                <div className="text-center">
                                    <FaCheckCircle className="text-success fs-1 mb-2 opacity-50"/>
                                    <p>No defects detected.</p>
                                </div>
                            )}
                        </Card.Body>
                    </Card>
                </Col>
            </Row>
        </div>
    );
};
