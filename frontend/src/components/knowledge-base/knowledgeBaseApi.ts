import { api } from "../../utils/api";
import { normalizeDoc } from "./types";
import type { Doc, KnowledgeDocumentRecord } from "./types";

export type KnowledgeListFilters = {
  projectId: number;
  page: number;
  search: string;
  type: string;
  start: string;
  end: string;
};

export type KnowledgeListResponse = {
  documents: KnowledgeDocumentRecord[];
  pagination: {
    page: number;
    page_size: number;
    total: number;
    total_pages: number;
  };
};

export type KnowledgeDocumentDetailResponse = {
  id: number;
  filename: string;
  content: string;
};

export type UploadKnowledgeDocumentResponse = {
  success: boolean;
  id: number;
  filename: string;
};

export type KnowledgeMutationResponse = {
  success: boolean;
  error?: string;
};

const buildKnowledgeListUrl = ({
  projectId,
  page,
  search,
  type,
  start,
  end,
}: KnowledgeListFilters) => {
  const params = new URLSearchParams({
    project_id: String(projectId),
    page: String(page),
    page_size: "8",
  });
  if (search) params.set("search", search);
  if (type) params.set("doc_type", type);
  if (start) params.set("start_date", start);
  if (end) params.set("end_date", end);
  return `/api/knowledge-list?${params.toString()}`;
};

export const fetchKnowledgeList = (filters: KnowledgeListFilters) =>
  api.get<KnowledgeListResponse>(buildKnowledgeListUrl(filters));

export const fetchKnowledgeDoc = (documentId: number) =>
  api.get<KnowledgeDocumentDetailResponse>(`/api/knowledge/${documentId}`);

export const uploadKnowledgeDocument = (uploadData: FormData) =>
  api.upload<UploadKnowledgeDocumentResponse>("/api/upload-knowledge", uploadData);

export const deleteKnowledgeDocument = (documentId: number) =>
  api.delete<KnowledgeMutationResponse>(`/api/knowledge/${documentId}`);

export const moveKnowledgeDocument = (payload: {
  project_id: number;
  doc_id: number;
  anchor_doc_id: number;
  position: "before" | "after";
}) => api.post<KnowledgeMutationResponse>("/api/knowledge/move", payload);

export const updateKnowledgeRelation = (payload: {
  doc_id: number;
  source_doc_id: number;
}) => api.post<KnowledgeMutationResponse>("/api/knowledge/update-relation", payload);

export const fetchAllTestCaseCandidates = async (projectId: number): Promise<Doc[]> => {
  const pageSize = 200;
  let currentPage = 1;
  let totalPages = 1;
  const allDocuments: Doc[] = [];

  while (currentPage <= totalPages) {
    const params = new URLSearchParams({
      project_id: String(projectId),
      doc_type: "test_case",
      include_linked_test_cases: "true",
      page: String(currentPage),
      page_size: String(pageSize),
    });
    const response = await api.get<KnowledgeListResponse>(`/api/knowledge-list?${params.toString()}`);
    const pageDocuments = response.documents.map(normalizeDoc);

    allDocuments.push(...pageDocuments);
    totalPages = Math.max(1, response.pagination.total_pages);
    currentPage += 1;
  }

  return Array.from(new Map(allDocuments.map((document) => [document.id, document])).values());
};
