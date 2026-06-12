from ._shared import Base, Column, Integer, String, Boolean, JSON, DateTime, ForeignKey, Float, Text, func, UniqueConstraint, relationship, backref, LONGTEXT

class User(Base):
    """
    用户模型 (User Model)
    
    存储系统用户信息。
    作为大多数其他模型的根关联实体 (通过 user_id)。
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False, comment="用户名")
    email = Column(String(100), unique=True, index=True, nullable=True, comment="邮箱地址")
    hashed_password = Column(String(255), nullable=False, comment="哈希加密后的密码")
    is_active = Column(Boolean, default=True, comment="账户是否激活")
    created_at = Column(DateTime, server_default=func.now(), comment="账户创建时间")

class SystemConfig(Base):
    """
    系统配置模型 (System Config Model)
    
    用于存储系统级配置，如AI模型提供商设置、API密钥等。
    支持版本控制 (version) 和软删除/激活状态 (is_active)。
    """
    __tablename__ = "system_configs"

    # 主键
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID (配置归属人)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 配置版本号 (乐观锁/审计)
    version = Column(Integer, default=1, nullable=False, comment="配置版本号")
    
    # 是否激活 (同一时间应只有一个激活配置)
    is_active = Column(Integer, default=0, index=True, comment="是否为当前激活配置 (0:否, 1:是)")
    
    # 提供商类型: dashscope, openai, ollama
    provider = Column(String(50), nullable=False, comment="模型提供商 (dashscope, openai, ollama)")
    
    # API密钥 (加密存储)
    api_key = Column(Text, nullable=True, comment="API密钥 (加密存储)")
    
    # API基础URL (用于本地模型或OpenAI兼容接口)
    base_url = Column(String(255), nullable=True, comment="API基础URL")
    
    # 模型名称 (文本模型)
    model_name = Column(String(100), nullable=False, comment="主聊天模型名称")
    
    # 视觉语言模型 (图像模型)
    vl_model_name = Column(String(100), nullable=True, comment="视觉模型名称")
    
    # 轻量模型 (上下文压缩模型)
    turbo_model_name = Column(String(100), nullable=True, comment="轻量/快速模型名称")
    
    # 额外元数据 (JSON)
    metadata_info = Column(JSON, nullable=True, comment="额外配置元数据")
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    
    # 更新时间
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class Project(Base):
    """
    项目模型 (Project Model)
    
    用于存储项目信息，支持无限层级的树状项目结构 (父子项目)。
    是测试用例、文档、执行记录等数据的核心容器。
    """
    __tablename__ = "projects"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID (项目所有者)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 项目名称
    name = Column(String(100), nullable=False, comment="项目名称")
    
    # 项目描述
    description = Column(String(255), nullable=True, comment="项目描述")
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    
    # 层级关系字段
    parent_id = Column(Integer, ForeignKey('projects.id'), nullable=True, comment="父项目ID")
    level = Column(Integer, default=1, comment="项目层级 (1:根, 2:子...)")
    
    # 层级关系 (自关联)
    children = relationship("Project", backref=backref('parent', remote_side=[id]))

class TestGeneration(Base):
    """
    测试生成记录模型 (Test Generation Model)
    
    记录用户输入的测试需求和AI生成的测试用例原始结果。
    """
    __tablename__ = "test_generations"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 测试需求文本
    requirement_text = Column(LONGTEXT, nullable=True, comment="原始需求文本")
    
    # 生成的测试用例结果
    generated_result = Column(LONGTEXT, nullable=True, comment="AI生成的测试用例内容")
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())

class UIExecution(Base):
    """
    UI自动化执行记录模型 (UI Execution Model)
    
    存储UI自动化测试的执行请求、生成的脚本、以及执行结果。
    关联到具体的测试用例 (KnowledgeDocument)。
    """
    __tablename__ = "ui_executions"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 关联的测试用例ID（可选）
    test_case_id = Column(Integer, ForeignKey('knowledge_documents.id'), nullable=True, comment="关联的测试用例ID")
    
    # 测试URL（Web自动化）
    url = Column(String(255), nullable=True, comment="测试目标URL")
    
    # App包名或路径（App自动化）
    app_info = Column(String(255), nullable=True, comment="App包名或路径")
    
    # 任务描述
    task_description = Column(Text, nullable=False, comment="具体的执行任务描述")
    
    # 自动化类型：web（Web自动化）或app（App自动化）
    automation_type = Column(String(20), nullable=False, default="web", comment="自动化类型 (web/app)")
    
    # 生成的自动化脚本
    generated_script = Column(Text, nullable=True, comment="生成的Playwright/Appium脚本")
    
    # 截图路径列表 (JSON)
    screenshot_paths = Column(JSON, nullable=True, comment="执行过程中的截图路径列表")
    
    # 质量评分 (0-10)
    quality_score = Column(Float, nullable=True, comment="自动化脚本质量评分")
    
    # 评估结果
    evaluation_result = Column(Text, nullable=True, comment="详细的评估报告")
    
    # 执行结果
    execution_result = Column(Text, nullable=True, comment="脚本执行的stdout/stderr")
    
    # 执行状态：success（成功）、failed（失败）、pending（待执行）
    status = Column(String(20), nullable=False, default="pending", comment="执行状态")
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())

class UIErrorOperation(Base):
    """
    UI自动化错误操作分析模型 (UI Error Operation Model)
    
    用于存储UI自动化测试失败时的详细分析，包括失败操作与正确操作的对比。
    用于自愈 (Self-Healing) 或报告分析。
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
    failed_operation = Column(Text, nullable=False, comment="导致失败的操作描述")
    
    # 正确操作描述
    correct_operation = Column(Text, nullable=True, comment="建议的正确操作")
    
    # AI对比分析结果
    ai_comparison_result = Column(Text, nullable=True, comment="AI分析的错误原因")
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())

class APIExecution(Base):
    """
    API测试执行记录模型 (API Execution Model)
    
    存储API测试的执行请求、生成的脚本、以及执行结果。
    """
    __tablename__ = "api_executions"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # API测试需求
    requirement = Column(Text, nullable=False, comment="API测试需求描述")
    
    # 生成的测试脚本
    generated_script = Column(Text, nullable=True, comment="生成的Python API测试脚本")
    
    # 执行结果
    execution_result = Column(Text, nullable=True, comment="脚本执行结果")

    # 结构化报告 (JSON)
    structured_report = Column(JSON, nullable=True, comment="结构化的测试报告数据")
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())

class Evaluation(Base):
    """
    测试评估模型 (Evaluation Model)
    
    用于存储对生成的测试用例质量的评估结果。
    """
    __tablename__ = "evaluations"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 评估的测试用例内容
    test_case_content = Column(Text, nullable=False, comment="被评估的测试用例内容")
    
    # 评估结果
    evaluation_result = Column(Text, nullable=True, comment="AI生成的评估报告")
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())

class TestGenerationComparison(Base):
    """
    测试用例修改对比模型 (Test Generation Comparison Model)
    
    记录AI生成的用例与用户人工修改后用例的差异，用于RLHF或模型优化。
    """
    __tablename__ = "test_generation_comparisons"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 生成的测试用例
    generated_test_case = Column(LONGTEXT, nullable=False, comment="AI原始生成的用例")
    
    # 修改后的测试用例
    modified_test_case = Column(LONGTEXT, nullable=False, comment="用户修改后的用例")
    
    # AI对比分析结果
    comparison_result = Column(LONGTEXT, nullable=True, comment="差异分析结果")
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())

class LogEntry(Base):
    """
    操作日志模型 (Log Entry Model)
    
    用于存储用户操作日志和系统运行日志。
    """
    __tablename__ = "operation_logs"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 日志类型：user（用户操作日志）、system（系统日志）
    log_type = Column(String(20), nullable=False, comment="日志类型 (user/system)")
    
    # 日志内容
    message = Column(Text, nullable=False, comment="日志详情")
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())

class RecallMetric(Base):
    """
    RAG召回率指标模型 (Recall Metric Model)
    
    用于存储RAG系统的检索质量评估结果 (召回率)。
    """
    __tablename__ = "recall_metrics"

    # 主键ID
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联的项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 检索到的项目列表
    retrieved_items = Column(JSON, nullable=True, comment="检索到的文档ID列表")
    
    # 相关项目列表
    relevant_items = Column(JSON, nullable=True, comment="实际相关的文档ID列表 (Ground Truth)")
    
    # 召回率分数
    recall_score = Column(Float, nullable=False, comment="计算出的召回率")
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
