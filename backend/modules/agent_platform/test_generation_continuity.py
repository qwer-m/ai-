from __future__ import annotations

from typing import Any, TYPE_CHECKING

from .test_generation_batching import (
    _normalize_batch_accounting_item,
    _stable_payload_hash,
)

if TYPE_CHECKING:
    from .registry import ToolExecutionContext


MAX_CONTINUITY_AUDIT_ITEMS = 100


def _required_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是对象")
    return dict(value)


def _catalog_items(value: Any) -> list[dict[str, Any]]:
    catalog = _required_object(value, "evidence_catalog")
    raw_items = catalog.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("evidence_catalog.items 必须是数组")
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for position, raw_item in enumerate(raw_items):
        item = _required_object(raw_item, f"evidence_catalog.items[{position}]")
        evidence_id = str(item.get("evidence_id") or "").strip()
        if not evidence_id or evidence_id in seen_ids:
            raise ValueError(f"证据目录 ID 为空或重复: evidence_id={evidence_id}")
        seen_ids.add(evidence_id)
        items.append(item)
    return items


def _routing_index(
    value: Any,
    *,
    catalog_items: list[dict[str, Any]],
    module_count: int,
) -> dict[str, dict[str, Any]]:
    routing = _required_object(value, "routing")
    raw_accounting = routing.get("evidence_accounting")
    if not isinstance(raw_accounting, list):
        raise ValueError("routing.evidence_accounting 必须是数组")
    expected_ids = [str(item["evidence_id"]) for item in catalog_items]
    accounting: dict[str, dict[str, Any]] = {}
    for raw_item in raw_accounting:
        item = _normalize_batch_accounting_item(
            raw_item=raw_item,
            module_count=module_count,
        )
        evidence_id = item["evidence_id"]
        if evidence_id in accounting:
            raise ValueError(f"证据总账包含重复 ID: evidence_id={evidence_id}")
        accounting[evidence_id] = item
    if list(accounting) != expected_ids:
        raise ValueError(
            "证据总账必须按目录顺序完整覆盖: "
            f"expected={expected_ids}, actual={list(accounting)}"
        )
    return accounting


def _validated_continuation(
    *,
    catalog_items: list[dict[str, Any]],
    right_position: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    right = catalog_items[right_position]
    continuation = _required_object(right.get("continuation"), "continuation")
    if continuation.get("confidence") != "high":
        raise ValueError("跨页审计只接受高置信 continuation")
    if right_position < 1:
        raise ValueError("continuation 缺少直接相邻的左证据")
    left = catalog_items[right_position - 1]
    left_id = str(left.get("evidence_id") or "")
    right_id = str(right.get("evidence_id") or "")
    if str(continuation.get("previous_evidence_id") or "") != left_id:
        raise ValueError(
            "continuation.previous_evidence_id 不是目录直接前项: "
            f"evidence_id={right_id}"
        )
    left_page = int(left.get("page_number") or 0)
    right_page = int(right.get("page_number") or 0)
    if left_page < 1 or right_page != left_page + 1:
        raise ValueError(
            "continuation 必须连接直接相邻的物理页: "
            f"left_page={left_page}, right_page={right_page}"
        )
    if (
        int(left.get("document_id") or 0) != int(right.get("document_id") or 0)
        or str(left.get("asset_source_sha256") or "")
        != str(right.get("asset_source_sha256") or "")
    ):
        raise ValueError("continuation 两侧必须来自同一文档资产版本")
    left_span = _required_object(
        continuation.get("left_tail_span"),
        "continuation.left_tail_span",
    )
    left_marker_span = _required_object(
        continuation.get("left_marker_span"),
        "continuation.left_marker_span",
    )
    minimum_governing_span = _required_object(
        continuation.get("minimum_governing_span"),
        "continuation.minimum_governing_span",
    )
    right_range = _required_object(
        continuation.get("right_range"),
        "continuation.right_range",
    )
    left_text = str(left.get("text") or "")
    right_text = str(right.get("text") or "")
    if (
        set(left_span) != {"start", "end"}
        or left_span.get("start") not in range(len(left_text) + 1)
        or left_span.get("end") != len(left_text)
        or int(left_span["start"]) >= int(left_span["end"])
    ):
        raise ValueError("continuation.left_tail_span 超出左证据正文范围")
    if (
        set(left_marker_span) != {"start", "end"}
        or isinstance(left_marker_span.get("start"), bool)
        or not isinstance(left_marker_span.get("start"), int)
        or isinstance(left_marker_span.get("end"), bool)
        or not isinstance(left_marker_span.get("end"), int)
        or int(left_marker_span["start"]) < int(left_span["start"])
        or int(left_marker_span["end"]) > int(left_span["end"])
        or int(left_marker_span["start"]) >= int(left_marker_span["end"])
    ):
        raise ValueError("continuation.left_marker_span 必须严格位于左证据页尾范围")
    if (
        set(minimum_governing_span) != {"start", "end"}
        or isinstance(minimum_governing_span.get("start"), bool)
        or not isinstance(minimum_governing_span.get("start"), int)
        or isinstance(minimum_governing_span.get("end"), bool)
        or not isinstance(minimum_governing_span.get("end"), int)
        or int(minimum_governing_span["start"]) < int(left_span["start"])
        or int(minimum_governing_span["end"]) > int(left_span["end"])
        or int(minimum_governing_span["start"]) > int(left_marker_span["start"])
        or int(minimum_governing_span["end"]) < int(left_marker_span["end"])
    ):
        raise ValueError(
            "continuation.minimum_governing_span 必须位于页尾并覆盖边界标记"
        )
    if set(right_range) != {"start", "end", "head_end"}:
        raise ValueError("continuation.right_range 字段不完整")
    if (
        right_range.get("start") != 0
        or right_range.get("end") != len(right_text)
        or isinstance(right_range.get("head_end"), bool)
        or not isinstance(right_range.get("head_end"), int)
        or not 0 < int(right_range["head_end"]) <= len(right_text)
    ):
        raise ValueError("continuation.right_range 超出右证据正文范围")
    return left, right, continuation


def _compact_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    modules = plan.get("business_modules")
    if not isinstance(modules, list) or not modules:
        raise ValueError("draft_plan.business_modules 必须是非空数组")
    compact: list[dict[str, Any]] = []
    for module_index, raw_module in enumerate(modules):
        module = _required_object(
            raw_module,
            f"draft_plan.business_modules[{module_index}]",
        )
        compact.append(
            {
                "module_index": module_index,
                "name": str(module.get("name") or ""),
                "objective": str(module.get("objective") or ""),
            }
        )
    return compact


def prepare_continuity_audit_items(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """只为目录中的高置信跨页链接构造短审计输入。"""

    plan = _required_object(arguments.get("draft_plan"), "draft_plan")
    compact_plan = _compact_plan(plan)
    catalog_items = _catalog_items(arguments.get("evidence_catalog"))
    accounting = _routing_index(
        arguments.get("routing"),
        catalog_items=catalog_items,
        module_count=len(compact_plan),
    )
    items: list[dict[str, Any]] = []
    for right_position, catalog_item in enumerate(catalog_items):
        raw_continuation = catalog_item.get("continuation")
        if raw_continuation is None:
            continue
        if not isinstance(raw_continuation, dict):
            raise ValueError("continuation 必须是对象")
        if raw_continuation.get("confidence") != "high":
            continue
        left, right, continuation = _validated_continuation(
            catalog_items=catalog_items,
            right_position=right_position,
        )
        left_span = continuation["left_tail_span"]
        right_range = continuation["right_range"]
        left_text = str(left["text"])
        right_text = str(right["text"])
        items.append(
            {
                "continuity_index": len(items),
                "plan_modules": compact_plan,
                "left_evidence": {
                    "evidence_id": str(left["evidence_id"]),
                    "page_number": int(left["page_number"]),
                    "tail_span": dict(left_span),
                    "tail_text": left_text[int(left_span["start"]) :],
                },
                "right_evidence": {
                    "evidence_id": str(right["evidence_id"]),
                    "page_number": int(right["page_number"]),
                    "text": right_text,
                    "head_text": right_text[: int(right_range["head_end"])],
                },
                "structure": dict(continuation),
            }
        )
        if len(items) > MAX_CONTINUITY_AUDIT_ITEMS:
            raise ValueError(
                "跨页连续性审计项超过 agent_map 上限: "
                f"count={len(items)}, limit={MAX_CONTINUITY_AUDIT_ITEMS}"
            )
    context.artifacts["continuity_audit_prepare"] = {
        "link_count": len(items),
        "evidence_pairs": [
            [item["left_evidence"]["evidence_id"], item["right_evidence"]["evidence_id"]]
            for item in items
        ],
    }
    return {"items": items, "link_count": len(items)}


def _normalize_spans(value: Any, *, text_length: int) -> list[dict[str, int]]:
    if not isinstance(value, list):
        raise ValueError("continuity.spans 必须是数组")
    spans: list[dict[str, int]] = []
    previous_end = -1
    for raw_span in value:
        span = _required_object(raw_span, "continuity.spans[]")
        if set(span) != {"start", "end"}:
            raise ValueError("continuity.spans 只允许 start/end")
        start = span.get("start")
        end = span.get("end")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start < 0
            or end <= start
            or end > text_length
            or start < previous_end
        ):
            raise ValueError("continuity.spans 越界、交叉或为空")
        spans.append({"start": start, "end": end})
        previous_end = end
    return spans


def merge_continuity_audit(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """严格合并跨页审计；只有整项继承可改写右项证据总账。"""

    plan = _required_object(arguments.get("draft_plan"), "draft_plan")
    module_count = len(_compact_plan(plan))
    catalog_items = _catalog_items(arguments.get("evidence_catalog"))
    accounting = _routing_index(
        arguments.get("routing"),
        catalog_items=catalog_items,
        module_count=module_count,
    )
    prepared_items = arguments.get("prepared_items")
    records = arguments.get("continuity_records")
    if not isinstance(prepared_items, list) or not isinstance(records, list):
        raise ValueError("prepared_items 和 continuity_records 必须是数组")
    if len(prepared_items) != len(records):
        raise ValueError("跨页审计输入与结果数量不一致")
    if not prepared_items:
        context.artifacts["continuity_audit_merge"] = {
            "link_count": 0,
            "inherited_count": 0,
        }
        return {"evidence_accounting": list(accounting.values())}

    catalog_position = {
        str(item["evidence_id"]): position
        for position, item in enumerate(catalog_items)
    }
    records_by_index: dict[int, dict[str, Any]] = {}
    for record in records:
        record = _required_object(record, "continuity_records[]")
        item_index = record.get("item_index")
        if (
            isinstance(item_index, bool)
            or not isinstance(item_index, int)
            or item_index < 0
            or item_index >= len(prepared_items)
            or item_index in records_by_index
        ):
            raise ValueError(f"跨页审计 item_index 无效或重复: {item_index}")
        records_by_index[item_index] = record

    inherited_count = 0
    for item_index, raw_prepared in enumerate(prepared_items):
        prepared = _required_object(raw_prepared, f"prepared_items[{item_index}]")
        if prepared.get("continuity_index") != item_index:
            raise ValueError("跨页审计 continuity_index 与映射顺序不一致")
        left_input = _required_object(prepared.get("left_evidence"), "left_evidence")
        right_input = _required_object(prepared.get("right_evidence"), "right_evidence")
        left_id = str(left_input.get("evidence_id") or "")
        right_id = str(right_input.get("evidence_id") or "")
        right_position = catalog_position.get(right_id, -1)
        if right_position < 1 or catalog_position.get(left_id) != right_position - 1:
            raise ValueError("跨页审计证据 ID 不是目录直接相邻项")
        left, right, continuation = _validated_continuation(
            catalog_items=catalog_items,
            right_position=right_position,
        )
        if left_id != str(left["evidence_id"]) or right_id != str(right["evidence_id"]):
            raise ValueError("跨页审计证据 ID 与 continuation 不一致")
        expected_left_span = continuation["left_tail_span"]
        expected_right_range = continuation["right_range"]
        expected_left = {
            "evidence_id": left_id,
            "page_number": int(left["page_number"]),
            "tail_span": dict(expected_left_span),
            "tail_text": str(left["text"])[int(expected_left_span["start"]) :],
        }
        expected_right = {
            "evidence_id": right_id,
            "page_number": int(right["page_number"]),
            "text": str(right["text"]),
            "head_text": str(right["text"])[: int(expected_right_range["head_end"])],
        }
        if left_input != expected_left or right_input != expected_right:
            raise ValueError("跨页审计准备输入与真实目录正文不一致")
        if prepared.get("plan_modules") != _compact_plan(plan):
            raise ValueError("跨页审计准备输入与当前规划不一致")
        if prepared.get("structure") != continuation:
            raise ValueError("跨页审计版式结构与真实目录不一致")
        record = records_by_index.get(item_index)
        if record is None:
            raise ValueError(f"跨页审计缺少结果: item_index={item_index}")
        if record.get("input_hash") != _stable_payload_hash(prepared):
            raise ValueError(f"跨页审计输入指纹不一致: item_index={item_index}")
        output = _required_object(record.get("output"), "continuity.output")
        if set(output) != {
            "previous_evidence_id",
            "evidence_id",
            "relation",
            "governing_scopes",
            "spans",
            "reason",
        }:
            raise ValueError("跨页审计输出字段不完整或包含额外字段")
        if (
            output.get("previous_evidence_id") != left_id
            or output.get("evidence_id") != right_id
        ):
            raise ValueError("跨页审计输出了错误证据 ID")
        relation = output.get("relation")
        if relation not in {
            "independent",
            "inherits_entire_item",
            "inherits_leading_span",
            "uncertain",
        }:
            raise ValueError(f"跨页审计 relation 无效: {relation}")
        spans = _normalize_spans(
            output.get("spans"),
            text_length=len(str(right["text"])),
        )
        raw_scopes = output.get("governing_scopes")
        if not isinstance(raw_scopes, list) or len(raw_scopes) > 1:
            raise ValueError("governing_scopes 当前只允许零或一个治理范围")
        governing_scopes: list[dict[str, Any]] = []
        for raw_scope in raw_scopes:
            scope = _required_object(raw_scope, "governing_scopes[]")
            if set(scope) != {"span", "module_indexes"}:
                raise ValueError("governing_scopes 只允许 span 和 module_indexes")
            scope_spans = _normalize_spans(
                [scope.get("span")],
                text_length=len(str(left["text"])),
            )
            scope_indexes = scope.get("module_indexes")
            if (
                not isinstance(scope_indexes, list)
                or not scope_indexes
                or scope_indexes != sorted(set(scope_indexes))
                or any(
                    isinstance(index, bool)
                    or not isinstance(index, int)
                    or index < 0
                    or index >= module_count
                    for index in scope_indexes
                )
            ):
                raise ValueError("governing_scopes.module_indexes 必须是非空有序有效下标")
            governing_scopes.append(
                {"span": scope_spans[0], "module_indexes": list(scope_indexes)}
            )
        left_tail_span = prepared["structure"]["left_tail_span"]
        minimum_span = prepared["structure"]["minimum_governing_span"]
        for scope in governing_scopes:
            scope_span = scope["span"]
            left_text = str(left["text"])
            if (
                scope_span["start"] < int(left_tail_span["start"])
                or scope_span["end"] > int(left_tail_span["end"])
            ):
                raise ValueError("governing_scopes.span 必须位于 left_tail_span 全局坐标内")
            if (
                scope_span["start"] > int(minimum_span["start"])
                or scope_span["end"] < int(minimum_span["end"])
            ):
                raise ValueError("governing_scopes.span 必须完整覆盖 minimum_governing_span")
            if (
                scope_span["start"] > 0
                and left_text[scope_span["start"] - 1] != "\n"
            ) or (
                scope_span["end"] < len(left_text)
                and left_text[scope_span["end"]] != "\n"
            ):
                raise ValueError("governing_scopes.span 必须对齐左证据完整行边界")
        scoped_indexes: list[int] = (
            governing_scopes[0]["module_indexes"] if governing_scopes else []
        )
        left_indexes = set(accounting[left_id]["module_indexes"])
        if not set(scoped_indexes).issubset(left_indexes):
            raise ValueError(
                "governing_scopes.module_indexes 必须是左项已记账模块的子集"
            )
        reason = str(output.get("reason") or "").strip()
        if not reason or len(reason) > 160:
            raise ValueError("跨页审计 reason 必须是 1 至 160 字的字符串")

        if relation == "independent":
            if governing_scopes or spans:
                raise ValueError("independent 不允许模块下标或继承范围")
            continue
        if relation == "inherits_entire_item":
            if spans != [{"start": 0, "end": len(str(right["text"]))}]:
                raise ValueError("inherits_entire_item 必须精确覆盖右项全文")
            if not governing_scopes:
                raise ValueError("inherits_entire_item 必须提供段落级 governing_scopes")
            accounting[right_id] = {
                "evidence_id": right_id,
                "module_indexes": list(scoped_indexes),
                "disposition": "assigned",
                "reason": reason,
            }
            inherited_count += 1
            continue
        if relation == "inherits_leading_span":
            if (
                not governing_scopes
                or not spans
                or spans[0]["start"] != 0
                or spans[-1]["end"] >= len(str(right["text"]))
            ):
                raise ValueError("inherits_leading_span 的范围必须是右项的非全文前缀")
            raise ValueError(
                "跨页审计发现右证据只有前缀继承，必须先在源头重新分块"
            )
        if governing_scopes or spans:
            raise ValueError("uncertain 不允许声明确定模块或范围")
        raise ValueError("跨页审计结果不确定，禁止进入规划合并")

    context.artifacts["continuity_audit_merge"] = {
        "link_count": len(prepared_items),
        "inherited_count": inherited_count,
    }
    return {"evidence_accounting": list(accounting.values())}
