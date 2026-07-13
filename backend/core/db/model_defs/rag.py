import hashlib

from sqlalchemy import event

from ._shared import Base, Column, Integer, String, Boolean, JSON, DateTime, ForeignKey, Float, Text, func, UniqueConstraint, relationship, backref, LONGTEXT


def _build_query_hash(query: str | None) -> str:
    normalized = str(query or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

class RagDataset(Base):
    """
    RAG 评测数据集。
    """
    __tablename__ = "rag_datasets"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_rag_datasets_user_name"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(120), nullable=False, index=True, comment="数据集名称")
    type = Column(String(30), nullable=False, index=True, comment="validation/test/challenge/regression")
    description = Column(Text, nullable=True, comment="数据集描述")
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class RagDatasetSample(Base):
    """
    RAG 评测样本。
    """
    __tablename__ = "rag_dataset_samples"
    __table_args__ = (
        UniqueConstraint("dataset_id", "query_hash", name="uq_rag_dataset_samples_dataset_query_hash"),
    )

    id = Column(Integer, primary_key=True, index=True)
    dataset_id = Column(Integer, ForeignKey("rag_datasets.id"), nullable=False, index=True)
    query = Column(Text, nullable=False, comment="问题")
    query_hash = Column(String(64), nullable=False, index=True, comment="问题文本SHA256，用于长文本去重")
    gold_docs = Column(JSON, nullable=True, comment="标准文档ID/名称列表")
    gold_chunks = Column(JSON, nullable=True, comment="标准chunk_id列表")
    gold_answer = Column(LONGTEXT, nullable=True, comment="标准答案")
    answer_points = Column(JSON, nullable=True, comment="关键点列表")
    tags = Column(JSON, nullable=True, comment="标签列表")
    difficulty = Column(String(20), nullable=False, default="medium", index=True, comment="easy/medium/hard")
    metadata_filters = Column(JSON, nullable=True, comment="可选检索过滤条件")
    expected_doc_version = Column(String(50), nullable=True, comment="期望文档版本")
    enabled = Column(Boolean, nullable=False, default=True, index=True, comment="是否启用")
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


@event.listens_for(RagDatasetSample.query, "set", retval=True)
def _sync_rag_sample_query_hash(target, value, oldvalue, initiator):
    target.query_hash = _build_query_hash(value)
    return value


@event.listens_for(RagDatasetSample, "before_insert")
@event.listens_for(RagDatasetSample, "before_update")
def _ensure_rag_sample_query_hash(mapper, connection, target):
    target.query_hash = _build_query_hash(target.query)

class RagEvalRun(Base):
    """
    RAG 评测运行记录。
    """
    __tablename__ = "rag_eval_runs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    dataset_id = Column(Integer, ForeignKey("rag_datasets.id"), nullable=False, index=True)
    run_name = Column(String(150), nullable=True, comment="运行名称")
    config_json = Column(JSON, nullable=False, comment="评测配置")
    status = Column(String(30), nullable=False, default="pending", index=True, comment="pending/running/success/failed/stopped")
    total_samples = Column(Integer, nullable=False, default=0)
    finished_samples = Column(Integer, nullable=False, default=0)
    cursor = Column(Integer, nullable=False, default=0, comment="断点位置")
    stop_requested = Column(Boolean, nullable=False, default=False, comment="是否请求停止")
    metrics_json = Column(JSON, nullable=True, comment="汇总指标")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class RagEvalSampleResult(Base):
    """
    RAG 评测样本结果。
    """
    __tablename__ = "rag_eval_sample_results"
    __table_args__ = (
        UniqueConstraint("run_id", "sample_id", name="uq_rag_eval_sample_results_run_sample"),
    )

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("rag_eval_runs.id"), nullable=False, index=True)
    sample_id = Column(Integer, ForeignKey("rag_dataset_samples.id"), nullable=False, index=True)
    retrieved_chunks = Column(JSON, nullable=True, comment="原始召回结果")
    reranked_chunks = Column(JSON, nullable=True, comment="重排结果")
    first_hit_rank = Column(Integer, nullable=True, comment="首命中排名")
    recall_hit = Column(Boolean, nullable=False, default=False, index=True, comment="是否命中")
    answer_text = Column(LONGTEXT, nullable=True, comment="模型回答")
    answer_correct = Column(Boolean, nullable=False, default=False, index=True, comment="答案是否正确")
    answer_correctness_score = Column(Float, nullable=True, comment="答案正确性评分")
    faithfulness_score = Column(Float, nullable=True, comment="忠实性评分")
    context_precision = Column(Float, nullable=True, comment="上下文精确率")
    context_recall = Column(Float, nullable=True, comment="上下文召回率")
    failure_reason = Column(String(50), nullable=True, index=True, comment="失败归因")
    failure_detail = Column(Text, nullable=True, comment="失败细节")
    latency_ms = Column(Float, nullable=True)
    retrieval_latency_ms = Column(Float, nullable=True)
    generation_latency_ms = Column(Float, nullable=True)
    token_usage_json = Column(JSON, nullable=True)
    cost_json = Column(JSON, nullable=True)
    detail_json = Column(JSON, nullable=True, comment="完整细节")
    created_at = Column(DateTime, server_default=func.now(), index=True)

class RagEvalCandidate(Base):
    """
    RAG 评测候选回流记录。
    用于沉淀真实 bad case，支持审核后加入 challenge / regression 数据集。
    """
    __tablename__ = "rag_eval_candidates"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_rag_eval_candidates_source"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True, comment="候选归属用户ID")
    source_type = Column(String(30), nullable=False, index=True, comment="debug_log/eval_result/online_query")
    source_id = Column(Integer, nullable=False, index=True, comment="来源记录ID")
    query = Column(Text, nullable=False, comment="原始query")
    retrieved_chunks = Column(JSON, nullable=True, comment="召回/重排片段")
    answer_text = Column(LONGTEXT, nullable=True, comment="模型回答")
    failure_reason = Column(String(50), nullable=True, index=True, comment="失败归因")
    judge_score_json = Column(JSON, nullable=True, comment="评分信息")
    suggested_dataset_type = Column(String(20), nullable=False, default="challenge", index=True, comment="challenge/regression")
    status = Column(String(20), nullable=False, default="pending", index=True, comment="pending/approved/rejected")
    suggested_gold_docs = Column(JSON, nullable=True, comment="建议 gold_docs")
    suggested_gold_chunks = Column(JSON, nullable=True, comment="建议 gold_chunks")
    suggested_answer_points = Column(JSON, nullable=True, comment="建议 answer_points")
    notes = Column(Text, nullable=True, comment="候选备注")
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
