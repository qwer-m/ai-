from ._shared import Base, Column, Integer, String, Boolean, JSON, DateTime, ForeignKey, Text, func, UniqueConstraint, relationship, backref, LONGTEXT

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
    
    # 日志类型：user（用户操作）、system（系统）、clear_marker（清空水位）
    log_type = Column(String(20), nullable=False, comment="日志类型")
    
    # 日志内容
    message = Column(Text, nullable=False, comment="日志详情")
    
    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
