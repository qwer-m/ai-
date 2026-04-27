import { Button, Card, Col, Form, InputGroup, Row, Spinner } from 'react-bootstrap';
import { FaSearch, FaWifi } from 'react-icons/fa';

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
    <Card className="border-0 shadow-sm search-card knowledge-toolbar-card panel-card">
      <Card.Body className="p-3">
        <Row className="g-3 align-items-center">
          <Col md={3}>
            <InputGroup size="sm">
              <Form.Control
                type="file"
                className="text-center"
                onChange={(e: React.ChangeEvent<HTMLInputElement>) => onFileChange(e.target.files?.[0] ?? null)}
                disabled={!isOnline}
              />
            </InputGroup>
          </Col>

          <Col md={2}>
            <Form.Select
              size="sm"
              className="text-center"
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

          <Col md={1} className="d-flex align-items-end justify-content-center">
            <Button
              variant="primary"
              size="sm"
              className="px-4 d-flex align-items-center justify-content-center knowledge-upload-btn"
              onClick={onUpload}
              disabled={uploading || !projectId || !isOnline}
            >
              {uploading ? <Spinner size="sm" animation="border" /> : '上传'}
            </Button>
          </Col>

          <Col className="border-start ps-4">
            <Row className="g-2">
              <Col md={4}>
                <InputGroup size="sm">
                  <InputGroup.Text className="bg-transparent border-0 ps-2 pe-1 shadow-none">
                    <FaSearch />
                  </InputGroup.Text>
                  <Form.Control
                    className="text-center"
                    placeholder="搜索文件名..."
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                  />
                </InputGroup>
              </Col>

              <Col md={3} className="d-flex align-items-end">
                <Form.Select
                  size="sm"
                  value={filterDocType}
                  onChange={(e) => setFilterDocType(e.target.value)}
                  aria-label="文档类型过滤"
                  className={`text-center knowledge-filter-select ${filterDocType ? '' : 'text-muted'}`}
                >
                  <option value="">全部类别</option>
                  <option value="requirement">需求文档</option>
                  <option value="test_case">测试用例</option>
                  <option value="prototype">原型图</option>
                  <option value="product_requirement">产品需求</option>
                  <option value="incomplete">残缺文档</option>
                </Form.Select>
              </Col>

              <Col md={3}>
                <InputGroup size="sm">
                  <Form.Control
                    className="text-center"
                    type="date"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                    aria-label="开始日期"
                  />
                  <InputGroup.Text className="px-1 border-0 bg-transparent rounded-0">-</InputGroup.Text>
                  <Form.Control
                    className="text-center"
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                    aria-label="结束日期"
                  />
                </InputGroup>
              </Col>

              <Col md={2} className="d-flex align-items-end justify-content-center">
                <Button variant="secondary" size="sm" className="px-4 d-flex align-items-center justify-content-center" onClick={onSearch}>
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
      <strong>离线模式</strong>: 当前处于离线状态，部分功能不可用。
    </div>
  );
}
