from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from .requirement_semantic_graph import (
    STRUCTURAL_GRAPH_RECOMPILE_ERROR_CODES,
    UNREPAIRABLE_PRIMARY_FLOW_ERROR_CODES,
)
from .semantic_contract import (
    canonicalize_requirement_semantic_candidate,
    evidence_supported,
)


_LEGACY_TOPOLOGY_ROOTS = ("functional_architecture", "workflow_blueprints")
_GRAPH_TOPOLOGY_ROOTS = (
    "evidence_facts",
    "semantic_graph",
    "workflow_blueprints",
)
_TOPOLOGY_ROOTS = tuple(
    dict.fromkeys((*_LEGACY_TOPOLOGY_ROOTS, *_GRAPH_TOPOLOGY_ROOTS))
)
_MAX_DIFFS = 512
_MAX_DIAGNOSTIC_DIFFS = 64
_MAX_FEEDBACK_DIAGNOSTICS = 32
_MAX_REPAIR_TARGETS = 32
_WILDCARD = object()


def _is_repair_value_field(field: Any) -> bool:
    """证据和置信度可在重试中修复，但它们不属于业务拓扑。"""
    key = str(field or "").strip().lower()
    return bool(
        "evidence" in key
        or key == "confidence"
        or key.startswith("confidence_")
        or key.endswith("_confidence")
    )


def _contract_payload(candidate: Any) -> dict[str, Any]:
    contract, _ = canonicalize_requirement_semantic_candidate(candidate)
    return contract


_ORDER_INSENSITIVE_SCALAR_COLLECTIONS = {
    "aliases",
    "fact_ids",
    "interaction_ids",
    "relation_ids",
    "required_stage_ids",
    "terminal_states",
    "transferred_entity_node_ids",
}
_ORDER_INSENSITIVE_IDENTITY_COLLECTIONS = {
    "evidence_facts",
    "nodes",
    "edges",
    "fact_dispositions",
    "scope_candidates",
}


def _projection_identity_sort_key(
    value: Any,
    identity_fields: tuple[str, ...],
) -> tuple[str, ...] | None:
    if not isinstance(value, dict):
        return None
    identity = tuple(str(value.get(field) or "").strip() for field in identity_fields)
    return identity if identity and all(identity) else None


def _project_value(value: Any, *, collection: str = "") -> Any:
    if isinstance(value, dict):
        return {
            str(key): _project_value(item, collection=str(key))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not _is_repair_value_field(key)
        }
    if isinstance(value, list):
        projected = [_project_value(item) for item in value]
        identity_fields = _REPAIR_COLLECTION_IDENTITIES.get(collection)
        if identity_fields and collection in _ORDER_INSENSITIVE_IDENTITY_COLLECTIONS:
            keyed = [
                (_projection_identity_sort_key(item, identity_fields), item)
                for item in projected
            ]
            if all(identity is not None for identity, _ in keyed):
                return [
                    item
                    for _, item in sorted(
                        keyed,
                        key=lambda pair: pair[0] or (),
                    )
                ]
        if collection in _ORDER_INSENSITIVE_SCALAR_COLLECTIONS and all(
            not isinstance(item, (dict, list)) for item in projected
        ):
            return sorted(projected, key=lambda item: str(item))
        return projected
    return value


def build_semantic_topology_projection(candidate: Any) -> dict[str, Any]:
    """提取模块、交互、工作流、步骤和状态的非证据结构。"""
    payload = _contract_payload(candidate)
    roots = (
        _GRAPH_TOPOLOGY_ROOTS
        if "semantic_graph" in payload or "evidence_facts" in payload
        else _LEGACY_TOPOLOGY_ROOTS
    )
    return {
        root: _project_value(payload[root], collection=root)
        for root in roots
        if root in payload
    }


def _fingerprint(projection: dict[str, Any]) -> str:
    canonical = json.dumps(
        projection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _format_path(tokens: tuple[Any, ...]) -> str:
    output = "$"
    for token in tokens:
        if token is _WILDCARD:
            output += "[*]"
        elif isinstance(token, int):
            output += f"[{token}]"
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(token)):
            output += f".{token}"
        else:
            output += f"[{json.dumps(str(token), ensure_ascii=False)}]"
    return output


def _value_kind(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return type(value).__name__


def _collect_diffs(
    anchor: Any,
    candidate: Any,
    *,
    path: tuple[Any, ...] = (),
    output: list[dict[str, Any]],
) -> bool:
    """返回是否因差异过多而截断；诊断只记录结构，不回显业务文本。"""
    if len(output) >= _MAX_DIFFS:
        return True
    if type(anchor) is not type(candidate):
        output.append(
            {
                "path": _format_path(path),
                "path_tokens": path,
                "change": "type_changed",
                "anchor_kind": _value_kind(anchor),
                "candidate_kind": _value_kind(candidate),
            }
        )
        return False
    if isinstance(anchor, dict):
        anchor_keys = set(anchor)
        candidate_keys = set(candidate)
        for key in sorted(anchor_keys - candidate_keys):
            output.append(
                {
                    "path": _format_path((*path, key)),
                    "path_tokens": (*path, key),
                    "change": "field_removed",
                }
            )
            if len(output) >= _MAX_DIFFS:
                return True
        for key in sorted(candidate_keys - anchor_keys):
            output.append(
                {
                    "path": _format_path((*path, key)),
                    "path_tokens": (*path, key),
                    "change": "field_added",
                }
            )
            if len(output) >= _MAX_DIFFS:
                return True
        for key in sorted(anchor_keys & candidate_keys):
            if _collect_diffs(
                anchor[key],
                candidate[key],
                path=(*path, key),
                output=output,
            ):
                return True
        return False
    if isinstance(anchor, list):
        collection = str(path[-1]) if path and isinstance(path[-1], str) else ""
        identity_fields = _REPAIR_COLLECTION_IDENTITIES.get(collection)
        identity_matching_required = bool(
            identity_fields
            and (
                collection in _ORDER_INSENSITIVE_IDENTITY_COLLECTIONS
                or (
                    collection in {"workflow_blueprints", "module_interactions"}
                    and len(anchor) != len(candidate)
                )
            )
        )
        if identity_matching_required:
            anchor_index, anchor_missing = _identity_index(anchor, identity_fields)
            candidate_index, candidate_missing = _identity_index(
                candidate,
                identity_fields,
            )
            ambiguous = any(len(indices) != 1 for indices in anchor_index.values()) or any(
                len(indices) != 1 for indices in candidate_index.values()
            )
            if not anchor_missing and not candidate_missing and not ambiguous:
                anchor_ids = set(anchor_index)
                candidate_ids = set(candidate_index)
                for identity in sorted(
                    anchor_ids - candidate_ids,
                    key=lambda item: anchor_index[item][0],
                ):
                    output.append(
                        {
                            "path": _format_path(
                                (*path, anchor_index[identity][0])
                            ),
                            "path_tokens": (*path, anchor_index[identity][0]),
                            "change": "item_removed",
                            "identity_fields": list(identity_fields),
                        }
                    )
                    if len(output) >= _MAX_DIFFS:
                        return True
                for identity in sorted(
                    candidate_ids - anchor_ids,
                    key=lambda item: candidate_index[item][0],
                ):
                    output.append(
                        {
                            "path": _format_path(
                                (*path, candidate_index[identity][0])
                            ),
                            "path_tokens": (*path, candidate_index[identity][0]),
                            "change": "item_added",
                            "identity_fields": list(identity_fields),
                        }
                    )
                    if len(output) >= _MAX_DIFFS:
                        return True
                for identity in sorted(
                    anchor_ids & candidate_ids,
                    key=lambda item: anchor_index[item][0],
                ):
                    anchor_item_index = anchor_index[identity][0]
                    candidate_item_index = candidate_index[identity][0]
                    if _collect_diffs(
                        anchor[anchor_item_index],
                        candidate[candidate_item_index],
                        path=(*path, anchor_item_index),
                        output=output,
                    ):
                        return True
                return False
        if len(anchor) != len(candidate):
            output.append(
                {
                    "path": _format_path(path),
                    "path_tokens": path,
                    "change": "array_length_changed",
                    "anchor_size": len(anchor),
                    "candidate_size": len(candidate),
                }
            )
            if len(output) >= _MAX_DIFFS:
                return True
        for index, (anchor_item, candidate_item) in enumerate(zip(anchor, candidate)):
            if _collect_diffs(
                anchor_item,
                candidate_item,
                path=(*path, index),
                output=output,
            ):
                return True
        return False
    if anchor != candidate:
        output.append(
            {
                "path": _format_path(path),
                "path_tokens": path,
                "change": "value_changed",
                "anchor_kind": _value_kind(anchor),
                "candidate_kind": _value_kind(candidate),
            }
        )
    return False


def diff_semantic_topology(anchor_candidate: Any, candidate: Any) -> list[dict[str, Any]]:
    """比较两个候选的拓扑；返回值不包含证据、置信度和业务字段值。"""
    output: list[dict[str, Any]] = []
    truncated = _collect_diffs(
        build_semantic_topology_projection(anchor_candidate),
        build_semantic_topology_projection(candidate),
        output=output,
    )
    if truncated:
        output.append(
            {
                "path": "$",
                "path_tokens": (),
                "change": "diff_limit_exceeded",
            }
        )
    return output


_REPAIR_COLLECTION_IDENTITIES: dict[str, tuple[str, ...]] = {
    "evidence_facts": ("fact_id",),
    "nodes": ("node_id",),
    "edges": ("edge_id",),
    "fact_dispositions": ("fact_id",),
    "functional_modules": ("module_key",),
    "module_interactions": ("interaction_id",),
    "workflow_blueprints": ("workflow_id",),
    "steps": ("id",),
    # scope_id 是候选职责范围的稳定身份；role 是可被确定性修复的属性。
    "scope_candidates": ("scope_id",),
    "module_candidates": ("module_key", "role"),
    "required_states": (
        "entity",
        "state",
        "source",
        "scope",
        "polarity",
        "temporal",
    ),
    "produced_states": (
        "entity",
        "state",
        "source",
        "scope",
        "polarity",
        "temporal",
    ),
}


class _RepairMergeDiagnostics:
    def __init__(self) -> None:
        self.copied_repair_paths: list[str] = []
        self.copied_repair_field_count = 0
        self.preserved_verified_evidence_paths: list[str] = []
        self.preserved_verified_evidence_count = 0
        self.matched_identity_count = 0
        self.unmatched_base_identity_count = 0
        self.unmatched_retry_identity_count = 0
        self.missing_identity_count = 0
        self.ambiguous_identity_count = 0
        self.skipped_identity_paths: list[str] = []
        self.paths_truncated = False

    def _record_path(self, target: list[str], path: tuple[Any, ...]) -> None:
        rendered = _format_path(path)
        if rendered in target:
            return
        if len(target) >= _MAX_DIAGNOSTIC_DIFFS:
            self.paths_truncated = True
            return
        target.append(rendered)

    def record_copied(self, path: tuple[Any, ...]) -> None:
        self.copied_repair_field_count += 1
        self._record_path(self.copied_repair_paths, path)

    def record_skipped_identity(self, path: tuple[Any, ...]) -> None:
        self._record_path(self.skipped_identity_paths, path)

    def record_preserved_evidence(self, path: tuple[Any, ...]) -> None:
        self.preserved_verified_evidence_count += 1
        self._record_path(self.preserved_verified_evidence_paths, path)


def _merge_payload(value: Any) -> tuple[dict[str, Any] | None, bool]:
    if not isinstance(value, dict):
        return None, False
    nested = value.get("requirement_semantic_contract")
    wrapped = isinstance(nested, dict)
    canonical, _ = canonicalize_requirement_semantic_candidate(value)
    if wrapped:
        value["requirement_semantic_contract"] = canonical
        return value["requirement_semantic_contract"], True
    value.clear()
    value.update(canonical)
    return value, False


def _stable_identity(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    return str(value).strip()


def _is_retry_repair_field(field: Any) -> bool:
    """只接纳契约原始修复值，不复制 evidence_verified 等派生信任字段。"""
    return str(field or "").strip().lower() in {"evidence", "confidence"}


def _collection_identity(
    item: Any,
    identity_fields: tuple[str, ...],
) -> tuple[str, ...] | None:
    if not isinstance(item, dict):
        return None
    identity = tuple(_stable_identity(item.get(field)) for field in identity_fields)
    return identity if identity and all(identity) else None


def _identity_index(
    items: list[Any],
    identity_fields: tuple[str, ...],
) -> tuple[dict[tuple[str, ...], list[int]], list[int]]:
    index: dict[tuple[str, ...], list[int]] = {}
    missing: list[int] = []
    for item_index, item in enumerate(items):
        identity = _collection_identity(item, identity_fields)
        if identity is None:
            missing.append(item_index)
            continue
        index.setdefault(identity, []).append(item_index)
    return index, missing


def _merge_identity_collection(
    base_items: list[Any],
    retry_items: list[Any],
    *,
    collection: str,
    path: tuple[Any, ...],
    diagnostics: _RepairMergeDiagnostics,
    verified_evidence_source: str,
) -> None:
    identity_fields = _REPAIR_COLLECTION_IDENTITIES[collection]
    base_index, base_missing = _identity_index(base_items, identity_fields)
    retry_index, retry_missing = _identity_index(retry_items, identity_fields)
    diagnostics.missing_identity_count += len(base_missing) + len(retry_missing)
    for item_index in base_missing:
        diagnostics.record_skipped_identity((*path, item_index))
    for item_index in retry_missing:
        diagnostics.record_skipped_identity((*path, item_index))

    ambiguous_base = {
        identity for identity, indices in base_index.items() if len(indices) != 1
    }
    ambiguous_retry = {
        identity for identity, indices in retry_index.items() if len(indices) != 1
    }
    ambiguous = ambiguous_base | ambiguous_retry
    diagnostics.ambiguous_identity_count += sum(
        len(base_index.get(identity, [])) + len(retry_index.get(identity, []))
        for identity in ambiguous
    )
    for identity in sorted(ambiguous):
        for item_index in base_index.get(identity, []):
            diagnostics.record_skipped_identity((*path, item_index))
        for item_index in retry_index.get(identity, []):
            diagnostics.record_skipped_identity((*path, item_index))

    base_identities = set(base_index) - ambiguous
    retry_identities = set(retry_index) - ambiguous
    matched = base_identities & retry_identities
    diagnostics.matched_identity_count += len(matched)
    diagnostics.unmatched_base_identity_count += len(base_identities - matched)
    diagnostics.unmatched_retry_identity_count += len(retry_identities - matched)

    # 始终沿用 base 的位置；retry 数组顺序不会参与配对。
    for identity in sorted(matched, key=lambda value: base_index[value][0]):
        base_item_index = base_index[identity][0]
        retry_item_index = retry_index[identity][0]
        base_item = base_items[base_item_index]
        retry_item = retry_items[retry_item_index]
        if not isinstance(base_item, dict) or not isinstance(retry_item, dict):
            continue
        _merge_repair_fields(
            base_item,
            retry_item,
            path=(*path, base_item_index),
            diagnostics=diagnostics,
            verified_evidence_source=verified_evidence_source,
        )


def _merge_repair_fields(
    base_value: dict[str, Any],
    retry_value: dict[str, Any],
    *,
    path: tuple[Any, ...],
    diagnostics: _RepairMergeDiagnostics,
    verified_evidence_source: str,
) -> None:
    for key, retry_item in retry_value.items():
        item_path = (*path, str(key))
        base_item = base_value.get(key)
        if _is_retry_repair_field(key):
            if (
                str(key).strip().lower() == "evidence"
                and verified_evidence_source
                and isinstance(base_item, list)
                and evidence_supported(base_item, verified_evidence_source)
            ):
                diagnostics.record_preserved_evidence(item_path)
                continue
            base_value[key] = copy.deepcopy(retry_item)
            diagnostics.record_copied(item_path)
            continue
        if isinstance(base_item, dict) and isinstance(retry_item, dict):
            _merge_repair_fields(
                base_item,
                retry_item,
                path=item_path,
                diagnostics=diagnostics,
                verified_evidence_source=verified_evidence_source,
            )
            continue
        if (
            key in _REPAIR_COLLECTION_IDENTITIES
            and isinstance(base_item, list)
            and isinstance(retry_item, list)
        ):
            _merge_identity_collection(
                base_item,
                retry_item,
                collection=key,
                path=item_path,
                diagnostics=diagnostics,
                verified_evidence_source=verified_evidence_source,
            )


def _preserve_verified_evidence_fields(
    base_value: dict[str, Any],
    retry_value: dict[str, Any],
    *,
    path: tuple[Any, ...],
    source_text: str,
    diagnostics: _RepairMergeDiagnostics,
) -> None:
    owner_topology_unchanged = (
        {
            str(key): _project_value(item)
            for key, item in base_value.items()
            if not _is_repair_value_field(key)
            and key not in _REPAIR_COLLECTION_IDENTITIES
        }
        == {
            str(key): _project_value(item)
            for key, item in retry_value.items()
            if not _is_repair_value_field(key)
            and key not in _REPAIR_COLLECTION_IDENTITIES
        }
    )
    for key, base_item in base_value.items():
        if key not in retry_value:
            continue
        retry_item = retry_value.get(key)
        item_path = (*path, str(key))
        if (
            str(key).strip().lower() == "evidence"
            and owner_topology_unchanged
            and isinstance(base_item, list)
            and evidence_supported(base_item, source_text)
        ):
            retry_value[key] = copy.deepcopy(base_item)
            diagnostics.record_preserved_evidence(item_path)
            continue
        if isinstance(base_item, dict) and isinstance(retry_item, dict):
            _preserve_verified_evidence_fields(
                base_item,
                retry_item,
                path=item_path,
                source_text=source_text,
                diagnostics=diagnostics,
            )
            continue
        if (
            key in _REPAIR_COLLECTION_IDENTITIES
            and isinstance(base_item, list)
            and isinstance(retry_item, list)
        ):
            identity_fields = _REPAIR_COLLECTION_IDENTITIES[key]
            base_index, base_missing = _identity_index(base_item, identity_fields)
            retry_index, retry_missing = _identity_index(retry_item, identity_fields)
            if base_missing or retry_missing:
                continue
            for identity in sorted(set(base_index) & set(retry_index)):
                if len(base_index[identity]) != 1 or len(retry_index[identity]) != 1:
                    continue
                base_index_value = base_index[identity][0]
                retry_index_value = retry_index[identity][0]
                base_child = base_item[base_index_value]
                retry_child = retry_item[retry_index_value]
                if not isinstance(base_child, dict) or not isinstance(retry_child, dict):
                    continue
                _preserve_verified_evidence_fields(
                    base_child,
                    retry_child,
                    path=(*item_path, retry_index_value),
                    source_text=source_text,
                    diagnostics=diagnostics,
                )


def preserve_verified_semantic_evidence(
    base_candidate: Any,
    retry_candidate: Any,
    *,
    source_text: str,
) -> tuple[Any, dict[str, Any]]:
    """保留已验证证据，只让重试改动尚未通过验证的证据字段。"""

    base_copy = copy.deepcopy(base_candidate)
    result = copy.deepcopy(retry_candidate)
    base_payload, base_wrapper = _merge_payload(base_copy)
    retry_payload, retry_wrapper = _merge_payload(result)
    diagnostics = _RepairMergeDiagnostics()
    status = "preserved"
    if base_payload is None:
        status = "base_candidate_not_object"
    elif retry_payload is None:
        status = "retry_candidate_not_object"
    elif not str(source_text or ""):
        status = "source_text_empty"
    else:
        _preserve_verified_evidence_fields(
            base_payload,
            retry_payload,
            path=(),
            source_text=str(source_text),
            diagnostics=diagnostics,
        )
    return result, {
        "status": status,
        "base_wrapper": bool(base_wrapper),
        "retry_wrapper": bool(retry_wrapper),
        "base_topology_fingerprint": _fingerprint(
            build_semantic_topology_projection(base_candidate)
        ),
        "retry_topology_fingerprint": _fingerprint(
            build_semantic_topology_projection(retry_candidate)
        ),
        "result_topology_fingerprint": _fingerprint(
            build_semantic_topology_projection(result)
        ),
        "preserved_verified_evidence_count": int(
            diagnostics.preserved_verified_evidence_count
        ),
        "preserved_verified_evidence_paths": (
            diagnostics.preserved_verified_evidence_paths
        ),
        "diagnostic_paths_truncated": bool(diagnostics.paths_truncated),
    }


@dataclass(frozen=True)
class _TopologyCopyRule:
    requested_path: str
    tokens: tuple[Any, ...]
    subtree: bool


def _safe_requested_path(value: Any) -> str:
    if not isinstance(value, str):
        return "<non-string-path>"
    text = value.strip()
    return text[:320] if text else "<empty-path>"


def _parse_topology_copy_rule(
    value: Any,
) -> tuple[_TopologyCopyRule | None, str]:
    requested = _safe_requested_path(value)
    if not isinstance(value, str):
        return None, "path_not_string"
    text = value.strip()
    if not text or len(text) > 320 or not text.startswith("$"):
        return None, "path_syntax_invalid"

    tokens: list[Any] = []
    subtree = False
    position = 1
    while position < len(text):
        if text.startswith(".**", position):
            if position + 3 != len(text):
                return None, "subtree_must_be_terminal"
            subtree = True
            position += 3
            break
        if text[position] == ".":
            matched = re.match(r"\.([A-Za-z_][A-Za-z0-9_]*)", text[position:])
            if not matched:
                return None, "path_syntax_invalid"
            tokens.append(matched.group(1))
            position += len(matched.group(0))
            continue
        if text[position] == "[":
            closing = text.find("]", position + 1)
            if closing < 0:
                return None, "path_syntax_invalid"
            raw_index = text[position + 1 : closing]
            if raw_index == "*":
                tokens.append(_WILDCARD)
            elif raw_index.isdigit():
                tokens.append(int(raw_index))
            else:
                return None, "list_index_invalid"
            position = closing + 1
            continue
        return None, "path_syntax_invalid"

    if position != len(text) or not tokens:
        return None, "path_syntax_invalid"
    if tokens[0] == "requirement_semantic_contract":
        tokens = tokens[1:]
    if not tokens or tokens[0] not in _TOPOLOGY_ROOTS:
        return None, "topology_root_invalid"
    if any(
        isinstance(token, str) and _is_repair_value_field(token)
        for token in tokens
    ):
        return None, "repair_value_path_not_allowed"
    return (
        _TopologyCopyRule(
            requested_path=requested,
            tokens=tuple(tokens),
            subtree=subtree,
        ),
        "",
    )


def _expand_retry_copy_paths(
    retry_value: Any,
    tokens: tuple[Any, ...],
) -> tuple[list[tuple[Any, ...]], list[tuple[tuple[Any, ...], str]]]:
    expanded: list[tuple[Any, ...]] = []
    skipped: list[tuple[tuple[Any, ...], str]] = []

    def walk(value: Any, token_index: int, path: tuple[Any, ...]) -> None:
        if token_index >= len(tokens):
            expanded.append(path)
            return
        token = tokens[token_index]
        if token is _WILDCARD:
            if not isinstance(value, list):
                skipped.append((path, "source_not_list"))
                return
            if not value:
                skipped.append((path, "wildcard_no_matches"))
                return
            for item_index, item in enumerate(value):
                walk(item, token_index + 1, (*path, item_index))
            return
        if isinstance(token, int):
            if not isinstance(value, list):
                skipped.append(((*path, token), "source_not_list"))
                return
            if token >= len(value):
                skipped.append(((*path, token), "source_index_out_of_bounds"))
                return
            walk(value[token], token_index + 1, (*path, token))
            return
        if not isinstance(value, dict):
            skipped.append(((*path, token), "source_not_object"))
            return
        if token not in value:
            skipped.append(((*path, token), "source_path_missing"))
            return
        walk(value[token], token_index + 1, (*path, token))

    walk(retry_value, 0, ())
    return expanded, skipped


def _value_at_concrete_path(value: Any, path: tuple[Any, ...]) -> tuple[Any, str]:
    current = value
    for token in path:
        if isinstance(token, int):
            if not isinstance(current, list):
                return None, "source_not_list"
            if token >= len(current):
                return None, "source_index_out_of_bounds"
            current = current[token]
            continue
        if not isinstance(current, dict):
            return None, "source_not_object"
        if token not in current:
            return None, "source_path_missing"
        current = current[token]
    return current, ""


def _copy_retry_path(
    merged_value: dict[str, Any],
    retry_value: dict[str, Any],
    *,
    path: tuple[Any, ...],
    subtree: bool,
) -> str:
    source, source_error = _value_at_concrete_path(retry_value, path)
    if source_error:
        return source_error
    if not subtree and isinstance(source, (dict, list)):
        return "container_requires_subtree"

    target: Any = merged_value
    source_parent: Any = retry_value
    for token_index, token in enumerate(path[:-1]):
        if isinstance(token, int):
            if not isinstance(source_parent, list):
                return "source_not_list"
            if token >= len(source_parent):
                return "source_index_out_of_bounds"
            if not isinstance(target, list):
                return "target_not_list"
            source_item = source_parent[token]
            collection = str(path[token_index - 1]) if token_index >= 1 else ""
            identity_fields = _REPAIR_COLLECTION_IDENTITIES.get(collection)
            if identity_fields:
                source_identity = _collection_identity(source_item, identity_fields)
                if source_identity is None:
                    return "source_identity_missing"
                matching_indexes = [
                    index
                    for index, item in enumerate(target)
                    if _collection_identity(item, identity_fields) == source_identity
                ]
                if not matching_indexes:
                    return "target_identity_missing"
                if len(matching_indexes) > 1:
                    return "target_identity_ambiguous"
                target = target[matching_indexes[0]]
            else:
                if token >= len(target):
                    return "target_index_out_of_bounds"
                target = target[token]
            source_parent = source_item
            continue
        if not isinstance(source_parent, dict):
            return "source_not_object"
        if token not in source_parent:
            return "source_path_missing"
        if not isinstance(target, dict):
            return "target_not_object"
        if token not in target:
            return "target_parent_missing"
        source_parent = source_parent[token]
        target = target[token]

    leaf = path[-1]
    if isinstance(leaf, int):
        if not isinstance(target, list):
            return "target_not_list"
        if subtree:
            collection = str(path[-2]) if len(path) >= 2 else ""
            identity_fields = _REPAIR_COLLECTION_IDENTITIES.get(collection)
            source_identity = _collection_identity(source, identity_fields or ())
            if identity_fields:
                if source_identity is None:
                    return "source_identity_missing"
                matching_indexes = [
                    index
                    for index, item in enumerate(target)
                    if _collection_identity(item, identity_fields) == source_identity
                ]
                if len(matching_indexes) > 1:
                    return "target_identity_ambiguous"
                if matching_indexes:
                    target[matching_indexes[0]] = copy.deepcopy(source)
                else:
                    target.append(copy.deepcopy(source))
                return ""
        if leaf >= len(target):
            return "target_index_out_of_bounds"
        target[leaf] = copy.deepcopy(source)
        return ""
    if not isinstance(target, dict):
        return "target_not_object"
    target[leaf] = copy.deepcopy(source)
    return ""


def _remove_missing_identity_item(
    merged_value: dict[str, Any],
    retry_value: dict[str, Any],
    base_reference: dict[str, Any],
    *,
    path: tuple[Any, ...],
) -> str:
    """只按工作基线中的稳定身份删除 retry 已明确移除的集合项。"""

    if len(path) < 2 or not isinstance(path[-1], int):
        return "removal_path_not_indexed_item"
    collection = path[-2]
    identity_fields = _REPAIR_COLLECTION_IDENTITIES.get(str(collection))
    if not identity_fields:
        return "removal_collection_identity_unsupported"

    reference_items, reference_error = _value_at_concrete_path(
        base_reference,
        path[:-1],
    )
    retry_items, retry_error = _value_at_concrete_path(retry_value, path[:-1])
    merged_items, merged_error = _value_at_concrete_path(
        merged_value,
        path[:-1],
    )
    if reference_error or retry_error or merged_error:
        return reference_error or retry_error or merged_error
    if not all(
        isinstance(items, list)
        for items in (reference_items, retry_items, merged_items)
    ):
        return "removal_parent_not_list"

    reference_index = int(path[-1])
    if reference_index >= len(reference_items):
        return "removal_base_index_out_of_bounds"
    identity = _collection_identity(
        reference_items[reference_index],
        identity_fields,
    )
    if identity is None:
        return "removal_base_identity_missing"

    retry_matches = [
        index
        for index, item in enumerate(retry_items)
        if _collection_identity(item, identity_fields) == identity
    ]
    if retry_matches:
        return "removal_identity_still_present_in_retry"
    merged_matches = [
        index
        for index, item in enumerate(merged_items)
        if _collection_identity(item, identity_fields) == identity
    ]
    if len(merged_matches) != 1:
        return "removal_merged_identity_not_unique"
    merged_items.pop(merged_matches[0])
    return ""


def _copy_rule_allows_tokens(
    rule: _TopologyCopyRule,
    actual_tokens: tuple[Any, ...],
) -> bool:
    if rule.subtree:
        if len(actual_tokens) < len(rule.tokens):
            return False
        compared = actual_tokens[: len(rule.tokens)]
    else:
        if len(actual_tokens) != len(rule.tokens):
            return False
        compared = actual_tokens
    return all(
        expected is _WILDCARD or expected == actual
        for expected, actual in zip(rule.tokens, compared)
    )


def _canonical_copy_rule(
    rule: _TopologyCopyRule,
    *,
    retry_payload: dict[str, Any] | None,
    merged_projection: dict[str, Any],
) -> _TopologyCopyRule:
    """把候选原始索引按稳定身份映射到合并结果的规范索引。"""

    if retry_payload is None or not rule.tokens:
        return rule
    canonical_tokens = list(rule.tokens)
    identity_index_found = False
    for token_index, token in enumerate(rule.tokens):
        if not isinstance(token, int) or token_index < 1:
            continue
        collection = str(rule.tokens[token_index - 1])
        identity_fields = _REPAIR_COLLECTION_IDENTITIES.get(collection)
        if not identity_fields:
            continue
        source, source_error = _value_at_concrete_path(
            retry_payload,
            rule.tokens[: token_index + 1],
        )
        identity = _collection_identity(source, identity_fields)
        projected_items, projected_error = _value_at_concrete_path(
            merged_projection,
            tuple(canonical_tokens[:token_index]),
        )
        if (
            source_error
            or projected_error
            or identity is None
            or not isinstance(projected_items, list)
        ):
            return rule
        matching_indexes = [
            index
            for index, item in enumerate(projected_items)
            if _collection_identity(item, identity_fields) == identity
        ]
        if len(matching_indexes) != 1:
            return rule
        canonical_tokens[token_index] = matching_indexes[0]
        identity_index_found = True
    if not identity_index_found:
        return rule
    return _TopologyCopyRule(
        requested_path=rule.requested_path,
        tokens=tuple(canonical_tokens),
        subtree=rule.subtree,
    )


def _topology_path_diagnostics(
    allowed_topology_paths: Any,
    *,
    merged_payload: dict[str, Any] | None,
    retry_payload: dict[str, Any] | None,
    base_reference_payload: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[_TopologyCopyRule]]:
    if allowed_topology_paths is None:
        raw_paths: list[Any] = []
    elif isinstance(allowed_topology_paths, str):
        raw_paths = [allowed_topology_paths]
    elif isinstance(allowed_topology_paths, (list, tuple)):
        raw_paths = list(allowed_topology_paths)
    else:
        raw_paths = [allowed_topology_paths]

    paths_truncated = len(raw_paths) > _MAX_DIAGNOSTIC_DIFFS
    raw_paths = raw_paths[:_MAX_DIAGNOSTIC_DIFFS]
    requested_paths = [_safe_requested_path(item) for item in raw_paths]
    applied_paths: list[str] = []
    skipped_paths: list[dict[str, str]] = []
    applied_rules: list[_TopologyCopyRule] = []

    for raw_path in raw_paths:
        rule, parse_error = _parse_topology_copy_rule(raw_path)
        requested = _safe_requested_path(raw_path)
        if rule is None:
            skipped_paths.append({"path": requested, "reason": parse_error})
            continue
        if merged_payload is None or retry_payload is None:
            skipped_paths.append({"path": requested, "reason": "payload_unavailable"})
            continue
        source, source_error = _value_at_concrete_path(
            retry_payload,
            rule.tokens,
        )
        if (
            source_error
            and base_reference_payload is not None
            and not rule.subtree
            and isinstance(rule.tokens[-1], int)
        ):
            removal_error = _remove_missing_identity_item(
                merged_payload,
                retry_payload,
                base_reference_payload,
                path=rule.tokens,
            )
            if not removal_error:
                concrete_display = _format_path(rule.tokens)
                if concrete_display not in applied_paths:
                    applied_paths.append(concrete_display)
                applied_rules.append(rule)
                continue
        concrete_paths, expansion_skips = _expand_retry_copy_paths(
            retry_payload,
            rule.tokens,
        )
        for skipped_path, reason in expansion_skips:
            skipped_paths.append(
                {"path": _format_path(skipped_path), "reason": reason}
            )
        for concrete_path in concrete_paths:
            copy_error = _copy_retry_path(
                merged_payload,
                retry_payload,
                path=concrete_path,
                subtree=rule.subtree,
            )
            concrete_display = _format_path(concrete_path)
            if copy_error:
                skipped_paths.append(
                    {"path": concrete_display, "reason": copy_error}
                )
                continue
            if rule.subtree:
                concrete_display += ".**"
            if concrete_display not in applied_paths:
                applied_paths.append(concrete_display)
            applied_rules.append(
                _TopologyCopyRule(
                    requested_path=rule.requested_path,
                    tokens=concrete_path,
                    subtree=rule.subtree,
                )
            )
        if not concrete_paths and not expansion_skips:
            skipped_paths.append(
                {"path": requested, "reason": "source_path_missing"}
            )

    return (
        {
            "requested_topology_path_count": int(len(requested_paths)),
            "requested_topology_paths": requested_paths,
            "applied_topology_path_count": int(len(applied_paths)),
            "applied_topology_paths": applied_paths,
            "skipped_topology_path_count": int(len(skipped_paths)),
            "skipped_topology_paths": skipped_paths,
            "topology_path_diagnostics_truncated": bool(paths_truncated),
        },
        applied_rules,
    )


def merge_semantic_retry_repair_values(
    base_candidate: Any,
    retry_candidate: Any,
    allowed_topology_paths: Any = None,
    *,
    verified_evidence_source: str = "",
) -> tuple[Any, dict[str, Any]]:
    """以 base 为底，合并修复值，并仅应用明确授权的拓扑路径。"""
    merged = copy.deepcopy(base_candidate)
    retry_copy = copy.deepcopy(retry_candidate)
    merged_payload, base_wrapper = _merge_payload(merged)
    retry_payload, retry_wrapper = _merge_payload(retry_copy)
    base_projection = build_semantic_topology_projection(base_candidate)
    diagnostics = _RepairMergeDiagnostics()
    status = "merged"
    base_reference_payload = (
        copy.deepcopy(merged_payload)
        if isinstance(merged_payload, dict)
        else None
    )
    if merged_payload is None:
        status = "base_candidate_not_object"
    elif retry_payload is None:
        status = "retry_candidate_not_object"
    else:
        _merge_repair_fields(
            merged_payload,
            retry_payload,
            path=(),
            diagnostics=diagnostics,
            verified_evidence_source=str(verified_evidence_source or ""),
        )

    topology_path_diagnostics, applied_copy_rules = _topology_path_diagnostics(
        allowed_topology_paths,
        merged_payload=merged_payload,
        retry_payload=retry_payload,
        base_reference_payload=base_reference_payload,
    )

    retry_projection = build_semantic_topology_projection(retry_candidate)
    merged_projection = build_semantic_topology_projection(merged)
    output_topology_diffs = diff_semantic_topology(base_candidate, merged)
    # 直接比较合并结果与重试候选，得到真正被投影丢弃的拓扑差异。
    # 这样无需用数字路径反推候选身份，也不会把不同对象的同索引差异混为一项。
    discarded_topology_diffs = diff_semantic_topology(merged, retry_candidate)
    merged_canonical_applied_rules = [
        _canonical_copy_rule(
            rule,
            retry_payload=retry_payload,
            merged_projection=merged_projection,
        )
        for rule in applied_copy_rules
    ]
    discarded_paths: list[str] = []
    for item in discarded_topology_diffs:
        path = str(item.get("path") or "$")
        if path not in discarded_paths:
            discarded_paths.append(path)
        if len(discarded_paths) >= _MAX_DIAGNOSTIC_DIFFS:
            break
    topology_changes_limited = all(
        any(
            _copy_rule_allows_tokens(
                rule,
                tuple(item.get("path_tokens") or ()),
            )
            for rule in merged_canonical_applied_rules
        )
        for item in output_topology_diffs
    )
    result_diagnostics = {
        "status": status,
        "base_wrapper": bool(base_wrapper),
        "retry_wrapper": bool(retry_wrapper),
        "base_topology_fingerprint": _fingerprint(base_projection),
        "retry_topology_fingerprint": _fingerprint(retry_projection),
        "merged_topology_fingerprint": _fingerprint(merged_projection),
        "topology_preserved": bool(merged_projection == base_projection),
        "topology_changes_limited_to_allowed_paths": bool(
            topology_changes_limited
        ),
        "retained_topology_diff_count": int(len(output_topology_diffs)),
        "discarded_topology_diff_count": int(len(discarded_topology_diffs)),
        "discarded_topology_paths": discarded_paths,
        "copied_repair_field_count": int(diagnostics.copied_repair_field_count),
        "copied_repair_paths": diagnostics.copied_repair_paths,
        "preserved_verified_evidence_count": int(
            diagnostics.preserved_verified_evidence_count
        ),
        "preserved_verified_evidence_paths": (
            diagnostics.preserved_verified_evidence_paths
        ),
        "matched_identity_count": int(diagnostics.matched_identity_count),
        "unmatched_base_identity_count": int(
            diagnostics.unmatched_base_identity_count
        ),
        "unmatched_retry_identity_count": int(
            diagnostics.unmatched_retry_identity_count
        ),
        "missing_identity_count": int(diagnostics.missing_identity_count),
        "ambiguous_identity_count": int(diagnostics.ambiguous_identity_count),
        "skipped_identity_paths": diagnostics.skipped_identity_paths,
        "diagnostic_paths_truncated": bool(
            diagnostics.paths_truncated
            or len(discarded_topology_diffs) > len(discarded_paths)
        ),
        **topology_path_diagnostics,
    }
    return merged, result_diagnostics


@dataclass(frozen=True)
class _AllowedPath:
    tokens: tuple[Any, ...]
    subtree: bool
    reason: str
    allowed_changes: tuple[str, ...] = ()
    subject_id_field: str = ""
    subject_id: str = ""
    candidate_equals: tuple[tuple[str, str], ...] = ()
    candidate_contains: tuple[tuple[str, str], ...] = ()
    candidate_any_equals: tuple[tuple[str, str], ...] = ()
    materialize_matching_path: bool = False

    @property
    def display_path(self) -> str:
        suffix = ".**" if self.subtree else ""
        return f"{_format_path(self.tokens)}{suffix}"


@dataclass(frozen=True)
class _EvidenceRepairPath:
    tokens: tuple[Any, ...]
    reason: str
    subject_id_field: str = ""
    subject_id: str = ""

    @property
    def display_path(self) -> str:
        return _format_path(self.tokens)


class _FeedbackPaths:
    def __init__(self) -> None:
        self.rules: list[_AllowedPath] = []
        self.evidence_rules: list[_EvidenceRepairPath] = []
        self.recognized: list[str] = []
        self.evidence_only_count = 0
        self.unrecognized_count = 0

    def add_rule(
        self,
        tokens: tuple[Any, ...],
        *,
        reason: str,
        subtree: bool = False,
        allowed_changes: tuple[str, ...] = (),
        subject_id_field: str = "",
        subject_id: str = "",
        candidate_equals: tuple[tuple[str, str], ...] = (),
        candidate_contains: tuple[tuple[str, str], ...] = (),
        candidate_any_equals: tuple[tuple[str, str], ...] = (),
        materialize_matching_path: bool = False,
    ) -> None:
        if not tokens or any(_is_repair_value_field(token) for token in tokens):
            self.add_evidence_rule(
                tokens,
                reason=reason,
                subject_id_field=subject_id_field,
                subject_id=subject_id,
            )
            return
        rule = _AllowedPath(
            tokens=tokens,
            subtree=subtree,
            reason=reason,
            allowed_changes=tuple(str(item) for item in allowed_changes),
            subject_id_field=str(subject_id_field or ""),
            subject_id=_stable_identity(subject_id),
            candidate_equals=tuple(
                (str(field), _stable_identity(value))
                for field, value in candidate_equals
            ),
            candidate_contains=tuple(
                (str(field), _stable_identity(value))
                for field, value in candidate_contains
            ),
            candidate_any_equals=tuple(
                (str(field), _stable_identity(value))
                for field, value in candidate_any_equals
            ),
            materialize_matching_path=bool(materialize_matching_path),
        )
        if rule not in self.rules:
            self.rules.append(rule)
        self.mark_recognized(reason)

    def add_evidence_rule(
        self,
        tokens: tuple[Any, ...],
        *,
        reason: str,
        subject_id_field: str = "",
        subject_id: str = "",
    ) -> None:
        if not tokens:
            self.mark_evidence(reason)
            return
        rule = _EvidenceRepairPath(
            tokens=tokens,
            reason=str(reason or ""),
            subject_id_field=str(subject_id_field or ""),
            subject_id=_stable_identity(subject_id),
        )
        if rule not in self.evidence_rules:
            self.evidence_rules.append(rule)
        self.mark_evidence(reason)

    def mark_evidence(self, reason: str) -> None:
        self.evidence_only_count += 1
        self.mark_recognized(reason)

    def mark_recognized(self, reason: str) -> None:
        cleaned = str(reason or "").strip()[:160]
        if cleaned and cleaned not in self.recognized:
            self.recognized.append(cleaned)

    def mark_unrecognized(self) -> None:
        self.unrecognized_count += 1


_COLLECTION_FIELDS = {
    "evidence_facts",
    "semantic_graph",
    "nodes",
    "edges",
    "fact_dispositions",
    "functional_modules",
    "excluded_modules",
    "module_interactions",
    "workflow_blueprints",
    "steps",
    "required_stage_ids",
    "terminal_states",
    "module_candidates",
    "scope_candidates",
    "interaction_ids",
    "relation_ids",
    "fact_ids",
    "transferred_entity_node_ids",
    "required_states",
    "produced_states",
}

_BROAD_GRAPH_COLLECTION_PATHS = {
    ("evidence_facts",),
    ("semantic_graph",),
    ("semantic_graph", "nodes"),
    ("semantic_graph", "edges"),
    ("semantic_graph", "fact_dispositions"),
}

def _is_broad_graph_collection_path(tokens: tuple[Any, ...]) -> bool:
    return tokens in _BROAD_GRAPH_COLLECTION_PATHS


def _is_unscoped_identity_collection_item_path(tokens: tuple[Any, ...]) -> bool:
    """裸数组索引没有稳定身份语义，不能单独成为拓扑授权。"""

    return bool(
        len(tokens) >= 2
        and isinstance(tokens[-1], int)
        and str(tokens[-2]) in _REPAIR_COLLECTION_IDENTITIES
    )


def _add_graph_item_field_rules(
    analysis: _FeedbackPaths,
    *,
    collection: str,
    identity_field: str,
    identifier: str,
    fields: tuple[str, ...],
    reason: str,
    candidate_equals: tuple[tuple[str, str], ...] = (),
) -> bool:
    if not identifier:
        return False
    for field in fields:
        # aliases 是标量列表，只有绑定稳定身份的字段规则才允许修复整个列表。
        subtree = field in _COLLECTION_FIELDS or field == "aliases"
        analysis.add_rule(
            ("semantic_graph", collection, _WILDCARD, field),
            reason=reason,
            subtree=subtree,
            allowed_changes=() if subtree else ("value_changed", "field_added"),
            subject_id_field=identity_field,
            subject_id=identifier,
            candidate_equals=candidate_equals,
        )
    return bool(fields)


def _add_graph_item_addition_rule(
    analysis: _FeedbackPaths,
    *,
    collection: str,
    reason: str,
    candidate_equals: tuple[tuple[str, str], ...] = (),
    candidate_contains: tuple[tuple[str, str], ...] = (),
    candidate_any_equals: tuple[tuple[str, str], ...] = (),
) -> None:
    analysis.add_rule(
        ("semantic_graph", collection, _WILDCARD),
        reason=reason,
        allowed_changes=("item_added",),
        candidate_equals=candidate_equals,
        candidate_contains=candidate_contains,
        candidate_any_equals=candidate_any_equals,
        materialize_matching_path=True,
    )


def _add_semantic_graph_feedback(
    analysis: _FeedbackPaths,
    item: dict[str, Any],
) -> bool:
    """把图错误编译为带身份约束的权限；未知集合级错误保持关闭。"""

    reason = str(item.get("code") or item.get("reason") or "").strip()
    paths = [
        _parse_field_path(item.get(key))
        for key in ("field_path", "json_path", "path", "location")
    ]
    graph_feedback = bool(
        reason
        and any(
            path and path[0] in {"semantic_graph", "evidence_facts"}
            for path in paths
        )
    )
    if not graph_feedback:
        return False

    identifier = _stable_identity(item.get("id") or item.get("identifier"))
    if reason in {
        "node_fact_dependency_rejected",
        "edge_fact_dependency_rejected",
        "edge_endpoint_dependency_rejected",
        "fact_disposition_dependency_rejected",
    }:
        # 引用本身已声明，真正错误位于被上游校验拒绝的依赖对象。
        analysis.mark_recognized(reason)
        return True
    handled = False
    for path in paths:
        if (
            len(path) == 3
            and path[0] == "evidence_facts"
            and isinstance(path[1], int)
            and isinstance(path[2], str)
            and _is_repair_value_field(path[2])
            and identifier
        ):
            analysis.add_evidence_rule(
                ("evidence_facts", _WILDCARD, str(path[2])),
                reason=reason,
                subject_id_field="fact_id",
                subject_id=identifier,
            )
            handled = True
    identity_field_by_collection = {
        "nodes": "node_id",
        "edges": "edge_id",
        "fact_dispositions": "fact_id",
    }

    # 校验器已经给出字段路径时，直接把该字段绑定到稳定身份。
    # 这样数组排序或无效项级联删除后，重试也不会按脆弱索引改错对象。
    for path in paths:
        if (
            len(path) == 4
            and path[0] == "semantic_graph"
            and path[1] in identity_field_by_collection
            and isinstance(path[2], int)
            and isinstance(path[3], str)
            and identifier
        ):
            if path[1] == "nodes" and path[3] == "workflow_role":
                # workflow_role 由 primary_flow 派生，任何重试都不能直接改写。
                continue
            handled = _add_graph_item_field_rules(
                analysis,
                collection=str(path[1]),
                identity_field=identity_field_by_collection[str(path[1])],
                identifier=identifier,
                fields=(str(path[3]),),
                reason=reason,
            ) or handled

    node_fields_by_reason = {
        "required_node_boundary_unresolved": ("boundary_status",),
        "required_scope_status_unresolved": ("scope_status",),
    }
    edge_fields_by_reason = {
        "edge_endpoint_unknown": ("source_node_id", "target_node_id"),
        "edge_self_reference": ("source_node_id", "target_node_id"),
        "contains_endpoint_kind_invalid": ("source_node_id", "target_node_id"),
        "ownership_endpoint_invalid": (
            "source_node_id",
            "target_node_id",
            "ownership_role",
        ),
        "interaction_contract_incomplete": ("trigger", "result_state"),
    }
    if handled:
        pass
    elif reason == "scope_alias_boundary_ambiguous":
        node_ids = sorted(
            {
                identifier
                for value in (
                    item.get("node_ids")
                    if isinstance(item.get("node_ids"), list)
                    else []
                )
                for identifier in [_stable_identity(value)]
                if identifier
            }
        )
        for node_id in node_ids:
            _add_graph_item_field_rules(
                analysis,
                collection="nodes",
                identity_field="node_id",
                identifier=node_id,
                fields=("name", "aliases"),
                reason=reason,
            )
        handled = bool(node_ids)
    elif reason in node_fields_by_reason:
        handled = _add_graph_item_field_rules(
            analysis,
            collection="nodes",
            identity_field="node_id",
            identifier=identifier,
            fields=node_fields_by_reason[reason],
            reason=reason,
        )
    elif reason in edge_fields_by_reason:
        handled = _add_graph_item_field_rules(
            analysis,
            collection="edges",
            identity_field="edge_id",
            identifier=identifier,
            fields=edge_fields_by_reason[reason],
            reason=reason,
        )
    elif reason == "capability_owner_missing" and identifier:
        _add_graph_item_addition_rule(
            analysis,
            collection="edges",
            reason=reason,
            candidate_equals=(
                ("type", "owns"),
                ("target_node_id", identifier),
            ),
        )
        handled = True
    elif reason == "capability_ownership_ambiguous" and identifier:
        analysis.add_rule(
            ("semantic_graph", "edges", _WILDCARD, "ownership_role"),
            reason=reason,
            allowed_changes=("value_changed", "field_added"),
            candidate_equals=(
                ("type", "owns"),
                ("target_node_id", identifier),
            ),
        )
        handled = True
    elif reason in {"missing_required_fact", "uncovered_fact"} and identifier:
        for collection in ("nodes", "edges"):
            _add_graph_item_addition_rule(
                analysis,
                collection=collection,
                reason=reason,
                candidate_contains=(("fact_ids", identifier),),
            )
        if reason == "uncovered_fact":
            _add_graph_item_addition_rule(
                analysis,
                collection="fact_dispositions",
                reason=reason,
                candidate_equals=(("fact_id", identifier),),
            )
        handled = True
    elif reason == "orphan_node" and identifier:
        _add_graph_item_addition_rule(
            analysis,
            collection="edges",
            reason=reason,
            candidate_any_equals=(
                ("source_node_id", identifier),
                ("target_node_id", identifier),
            ),
        )
        handled = True
    elif reason == "cross_module_interaction_id_missing":
        source_node_id = _stable_identity(item.get("source_node_id"))
        target_node_id = _stable_identity(item.get("target_node_id"))
        if source_node_id and target_node_id:
            _add_graph_item_addition_rule(
                analysis,
                collection="edges",
                reason=reason,
                candidate_equals=(
                    ("type", "interacts_with"),
                    ("source_node_id", source_node_id),
                    ("target_node_id", target_node_id),
                ),
            )
            handled = True
    elif reason == "required_fact_testability_unresolved" and identifier:
        analysis.add_rule(
            ("evidence_facts", _WILDCARD, "testability"),
            reason=reason,
            allowed_changes=("value_changed", "field_added"),
            subject_id_field="fact_id",
            subject_id=identifier,
        )
        handled = True

    # 暂不支持的图错误也会被识别，但不会因此获得集合级权限。
    if not handled:
        analysis.mark_recognized(reason)
    return True


def _path_has_repair_value(tokens: tuple[Any, ...]) -> bool:
    return any(
        isinstance(token, str) and _is_repair_value_field(token)
        for token in tokens
    )


def _parse_field_path(value: Any) -> tuple[Any, ...]:
    text = str(value or "").strip()
    if not text:
        return ()
    if text.startswith("/"):
        tokens: list[Any] = []
        for part in text.split("/")[1:]:
            part = part.replace("~1", "/").replace("~0", "~")
            tokens.append(int(part) if part.isdigit() else _WILDCARD if part == "*" else part)
        parsed = tuple(tokens)
    else:
        text = text.removeprefix("$").lstrip(".")
        parsed_tokens: list[Any] = []
        for match in re.finditer(r"(?:^|\.)([A-Za-z_][A-Za-z0-9_]*)|\[(\d+|\*)\]", text):
            name, index = match.groups()
            if name:
                parsed_tokens.append(name)
            elif index == "*":
                parsed_tokens.append(_WILDCARD)
            elif index is not None:
                parsed_tokens.append(int(index))
        parsed = tuple(parsed_tokens)
    if parsed and parsed[0] == "requirement_semantic_contract":
        parsed = parsed[1:]
    return parsed if parsed and parsed[0] in _TOPOLOGY_ROOTS else ()


def _workflow_path(workflow_index: Any) -> tuple[Any, ...] | None:
    try:
        index = int(workflow_index) - 1
    except (TypeError, ValueError):
        return None
    if index < 0:
        return None
    return ("workflow_blueprints", index)


def _step_path(workflow_index: Any, step_index: Any) -> tuple[Any, ...] | None:
    workflow = _workflow_path(workflow_index)
    try:
        index = int(step_index) - 1
    except (TypeError, ValueError):
        return None
    if workflow is None or index < 0:
        return None
    return (*workflow, "steps", index)


def _add_workflow_reason(
    analysis: _FeedbackPaths,
    workflow_index: Any,
    reason: str,
) -> bool:
    workflow = _workflow_path(workflow_index)
    if workflow is None:
        return False
    field_by_reason = {
        "workflow_name_missing": "name",
        "workflow_id_missing": "workflow_id",
        "workflow_id_invalid": "workflow_id",
        "primary_workflow_not_declared": "primary",
        "initial_state_missing": "initial_state",
        "initial_state_invalid": "initial_state",
        "initial_state_mismatch": "initial_state",
        "required_stage_ids_missing_or_invalid": "required_stage_ids",
        "required_stage_ids_mismatch": "required_stage_ids",
        "terminal_states_missing_or_invalid": "terminal_states",
        "terminal_states_mismatch": "terminal_states",
        "steps_not_list": "steps",
        "steps_empty": "steps",
        "step_count_exceeds_limit": "steps",
    }
    if reason == "workflow_not_object":
        analysis.add_rule(workflow, reason=reason, subtree=True)
        return True
    if reason == "workflow_count_exceeds_limit":
        analysis.add_rule(workflow, reason=reason, subtree=False)
        return True
    field = field_by_reason.get(reason)
    if not field:
        return False
    analysis.add_rule(
        (*workflow, field),
        reason=reason,
        subtree=field in _COLLECTION_FIELDS,
    )
    return True


def _add_step_reason(
    analysis: _FeedbackPaths,
    workflow_index: Any,
    step_index: Any,
    reason: str,
    candidate_field: str = "",
) -> bool:
    step = _step_path(workflow_index, step_index)
    if step is None:
        return False
    if reason == "not_object":
        analysis.add_rule(step, reason=reason, subtree=True)
        return True
    if reason in {"evidence_unverified", "unknown_evidence_ref"}:
        analysis.mark_evidence(reason)
        return True
    if reason in {
        "module_candidates_invalid_or_unverified",
        "required_states_invalid_or_unverified",
        "produced_states_invalid_or_unverified",
    }:
        # 该错误同时可能表示证据错误；没有字段级诊断时不扩大拓扑权限。
        analysis.mark_evidence(reason)
        return True
    fields: tuple[str, ...] = ()
    if reason in {"id_invalid", "id_duplicate"}:
        fields = ("id",)
    elif reason == "state_invalid":
        fields = ("state_in", "state_out")
    elif reason in {
        "interaction_ids_invalid_or_unknown",
        "cross_module_interaction_id_missing",
    }:
        fields = ("interaction_ids", "relation_ids")
    elif reason == "interaction_modules_not_declared":
        fields = (
            (candidate_field,)
            if candidate_field in {"module_candidates", "scope_candidates"}
            else ("scope_candidates",)
        )
    elif reason == "state_modules_not_declared":
        fields = (
            (candidate_field,)
            if candidate_field in {"module_candidates", "scope_candidates"}
            else ("scope_candidates",)
        )
    else:
        matched = re.fullmatch(r"([a-zA-Z_][a-zA-Z0-9_]*)_missing_or_invalid", reason)
        if matched:
            fields = (matched.group(1),)
    if not fields:
        return False
    for field in fields:
        analysis.add_rule(
            (*step, field),
            reason=reason,
            subtree=field in _COLLECTION_FIELDS,
        )
    return True


def _add_typed_state_feedback(analysis: _FeedbackPaths, item: dict[str, Any]) -> bool:
    collection = str(item.get("collection") or "").strip()
    if collection not in {"required_states", "produced_states"}:
        return False
    step = _step_path(item.get("workflow_index"), item.get("step_index"))
    if step is None:
        return False
    collection_path = (*step, collection)
    reason = str(item.get("reason") or "typed_state_invalid").strip()
    try:
        item_index = int(item.get("item_index")) - 1
    except (TypeError, ValueError):
        item_index = -1
    item_path = (*collection_path, item_index if item_index >= 0 else _WILDCARD)
    fields = [
        str(field or "").strip()
        for field in (
            item.get("missing_or_invalid_fields")
            or item.get("invalid_enum_fields")
            or item.get("incompatible_role_fields")
            or []
        )
        if str(field or "").strip()
    ]
    if fields:
        for field in fields:
            analysis.add_rule((*item_path, field), reason=reason)
        return True
    if reason == "collection_not_list":
        analysis.add_rule(collection_path, reason=reason, subtree=True)
        return True
    if reason in {"state_not_object", "item_schema_invalid"}:
        analysis.add_rule(item_path, reason=reason, subtree=True)
        return True
    if "evidence" in reason or "confidence" in reason:
        analysis.mark_evidence(reason)
        return True
    return False


def _add_interaction_direction_role_feedback(
    analysis: _FeedbackPaths,
    item: dict[str, Any],
) -> bool:
    """仅按稳定 scope_id 授权把交互角色改为诊断给出的确定方向。"""

    if str(item.get("reason") or "").strip() != (
        "interaction_direction_roles_mismatch"
    ):
        return False
    step = _step_path(item.get("workflow_index"), item.get("step_index"))
    if step is None:
        return False
    added = False
    for mismatch in item.get("role_mismatches") or []:
        if not isinstance(mismatch, dict):
            continue
        scope_id = _stable_identity(mismatch.get("module_key"))
        expected_role = _stable_identity(mismatch.get("expected_role"))
        if not scope_id or expected_role not in {"source", "target"}:
            continue
        analysis.add_rule(
            (*step, "scope_candidates", _WILDCARD, "role"),
            reason="interaction_direction_roles_mismatch",
            allowed_changes=("value_changed", "field_added"),
            subject_id_field="scope_id",
            subject_id=scope_id,
            candidate_equals=(("role", expected_role),),
        )
        added = True
    return added


def _add_architecture_item_feedback(analysis: _FeedbackPaths, item: dict[str, Any]) -> bool:
    item_type = str(item.get("item_type") or "").strip()
    collection_by_type = {
        "functional_module": "functional_modules",
        "module_interaction": "module_interactions",
    }
    collection = collection_by_type.get(item_type)
    if not collection:
        return False
    try:
        item_index = int(item.get("item_index")) - 1
    except (TypeError, ValueError):
        return False
    if item_index < 0:
        return False
    base = ("functional_architecture", collection, item_index)
    reason = str(item.get("reason") or "architecture_item_invalid").strip()
    fields = [
        str(field or "").strip()
        for field in (item.get("missing_or_invalid_fields") or [])
        if str(field or "").strip()
    ]
    if fields:
        for field in fields:
            analysis.add_rule((*base, field), reason=reason)
        return True
    fields_by_reason = {
        "module_identity_missing": ("module_key", "module_name"),
        "scope_not_in_scope": ("scope_status",),
        "excluded_module_scope_not_declared": ("scope_status",),
        "source_target_same_module": ("source_module_key", "target_module_key"),
        "module_reference_not_active": ("source_module_key", "target_module_key"),
        "interaction_duplicate": ("interaction_id",),
    }
    mapped_fields = fields_by_reason.get(reason, ())
    if mapped_fields:
        for field in mapped_fields:
            analysis.add_rule((*base, field), reason=reason)
        return True
    if "evidence" in reason or "confidence" in reason:
        analysis.mark_evidence(reason)
        return True
    if reason in {"module_invalid", "interaction_not_object"}:
        analysis.add_rule(base, reason=reason, subtree=True)
        return True
    return False


def _add_structured_feedback(analysis: _FeedbackPaths, item: dict[str, Any]) -> bool:
    graph_feedback = _add_semantic_graph_feedback(analysis, item)
    recognized = graph_feedback
    for key in ("field_path", "json_path", "path", "location"):
        path = _parse_field_path(item.get(key))
        if not path:
            continue
        reason = str(item.get("reason") or key)
        if graph_feedback:
            continue
        if _is_broad_graph_collection_path(path):
            # 裸集合路径没有目标身份，不能转化为整棵图的修改权限。
            analysis.mark_recognized(reason)
            recognized = True
            continue
        if _is_unscoped_identity_collection_item_path(path):
            analysis.mark_recognized(reason)
            recognized = True
            continue
        if _path_has_repair_value(path):
            analysis.mark_evidence(reason)
        else:
            analysis.add_rule(
                path,
                reason=reason,
                subtree=bool(item.get("allow_subtree"))
                or bool(path and path[-1] in _COLLECTION_FIELDS),
            )
        recognized = True

    if _add_typed_state_feedback(analysis, item):
        recognized = True
    if _add_interaction_direction_role_feedback(analysis, item):
        recognized = True
    if _add_architecture_item_feedback(analysis, item):
        recognized = True

    reason = str(item.get("reason") or "").strip()
    workflow_index = item.get("workflow_index")
    step_index = item.get("step_index")
    if reason and step_index not in (None, ""):
        if _add_step_reason(
            analysis,
            workflow_index,
            step_index,
            reason,
            candidate_field=str(item.get("candidate_field") or "").strip(),
        ):
            recognized = True
    elif reason and workflow_index not in (None, ""):
        if _add_workflow_reason(analysis, workflow_index, reason):
            recognized = True
    return recognized


def _add_string_feedback(analysis: _FeedbackPaths, value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith("workflow_consistency="):
        raw_json = text.split("=", 1)[1]
        try:
            parsed = json.loads(raw_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return isinstance(parsed, dict) and _add_structured_feedback(analysis, parsed)

    direct_path = _parse_field_path(text)
    if direct_path:
        if _is_broad_graph_collection_path(direct_path):
            analysis.mark_recognized(text)
            return True
        if _is_unscoped_identity_collection_item_path(direct_path):
            analysis.mark_recognized(text)
            return True
        if _path_has_repair_value(direct_path):
            analysis.mark_evidence(text)
        else:
            analysis.add_rule(
                direct_path,
                reason="explicit_field_path",
                subtree=direct_path[-1] in _COLLECTION_FIELDS,
            )
        return True

    step_match = re.match(r"workflow_(\d+):step_(\d+):(.+)", text)
    if step_match:
        workflow_index, step_index, tail = step_match.groups()
        collection_match = re.match(
            r"(required_states|produced_states):([^:]+)(?::fields=([^:;]+))?",
            tail,
        )
        if collection_match:
            collection, reason, fields_text = collection_match.groups()
            structured = {
                "workflow_index": int(workflow_index),
                "step_index": int(step_index),
                "collection": collection,
                "reason": reason,
                "missing_or_invalid_fields": [
                    field.strip()
                    for field in str(fields_text or "").split(",")
                    if field.strip()
                ],
            }
            return _add_typed_state_feedback(analysis, structured)
        reason = tail.split(":", 1)[0].strip()
        return _add_step_reason(analysis, workflow_index, step_index, reason)

    workflow_match = re.match(r"workflow_(\d+):([^:]+)", text)
    if workflow_match:
        return _add_workflow_reason(
            analysis,
            workflow_match.group(1),
            workflow_match.group(2).strip(),
        )

    if text in {
        "missing_workflow_declaration",
        "workflow_declaration_missing",
        "workflow_blueprints_not_list",
    }:
        analysis.add_rule(
            ("workflow_blueprints",),
            reason=text,
            subtree=True,
        )
        return True
    if text == "workflow_blueprints_location_conflict":
        analysis.mark_recognized(text)
        return True
    if "evidence" in text.lower() or "confidence" in text.lower():
        analysis.mark_evidence(text)
        return True
    return False


def _visit_feedback(analysis: _FeedbackPaths, feedback: Any) -> None:
    if feedback in (None, ""):
        return
    if isinstance(feedback, (list, tuple, set)):
        for item in feedback:
            _visit_feedback(analysis, item)
        return
    if isinstance(feedback, dict):
        recognized = _add_structured_feedback(analysis, feedback)
        container_keys = (
            "workflow_rejection_reasons",
            "typed_state_rejections",
            "workflow_consistency_rejections",
            "rejected_semantic_items",
            "semantic_graph_rejections",
            "validation_feedback",
        )
        had_container = False
        for key in container_keys:
            if key not in feedback:
                continue
            had_container = True
            _visit_feedback(analysis, feedback.get(key))
        if not recognized and not had_container:
            analysis.mark_unrecognized()
        return
    if not _add_string_feedback(analysis, str(feedback)):
        analysis.mark_unrecognized()


def derive_allowed_topology_paths(validation_feedback: Any) -> dict[str, Any]:
    """把字段级校验反馈编译成最小可变路径集合。"""
    analysis = _FeedbackPaths()
    _visit_feedback(analysis, validation_feedback)
    repair_suppressed_by = sorted(
        set(analysis.recognized)
        & (
            UNREPAIRABLE_PRIMARY_FLOW_ERROR_CODES
            | STRUCTURAL_GRAPH_RECOMPILE_ERROR_CODES
        )
    )
    # 非法主流程或结构冲突没有唯一的局部解；同轮其他错误不得旁路扩权改图。
    ordered_rules = (
        []
        if repair_suppressed_by
        else sorted(analysis.rules, key=_repair_rule_priority)
    )
    if ordered_rules:
        scope = "targeted"
    elif analysis.evidence_only_count:
        scope = "evidence_only"
    else:
        scope = "unscoped"
    return {
        "feedback_scope": scope,
        "allowed_paths": [rule.display_path for rule in ordered_rules],
        "allowed_path_reasons": [rule.reason for rule in ordered_rules],
        "recognized_feedback": analysis.recognized[:_MAX_FEEDBACK_DIAGNOSTICS],
        "evidence_only_feedback_count": int(analysis.evidence_only_count),
        "unrecognized_feedback_count": int(analysis.unrecognized_count),
        "topology_repair_suppressed_by": repair_suppressed_by,
        "_rules": ordered_rules,
        "_evidence_rules": list(analysis.evidence_rules),
    }


def _is_bare_identity_collection_item_rule(rule: _AllowedPath) -> bool:
    """识别只有数组索引、没有身份约束或子树语义的不可执行容器路径。"""

    return bool(
        (
            _is_unscoped_identity_collection_item_path(rule.tokens)
            or (
                len(rule.tokens) >= 2
                and rule.tokens[-1] is _WILDCARD
                and str(rule.tokens[-2]) in _REPAIR_COLLECTION_IDENTITIES
            )
        )
        and not rule.subtree
        and not rule.subject_id_field
        and not rule.candidate_equals
        and not rule.candidate_contains
        and not rule.candidate_any_equals
    )


def _repair_rule_priority(rule: _AllowedPath) -> int:
    """结构化身份规则优先，裸容器路径最后，且保持结果确定。"""

    if rule.subject_id_field and rule.subject_id:
        priority = 0
    elif (
        rule.candidate_equals
        or rule.candidate_contains
        or rule.candidate_any_equals
        or rule.materialize_matching_path
    ):
        priority = 1
    elif _is_bare_identity_collection_item_rule(rule):
        priority = 3
    else:
        priority = 2
    return priority


def compile_semantic_retry_repair_targets(
    validation_feedback: Any,
    *,
    limit: int = _MAX_REPAIR_TARGETS,
) -> list[dict[str, Any]]:
    """把反馈编译成模型可执行的最小修复目标，并保留稳定身份条件。"""

    analysis = derive_allowed_topology_paths(validation_feedback)
    rules = list(analysis.get("_rules") or [])
    evidence_rules = list(analysis.get("_evidence_rules") or [])
    targets: list[dict[str, Any]] = []
    grouped_identity_targets: dict[tuple[str, ...], dict[str, Any]] = {}
    grouped_candidate_targets: dict[tuple[str, str, str], dict[str, Any]] = {}
    seen: set[str] = set()
    seen_unscoped_paths: set[tuple[str, str]] = set()

    for rule in evidence_rules:
        if not isinstance(rule, _EvidenceRepairPath):
            continue
        target: dict[str, Any] = {
            "code": rule.reason or "evidence_validation_error",
            "path": rule.display_path,
            "operation": (
                "replace_with_verified_evidence_refs"
                if rule.tokens and str(rule.tokens[-1]) == "evidence"
                else "replace_value"
            ),
        }
        if rule.subject_id_field and rule.subject_id:
            group_key = (
                target["code"],
                target["path"],
                target["operation"],
                rule.subject_id_field,
            )
            existing = grouped_identity_targets.get(group_key)
            if existing is None:
                target["match"] = {rule.subject_id_field: [rule.subject_id]}
                grouped_identity_targets[group_key] = target
                targets.append(target)
            else:
                identities = existing["match"][rule.subject_id_field]
                if rule.subject_id not in identities:
                    identities.append(rule.subject_id)
            continue
        unscoped_key = (target["path"], target["operation"])
        if unscoped_key in seen_unscoped_paths:
            continue
        seen_unscoped_paths.add(unscoped_key)
        marker = json.dumps(target, ensure_ascii=False, sort_keys=True)
        if marker not in seen:
            seen.add(marker)
            targets.append(target)

    for rule in rules:
        if not isinstance(rule, _AllowedPath):
            continue
        operation = (
            "remove_item"
            if rule.reason == "workflow_count_exceeds_limit"
            else (
                "add_item"
                if rule.materialize_matching_path
                else "repair_subtree" if rule.subtree else "replace_value"
            )
        )
        if _is_bare_identity_collection_item_rule(rule) and operation != "remove_item":
            # 裸对象路径既无法安全合并，也没有稳定身份，不能占用模型修复预算。
            continue

        target: dict[str, Any] = {
            "code": rule.reason or "validation_error",
            "path": rule.display_path,
            "operation": operation,
        }
        if rule.subject_id_field and rule.subject_id:
            value_constraint = {}
            if rule.tokens and isinstance(rule.tokens[-1], str):
                target_field = str(rule.tokens[-1])
                constrained_values = [
                    value
                    for field, value in rule.candidate_equals
                    if field == target_field
                ]
                if len(constrained_values) == 1:
                    value_constraint = {"equals": constrained_values[0]}
            if value_constraint:
                target["value_constraint"] = value_constraint
            group_key = (
                target["code"],
                target["path"],
                target["operation"],
                rule.subject_id_field,
                json.dumps(value_constraint, ensure_ascii=False, sort_keys=True),
            )
            existing = grouped_identity_targets.get(group_key)
            if existing is None:
                target["match"] = {rule.subject_id_field: [rule.subject_id]}
                grouped_identity_targets[group_key] = target
                targets.append(target)
            else:
                identities = existing["match"][rule.subject_id_field]
                if rule.subject_id not in identities:
                    identities.append(rule.subject_id)
            continue

        candidate_match = {
            key: values
            for key, values in (
                (
                    "equals",
                    {field: value for field, value in rule.candidate_equals},
                ),
                (
                    "contains",
                    {field: value for field, value in rule.candidate_contains},
                ),
                (
                    "any_equals",
                    [
                        {"field": field, "value": value}
                        for field, value in rule.candidate_any_equals
                    ],
                ),
            )
            if values
        }
        if candidate_match:
            group_key = (target["code"], target["path"], target["operation"])
            existing = grouped_candidate_targets.get(group_key)
            if existing is None:
                target["candidate_match"] = [candidate_match]
                grouped_candidate_targets[group_key] = target
                targets.append(target)
            elif candidate_match not in existing["candidate_match"]:
                existing["candidate_match"].append(candidate_match)
            continue

        unscoped_key = (target["path"], target["operation"])
        if unscoped_key in seen_unscoped_paths:
            continue
        seen_unscoped_paths.add(unscoped_key)
        marker = json.dumps(target, ensure_ascii=False, sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        targets.append(target)

    return targets[: max(1, int(limit))]


def _tokens_match(rule_tokens: tuple[Any, ...], actual_tokens: tuple[Any, ...]) -> bool:
    if len(rule_tokens) != len(actual_tokens):
        return False
    return all(
        expected is _WILDCARD or expected == actual
        for expected, actual in zip(rule_tokens, actual_tokens)
    )


def _value_at_tokens(value: Any, tokens: tuple[Any, ...]) -> Any:
    current = value
    for token in tokens:
        if isinstance(token, int) and isinstance(current, list):
            if token < 0 or token >= len(current):
                return None
            current = current[token]
        elif isinstance(token, str) and isinstance(current, dict):
            if token not in current:
                return None
            current = current[token]
        else:
            return None
    return current


def _candidate_source_tokens(
    canonical_tokens: tuple[Any, ...],
    *,
    candidate_projection: dict[str, Any],
    candidate_payload: dict[str, Any] | None,
) -> tuple[Any, ...]:
    """把规范化集合索引换回候选原始索引，避免数组重排后复制错项。"""

    if candidate_payload is None or not canonical_tokens:
        return canonical_tokens
    indexed_positions = [
        index for index, token in enumerate(canonical_tokens) if isinstance(token, int)
    ]
    if not indexed_positions:
        return canonical_tokens
    item_index_position = indexed_positions[-1]
    if item_index_position <= 0:
        return canonical_tokens
    collection = str(canonical_tokens[item_index_position - 1])
    identity_fields = _REPAIR_COLLECTION_IDENTITIES.get(collection)
    if not identity_fields:
        return canonical_tokens
    canonical_item_tokens = canonical_tokens[: item_index_position + 1]
    canonical_item = _value_at_tokens(candidate_projection, canonical_item_tokens)
    identity = _collection_identity(canonical_item, identity_fields)
    if identity is None:
        return canonical_tokens
    raw_collection_tokens = canonical_tokens[:item_index_position]
    raw_items = _value_at_tokens(candidate_payload, raw_collection_tokens)
    if not isinstance(raw_items, list):
        return canonical_tokens
    matching_indexes = [
        index
        for index, item in enumerate(raw_items)
        if _collection_identity(item, identity_fields) == identity
    ]
    if len(matching_indexes) != 1:
        return canonical_tokens
    return (
        *raw_collection_tokens,
        matching_indexes[0],
        *canonical_tokens[item_index_position + 1 :],
    )


def _rule_subject_items(
    diff_tokens: tuple[Any, ...],
    rule: _AllowedPath,
    *,
    anchor_projection: dict[str, Any],
    candidate_projection: dict[str, Any],
) -> tuple[Any, Any]:
    try:
        wildcard_index = rule.tokens.index(_WILDCARD)
    except ValueError:
        return None, None
    item_tokens = diff_tokens[: wildcard_index + 1]
    return (
        _value_at_tokens(anchor_projection, item_tokens),
        _value_at_tokens(candidate_projection, item_tokens),
    )


def _item_field_matches(item: Any, field: str, expected: str) -> bool:
    return bool(
        isinstance(item, dict)
        and _stable_identity(item.get(field)) == expected
    )


def _candidate_constraint_matches(
    candidate_item: Any,
    rule: _AllowedPath,
) -> bool:
    if not isinstance(candidate_item, dict):
        return not (
            rule.candidate_equals
            or rule.candidate_contains
            or rule.candidate_any_equals
        )
    if any(
        not _item_field_matches(candidate_item, field, expected)
        for field, expected in rule.candidate_equals
    ):
        return False
    for field, expected in rule.candidate_contains:
        values = candidate_item.get(field)
        if not isinstance(values, list) or expected not in {
            _stable_identity(value) for value in values
        }:
            return False
    if rule.candidate_any_equals and not any(
        _item_field_matches(candidate_item, field, expected)
        for field, expected in rule.candidate_any_equals
    ):
        return False
    return True


def _diff_allowed(
    diff: dict[str, Any],
    rule: _AllowedPath,
    *,
    anchor_projection: dict[str, Any],
    candidate_projection: dict[str, Any],
) -> bool:
    diff_tokens = tuple(diff.get("path_tokens") or ())
    if (
        rule.reason == "cross_module_interaction_id_missing"
        and rule.tokens
        == ("functional_architecture", "module_interactions")
    ):
        return bool(
            len(diff_tokens) == len(rule.tokens) + 1
            and diff_tokens[: len(rule.tokens)] == rule.tokens
        )
    if rule.subtree:
        prefix = diff_tokens[: len(rule.tokens)]
        path_matches = len(diff_tokens) >= len(rule.tokens) and _tokens_match(
            rule.tokens,
            prefix,
        )
    else:
        path_matches = _tokens_match(rule.tokens, diff_tokens)
    if not path_matches:
        return False
    if rule.allowed_changes and str(diff.get("change") or "") not in rule.allowed_changes:
        return False
    anchor_item, candidate_item = _rule_subject_items(
        diff_tokens,
        rule,
        anchor_projection=anchor_projection,
        candidate_projection=candidate_projection,
    )
    if rule.subject_id_field and rule.subject_id and not any(
        _item_field_matches(item, rule.subject_id_field, rule.subject_id)
        for item in (anchor_item, candidate_item)
    ):
        return False
    return _candidate_constraint_matches(candidate_item, rule)


def _public_diff(diff: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in diff.items() if key != "path_tokens"}


def _evaluate_projections(
    anchor_projection: dict[str, Any],
    candidate_projection: dict[str, Any],
    validation_feedback: Any,
    *,
    candidate_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    diffs: list[dict[str, Any]] = []
    truncated = _collect_diffs(
        anchor_projection,
        candidate_projection,
        output=diffs,
    )
    if truncated:
        diffs.append(
            {
                "path": "$",
                "path_tokens": (),
                "change": "diff_limit_exceeded",
            }
        )
    feedback = derive_allowed_topology_paths(validation_feedback)
    rules = list(feedback.pop("_rules"))
    feedback.pop("_evidence_rules", None)
    allowed_diffs = [
        diff
        for diff in diffs
        if any(
            _diff_allowed(
                diff,
                rule,
                anchor_projection=anchor_projection,
                candidate_projection=candidate_projection,
            )
            and not (
                rule.reason == "cross_module_interaction_id_missing"
                and rule.tokens
                == ("functional_architecture", "module_interactions")
                and str(diff.get("change") or "") != "item_added"
            )
            for rule in rules
        )
    ]
    # 一条缺失交互诊断最多授权新增一条同端点边；平行候选保持 fail-close。
    for rule in rules:
        if not (
            rule.reason == "cross_module_interaction_id_missing"
            and rule.tokens == ("semantic_graph", "edges", _WILDCARD)
        ):
            continue
        matching_additions = [
            diff
            for diff in allowed_diffs
            if str(diff.get("change") or "") == "item_added"
            and _diff_allowed(
                diff,
                rule,
                anchor_projection=anchor_projection,
                candidate_projection=candidate_projection,
            )
        ]
        if len(matching_additions) > 1:
            allowed_diffs = [
                diff for diff in allowed_diffs if diff not in matching_additions
            ]
    blocked_diffs = [diff for diff in diffs if diff not in allowed_diffs]
    effective_allowed_paths: list[str] = []
    for rule in rules:
        matching_diffs = [
            diff
            for diff in allowed_diffs
            if _diff_allowed(
                diff,
                rule,
                anchor_projection=anchor_projection,
                candidate_projection=candidate_projection,
            )
        ]
        if (
            rule.reason == "cross_module_interaction_id_missing"
            and rule.tokens
            == ("functional_architecture", "module_interactions")
        ):
            for diff in allowed_diffs:
                tokens = tuple(diff.get("path_tokens") or ())
                if (
                    str(diff.get("change") or "") == "item_added"
                    and len(tokens) == len(rule.tokens) + 1
                    and tokens[: len(rule.tokens)] == rule.tokens
                ):
                    path = f"{_format_path(tokens)}.**"
                    if path not in effective_allowed_paths:
                        effective_allowed_paths.append(path)
            continue
        constrained = bool(
            rule.allowed_changes
            or rule.subject_id_field
            or rule.candidate_equals
            or rule.candidate_contains
            or rule.candidate_any_equals
        )
        if constrained:
            for diff in matching_diffs:
                tokens = tuple(diff.get("path_tokens") or ())
                if rule.materialize_matching_path or rule.subtree:
                    tokens = tuple(
                        actual if expected is _WILDCARD else expected
                        for expected, actual in zip(rule.tokens, tokens)
                    )
                    tokens = _candidate_source_tokens(
                        tokens,
                        candidate_projection=candidate_projection,
                        candidate_payload=candidate_payload,
                    )
                    path = f"{_format_path(tokens)}.**"
                else:
                    tokens = _candidate_source_tokens(
                        tokens,
                        candidate_projection=candidate_projection,
                        candidate_payload=candidate_payload,
                    )
                    path = _format_path(tokens)
                if path not in effective_allowed_paths:
                    effective_allowed_paths.append(path)
            continue
        if rule.display_path not in effective_allowed_paths:
            effective_allowed_paths.append(rule.display_path)
    allowed = not blocked_diffs
    if not diffs:
        decision = "topology_unchanged"
    elif allowed:
        decision = "targeted_changes_only"
    else:
        decision = "topology_drift_blocked"
    return {
        "applicable": True,
        "allowed": allowed,
        "decision": decision,
        "anchor_created": False,
        "anchor_fingerprint": _fingerprint(anchor_projection),
        "candidate_fingerprint": _fingerprint(candidate_projection),
        "topology_changed": bool(diffs),
        "topology_diff_count": int(len(diffs)),
        "allowed_diff_count": int(len(allowed_diffs)),
        "blocked_diff_count": int(len(blocked_diffs)),
        "allowed_paths": effective_allowed_paths,
        "feedback_scope": feedback["feedback_scope"],
        "feedback_diagnostics": {
            key: value
            for key, value in feedback.items()
            if key not in {"allowed_paths", "feedback_scope"}
        },
        "topology_diffs": [
            _public_diff(item) for item in diffs[:_MAX_DIAGNOSTIC_DIFFS]
        ],
        "blocked_topology_diffs": [
            _public_diff(item) for item in blocked_diffs[:_MAX_DIAGNOSTIC_DIFFS]
        ],
        "diff_diagnostics_truncated": bool(len(diffs) > _MAX_DIAGNOSTIC_DIFFS),
    }


def evaluate_semantic_retry_topology(
    anchor_candidate: Any,
    candidate: Any,
    *,
    validation_feedback: Any = None,
) -> dict[str, Any]:
    """以给定锚点校验一次重试，锚点本身不会被后续候选覆盖。"""
    if not isinstance(anchor_candidate, dict) or not isinstance(candidate, dict):
        return {
            "applicable": False,
            "allowed": False,
            "decision": "candidate_not_parseable",
            "anchor_created": False,
            "topology_changed": False,
            "topology_diff_count": 0,
            "allowed_diff_count": 0,
            "blocked_diff_count": 0,
            "allowed_paths": [],
            "feedback_scope": "unscoped",
            "feedback_diagnostics": {},
            "topology_diffs": [],
            "blocked_topology_diffs": [],
            "diff_diagnostics_truncated": False,
        }
    return _evaluate_projections(
        build_semantic_topology_projection(anchor_candidate),
        build_semantic_topology_projection(candidate),
        validation_feedback,
        candidate_payload=_contract_payload(candidate),
    )


class SemanticRetryTopologyGuard:
    """有状态的重试守卫：首个可解析候选固定为本轮不可变锚点。"""

    def __init__(self) -> None:
        self._anchor_projection: dict[str, Any] | None = None
        self._working_projection: dict[str, Any] | None = None
        self._parseable_candidate_count = 0

    @property
    def anchored(self) -> bool:
        return self._anchor_projection is not None

    @property
    def anchor_fingerprint(self) -> str:
        return _fingerprint(self._anchor_projection) if self._anchor_projection is not None else ""

    @property
    def working_fingerprint(self) -> str:
        return (
            _fingerprint(self._working_projection)
            if self._working_projection is not None
            else ""
        )

    def advance_working_candidate(self, candidate: Any) -> bool:
        """仅在候选通过本轮拓扑守卫后推进工作基线，避免旧授权跨轮复用。"""

        if not isinstance(candidate, dict):
            return False
        projection = build_semantic_topology_projection(candidate)
        if self._anchor_projection is None:
            self._anchor_projection = projection
        self._working_projection = projection
        return True

    def evaluate(
        self,
        candidate: Any,
        *,
        validation_feedback: Any = None,
    ) -> dict[str, Any]:
        if not isinstance(candidate, dict):
            return {
                "applicable": False,
                "allowed": False,
                "decision": "candidate_not_parseable",
                "anchor_created": False,
                "parseable_candidate_count": int(self._parseable_candidate_count),
                "anchor_fingerprint": self.anchor_fingerprint,
                "topology_changed": False,
                "topology_diff_count": 0,
                "allowed_diff_count": 0,
                "blocked_diff_count": 0,
                "allowed_paths": [],
                "feedback_scope": "unscoped",
                "feedback_diagnostics": {},
                "topology_diffs": [],
                "blocked_topology_diffs": [],
                "diff_diagnostics_truncated": False,
            }

        self._parseable_candidate_count += 1
        candidate_projection = build_semantic_topology_projection(candidate)
        if self._anchor_projection is None:
            self._anchor_projection = candidate_projection
            self._working_projection = candidate_projection
            fingerprint = _fingerprint(candidate_projection)
            return {
                "applicable": True,
                "allowed": True,
                "decision": "anchor_created",
                "anchor_created": True,
                "parseable_candidate_count": int(self._parseable_candidate_count),
                "anchor_fingerprint": fingerprint,
                "working_base_fingerprint": fingerprint,
                "candidate_fingerprint": fingerprint,
                "topology_changed": False,
                "topology_diff_count": 0,
                "allowed_diff_count": 0,
                "blocked_diff_count": 0,
                "allowed_paths": [],
                "feedback_scope": "anchor",
                "feedback_diagnostics": {},
                "topology_diffs": [],
                "blocked_topology_diffs": [],
                "diff_diagnostics_truncated": False,
            }

        working_projection = self._working_projection or self._anchor_projection
        result = _evaluate_projections(
            working_projection,
            candidate_projection,
            validation_feedback,
            candidate_payload=_contract_payload(candidate),
        )
        result["anchor_fingerprint"] = self.anchor_fingerprint
        result["working_base_fingerprint"] = self.working_fingerprint
        result["parseable_candidate_count"] = int(self._parseable_candidate_count)
        return result


__all__ = [
    "SemanticRetryTopologyGuard",
    "build_semantic_topology_projection",
    "compile_semantic_retry_repair_targets",
    "derive_allowed_topology_paths",
    "diff_semantic_topology",
    "evaluate_semantic_retry_topology",
    "merge_semantic_retry_repair_values",
    "preserve_verified_semantic_evidence",
]
