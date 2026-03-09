from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, func, JSON
from sqlalchemy.orm import relationship, backref
from core.database import Base

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
