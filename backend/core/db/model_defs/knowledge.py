from ._shared import Base, Column, Integer, String, Boolean, JSON, DateTime, ForeignKey, Float, Text, func, relationship, backref, LONGTEXT

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
    project_specific_id = Column(Integer, nullable=False, comment="项目内自增ID (用于展示)")
    
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
