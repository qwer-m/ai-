from ._shared import (
    Base,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)


class AgentDefinition(Base):
    """项目内可版本化的智能体定义。"""

    __tablename__ = "agent_definitions"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "agent_key",
            "version",
            name="uq_agent_definition_project_key_version",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    agent_key = Column(String(120), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default="")
    instructions = Column(Text, nullable=False)
    model = Column(String(120), nullable=False, default="")
    output_schema = Column(JSON, nullable=False, default=dict)
    runtime_config = Column(JSON, nullable=False, default=dict)
    version = Column(Integer, nullable=False, default=1)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    builtin = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentToolDefinition(Base):
    """工具元数据；真正的处理函数只能从受信任注册表解析。"""

    __tablename__ = "agent_tool_definitions"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "tool_key",
            name="uq_agent_tool_project_key",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    tool_key = Column(String(160), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default="")
    handler_key = Column(String(200), nullable=False)
    input_schema = Column(JSON, nullable=False, default=dict)
    output_schema = Column(JSON, nullable=False, default=dict)
    risk_level = Column(String(20), nullable=False, default="low")
    requires_approval = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    builtin = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentToolBinding(Base):
    """智能体到工具的显式最小权限绑定。"""

    __tablename__ = "agent_tool_bindings"
    __table_args__ = (
        UniqueConstraint(
            "agent_definition_id",
            "tool_definition_id",
            name="uq_agent_tool_binding",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    agent_definition_id = Column(
        Integer,
        ForeignKey("agent_definitions.id"),
        nullable=False,
        index=True,
    )
    tool_definition_id = Column(
        Integer,
        ForeignKey("agent_tool_definitions.id"),
        nullable=False,
        index=True,
    )
    enabled = Column(Boolean, nullable=False, default=True)
    binding_config = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class AgentWorkflowDefinition(Base):
    """数据驱动的工作流定义，不在代码中维护固定阶段顺序。"""

    __tablename__ = "agent_workflow_definitions"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "workflow_key",
            "version",
            name="uq_agent_workflow_project_key_version",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    workflow_key = Column(String(120), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default="")
    definition = Column(JSON, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    builtin = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentRun(Base):
    """工作流一次持久运行，是平台调度与恢复的事实来源。"""

    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    workflow_definition_id = Column(
        Integer,
        ForeignKey("agent_workflow_definitions.id"),
        nullable=False,
        index=True,
    )
    status = Column(String(30), nullable=False, default="pending", index=True)
    current_node_key = Column(String(160), nullable=True, index=True)
    input_payload = Column(JSON, nullable=False, default=dict)
    run_context = Column(JSON, nullable=False, default=dict)
    output_payload = Column(JSON, nullable=False, default=dict)
    error_message = Column(Text, nullable=False, default="")
    task_id = Column(String(120), nullable=True, index=True)
    claim_token = Column(String(120), nullable=True, index=True)
    heartbeat_at = Column(DateTime, nullable=True, index=True)
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    parent_run_id = Column(Integer, ForeignKey("agent_runs.id"), nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentNodeRun(Base):
    """工作流节点运行记录，支持独立重试、诊断与审批恢复。"""

    __tablename__ = "agent_node_runs"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "node_key",
            "attempt",
            name="uq_agent_node_run_attempt",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("agent_runs.id"), nullable=False, index=True)
    node_key = Column(String(160), nullable=False, index=True)
    node_type = Column(String(30), nullable=False)
    agent_definition_id = Column(
        Integer,
        ForeignKey("agent_definitions.id"),
        nullable=True,
        index=True,
    )
    tool_definition_id = Column(
        Integer,
        ForeignKey("agent_tool_definitions.id"),
        nullable=True,
        index=True,
    )
    status = Column(String(30), nullable=False, default="pending", index=True)
    attempt = Column(Integer, nullable=False, default=1)
    input_payload = Column(JSON, nullable=False, default=dict)
    output_payload = Column(JSON, nullable=False, default=dict)
    sdk_state = Column(JSON, nullable=False, default=dict)
    error_message = Column(Text, nullable=False, default="")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentRunEvent(Base):
    """追加式运行事件，供审计、监控和前端增量展示使用。"""

    __tablename__ = "agent_run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_run_event_sequence"),
    )

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("agent_runs.id"), nullable=False, index=True)
    node_run_id = Column(Integer, ForeignKey("agent_node_runs.id"), nullable=True, index=True)
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(80), nullable=False, index=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class AgentApproval(Base):
    """高风险工具或人工门禁的持久审批请求。"""

    __tablename__ = "agent_approvals"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("agent_runs.id"), nullable=False, index=True)
    node_run_id = Column(Integer, ForeignKey("agent_node_runs.id"), nullable=False, index=True)
    tool_definition_id = Column(
        Integer,
        ForeignKey("agent_tool_definitions.id"),
        nullable=True,
        index=True,
    )
    status = Column(String(30), nullable=False, default="pending", index=True)
    request_payload = Column(JSON, nullable=False, default=dict)
    decision_payload = Column(JSON, nullable=False, default=dict)
    requested_at = Column(DateTime, server_default=func.now(), index=True)
    decided_at = Column(DateTime, nullable=True)
    decided_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
