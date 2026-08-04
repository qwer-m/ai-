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


class WorkflowNode(BaseModel):
    node_key: str = Field(min_length=1, max_length=160)
    node_type: Literal["agent", "tool"]
    reference_key: str = Field(min_length=1, max_length=200)
    depends_on: list[str] = Field(default_factory=list)
    max_attempts: int = Field(default=1, ge=1, le=5)
    input_mapping: dict[str, str] = Field(default_factory=dict)


class WorkflowGraph(BaseModel):
    nodes: list[WorkflowNode] = Field(min_length=1)
    output_node_key: str = Field(min_length=1, max_length=160)
    input_schema: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowGraph":
        keys = [node.node_key for node in self.nodes]
        if len(keys) != len(set(keys)):
            raise ValueError("工作流节点键不能重复")
        key_set = set(keys)
        if self.output_node_key not in key_set:
            raise ValueError("输出节点不存在")
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
    definition: WorkflowGraph
    version: int = Field(default=1, ge=1)


class AgentRunCreate(BaseModel):
    project_id: int
    workflow_key: str = Field(min_length=1, max_length=120)
    input_payload: dict[str, Any] = Field(default_factory=dict)


class AgentToolBindingRequest(BaseModel):
    project_id: int
    agent_key: str = Field(min_length=1, max_length=120)
    tool_key: str = Field(min_length=1, max_length=160)


class ApprovalDecision(BaseModel):
    approved: bool
    reason: str = Field(default="", max_length=1000)
