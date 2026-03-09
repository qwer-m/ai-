export type LinkedDoc = {
  id: number;
  global_id: number;
  filename: string;
  content_preview: string;
};

export type Doc = {
  id: number; // 项目内序号 ID（用于展示）
  global_id: number;
  filename: string;
  doc_type: string;
  created_at: string;
  file_size?: number;
  source_doc_id?: number | null;
  source_doc_name?: string | null;
  linked_test_cases?: LinkedDoc[];
  content_preview?: string;
  isNew?: boolean; // 用于新增项焦点管理
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
  evaluation_report: "评估报告",
};

export const docTypeColor: Record<string, string> = {
  requirement: "primary",
  test_case: "success",
  prototype: "info",
  product_requirement: "primary",
  incomplete: "warning",
  evaluation_report: "secondary",
};

const normalizeLinkedDoc = (raw: any): LinkedDoc => {
  // 兼容后端字段差异：global_id / id
  const globalId = Number(raw?.global_id ?? raw?.id ?? 0);
  const localId = Number(raw?.id ?? globalId);
  return {
    id: localId,
    global_id: globalId,
    filename: String(raw?.filename ?? ""),
    content_preview: String(raw?.content_preview ?? ""),
  };
};

export const normalizeDoc = (raw: any): Doc => {
  // 同时兼容新旧后端字段：global_id / id / project_specific_id
  const globalId = Number(raw?.global_id ?? raw?.id ?? 0);
  const localId = Number(raw?.project_specific_id ?? raw?.id ?? globalId);
  const linkedDocs = Array.isArray(raw?.linked_test_cases)
    ? raw.linked_test_cases.map(normalizeLinkedDoc)
    : [];

  return {
    id: localId,
    global_id: globalId,
    filename: String(raw?.filename ?? ""),
    doc_type: String(raw?.doc_type ?? ""),
    created_at: String(raw?.created_at ?? ""),
    file_size: raw?.file_size != null ? Number(raw.file_size) : undefined,
    source_doc_id: raw?.source_doc_id != null ? Number(raw.source_doc_id) : null,
    source_doc_name:
      raw?.source_doc_name != null ? String(raw.source_doc_name) : null,
    linked_test_cases: linkedDocs,
    content_preview:
      raw?.content_preview != null ? String(raw.content_preview) : undefined,
    isNew: Boolean(raw?.isNew),
    _isLinked: Boolean(raw?._isLinked),
  };
};
