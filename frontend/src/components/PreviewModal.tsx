import { Modal, Button, Accordion, Badge } from 'react-bootstrap';

type Props = {
  show: boolean;
  onHide: () => void;
  title: string;
  content: string;
  linkedDocs?: { id: number; filename: string; content: string }[];
  loading?: boolean;
};

export function PreviewModal({ show, onHide, title, content, linkedDocs, loading }: Props) {
  return (
    <Modal show={show} onHide={onHide} size="xl">
      <Modal.Header closeButton>
        <Modal.Title>{title}</Modal.Title>
      </Modal.Header>
      <Modal.Body>
        {loading ? (
            <div className="text-center py-5">
                <div className="spinner-border text-primary" role="status">
                    <span className="visually-hidden">Loading...</span>
                </div>
            </div>
        ) : (
        <div className="d-flex flex-column gap-3">
            <div>
                <h6 className="mb-2">文档内容</h6>
                <pre className="bg-light p-3 border rounded mb-0" style={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', maxHeight: '50vh', overflowY: 'auto' }}>
                {content}
                </pre>
            </div>

            {linkedDocs && linkedDocs.length > 0 && (
                <div>
                    <h6 className="mb-2 d-flex align-items-center gap-2">
                        关联测试用例 <Badge bg="info">{linkedDocs.length}</Badge>
                    </h6>
                    <Accordion>
                        {linkedDocs.map((doc, idx) => (
                            <Accordion.Item eventKey={String(idx)} key={doc.id}>
                                <Accordion.Header>{doc.filename}</Accordion.Header>
                                <Accordion.Body>
                                    <pre className="bg-light p-2 border rounded mb-0" style={{ whiteSpace: 'pre-wrap', fontFamily: 'monospace', maxHeight: '30vh', overflowY: 'auto', fontSize: '0.9em' }}>
                                        {doc.content}
                                    </pre>
                                </Accordion.Body>
                            </Accordion.Item>
                        ))}
                    </Accordion>
                </div>
            )}
        </div>
        )}
      </Modal.Body>
      <Modal.Footer>
        <Button variant="secondary" onClick={onHide}>关闭</Button>
      </Modal.Footer>
    </Modal>
  );
}
