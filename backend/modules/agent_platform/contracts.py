from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


AgentRunStatus = Literal[
    "pending",
    "running",
    "waiting_approval",
    "success",
    "failed",
    "cancelled",
]
NodeRunStatus = Literal[
    "pending",
    "running",
    "waiting_approval",
    "success",
    "failed",
    "cancelled",
]


class AgentMapConfig(BaseModel):
    """Agent 映射节点配置：按独立实例并发执行、逐项落盘并汇总结果。"""

    items_key: str = Field(default="items", min_length=1, max_length=120)
    output_key: str = Field(default="items", min_length=1, max_length=120)
    max_items: int = Field(default=100, ge=1, le=500)
    max_concurrency: int = Field(default=1, ge=1, le=16)
    allow_empty: bool = False
    item_postprocessor: str | None = Field(default=None, min_length=1, max_length=200)


class WorkflowNode(BaseModel):
    node_key: str = Field(min_length=1, max_length=160)
    node_type: Literal["agent", "agent_network", "agent_map", "tool"]
    reference_key: str = Field(min_length=1, max_length=200)
    depends_on: list[str] = Field(default_factory=list)
    max_attempts: int = Field(default=1, ge=1, le=5)
    time_budget_seconds: int | None = Field(default=None, ge=1, le=7200)
    input_mapping: dict[str, str] = Field(default_factory=dict)
    map_config: AgentMapConfig | None = None

    @model_validator(mode="after")
    def validate_map_config(self) -> "WorkflowNode":
        if self.node_type == "agent_map" and self.map_config is None:
            raise ValueError("agent_map 节点必须配置 map_config")
        if self.node_type != "agent_map" and self.map_config is not None:
            raise ValueError("只有 agent_map 节点可以配置 map_config")
        return self


class WorkflowDisplayStage(BaseModel):
    """工作流的业务展示阶段，由定义数据映射到真实节点。"""

    stage_key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=300)
    node_keys: list[str] = Field(min_length=1)


class WorkflowGraph(BaseModel):
    execution_mode: Literal["dag"] = "dag"
    nodes: list[WorkflowNode] = Field(min_length=1)
    output_node_key: str = Field(min_length=1, max_length=160)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    display_stages: list[WorkflowDisplayStage] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowGraph":
        keys = [node.node_key for node in self.nodes]
        if len(keys) != len(set(keys)):
            raise ValueError("工作流节点键不能重复")
        key_set = set(keys)
        if self.output_node_key not in key_set:
            raise ValueError("输出节点不存在")
        stage_keys = [stage.stage_key for stage in self.display_stages]
        if len(stage_keys) != len(set(stage_keys)):
            raise ValueError("工作流展示阶段键不能重复")
        for stage in self.display_stages:
            unknown_stage_nodes = set(stage.node_keys) - key_set
            if unknown_stage_nodes:
                raise ValueError(
                    f"展示阶段 {stage.stage_key} 引用了不存在的节点: "
                    f"{sorted(unknown_stage_nodes)}"
                )
        for node in self.nodes:
            unknown = set(node.depends_on) - key_set
            if unknown:
                raise ValueError(
                    f"节点 {node.node_key} 引用了不存在的依赖: {sorted(unknown)}"
                )
            if node.node_key in node.depends_on:
                raise ValueError(f"节点 {node.node_key} 不能依赖自身")

        visiting: set[str] = set()
        visited: set[str] = set()
        dependencies = {node.node_key: node.depends_on for node in self.nodes}

        def visit(node_key: str) -> None:
            if node_key in visited:
                return
            if node_key in visiting:
                raise ValueError("工作流不能包含循环依赖")
            visiting.add(node_key)
            for dependency in dependencies[node_key]:
                visit(dependency)
            visiting.remove(node_key)
            visited.add(node_key)

        for key in keys:
            visit(key)
        return self

    def execution_order(self) -> list[WorkflowNode]:
        indexed = {node.node_key: node for node in self.nodes}
        ordered: list[WorkflowNode] = []
        emitted: set[str] = set()
        while len(ordered) < len(self.nodes):
            ready = [
                node
                for node in self.nodes
                if node.node_key not in emitted
                and set(node.depends_on).issubset(emitted)
            ]
            if not ready:
                raise ValueError("工作流没有可执行节点")
            for node in ready:
                ordered.append(indexed[node.node_key])
                emitted.add(node.node_key)
        return ordered


class AgentProgramDefinition(BaseModel):
    """兼容顶层动态执行；新流程优先在 DAG 中使用 agent_network 节点。"""

    execution_mode: Literal["agent_network"] = "agent_network"
    entry_agent_key: str = Field(min_length=1, max_length=120)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=2, ge=1, le=5)
    time_budget_seconds: int | None = Field(default=None, ge=1, le=7200)
    required_artifact_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
    )


ExecutionDefinition = WorkflowGraph | AgentProgramDefinition


def parse_execution_definition(value: Any) -> ExecutionDefinition:
    """按显式执行模式解析定义，兼容未声明模式的历史 DAG。"""

    raw = dict(value or {})
    execution_mode = raw.get("execution_mode")
    if execution_mode == "agent_network":
        return AgentProgramDefinition.model_validate(raw)
    if execution_mode not in (None, "dag"):
        raise ValueError(f"未知的工作流执行模式: {execution_mode}")
    return WorkflowGraph.model_validate(raw)


class AgentDefinitionCreate(BaseModel):
    project_id: int
    agent_key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    instructions: str = Field(min_length=1)
    model: str = ""
    output_schema: dict[str, Any] = Field(default_factory=dict)
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)


class WorkflowDefinitionCreate(BaseModel):
    project_id: int
    workflow_key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    definition: ExecutionDefinition
    version: int = Field(default=1, ge=1)


class AgentRunExecutionLimits(BaseModel):
    """兼容旧客户端的用量配置结构；当前仅记录，不再阻断 Agent 调用。"""

    max_requests: int | None = Field(default=None, ge=1)
    max_input_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    max_total_tokens: int | None = Field(default=None, ge=1)


class AgentRunCreate(BaseModel):
    project_id: int
    workflow_key: str = Field(min_length=1, max_length=120)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    execution_limits: AgentRunExecutionLimits | None = None


class AgentToolBindingRequest(BaseModel):
    project_id: int
    agent_key: str = Field(min_length=1, max_length=120)
    tool_key: str = Field(min_length=1, max_length=160)


class ApprovalDecision(BaseModel):
    approved: bool
    reason: str = Field(default="", max_length=1000)
