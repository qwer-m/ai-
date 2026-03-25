from ._shared import Base, Column, Integer, String, Boolean, JSON, DateTime, ForeignKey, Float, Text, func, UniqueConstraint, relationship, backref, LONGTEXT

class StandardInterface(Base):
    """
    标准接口定义模型 (Standard Interface Model)
    
    用于API测试模块，存储接口定义、参数、Headers等信息。
    支持树状文件夹结构管理接口。
    """
    __tablename__ = "standard_interfaces"

    # 主键
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联用户ID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    # 关联项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 接口/文件夹名称
    name = Column(String(100), nullable=False, comment="接口或文件夹名称")
    
    # 描述
    description = Column(String(255), nullable=True, comment="接口描述")
    
    # 类型: request, folder
    type = Column(String(20), nullable=False, default="request", comment="类型 (request/folder)")
    
    # 父级ID (用于文件夹结构)
    parent_id = Column(Integer, ForeignKey('standard_interfaces.id'), nullable=True, comment="父节点ID")
    
    # --- 请求详情 ---
    method = Column(String(10), nullable=True, comment="HTTP方法 (GET/POST等)")
    base_url = Column(String(255), nullable=True, comment="基础URL")
    api_path = Column(String(255), nullable=True, comment="API路径")
    
    # JSON存储复杂结构
    headers = Column(JSON, nullable=True, comment="请求头配置 (JSON)")  # [{key, value, desc}]
    params = Column(JSON, nullable=True, comment="请求参数配置 (JSON)")   # [{key, value, desc}]
    
    body_mode = Column(String(50), nullable=True, comment="Body模式 (none/json/form-data)") # none, raw, form-data...
    raw_type = Column(String(20), nullable=True, comment="Raw类型 (JSON/Text)")  # JSON, Text...
    body_content = Column(Text, nullable=True, comment="Body内容")
    
    # 额外的测试配置
    test_config = Column(JSON, nullable=True, comment="测试配置 (断言/提取等)") # {testTypes: {...}, ...}

    # 创建时间
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    children = relationship("StandardInterface", backref=backref('parent', remote_side=[id]))

class UITestCase(Base):
    """
    UI测试用例/脚本模型 (UI Test Case Model)
    
    支持树状结构管理UI自动化脚本 (类似于 StandardInterface)。
    每个节点可以是文件夹或具体的脚本文件。
    """
    __tablename__ = "ui_test_cases"

    # 主键
    id = Column(Integer, primary_key=True, index=True)
    
    # 关联项目ID
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    
    # 节点名称 (文件夹名或脚本名)
    name = Column(String(100), nullable=False, comment="名称")
    
    # 描述
    description = Column(String(255), nullable=True, comment="描述")
    
    # 类型: 'folder' 或 'file'
    type = Column(String(20), nullable=False, default="file", comment="类型 (folder/file)")
    
    # 父节点ID (用于树状结构)
    parent_id = Column(Integer, ForeignKey('ui_test_cases.id'), nullable=True, comment="父节点ID")
    
    # --- 脚本详情 (仅 file 类型有效) ---
    # 脚本内容 (Python代码)
    script_content = Column(Text, nullable=True, comment="Python脚本内容")
    
    # 关联的需求描述 (Requirements)
    requirements = Column(Text, nullable=True, comment="关联的测试需求/用例描述")
    
    # 自动化类型: web / app
    automation_type = Column(String(20), default="web", comment="自动化类型 (web/app)")
    
    # 目标配置 (URL 或 AppID)
    target_config = Column(String(255), nullable=True, comment="目标URL或AppID")

    # 创建/更新时间
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # 自关联 (Children)
    children = relationship("UITestCase", backref=backref('parent', remote_side=[id]), cascade="all, delete-orphan")
