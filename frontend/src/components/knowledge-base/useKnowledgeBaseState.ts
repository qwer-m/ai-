import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { normalizeDoc } from "./types";
import { fetchKnowledgeList } from "./knowledgeBaseApi";
import type { Doc } from "./types";

type UseKnowledgeBaseStateParams = {
  projectId: number | null;
  onLog: (msg: string) => void;
};

export type UseKnowledgeBaseStateResult = {
  docs: Doc[];
  loading: boolean;
  page: number;
  totalPages: number;
  totalItems: number;
  searchTerm: string;
  setSearchTerm: Dispatch<SetStateAction<string>>;
  filterDocType: string;
  setFilterDocType: Dispatch<SetStateAction<string>>;
  startDate: string;
  setStartDate: Dispatch<SetStateAction<string>>;
  endDate: string;
  setEndDate: Dispatch<SetStateAction<string>>;
  setLoading: Dispatch<SetStateAction<boolean>>;
  setDocs: Dispatch<SetStateAction<Doc[]>>;
  fetchList: (p?: number) => void;
  doFetchList: (
    pid: number,
    p: number,
    search: string,
    type: string,
    start: string,
    end: string,
  ) => Promise<any>;
};

const sessionKey = (projectId: number | null, suffix: string) =>
  projectId ? `kb_${suffix}_${projectId}` : "";

export function useKnowledgeBaseState({
  projectId,
  onLog,
}: UseKnowledgeBaseStateParams): UseKnowledgeBaseStateResult {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(() => {
    if (!projectId) return 1;
    const saved = sessionStorage.getItem(sessionKey(projectId, "page"));
    return saved ? parseInt(saved) : 1;
  });
  const [totalPages, setTotalPages] = useState(1);
  const [totalItems, setTotalItems] = useState(0);
  const [searchTerm, setSearchTerm] = useState(
    () => sessionStorage.getItem(sessionKey(projectId, "search")) || "",
  );
  const [filterDocType, setFilterDocType] = useState(
    () => sessionStorage.getItem(sessionKey(projectId, "type")) || "",
  );
  const [startDate, setStartDate] = useState(
    () => sessionStorage.getItem(sessionKey(projectId, "start")) || "",
  );
  const [endDate, setEndDate] = useState(
    () => sessionStorage.getItem(sessionKey(projectId, "end")) || "",
  );

  const prevProjectId = useRef(projectId);
  const prevFilters = useRef({ searchTerm, filterDocType, startDate, endDate });

  useEffect(() => {
    if (!projectId) return;
    sessionStorage.setItem(sessionKey(projectId, "search"), searchTerm);
    sessionStorage.setItem(sessionKey(projectId, "type"), filterDocType);
    sessionStorage.setItem(sessionKey(projectId, "start"), startDate);
    sessionStorage.setItem(sessionKey(projectId, "end"), endDate);
  }, [projectId, searchTerm, filterDocType, startDate, endDate]);

  useEffect(() => {
    if (!projectId) return;
    const savedPage = sessionStorage.getItem(sessionKey(projectId, "page"));
    if (savedPage) {
      const p = parseInt(savedPage);
      if (p !== page) setPage(p);
    }
  }, [projectId, page]);

  const doFetchList = async (
    pid: number,
    p: number,
    search: string,
    type: string,
    start: string,
    end: string,
  ) => {
    setLoading(true);
    try {
      const data = await fetchKnowledgeList({
        projectId: pid,
        page: p,
        search,
        type,
        start,
        end,
      });

      if (Array.isArray(data.documents)) {
        const normalizedDocs = data.documents.map(normalizeDoc);
        setDocs(normalizedDocs);
        setPage(data.pagination.page);
        setTotalPages(data.pagination.total_pages);
        setTotalItems(data.pagination.total || normalizedDocs.length);
        sessionStorage.setItem(sessionKey(pid, "page"), String(data.pagination.page));
      } else {
        setDocs([]);
        setTotalItems(0);
      }
      return data;
    } catch (e) {
      onLog(`Failed to fetch list: ${e}`);
      return null;
    } finally {
      setLoading(false);
    }
  };

  const fetchList = (p = 1) => {
    if (!projectId) return;
    doFetchList(projectId, p, searchTerm, filterDocType, startDate, endDate);
  };

  useEffect(() => {
    if (!projectId) {
      setDocs([]);
      return;
    }

    const filtersChanged =
      searchTerm !== prevFilters.current.searchTerm ||
      filterDocType !== prevFilters.current.filterDocType ||
      startDate !== prevFilters.current.startDate ||
      endDate !== prevFilters.current.endDate;
    const projectChanged = projectId !== prevProjectId.current;

    if (filtersChanged) {
      fetchList(1);
    } else if (projectChanged) {
      const pId = projectId;
      const savedSearch = sessionStorage.getItem(sessionKey(pId, "search")) || "";
      const savedType = sessionStorage.getItem(sessionKey(pId, "type")) || "";
      const savedStart = sessionStorage.getItem(sessionKey(pId, "start")) || "";
      const savedEnd = sessionStorage.getItem(sessionKey(pId, "end")) || "";
      const savedPage = sessionStorage.getItem(sessionKey(pId, "page"));

      setSearchTerm(savedSearch);
      setFilterDocType(savedType);
      setStartDate(savedStart);
      setEndDate(savedEnd);

      const targetPage = savedPage ? parseInt(savedPage) : 1;
      doFetchList(pId, targetPage, savedSearch, savedType, savedStart, savedEnd);

      prevFilters.current = {
        searchTerm: savedSearch,
        filterDocType: savedType,
        startDate: savedStart,
        endDate: savedEnd,
      };
    } else {
      const savedPage = sessionStorage.getItem(sessionKey(projectId, "page"));
      const targetPage = savedPage ? parseInt(savedPage) : 1;
      fetchList(targetPage);
    }

    prevProjectId.current = projectId;
    if (!projectChanged) {
      prevFilters.current = { searchTerm, filterDocType, startDate, endDate };
    }
  }, [projectId, searchTerm, filterDocType, startDate, endDate]);

  return {
    docs,
    loading,
    page,
    totalPages,
    totalItems,
    searchTerm,
    setSearchTerm,
    filterDocType,
    setFilterDocType,
    startDate,
    setStartDate,
    endDate,
    setEndDate,
    setLoading,
    setDocs,
    fetchList,
    doFetchList,
  };
}
