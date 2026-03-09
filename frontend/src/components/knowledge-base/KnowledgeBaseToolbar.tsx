import { Button, Card, Col, Form, InputGroup, Row, Spinner } from "react-bootstrap";
import { FaSearch, FaWifi } from "react-icons/fa";

type KnowledgeBaseToolbarProps = {
  isOnline: boolean;
  docType: string;
  setDocType: (value: string) => void;
  uploading: boolean;
  projectId: number | null;
  onUpload: () => void;
  onFileChange: (file: File | null) => void;
  searchTerm: string;
  setSearchTerm: (value: string) => void;
  filterDocType: string;
  setFilterDocType: (value: string) => void;
  startDate: string;
  setStartDate: (value: string) => void;
  endDate: string;
  setEndDate: (value: string) => void;
  onSearch: () => void;
};

export function KnowledgeBaseToolbar({
  isOnline,
  docType,
  setDocType,
  uploading,
  projectId,
  onUpload,
  onFileChange,
  searchTerm,
  setSearchTerm,
  filterDocType,
  setFilterDocType,
  startDate,
  setStartDate,
  endDate,
  setEndDate,
  onSearch,
}: KnowledgeBaseToolbarProps) {
  return (
    <Card className="border-0 shadow-sm search-card">
      <Card.Body className="p-3">
        <Row className="g-3 align-items-end">
          <Col md={3}>
            <Form.Label className="small fw-bold text-secondary">
              上传文档 {isOnline ? "" : "(离线)"}
            </Form.Label>
            <InputGroup size="sm">
              <Form.Control
                type="file"
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  onFileChange(e.target.files?.[0] ?? null)
                }
                disabled={!isOnline}
              />
            </InputGroup>
          </Col>

          <Col md={2}>
            <Form.Label className="small fw-bold text-secondary">类型</Form.Label>
            <Form.Select
              size="sm"
              value={docType}
              onChange={(e) => setDocType(e.target.value)}
              disabled={!isOnline}
            >
              <option value="requirement">需求文档</option>
              <option value="test_case">测试用例</option>
              <option value="prototype">原型图</option>
              <option value="product_requirement">产品需求</option>
              <option value="incomplete">残缺文档</option>
            </Form.Select>
          </Col>

          <Col md={1}>
            <Button
              variant="primary"
              size="sm"
              className="w-100"
              onClick={onUpload}
              disabled={uploading || !projectId || !isOnline}
            >
              {uploading ? <Spinner size="sm" animation="border" /> : "上传"}
            </Button>
          </Col>

          <Col className="border-start ps-4">
            <Row className="g-2">
              <Col md={4}>
                <Form.Label className="small fw-bold text-secondary">关键词</Form.Label>
                <InputGroup size="sm">
                  <InputGroup.Text>
                    <FaSearch />
                  </InputGroup.Text>
                  <Form.Control
                    placeholder="搜索文件名..."
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                  />
                </InputGroup>
              </Col>

              <Col md={2} className="d-flex align-items-end">
                <Form.Select
                  size="sm"
                  value={filterDocType}
                  onChange={(e) => setFilterDocType(e.target.value)}
                  aria-label="文档类型过滤"
                >
                  <option value="">所有类型</option>
                  <option value="requirement">需求文档</option>
                  <option value="test_case">测试用例</option>
                  <option value="prototype">原型图</option>
                  <option value="product_requirement">产品需求</option>
                  <option value="incomplete">残缺文档</option>
                </Form.Select>
              </Col>

              <Col md={4}>
                <Form.Label className="small fw-bold text-secondary">日期范围</Form.Label>
                <InputGroup size="sm">
                  <Form.Control
                    type="date"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                    aria-label="开始日期"
                  />
                  <InputGroup.Text className="px-1">-</InputGroup.Text>
                  <Form.Control
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                    aria-label="结束日期"
                  />
                </InputGroup>
              </Col>

              <Col md={2} className="d-flex align-items-end">
                <Button variant="secondary" size="sm" className="w-100" onClick={onSearch}>
                  查询
                </Button>
              </Col>
            </Row>
          </Col>
        </Row>
      </Card.Body>
    </Card>
  );
}

export function OfflineBanner({ isOnline }: { isOnline: boolean }) {
  if (isOnline) return null;
  return (
    <div className="alert alert-warning d-flex align-items-center py-2 mb-0" role="alert">
      <FaWifi className="me-2 offline-badge" />
      <strong>离线模式</strong>: 您当前处于离线状态，部分功能不可用。
    </div>
  );
}
