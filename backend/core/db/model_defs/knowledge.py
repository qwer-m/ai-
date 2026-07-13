from ._shared import Base, Column, Integer, String, Boolean, JSON, DateTime, ForeignKey, Float, Text, func, UniqueConstraint, relationship, backref, LONGTEXT

class KnowledgeDocument(Base):
    """
    知识库文档模型 (Knowledge Document Model)
    
    系统的核心知识存储实体。
    存储需求文档、测试用例文档等。
    支持文档间的关联 (如测试用例 -> 需求)。
    """
    __tablename__ = "knowledge_documents"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 项目内的序号（仅需求文档使用）
    project_specific_id = Column(Integer, nullable=True, comment="项目内自增ID (用于展示)")
    
    # 文件名
    filename = Column(String(255), nullable=False, comment="文档标题/文件名")
    
    # 文档内容
    content = Column(LONGTEXT, nullable=False, comment="文档完整内容")
    
    # 内容哈希值，用于去重
    content_hash = Column(String(64), nullable=True, index=True, comment="内容SHA256哈希")
    
    # 文档类型：requirement（需求文档）、test_case（测试用例）
    doc_type = Column(String(50), nullable=True, comment="文档类型 (requirement/test_case)")
    
    # 压缩摘要 (Context Compression)
    summary = Column(Text, nullable=True, comment="文档摘要 (用于快速检索)")

    # 解析状态机：用于“上传入队 -> 异步解析 -> 状态查询”的最小闭环。
    # 取值约定：pending / parsing / success / failed
    parse_status = Column(
        String(20),
        nullable=False,
        default="success",
        index=True,
        comment="离线解析状态 (pending/parsing/success/failed)",
    )
    parse_error = Column(Text, nullable=True, comment="离线解析错误信息")
    parsed_at = Column(DateTime, nullable=True, comment="最近一次解析完成时间")
    task_id = Column(String(64), nullable=True, index=True, comment="Celery 任务ID")
    retry_count = Column(Integer, nullable=False, default=0, comment="解析任务重试次数")
    
    # 显示顺序 (用于自定义排序)
    display_order = Column(Float, default=0.0, comment="前端显示排序权重")

    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    
    # 自引用关系：测试用例可以关联到需求文档
    source_doc_id = Column(Integer, ForeignKey('knowledge_documents.id'), nullable=True, comment="源文档ID (如测试用例对应的需求文档)")
    source_doc = relationship("KnowledgeDocument", remote_side=[id], backref="linked_docs")

class ProjectContextSnapshot(Base):
    """
    项目级上下文快照模型 (Project Context Snapshot Model)

    业务目标：
    1. 缓存“项目知识上下文压缩结果”，降低在线重复压缩成本。
    2. 基于语料哈希进行复用/更新判定，减少外部模型波动影响。
    3. 暴露构建状态与失败原因，提升可观测性与排障效率。
    """
    __tablename__ = "project_context_snapshots"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_project_context_snapshots_project"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True, index=True, comment="最近一次构建发起用户ID")
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False, index=True, comment="项目ID")

    snapshot_text = Column(LONGTEXT, nullable=True, comment="项目级上下文压缩快照文本")
    snapshot_version = Column(Integer, nullable=False, default=0, comment="快照版本号（每次成功构建递增）")
    snapshot_fingerprint = Column(
        String(64),
        nullable=True,
        index=True,
        comment="快照文本指纹（用于版本治理与排障）",
    )
    corpus_hash = Column(String(64), nullable=True, index=True, comment="项目知识语料哈希")
    source_doc_count = Column(Integer, nullable=False, default=0, comment="参与构建的文档数量")
    source_fingerprints = Column(LONGTEXT, nullable=True, comment="文档指纹映射(JSON)")

    build_status = Column(
        String(20),
        nullable=False,
        default="pending",
        index=True,
        comment="构建状态 (pending/success/failed)",
    )
    build_error = Column(Text, nullable=True, comment="最近一次构建失败原因")
    last_build_latency_ms = Column(Float, nullable=True, comment="最近一次成功构建耗时（毫秒）")
    rebuild_reason = Column(
        String(30),
        nullable=True,
        comment="构建原因 (full_rebuild/incremental_merge/manual/reuse/no_docs)",
    )

    incremental_merge_count = Column(Integer, nullable=False, default=0, comment="连续增量合并次数")
    last_built_at = Column(DateTime, nullable=True, comment="最近一次构建完成时间")
    last_used_at = Column(DateTime, nullable=True, comment="最近一次被生成链路使用时间")
    last_full_built_at = Column(DateTime, nullable=True, comment="最近一次全量重建时间")

    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class CacheEntry(Base):
    """
    L4级持久化缓存模型 (Cache Entry Model)
    
    用于持久化存储AI调用的结果 (Prompt -> Response)，减少API成本和延迟。
    """
    __tablename__ = "cache_entries"

    # 主键
    id = Column(Integer, primary_key=True, index=True)
    
    # 缓存键哈希 (SHA256)
    key_hash = Column(String(64), unique=True, index=True, nullable=False, comment="缓存Key的哈希值")
    
    # 缓存层级 (L2, L3, L4)
    cache_level = Column(String(10), index=True, nullable=False, comment="缓存级别")
    
    # 缓存内容 (JSON or Text)
    value = Column(Text, nullable=False, comment="缓存的响应内容")
    
    # 元数据 (JSON string)
    metadata_info = Column(Text, nullable=True, comment="元数据 (如模型参数)")
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    
    # 最后访问时间
    last_accessed_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
