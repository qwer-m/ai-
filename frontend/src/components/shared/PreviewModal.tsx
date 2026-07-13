import { Modal, Button, Accordion, Badge } from 'react-bootstrap';

type Props = {
  show: boolean;
  onHide: () => void;
  title: string;
  content: string;
  linkedDocs?: { id: number; filename: string; content: string }[];
  loading?: boolean;
};

const TRUSTED_PREVIEW_TAGS = ['div', 'table', 'tbody', 'thead', 'tr', 'td', 'th', 'h5'] as const;
const TRUSTED_PREVIEW_CLASS_PREFIXES = ['bg-', 'border', 'rounded', 'p-', 'table', 'mb-', 'preview-modal-content-html'];

function isTrustedPreviewHtml(content: string): boolean {
  if (!content || (!content.includes('<table') && !content.includes('<h5'))) {
    return false;
  }

  const lowered = content.toLowerCase();
  const blockedPatterns = [
    '<script',
    '<iframe',
    '<object',
    '<embed',
    '<svg',
    '<math',
    'javascript:',
    'data:text/html',
    'srcdoc=',
    'onerror=',
    'onload=',
    'onclick=',
    'onmouseover=',
    'onfocus=',
    'onmouseenter=',
  ];
  if (blockedPatterns.some((pattern) => lowered.includes(pattern))) {
    return false;
  }

  const forbiddenStylePatterns = ['expression(', 'url(', '@import'];
  if (forbiddenStylePatterns.some((pattern) => lowered.includes(pattern))) {
    return false;
  }

  const tagMatches = content.match(/<\/?([a-zA-Z0-9-]+)/g) || [];
  if (
    tagMatches.some((rawTag) => {
      const tag = rawTag.replace(/[</>]/g, '').toLowerCase();
      return !TRUSTED_PREVIEW_TAGS.includes(tag as (typeof TRUSTED_PREVIEW_TAGS)[number]);
    })
  ) {
    return false;
  }

  const classMatches = content.match(/class="([^"]*)"/g) || [];
  return classMatches.every((entry) => {
    const raw = entry.slice('class="'.length, -1);
    return raw
      .split(/\s+/)
      .filter(Boolean)
      .every((cls) => TRUSTED_PREVIEW_CLASS_PREFIXES.some((prefix) => cls === prefix || cls.startsWith(prefix)));
  });
}

export function PreviewModal({ show, onHide, title, content, linkedDocs, loading }: Props) {
  const canRenderTrustedHtml = isTrustedPreviewHtml(content);

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
              {canRenderTrustedHtml ? (
                <div
                  className="bg-light p-3 border rounded mb-0 table-responsive preview-modal-content-html"
                  dangerouslySetInnerHTML={{ __html: content }}
                />
              ) : (
                <pre className="bg-light p-3 border rounded mb-0 preview-modal-content-pre">{content}</pre>
              )}
              {content && content.includes('[Unsupported file type') && (content.includes('.xls') || content.includes('.xlsx')) && (
                <div className="alert alert-warning mt-2 small d-flex align-items-center">
                  <i className="me-2">提示</i>
                  <div>
                    <strong>需要重新上传</strong>
                    <div>Excel 预览支持已启用。请删除当前文档并重新上传，即可查看表格预览。</div>
                  </div>
                </div>
              )}
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
                        <pre className="bg-light p-2 border rounded mb-0 preview-modal-linked-pre">{doc.content}</pre>
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
