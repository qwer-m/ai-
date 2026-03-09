import { Badge, Button, Card, Col, OverlayTrigger, Placeholder, Popover, Row } from "react-bootstrap";
import { FaCalendarAlt, FaEye, FaFileAlt, FaLink, FaTimes, FaTrash } from "react-icons/fa";
import { docTypeColor, docTypeMap } from "./types";
import type { Doc, DragTarget, LinkedDoc } from "./types";

type KnowledgeBaseContentProps = {
  loading: boolean;
  docs: Doc[];
  dragTarget: DragTarget | null;
  onDragStart: (e: React.DragEvent, index: number, doc: Doc) => void;
  onItemDragOver: (e: React.DragEvent, index: number) => void;
  onDragLeave: () => void;
  onDrop: (e: React.DragEvent) => void;
  onMouseEnter: (doc: Doc) => void;
  onPreview: (doc: Doc) => void;
  onDelete: (doc: Doc) => void;
  onOpenManage: (doc: Doc) => void;
  onUnlink: (parentDoc: Doc, linkedDoc: LinkedDoc) => void;
};

export function KnowledgeBaseContent({
  loading,
  docs,
  dragTarget,
  onDragStart,
  onItemDragOver,
  onDragLeave,
  onDrop,
  onMouseEnter,
  onPreview,
  onDelete,
  onOpenManage,
  onUnlink,
}: KnowledgeBaseContentProps) {
  return (
    <div className="flex-grow-1 overflow-auto p-3">
      {loading ? (
        <Row className="g-3">
          {[...Array(8)].map((_, i) => (
            <Col key={i} md={6} lg={4} xl={3}>
              <Card className="h-100 border-0 shadow-sm">
                <Card.Body>
                  <Placeholder as={Card.Title} animation="glow">
                    <Placeholder xs={8} />
                  </Placeholder>
                  <Placeholder as={Card.Text} animation="glow">
                    <Placeholder xs={4} /> <Placeholder xs={6} />
                    <Placeholder xs={12} className="mt-3" style={{ height: "60px" }} />
                  </Placeholder>
                </Card.Body>
              </Card>
            </Col>
          ))}
        </Row>
      ) : docs.length === 0 ? (
        <div className="text-center py-5 text-muted bg-light rounded border border-dashed">
          <FaFileAlt size={48} className="mb-3 opacity-50" />
          <h5>暂无文档</h5>
          <p>上传您的首个测试文档开始构建知识库</p>
        </div>
      ) : (
        <Row className="g-3">
          {docs.map((doc, index) => (
            <Col
              key={doc.global_id}
              md={6}
              lg={4}
              xl={3}
              draggable
              onDragStart={(e) => onDragStart(e, index, doc)}
              onDragOver={(e) => onItemDragOver(e, index)}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
              style={{
                cursor: "move",
                transition: "all 0.1s",
                transform:
                  dragTarget?.index === index
                    ? dragTarget.position === "before"
                      ? "translateY(2px)"
                      : "translateY(-2px)"
                    : "none",
                boxShadow:
                  dragTarget?.index === index
                    ? dragTarget.position === "before"
                      ? "0 -4px 0 0 #0d6efd"
                      : "0 4px 0 0 #0d6efd"
                    : "none",
              }}
            >
              <Card
                className={`h-100 doc-card ${["incomplete", "prototype"].includes(doc.doc_type) ? "doc-card-texture-warning" : "doc-card-texture-success"}`}
                onMouseEnter={() => onMouseEnter(doc)}
                tabIndex={0}
                role="article"
                aria-label={`${doc.filename}, 类型: ${docTypeMap[doc.doc_type]}`}
                onKeyDown={(e) => {
                  if (e.key === "Enter") onPreview(doc);
                  if (e.key === "Delete") onDelete(doc);
                }}
              >
                <Card.Body className="d-flex flex-column">
                  <div className="d-flex justify-content-between align-items-start mb-2">
                    <Badge bg={docTypeColor[doc.doc_type] || "secondary"} className="mb-2">
                      {docTypeMap[doc.doc_type] || doc.doc_type}
                    </Badge>
                    <small
                      className="text-muted d-flex align-items-center gap-1"
                      style={{ fontSize: "0.75rem" }}
                    >
                      <FaCalendarAlt /> {new Date(doc.created_at).toLocaleDateString()}
                    </small>
                  </div>

                  <Card.Title
                    className="h6 mb-3 flex-grow-1"
                    style={{
                      lineHeight: "1.4",
                      display: "-webkit-box",
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      wordBreak: "break-all",
                    }}
                    title={doc.filename}
                  >
                    {doc.filename}
                  </Card.Title>

                  <div
                    className="bg-white bg-opacity-50 rounded p-2 mb-3 border border-light"
                    style={{ maxHeight: "150px", overflowY: "auto" }}
                  >
                    <div className="d-flex justify-content-between align-items-center mb-2">
                      <small className="text-muted fw-bold" style={{ fontSize: "0.7rem" }}>
                        {["requirement", "product_requirement", "incomplete"].includes(doc.doc_type)
                          ? "关联用例"
                          : "关联需求"}
                      </small>
                      {["requirement", "product_requirement", "incomplete"].includes(
                        doc.doc_type,
                      ) && (
                        <Button
                          variant="link"
                          className="p-0 text-primary small"
                          style={{ fontSize: "0.7rem", textDecoration: "none" }}
                          onClick={() => onOpenManage(doc)}
                        >
                          <FaLink className="me-1" />
                          管理
                        </Button>
                      )}
                    </div>
                    {doc.doc_type === "test_case" ? (
                      doc.source_doc_name ? (
                        <Badge
                          bg="secondary"
                          text="light"
                          className="border fw-normal text-truncate"
                          style={{ maxWidth: "100%" }}
                        >
                          {doc.source_doc_name}
                        </Badge>
                      ) : (
                        <span className="text-muted small fst-italic">暂无关联</span>
                      )
                    ) : doc.linked_test_cases && doc.linked_test_cases.length > 0 ? (
                      <div className="d-flex flex-wrap gap-1">
                        {doc.linked_test_cases.map((ld) => (
                          <OverlayTrigger
                            key={ld.global_id}
                            placement="top"
                            overlay={
                              <Popover id={`popover-${ld.global_id}`}>
                                <Popover.Header as="h3" className="fs-6">
                                  {ld.filename}
                                </Popover.Header>
                                <Popover.Body className="small text-secondary py-2">
                                  {ld.content_preview ? (
                                    <div style={{ maxHeight: "150px", overflowY: "auto" }}>
                                      {ld.content_preview}
                                    </div>
                                  ) : (
                                    "暂无预览"
                                  )}
                                </Popover.Body>
                              </Popover>
                            }
                          >
                            <Badge
                              bg="secondary"
                              text="light"
                              className="border fw-normal text-truncate position-relative pe-3"
                              style={{ maxWidth: "100%", cursor: "pointer" }}
                            >
                              {ld.filename}
                              <span
                                className="position-absolute top-50 end-0 translate-middle-y me-1 text-danger p-0 d-flex align-items-center justify-content-center"
                                style={{ width: "12px", height: "12px", borderRadius: "50%" }}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  onUnlink(doc, ld);
                                }}
                                title="移除关联"
                              >
                                <FaTimes size={10} />
                              </span>
                            </Badge>
                          </OverlayTrigger>
                        ))}
                      </div>
                    ) : (
                      <span className="text-muted small fst-italic">暂无关联</span>
                    )}
                  </div>

                  <div className="d-flex justify-content-between align-items-center mt-auto pt-2 border-top">
                    <Button
                      variant="outline-primary"
                      size="sm"
                      className="border-0 px-2"
                      onClick={() => onPreview(doc)}
                    >
                      <FaEye className="me-1" /> 查看
                    </Button>
                    <Button
                      variant="outline-danger"
                      size="sm"
                      className="border-0 px-2"
                      onClick={() => onDelete(doc)}
                    >
                      <FaTrash className="me-1" /> 删除
                    </Button>
                  </div>
                </Card.Body>
              </Card>
            </Col>
          ))}
        </Row>
      )}
    </div>
  );
}
