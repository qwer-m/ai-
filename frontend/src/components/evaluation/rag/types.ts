export type RagDatasetType = 'validation' | 'test' | 'challenge' | 'regression';

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
    retrieval_mode: 'vector' | 'hybrid' | 'bm25';
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

