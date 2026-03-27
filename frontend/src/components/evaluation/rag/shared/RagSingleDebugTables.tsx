import { Badge, Table } from 'react-bootstrap';

export type RagChunkRow = {
  chunk_id?: string;
  doc_id?: string | number;
  filename?: string;
  score?: number;
  final_score?: number;
  vector_score?: number;
  keyword_score?: number;
  title_score?: number;
  fusion_score?: number;
  selection_reason?: string;
  kept_reason?: string;
  chunk_text?: string;
};

export type RagDocHitRow = {
  doc_id?: string | number;
  filename?: string;
  doc_type?: string;
  hit_chunks?: number;
  top_score?: number;
  avg_score?: number;
  title_hit_terms?: string[];
  content_hit_terms?: string[];
};

export function scoreText(value: unknown): string {
  const n = Number(value ?? 0);
  return Number.isFinite(n) ? n.toFixed(4) : '0.0000';
}

function Chip({ items }: { items?: string[] }) {
  const arr = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!arr.length) return <span className="text-muted">-</span>;
  return <span>{arr.slice(0, 6).join('、')}</span>;
}

export function DocHitStatsTable({ rows }: { rows: RagDocHitRow[] }) {
  return (
    <div className="table-responsive rag-console-table-wrap">
      <Table striped bordered hover size="sm" className="mb-0 rag-console-table">
        <thead>
          <tr>
            <th>doc_id/文档名</th>
            <th>命中chunk</th>
            <th>top_score</th>
            <th>avg_score</th>
            <th>标题命中词</th>
            <th>正文命中词</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={6} className="text-center text-muted">
                暂无数据
              </td>
            </tr>
          ) : (
            rows.map((r, i) => (
              <tr key={`${String(r.doc_id || 'x')}-${i}`}>
                <td>
                  {String(r.doc_id || '-')}/{String(r.filename || '-')}
                </td>
                <td>{Number(r.hit_chunks || 0)}</td>
                <td>{scoreText(r.top_score)}</td>
                <td>{scoreText(r.avg_score)}</td>
                <td>
                  <Chip items={r.title_hit_terms} />
                </td>
                <td>
                  <Chip items={r.content_hit_terms} />
                </td>
              </tr>
            ))
          )}
        </tbody>
      </Table>
    </div>
  );
}

export function ChunkTable({
  title,
  rows,
  goldSet,
  hasGoldReference,
}: {
  title: string;
  rows: RagChunkRow[];
  goldSet: Set<string>;
  hasGoldReference: boolean;
}) {
  return (
    <div className="mt-3 rag-console-chunk-block">
      <div className="fw-bold mb-2 rag-console-block-title">{title}</div>
      <div className="table-responsive rag-console-table-wrap">
        <Table striped bordered hover size="sm" className="mb-0 rag-console-table">
          <thead>
            <tr>
              <th>chunk_id</th>
              <th>doc_id/文档名</th>
              <th>vector</th>
              <th>keyword</th>
              <th>title</th>
              <th>fusion/final</th>
              <th>gold命中</th>
              <th>选中原因</th>
              <th>文本预览</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={9} className="text-center text-muted">
                  暂无数据
                </td>
              </tr>
            ) : (
              rows.map((row, idx) => {
                const cid = String(row.chunk_id || '').trim();
                const hit = cid && goldSet.has(cid);
                const final = Number(row.final_score ?? row.fusion_score ?? row.score ?? 0);
                return (
                  <tr key={`${cid || 'none'}-${idx}`}>
                    <td className="rag-console-chunk-id-cell">{cid || '-'}</td>
                    <td>
                      {String(row.doc_id || '-')}/{String(row.filename || '-')}
                    </td>
                    <td>{scoreText(row.vector_score ?? row.score)}</td>
                    <td>{scoreText(row.keyword_score)}</td>
                    <td>{scoreText(row.title_score)}</td>
                    <td>{scoreText(final)}</td>
                    <td>
                      {!hasGoldReference ? (
                        <span className="text-muted">-</span>
                      ) : hit ? (
                        <Badge bg="success">命中</Badge>
                      ) : (
                        <Badge bg="secondary">未命中</Badge>
                      )}
                    </td>
                    <td>{String(row.selection_reason || row.kept_reason || '-')}</td>
                    <td className="rag-console-chunk-text-cell">{String(row.chunk_text || '').slice(0, 220)}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </Table>
      </div>
    </div>
  );
}
