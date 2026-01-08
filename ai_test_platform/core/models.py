from sqlalchemy import Column, Integer, String, Boolean, JSON, DateTime, ForeignKey, Float, Text, func
from sqlalchemy.orm import relationship, backref
from sqlalchemy.dialects.mysql import LONGTEXT
from core.database import Base

class User(Base):
    """用户模型"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=True)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())

class SystemConfig(Base):
    """系统配置模型
    
    用于存储系统级配置，如AI模型提供商设置、API密钥等。
    """
    __tablename__ = "system_configs"

    # 主键
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 配置版本号 (乐观锁/审计)
    version = Column(Integer, default=1, nullable=False)
    
    # 是否激活 (同一时间应只有一个激活配置)
    is_active = Column(Integer, default=0, index=True)  # 0: False, 1: True (sqlite/mysql boolean)
    
    # 提供商类型: dashscope, openai, ollama
    provider = Column(String(50), nullable=False)
    
    # API密钥 (加密存储)
    api_key = Column(Text, nullable=True)
    
    # API基础URL (用于本地模型或OpenAI兼容接口)
    base_url = Column(String(255), nullable=True)
    
    # 模型名称 (文本模型)
    model_name = Column(String(100), nullable=False)
    
    # 视觉语言模型 (图像模型)
    vl_model_name = Column(String(100), nullable=True)
    
    # 轻量模型 (上下文压缩模型)
    turbo_model_name = Column(String(100), nullable=True)
    
    # 额外元数据 (JSON)
    metadata_info = Column(JSON, nullable=True)
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    
    # 更新时间
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class Project(Base):
    """项目模型
    
    用于存储项目信息，支持项目层级结构。
    """
    __tablename__ = "projects"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 项目名称
    name = Column(String(100), nullable=False)
    
    # 项目描述
    description = Column(String(255), nullable=True)
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    
    # 层级关系字段
    parent_id = Column(Integer, ForeignKey('projects.id'), nullable=True)  # 父项目ID
    level = Column(Integer, default=1)  # 项目层级：1-根项目, 2-子项目, 3-孙子项目
    
    # 层级关系
    children = relationship("Project", backref=backref('parent', remote_side=[id]))


class TestGeneration(Base):
    """测试生成模型
    
    用于存储测试用例生成的记录。
    """
    __tablename__ = "test_generations"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 测试需求文本
    requirement_text = Column(LONGTEXT, nullable=True)
    
    # 生成的测试用例结果
    generated_result = Column(LONGTEXT, nullable=True)
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())


class UIExecution(Base):
    """UI自动化执行模型
    
    用于存储UI自动化测试的执行记录。
    """
    __tablename__ = "ui_executions"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 关联的测试用例ID（可选）
    test_case_id = Column(Integer, ForeignKey('knowledge_documents.id'), nullable=True)
    
    # 测试URL（Web自动化）
    url = Column(String(255), nullable=True)
    
    # App包名或路径（App自动化）
    app_info = Column(String(255), nullable=True)
    
    # 任务描述
    task_description = Column(Text, nullable=False)
    
    # 自动化类型：web（Web自动化）或app（App自动化）
    automation_type = Column(String(20), nullable=False, default="web")
    
    # 生成的自动化脚本
    generated_script = Column(Text, nullable=True)
    
    # 执行结果
    execution_result = Column(Text, nullable=True)
    
    # 执行状态：success（成功）、failed（失败）、pending（待执行）
    status = Column(String(20), nullable=False, default="pending")
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())


class UIErrorOperation(Base):
    """UI自动化错误操作模型
    
    用于存储UI自动化测试中的错误操作记录，包括失败操作和正确操作的对比分析。
    """
    __tablename__ = "ui_error_operations"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 关联的UI执行记录ID
    ui_execution_id = Column(Integer, ForeignKey('ui_executions.id'), nullable=True)
    
    # 失败操作描述
    failed_operation = Column(Text, nullable=False)
    
    # 正确操作描述
    correct_operation = Column(Text, nullable=True)
    
    # AI对比分析结果
    ai_comparison_result = Column(Text, nullable=True)
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())


class APIExecution(Base):
    """API测试执行模型
    
    用于存储API测试的执行记录。
    """
    __tablename__ = "api_executions"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # API测试需求
    requirement = Column(Text, nullable=False)
    
    # 生成的测试脚本
    generated_script = Column(Text, nullable=True)
    
    # 执行结果
    execution_result = Column(Text, nullable=True)

    # 结构化报告 (JSON)
    structured_report = Column(JSON, nullable=True)
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())


class Evaluation(Base):
    """测试评估模型
    
    用于存储测试用例质量评估的记录。
    """
    __tablename__ = "evaluations"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 评估的测试用例内容
    test_case_content = Column(Text, nullable=False)
    
    # 评估结果
    evaluation_result = Column(Text, nullable=True)
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())


class TestGenerationComparison(Base):
    """测试用例比较模型
    
    用于存储生成的测试用例与用户修改后的测试用例的比较记录。
    """
    __tablename__ = "test_generation_comparisons"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 生成的测试用例
    generated_test_case = Column(Text, nullable=False)
    
    # 修改后的测试用例
    modified_test_case = Column(Text, nullable=False)
    
    # AI对比分析结果
    comparison_result = Column(Text, nullable=True)
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())


class LogEntry(Base):
    """操作日志模型
    
    用于存储用户操作日志和系统日志。
    """
    __tablename__ = "operation_logs"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 日志类型：user（用户操作日志）、system（系统日志）
    log_type = Column(String(20), nullable=False)
    
    # 日志内容
    message = Column(Text, nullable=False)
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())


class RecallMetric(Base):
    """召回率指标模型
    
    用于存储召回率计算结果。
    """
    __tablename__ = "recall_metrics"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 检索到的项目列表
    retrieved_items = Column(JSON, nullable=True)
    
    # 相关项目列表
    relevant_items = Column(JSON, nullable=True)
    
    # 召回率分数
    recall_score = Column(Float, nullable=False)
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())


class KnowledgeDocument(Base):
    """知识库文档模型
    
    用于存储各种文档，包括需求文档和测试用例文档。
    支持文档之间的关联关系，例如测试用例关联到需求文档。
    """
    __tablename__ = "knowledge_documents"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 项目内的序号（仅需求文档使用）
    project_specific_id = Column(Integer, nullable=True)
    
    # 文件名
    filename = Column(String(255), nullable=False)
    
    # 文档内容
    content = Column(LONGTEXT, nullable=False)
    
    # 内容哈希值，用于去重
    content_hash = Column(String(64), nullable=True, index=True)
    
    # 文档类型：requirement（需求文档）、test_case（测试用例）
    doc_type = Column(String(50), nullable=True)
    
    # 压缩摘要 (Context Compression)
    summary = Column(Text, nullable=True)
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    
    # 自引用关系：测试用例可以关联到需求文档
    source_doc_id = Column(Integer, ForeignKey('knowledge_documents.id'), nullable=True)
    source_doc = relationship("KnowledgeDocument", remote_side=[id], backref="linked_docs")


class CacheEntry(Base):
    """缓存条目模型
    
    用于持久化存储缓存数据（L2-L4）。
    """
    __tablename__ = "cache_entries"

    # 主键
    id = Column(Integer, primary_key=True, index=True)
    
    # 缓存键哈希 (SHA256)
    key_hash = Column(String(64), unique=True, index=True, nullable=False)
    
    # 缓存层级 (L2, L3, L4)
    cache_level = Column(String(10), index=True, nullable=False)
    
    # 缓存内容 (JSON or Text)
    value = Column(Text, nullable=False)
    
    # 元数据 (JSON string)
    metadata_info = Column(Text, nullable=True)
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    
    # 最后访问时间
    last_accessed_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
