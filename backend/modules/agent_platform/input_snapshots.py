"""节点输入只引用本轮较早的成功输出，读取时校验并还原完整 JSON。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Callable, Iterable


_CONTRACT = "agent-node-input-v1"
_MARKER = "$agent_node_input"
_MIN_REFERENCE_BYTES = 256


def _encoded(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _walk(value: Any, path: list[str | int]):
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, [*path, key])
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, [*path, index])


def _at_path(value: Any, path: list[str | int]) -> Any:
    for key in path:
        if isinstance(value, dict) and isinstance(key, str) and key in value:
            value = value[key]
        elif isinstance(value, list) and type(key) is int and 0 <= key < len(value):
            value = value[key]
        else:
            raise ValueError("节点输入快照的引用路径不存在")
    return value


def _validate_path(value: Any) -> list[str | int]:
    if not isinstance(value, list) or any(type(key) not in {str, int} for key in value):
        raise ValueError("节点输入快照的引用路径无效")
    return value


def pack_node_input(
    node_run: Any,
    input_payload: dict[str, Any],
    source_nodes: Iterable[Any],
) -> dict[str, Any]:
    """按完整内容摘要匹配已有输出；独有内容保留原样，不做语义猜测。"""

    run_id = int(node_run.run_id)
    node_id = getattr(node_run, "id", None)
    indexed: dict[str, tuple[int, list[str | int]]] = {}
    for source in sorted(source_nodes, key=lambda item: int(item.id or 0)):
        if (
            source.run_id != run_id or source.status != "success"
            or not source.id or (node_id is not None and source.id >= node_id)
        ):
            continue
        for path, value in _walk(source.output_payload or {}, []):
            encoded = _encoded(value)
            if len(encoded) >= _MIN_REFERENCE_BYTES:
                indexed.setdefault(hashlib.sha256(encoded).hexdigest(), (int(source.id), path))
    references: list[dict[str, Any]] = []

    def compact(value: Any, path: list[str | int]) -> Any:
        encoded = _encoded(value)
        digest = hashlib.sha256(encoded).hexdigest()
        source = indexed.get(digest) if len(encoded) >= _MIN_REFERENCE_BYTES else None
        if source is not None:
            source_id, source_path = source
            reference = {
                "path": path, "node_run_id": source_id,
                "source_path": source_path, "sha256": digest,
            }
            if len(_encoded(reference)) < len(encoded):
                references.append(reference)
                return None
        if isinstance(value, dict):
            return {key: compact(item, [*path, key]) for key, item in value.items()}
        if isinstance(value, list):
            return [compact(item, [*path, index]) for index, item in enumerate(value)]
        return value

    inline = compact(input_payload, [])
    if not references and _MARKER not in input_payload:
        return deepcopy(input_payload)
    packed = {
        _MARKER: _CONTRACT, "run_id": run_id,
        "sha256": _digest(input_payload), "inline": inline, "references": references,
    }
    return packed if (
        _MARKER in input_payload or len(_encoded(packed)) < len(_encoded(input_payload))
    ) else deepcopy(input_payload)


def restore_node_input(
    node_run: Any,
    load_node: Callable[[int], Any | None] | None = None,
) -> dict[str, Any]:
    """引用缺失、跨运行或内容改变时直接拒绝，不能混入别轮数据。"""

    snapshot = node_run.input_payload or {}
    if not isinstance(snapshot, dict):
        raise ValueError("节点输入必须是 JSON 对象")
    if snapshot.get(_MARKER) != _CONTRACT:
        return deepcopy(snapshot)
    if set(snapshot) != {_MARKER, "run_id", "sha256", "inline", "references"}:
        raise ValueError("节点输入快照结构无效")
    if snapshot["run_id"] != node_run.run_id or type(snapshot["run_id"]) is not int:
        raise ValueError("节点输入快照不能跨运行读取")
    references = snapshot["references"]
    if not isinstance(references, list) or (references and load_node is None):
        raise ValueError("节点输入快照缺少引用来源")
    output = deepcopy(snapshot["inline"])
    target_paths: set[tuple[str | int, ...]] = set()
    for reference in references:
        if not isinstance(reference, dict) or set(reference) != {
            "path", "node_run_id", "source_path", "sha256",
        }:
            raise ValueError("节点输入快照的引用结构无效")
        source_id = reference["node_run_id"]
        if type(source_id) is not int or source_id <= 0:
            raise ValueError("节点输入快照的来源节点无效")
        source = load_node(source_id)
        node_id = getattr(node_run, "id", None)
        if (
            source is None or source.id != source_id or source.run_id != node_run.run_id
            or source.status != "success" or (node_id is not None and source_id >= node_id)
        ):
            raise ValueError("节点输入快照必须引用本轮较早的成功节点")
        source_path = _validate_path(reference["source_path"])
        value = _at_path(source.output_payload or {}, source_path)
        if _digest(value) != reference["sha256"]:
            raise ValueError("节点输入快照的来源内容摘要不一致")
        path = _validate_path(reference["path"])
        target = tuple(path)
        if any(target[:len(old)] == old or old[:len(target)] == target for old in target_paths):
            raise ValueError("节点输入快照存在重复或重叠引用")
        target_paths.add(target)
        if _at_path(output, path) is not None:
            raise ValueError("节点输入快照的引用占位不为空")
        if path:
            parent = _at_path(output, path[:-1])
            parent[path[-1]] = deepcopy(value)
        else:
            output = deepcopy(value)
    if not isinstance(output, dict) or _digest(output) != snapshot["sha256"]:
        raise ValueError("节点输入快照还原后的完整摘要不一致")
    return output
