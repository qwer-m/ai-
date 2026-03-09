import { Pagination } from "react-bootstrap";

type KnowledgeBasePaginationBarProps = {
  totalItems: number;
  docsLength: number;
  page: number;
  totalPages: number;
  onFetchPage: (page: number) => void;
  onPageDragEnter: (targetPage: number) => void;
  onPageDragLeave: () => void;
  onPageDrop: (e: React.DragEvent, targetPage: number) => void;
  pageSwitchTimerActive: boolean;
};

export function KnowledgeBasePaginationBar({
  totalItems,
  docsLength,
  page,
  totalPages,
  onFetchPage,
  onPageDragEnter,
  onPageDragLeave,
  onPageDrop,
  pageSwitchTimerActive,
}: KnowledgeBasePaginationBarProps) {
  return (
    <div className="bg-light border-top px-3 py-2 d-flex justify-content-between align-items-center small text-secondary">
      <div>
        共找到 <strong>{totalItems || docsLength}</strong> 个项目
        {page > 1 && ` (第 ${page}/${totalPages} 页)`}
      </div>
      {totalPages > 1 && (
        <Pagination size="sm" className="m-0">
          <Pagination.First
            onClick={() => onFetchPage(1)}
            disabled={page === 1}
            onDragEnter={() => !pageSwitchTimerActive && page > 1 && onPageDragEnter(1)}
            onDragOver={(e) => e.preventDefault()}
            onDragLeave={onPageDragLeave}
            onDrop={(e) => onPageDrop(e, 1)}
          />
          <Pagination.Prev
            onClick={() => onFetchPage(page - 1)}
            disabled={page === 1}
            onDragEnter={() =>
              !pageSwitchTimerActive && page > 1 && onPageDragEnter(page - 1)
            }
            onDragOver={(e) => e.preventDefault()}
            onDragLeave={onPageDragLeave}
            onDrop={(e) => page > 1 && onPageDrop(e, page - 1)}
          />

          {[...Array(totalPages)].map((_, i) => {
            const p = i + 1;
            if (p === 1 || p === totalPages || Math.abs(page - p) <= 2) {
              return (
                <Pagination.Item
                  key={p}
                  active={p === page}
                  onClick={() => onFetchPage(p)}
                  onDragEnter={() => onPageDragEnter(p)}
                  onDragOver={(e) => e.preventDefault()}
                  onDragLeave={onPageDragLeave}
                  onDrop={(e) => onPageDrop(e, p)}
                >
                  {p}
                </Pagination.Item>
              );
            }
            if (p === page - 3 || p === page + 3) {
              return <Pagination.Ellipsis key={p} disabled />;
            }
            return null;
          })}

          <Pagination.Next
            onClick={() => onFetchPage(page + 1)}
            disabled={page === totalPages}
            onDragEnter={() =>
              !pageSwitchTimerActive && page < totalPages && onPageDragEnter(page + 1)
            }
            onDragOver={(e) => e.preventDefault()}
            onDragLeave={onPageDragLeave}
            onDrop={(e) => page < totalPages && onPageDrop(e, page + 1)}
          />
          <Pagination.Last
            onClick={() => onFetchPage(totalPages)}
            disabled={page === totalPages}
            onDragEnter={() =>
              !pageSwitchTimerActive && page < totalPages && onPageDragEnter(totalPages)
            }
            onDragOver={(e) => e.preventDefault()}
            onDragLeave={onPageDragLeave}
            onDrop={(e) => onPageDrop(e, totalPages)}
          />
        </Pagination>
      )}
    </div>
  );
}
