export type RagDatasetType = 'validation' | 'test' | 'challenge' | 'regression';

export type RagRetrievalMode = 'vector' | 'keyword' | 'hybrid' | 'bm25';

export type RagRetrieveContextDebugRequest = {
  project_id: number;
  query: string;
  limit?: number;
  max_tokens?: number;
  retrieval_mode?: RagRetrievalMode;
  recall_top_k?: number;
  rerank_top_n?: number;
  max_chunks_per_doc?: number;
  min_docs?: number;
  enable_query_rewrite?: boolean;
  enable_rerank?: boolean;
  title_weight?: number;
  keyword_weight?: number;
  vector_weight?: number;
  redundancy_threshold?: number;
  doc_types?: string[] | string;
  enable_biz_key_expansion?: boolean;
  related_top_k?: number;
};

export type RagEvalConfig = {
  dataset_selector: {
    dataset_type: RagDatasetType | 'all';
    tags: string[];
    difficulty: 'all' | 'easy' | 'medium' | 'hard';
    sample_range: string;
    sample_ids: number[];
    enabled_only: boolean;
  };
  retrieval: {
    top_k: number;
    rerank_top_n: number;
    retrieval_mode: 'vector' | 'keyword' | 'hybrid' | 'bm25';
    score_threshold: number | null;
  };
  context: {
    max_tokens: number;
    deduplication: boolean;
    compression: boolean;
    keep_order: boolean;
  };
  advanced: {
    enable_query_rewrite: boolean;
    enable_multi_query: boolean;
    enable_metadata_filter: boolean;
    enable_rerank: boolean;
    enable_generation: boolean;
  };
  model: {
    embedding_model: string;
    reranker_model: string;
    llm_model: string;
    judge_model: string;
  };
  judge: {
    answer_eval_mode: 'rule' | 'llm' | 'hybrid';
    faithfulness_eval_mode: 'rule' | 'llm' | 'hybrid';
  };
  run_control: {
    sample_range: string;
    only_unfinished: boolean;
  };
};

export type RagDatasetRow = {
  id: number;
  name: string;
  type: RagDatasetType;
  description?: string | null;
  sample_count?: number;
};

