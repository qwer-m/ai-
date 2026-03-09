import { Accordion, Badge, Card, Col, Row } from "react-bootstrap";
import { FaBug, FaCheckCircle } from "react-icons/fa";
import { ErrorTrace } from "./ErrorTrace";
import type { TestResult } from "./types";

type StructuredReportDashboardProps = {
  report: NonNullable<TestResult["structured_report"]>;
};

export function StructuredReportDashboard({ report }: StructuredReportDashboardProps) {
  // 汇总卡片中的通过率，避免分母为 0。
  const passRate = report.total > 0 ? (report.passed / report.total) * 100 : 0;

  return (
    <div className="d-flex flex-column gap-3 animate-fade-in p-3">
      <Row className="g-3">
        <Col md={3} xs={6}>
          <Card className="text-center h-100 border-success shadow-sm">
            <Card.Body className="d-flex flex-column justify-content-center py-2">
              <h3 className="text-success mb-0" style={{ fontWeight: 600 }}>
                {Math.round(passRate)}%
              </h3>
              <div className="small text-muted">通过率</div>
            </Card.Body>
          </Card>
        </Col>
        <Col md={3} xs={6}>
          <Card className="text-center h-100 border-primary shadow-sm">
            <Card.Body className="d-flex flex-column justify-content-center py-2">
              <h3 className="text-primary mb-0" style={{ fontWeight: 600 }}>
                {report.total}
              </h3>
              <div className="small text-muted">总用例</div>
            </Card.Body>
          </Card>
        </Col>
        <Col md={3} xs={6}>
          <Card
            className={`text-center h-100 shadow-sm ${report.failed > 0 ? "border-danger bg-danger bg-opacity-10" : "border-light"}`}
          >
            <Card.Body className="d-flex flex-column justify-content-center py-2">
              <h3
                className={`mb-0 ${report.failed > 0 ? "text-danger" : "text-secondary"}`}
                style={{ fontWeight: 600 }}
              >
                {report.failed}
              </h3>
              <div className="small text-muted">失败</div>
            </Card.Body>
          </Card>
        </Col>
        <Col md={3} xs={6}>
          <Card className="text-center h-100 border-light shadow-sm">
            <Card.Body className="d-flex flex-column justify-content-center py-2">
              <h5 className="text-secondary mb-0">{report.time.toFixed(2)}s</h5>
              <div className="small text-muted">耗时</div>
            </Card.Body>
          </Card>
        </Col>
      </Row>

      {report.failures.length > 0 ? (
        <Card className="border-danger shadow-sm mt-2">
          <Card.Header className="bg-danger text-white d-flex align-items-center gap-2 py-2">
            <FaBug />
            失败用例透视
          </Card.Header>
          <Accordion flush alwaysOpen>
            {report.failures.map((fail, idx) => (
              <Accordion.Item eventKey={String(idx)} key={idx}>
                <Accordion.Header>
                  <div className="d-flex align-items-center gap-2">
                    <Badge bg="danger">Failed</Badge>
                    <span className="font-monospace text-truncate" style={{ maxWidth: "300px" }}>
                      {fail.name}
                    </span>
                  </div>
                </Accordion.Header>
                <Accordion.Body className="bg-light p-2">
                  <div className="mb-2 small">
                    <strong className="text-secondary">错误原因：</strong>
                    <span className="text-danger ms-2" style={{ fontWeight: 500 }}>
                      {fail.message}
                    </span>
                  </div>
                  <ErrorTrace details={fail.details} />
                </Accordion.Body>
              </Accordion.Item>
            ))}
          </Accordion>
        </Card>
      ) : (
        <div className="alert alert-success d-flex align-items-center mt-2">
          <FaCheckCircle className="me-2" size={20} />
          <div>
            <span style={{ fontWeight: 600 }}>测试通过！</span>
            所有 {report.total} 个用例均执行成功。
          </div>
        </div>
      )}
    </div>
  );
}
