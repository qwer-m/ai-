import { useState } from "react";
import { Button } from "react-bootstrap";

type ErrorTraceProps = {
  details: string;
};

export function ErrorTrace({ details }: ErrorTraceProps) {
  // 默认只展示前三行，避免长堆栈压垮界面；可按需展开查看完整信息。
  const [expanded, setExpanded] = useState(false);
  const lines = details ? details.split("\n") : [];
  const preview = lines.slice(0, 3).join("\n");
  const hasMore = lines.length > 3;

  return (
    <div className="d-flex flex-column gap-1">
      <small className="text-muted">堆栈详情：</small>
      <pre
        className="bg-white border p-2 rounded small text-secondary mb-1 font-monospace"
        style={{ whiteSpace: "pre-wrap" }}
      >
        {expanded ? details : preview}
        {!expanded && hasMore && "..."}
      </pre>
      {hasMore && (
        <div className="text-end">
          <Button
            variant="link"
            size="sm"
            className="p-0 text-decoration-none"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? "收起详情" : "展开完整堆栈"}
          </Button>
        </div>
      )}
    </div>
  );
}
