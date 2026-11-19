import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { normalizeDoc } from "./types";
import { fetchKnowledgeList } from "./knowledgeBaseApi";
import type { KnowledgeListResponse } from "./knowledgeBaseApi";
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
  ) => Promise<KnowledgeListResponse | null>;
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

  const prevFilters = useRef({ searchTerm, filterDocType, startDate, endDate });
  const latestRequest = useRef(0);

  useEffect(() => {
    if (!projectId) return;
    sessionStorage.setItem(sessionKey(projectId, "search"), searchTerm);
    sessionStorage.setItem(sessionKey(projectId, "type"), filterDocType);
    sessionStorage.setItem(sessionKey(projectId, "start"), startDate);
    sessionStorage.setItem(sessionKey(projectId, "end"), endDate);
  }, [projectId, searchTerm, filterDocType, startDate, endDate]);

  const doFetchList = async (
    pid: number,
    p: number,
    search: string,
    type: string,
    start: string,
    end: string,
  ): Promise<KnowledgeListResponse | null> => {
    const request = ++latestRequest.current;
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
      // 初始列表、搜索和翻页共享同一读模型，只有最后一次请求能提交结果。
      if (request !== latestRequest.current) return null;

      const pagination = data?.pagination ?? {};
      const resolvedTotalPages = Math.max(1, Number(pagination.total_pages || 1));
      const resolvedPage = Math.max(1, Number(pagination.page || p));
      if (resolvedPage > resolvedTotalPages) {
        return await doFetchList(pid, resolvedTotalPages, search, type, start, end);
      }

      if (Array.isArray(data.documents)) {
        const normalizedDocs = data.documents.map(normalizeDoc);
        setDocs(normalizedDocs);
        setPage(resolvedPage);
        setTotalPages(resolvedTotalPages);
        setTotalItems(Number(pagination.total || normalizedDocs.length));
        sessionStorage.setItem(sessionKey(pid, "page"), String(resolvedPage));
      } else {
        setDocs([]);
        setTotalItems(0);
        setTotalPages(resolvedTotalPages);
      }
      return data;
    } catch (e) {
      if (request === latestRequest.current) onLog(`获取文档列表失败：${e}`);
      return null;
    } finally {
      if (request === latestRequest.current) setLoading(false);
    }
  };

  const fetchList = (p = 1) => {
    if (!projectId) return;
    doFetchList(projectId, p, searchTerm, filterDocType, startDate, endDate);
  };

  useEffect(() => {
    if (!projectId) {
      setDocs([]);
      setLoading(false);
      return;
    }

    const filtersChanged =
      searchTerm !== prevFilters.current.searchTerm ||
      filterDocType !== prevFilters.current.filterDocType ||
      startDate !== prevFilters.current.startDate ||
      endDate !== prevFilters.current.endDate;
    prevFilters.current = { searchTerm, filterDocType, startDate, endDate };
    // 项目切换由工作台 key 重建独立状态；筛选改变才回到第一页。
    fetchList(filtersChanged ? 1 : page);
    return () => {
      // 离开页面或改变查询后，旧响应不得覆盖新范围或提前结束加载状态。
      latestRequest.current += 1;
    };
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
