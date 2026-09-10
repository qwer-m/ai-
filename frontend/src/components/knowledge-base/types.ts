export type LinkedDocumentRecord = {
  id: number;
  display_id: number;
  filename: string;
  content_preview: string;
};

export type KnowledgeDocumentRecord = {
  id: number;
  display_id: number;
  filename: string;
  doc_type: string | null;
  created_at: string | null;
  file_size: number;
  source_doc_id: number | null;
  source_doc_name: string | null;
  linked_test_cases: LinkedDocumentRecord[];
  content_preview: string;
};

export type LinkedDoc = {
  id: number;
  display_id: number;
  filename: string;
  content_preview: string;
};

export type Doc = {
  id: number;
  display_id: number;
  filename: string;
  doc_type: string;
  created_at: string;
  file_size: number;
  source_doc_id: number | null;
  source_doc_name: string | null;
  linked_test_cases: LinkedDoc[];
  content_preview: string;
  _isLinked?: boolean;
};

export type DragTarget = {
  index: number;
  position: "before" | "after";
};

export const docTypeMap: Record<string, string> = {
  requirement: "需求文档",
  test_case: "测试用例",
  prototype: "原型图",
  product_requirement: "产品需求",
  incomplete: "残缺文档",
};

export const docTypeColor: Record<string, string> = {
  requirement: "primary",
  test_case: "success",
  prototype: "info",
  product_requirement: "primary",
  incomplete: "warning",
};

const normalizeLinkedDoc = (record: LinkedDocumentRecord): LinkedDoc => ({
  id: record.id,
  display_id: record.display_id,
  filename: record.filename,
  content_preview: record.content_preview,
});

export const normalizeDoc = (record: KnowledgeDocumentRecord): Doc => ({
  id: record.id,
  display_id: record.display_id,
  filename: record.filename,
  doc_type: record.doc_type ?? "",
  created_at: record.created_at ?? "",
  file_size: record.file_size,
  source_doc_id: record.source_doc_id,
  source_doc_name: record.source_doc_name,
  linked_test_cases: record.linked_test_cases.map(normalizeLinkedDoc),
  content_preview: record.content_preview,
});
