import { api } from "../../utils/api";
import { normalizeDoc } from "./types";
import type { Doc } from "./types";

export type KnowledgeListFilters = {
  projectId: number;
  page: number;
  search: string;
  type: string;
  start: string;
  end: string;
};

export const trackOperation = (action: string, metadata: object) => {
  console.log(`[AUDIT] Action: ${action}`, metadata, new Date().toISOString());
};

export const buildKnowledgeListUrl = ({
  projectId,
  page,
  search,
  type,
  start,
  end,
}: KnowledgeListFilters) => {
  let url = `/api/knowledge-list?project_id=${projectId}&page=${page}&page_size=8`;
  if (search) url += `&search=${encodeURIComponent(search)}`;
  if (type) url += `&doc_type=${encodeURIComponent(type)}`;
  if (start) url += `&start_date=${encodeURIComponent(start)}`;
  if (end) url += `&end_date=${encodeURIComponent(end)}`;
  return url;
};

export const fetchKnowledgeList = async (filters: KnowledgeListFilters) => {
  return api.get<any>(buildKnowledgeListUrl(filters));
};

export const fetchKnowledgeDoc = async (globalId: number) => {
  return api.get<any>(`/api/knowledge/${globalId}`);
};

export const uploadKnowledgeDocument = async (uploadData: FormData) => {
  return api.upload<any>("/api/upload-knowledge", uploadData);
};

export const deleteKnowledgeDocument = async (globalId: number) => {
  return api.delete<any>(`/api/knowledge/${globalId}`);
};

export const moveKnowledgeDocument = async (payload: {
  project_id: number;
  doc_id: number;
  anchor_doc_id: number;
  position: "before" | "after";
}) => {
  return api.post("/api/knowledge/move", payload);
};

export const updateKnowledgeRelation = async (payload: {
  doc_id: number;
  source_doc_id: number;
}) => {
  return api.post<any>("/api/knowledge/update-relation", payload);
};

export const fetchAllTestCaseCandidates = async (projectId: number): Promise<Doc[]> => {
  const pageSize = 200;
  let currentPage = 1;
  let totalPagesToFetch = 1;
  const allDocs: Doc[] = [];

  while (currentPage <= totalPagesToFetch) {
    const data = await api.get<any>(
      `/api/knowledge-list?project_id=${projectId}&doc_type=test_case&include_linked_test_cases=true&page=${currentPage}&page_size=${pageSize}`,
    );

    const pageDocs = Array.isArray(data?.documents) ? data.documents.map(normalizeDoc) : [];
    allDocs.push(...pageDocs);
    totalPagesToFetch = Math.max(1, Number(data?.pagination?.total_pages || 1));
    currentPage += 1;
    if (pageDocs.length === 0 && currentPage > totalPagesToFetch) break;
  }

  const dedup = new Map<number, Doc>();
  for (const doc of allDocs) dedup.set(doc.global_id, doc);
  return Array.from(dedup.values());
};
