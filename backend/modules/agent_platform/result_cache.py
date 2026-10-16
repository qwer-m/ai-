"""Agent 成功结果的身份匹配与只读复验；历史候选不具备自动信任权。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json
from typing import Any

from agents.exceptions import ModelBehaviorError
from sqlalchemy import text
from sqlalchemy.orm import Session

from .contracts import WorkflowNode
from .registry import ToolExecutionContext
from .sdk_adapter import (
    _normalize_final_output,
    _postprocess_agent_output,
    postprocess_agent_map_output,
)


def payload_hash(value: Any) -> str:
    """以稳定 JSON 编码计算输入与检查点的内容身份。"""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def result_cache_config(definition: Any) -> dict[str, int] | None:
    """缓存只声明是否启用和搜索容量，语义身份由实际契约自动计算。"""
    raw = dict(definition.runtime_config or {}).get("result_cache")
    if not isinstance(raw, dict) or not raw.get("enabled", True):
        return None
    return {"candidate_limit": max(1, min(100, int(raw.get("candidate_limit") or 20)))}


@dataclass(frozen=True)
class CachedAgentOutput:
    source: Any
    output: dict[str, Any]
    validation_outputs: list[dict[str, Any]]
    artifacts: dict[str, Any]


def validate_cached_agent_output(
    *,
    node: WorkflowNode,
    definition: Any,
    node_input: dict[str, Any],
    semantic_hash: str,
    output: dict[str, Any],
    validation_outputs: Any,
    execution_context: ToolExecutionContext,
) -> dict[str, Any]:
    """重新走原始模型输出的 Schema 和完整业务校验，避免对归一化结果重复转换。"""
    if not isinstance(validation_outputs, list):
        raise ValueError("缓存缺少可复验的原始输出")
    config = node.map_config
    inputs = node_input.get(config.items_key) if config is not None else [node_input]
    if not isinstance(inputs, list) or len(validation_outputs) != len(inputs):
        raise ValueError("缓存复验项数与当前输入不一致")
    normalized_items = []
    for index, (item_input, record) in enumerate(zip(inputs, validation_outputs)):
        if not isinstance(item_input, dict) or not isinstance(record, dict):
            raise ValueError("缓存复验输入与输出必须是对象")
        if record.get("item_index") != index or record.get("input_hash") != payload_hash(item_input):
            raise ValueError("缓存复验项的顺序或输入身份不一致")
        if record.get("semantic_hash") != semantic_hash:
            raise ValueError("缓存子项实际执行契约与节点身份不一致")
        raw_output = record.get("output")
        if not isinstance(raw_output, dict):
            raise ValueError("缓存缺少原始结构化输出")
        raw_output, _ = _normalize_final_output(deepcopy(raw_output), dict(definition.output_schema or {}))
        if config is not None:
            normalized = postprocess_agent_map_output(
                config=config, definition=definition, execution_context=execution_context,
                item_input=item_input, item_output=raw_output,
            )
        else:
            normalized = _postprocess_agent_output(
                agent_definition=definition, execution_context=execution_context,
                input_payload=item_input, output=raw_output,
            )
        normalized_items.append({"item_index": index, "input_hash": payload_hash(item_input), "output": normalized})
    validated = (
        {config.output_key: normalized_items, "completed_count": len(inputs), "total_count": len(inputs)}
        if config is not None else normalized_items[0]["output"]
    )
    if validated != output:
        raise ValueError("缓存结果与当前完整校验结果不一致")
    return validated


def reusable_agent_node_output(
    *, repo: Any, run: Any, node: WorkflowNode, definition: Any,
    node_input: dict[str, Any], semantic_hash: str,
    execution_context: ToolExecutionContext,
    rejections: list[dict[str, Any]],
) -> CachedAgentOutput | None:
    """只复用同输入、同语义且通过当前完整校验的成功结果。"""
    config = result_cache_config(definition)
    if config is None or dict(run.input_payload or {}).get("disable_result_cache"):
        return None
    input_hash = payload_hash(node_input)
    candidate_ids = repo.list_reusable_node_run_ids(
        run=run, node_key=node.node_key, node_type=node.node_type,
        agent_definition_id=int(definition.id), limit=config["candidate_limit"],
    )
    from core.db.model_defs import AgentNodeRun

    for candidate_id in candidate_ids:
        candidate = repo.db.get(AgentNodeRun, candidate_id)
        if candidate is None:
            continue
        cache = dict((candidate.sdk_state or {}).get("result_cache") or {})
        # 无自动指纹和原始复验依据的旧结果自然失效，禁止版本兼容放行。
        if cache.get("semantic_hash") != semantic_hash or cache.get("input_hash") != input_hash:
            continue
        try:
            source_input = repo.read_node_input(candidate)
        except ValueError:
            rejections.append({"source_node_run_id": candidate.id, "reason": "source_input_invalid"})
            continue
        if payload_hash(source_input) != input_hash:
            rejections.append({"source_node_run_id": candidate.id, "reason": "source_input_mismatch"})
            continue
        with Session(bind=repo.db.get_bind()) as validation_db:
            validation_db.execute(text("SET TRANSACTION READ ONLY"))
            context = replace(
                execution_context, db=validation_db,
                run_input=deepcopy(execution_context.run_input),
                artifacts=deepcopy(execution_context.artifacts), executed_tools=[], tool_calls=[],
            )
            try:
                validated = validate_cached_agent_output(
                    node=node, definition=definition, node_input=node_input,
                    semantic_hash=semantic_hash,
                    output=dict(candidate.output_payload or {}),
                    validation_outputs=cache.get("validation_outputs"), execution_context=context,
                )
                if validation_db.new or validation_db.dirty or validation_db.deleted:
                    raise ValueError("缓存校验不得产生数据库写入")
            except (ModelBehaviorError, ValueError, TypeError, KeyError) as exc:
                rejections.append({"source_node_run_id": candidate.id, "reason": "validation_failed", "error_type": type(exc).__name__})
                continue
            return CachedAgentOutput(
                source=candidate, output=validated,
                validation_outputs=deepcopy(cache["validation_outputs"]), artifacts=context.artifacts,
            )
    return None
