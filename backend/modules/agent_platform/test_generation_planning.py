"""测试生成的需求规划、分批路由与确定性合并逻辑。"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import re
import unicodedata
from typing import Any, TYPE_CHECKING

from core.db.model_defs import KnowledgeDocument
from modules.knowledge_base_components.document.document_asset_service import (
    load_document_manifest,
)
from .sources import SOURCE_ARTIFACT_KEY, SourceSnapshot, assert_same_source
from .output_repair import OutputRepairError, repairable_output
from .test_generation_repair import PLANNING_REPAIR_STRATEGY
from .test_generation_batching import build_planning_evidence_catalog
from .context_compression import (
    compress_evidence_catalog,
    context_compression_enabled,
    context_compression_max_tokens,
)
from .test_generation_schemas import (
    BUSINESS_PLANNING_BATCH_MAX_FACTS,
    BUSINESS_PLANNING_BATCH_MAX_JSON_CHARS,
    PLANNING_SCOPE_ROUTE_BATCH_SIZE,
    PLANNING_SCOPE_ROUTE_MAX_MODEL_INPUT_CHARS,
)

if TYPE_CHECKING:
    from .registry import ToolExecutionContext

def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name}不能为空")
    return text


def _required_source_text(value: Any, field_name: str) -> str:
    """校验事实源非空，并清理模型不可见控制字符与 OCR 兼容字符。"""

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+", " ", text)
    if not text.strip():
        raise ValueError(f"{field_name}不能为空")
    return text


def _identity(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").strip().casefold())


def _content_hash(content: str, stored_hash: Any = None) -> str:
    value = str(stored_hash or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", value):
        return value
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def submit_source_semantics(
    _context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """接收模型提交的来源事实，参数与返回值共享同一严格契约。"""

    return deepcopy(arguments)


def submit_generation_batch(
    _context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """接收当前真实批次生成结果，参数与返回值共享严格用例契约。"""

    return deepcopy(arguments)


def submit_scenario_design_guidance(
    _context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """接收场景拆分建议，参数与返回值共享严格结构契约。"""

    return deepcopy(arguments)


def submit_business_plan(
    _context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """接收全局业务规划，参数与返回值共享严格规划契约。"""

    return deepcopy(arguments)


def resolve_requirement_evidence(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """解析本次 Run 的唯一需求事实源。"""

    inline_requirement = str(arguments.get("requirement") or "").strip()
    requirement_doc_id = arguments.get("requirement_doc_id")

    source: dict[str, Any]
    if requirement_doc_id is not None:
        document = (
            context.db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.id == int(requirement_doc_id),
                KnowledgeDocument.project_id == context.project_id,
                KnowledgeDocument.doc_type.in_(
                    ("requirement", "product_requirement", "incomplete")
                ),
            )
            .first()
        )
        if document is None:
            raise ValueError("需求文档不存在、无权访问或类型不允许")
        if str(document.parse_status or "") != "success":
            raise ValueError("需求文档尚未解析成功")
        requirement = _required_source_text(document.content, "需求文档正文")
        stored_hash = _content_hash(requirement, document.content_hash)
        try:
            manifest = load_document_manifest(int(document.id))
        except FileNotFoundError as exc:
            raise ValueError("需求文档缺少 schema v3 页面资产，必须重新解析") from exc
        if int(manifest.get("schema_version") or 0) != 3:
            raise ValueError("需求文档页面资产不是 schema v3，必须重新解析")
        manifest_hash = str(manifest.get("source_sha256") or "").strip().lower()
        if manifest_hash != stored_hash:
            raise ValueError("文档资产与知识库记录指纹不一致，请重新准备该文档")
        source = {
            "kind": "knowledge_document",
            "document_id": int(document.id),
            "filename": str(document.filename or ""),
            "doc_type": str(document.doc_type or "requirement"),
            "content_hash": stored_hash,
            "asset_available": True,
            "page_count": int(manifest.get("page_count") or 0),
        }
    else:
        requirement = _required_text(inline_requirement, "真实需求")
        source = {
            "kind": "inline",
            "document_id": None,
            "filename": "",
            "doc_type": "inline_requirement",
            "content_hash": _content_hash(requirement),
            "asset_available": False,
            "page_count": 0,
        }

    saved_source = context.artifacts.get(SOURCE_ARTIFACT_KEY)
    actual_source = SourceSnapshot.from_dict(source)
    if saved_source is not None:
        assert_same_source(SourceSnapshot.from_dict(saved_source), actual_source)
    context.artifacts[SOURCE_ARTIFACT_KEY] = actual_source.to_dict()
    evidence_catalog = build_planning_evidence_catalog(
        source=source,
        requirement=requirement,
    )
    # 中文注释：保留完整 evidence_catalog 作为审计和锚点事实源，压缩只生成
    # 供 source semantics 使用的 evidence ID 视图，避免把摘要或裁剪文本写成来源。
    run_input = getattr(context, "run_input", {})
    if not isinstance(run_input, dict):
        run_input = {}
    compression_enabled = context_compression_enabled(run_input)
    compression_max_tokens = context_compression_max_tokens(run_input)
    compressed_catalog, compression_stats = compress_evidence_catalog(
        evidence_catalog,
        enabled=compression_enabled,
        max_tokens=compression_max_tokens,
    )
    context.artifacts["context_compression"] = {
        **compression_stats,
        "source_kind": str(source.get("kind") or ""),
        "document_id": source.get("document_id"),
        "selected_evidence_ids": [
            str(item.get("evidence_id") or "")
            for item in (compressed_catalog.get("items") or [])
        ],
        "candidate_selected_evidence_ids": [
            str(item.get("evidence_id") or "")
            for item in (compressed_catalog.get("candidate_items") or [])
        ],
        "candidate_catalog_chars": sum(
            len(str(item.get("text") or ""))
            for item in (compressed_catalog.get("candidate_items") or [])
        ),
    }
    context.artifacts["requirement_evidence"] = {
        "source": source,
        "evidence_catalog": evidence_catalog,
    }
    return {
        "requirement": requirement,
        "source": source,
        "evidence_catalog": evidence_catalog,
    }


def _summary_item_text(value: Any) -> str:
    """把摘要字段中的文本或结构化风险统一为可读且稳定的文本。"""

    if not isinstance(value, dict):
        return str(value or "").strip()
    description = str(value.get("description") or "").strip()
    if not description:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    prefix = ""
    risk_id = str(value.get("risk_id") or "").strip()
    severity = str(value.get("severity") or "").strip()
    if risk_id:
        prefix = risk_id
    if severity:
        prefix = f"{prefix}（级别：{severity}）" if prefix else f"级别：{severity}"
    related_fact_ids = [
        str(fact_id).strip()
        for fact_id in list(value.get("related_fact_ids") or [])
        if str(fact_id).strip()
    ]
    suffix = f"（关联事实：{'、'.join(related_fact_ids)}）" if related_fact_ids else ""
    body = f"{prefix}：{description}" if prefix else description
    return f"{body}{suffix}"


def validate_business_plan_output(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """校验业务模块、测试点和测试方法的唯一性及完整性。"""

    output = arguments.get("output")
    if not isinstance(output, dict) or not isinstance(output.get("business_modules"), list):
        raise ValueError("业务规划缺少 business_modules")
    input_payload = dict(arguments.get("input_payload") or {})
    partial_plans = list(input_payload.get("partial_plans") or [])
    if not partial_plans:
        raise ValueError("业务规划缺少已校验的分批规划草案")
    normalized = deepcopy(output)
    raw_group_catalog = input_payload.get("coverage_group_catalog")
    if raw_group_catalog is not None:
        group_catalog = list(raw_group_catalog or [])
        coverage_items_by_group_id: dict[str, list[str]] = {}
        for raw_group in group_catalog:
            group = dict(raw_group or {})
            group_id = _required_text(
                group.get("coverage_group_id"),
                "coverage_group_id",
            )
            if group_id in coverage_items_by_group_id:
                raise ValueError(f"覆盖语义组目录包含重复ID: {group_id}")
            coverage_items = [
                _required_text(value, "coverage_group.coverage_item")
                for value in list(group.get("coverage_items") or [])
            ]
            if not coverage_items:
                raise ValueError(f"覆盖语义组缺少原子覆盖项: {group_id}")
            coverage_items_by_group_id[group_id] = coverage_items
        if not coverage_items_by_group_id:
            raise ValueError("业务规划缺少覆盖语义组目录")
        used_group_ids: list[str] = []
        for module in list(normalized.get("business_modules") or []):
            for point in list(dict(module).get("test_points") or []):
                for design in list(dict(point).get("test_designs") or []):
                    coverage_group_ids = [
                        _required_text(value, "coverage_group_id")
                        for value in list(dict(design).get("coverage_items") or [])
                    ]
                    unknown_group_ids = set(coverage_group_ids) - set(
                        coverage_items_by_group_id
                    )
                    if unknown_group_ids:
                        raise ValueError(
                            "业务规划引用未知覆盖语义组: "
                            f"{sorted(unknown_group_ids)}"
                        )
                    used_group_ids.extend(coverage_group_ids)
                    design["coverage_items"] = [
                        coverage_item
                        for group_id in coverage_group_ids
                        for coverage_item in coverage_items_by_group_id[group_id]
                    ]
        duplicate_group_ids = sorted(
            group_id
            for group_id, count in Counter(used_group_ids).items()
            if count > 1
        )
        missing_group_ids = sorted(
            set(coverage_items_by_group_id) - set(used_group_ids)
        )
        if duplicate_group_ids or missing_group_ids:
            raise ValueError(
                "业务规划必须且只能承接一次全部覆盖语义组: "
                f"missing={missing_group_ids[:20]}, duplicate={duplicate_group_ids[:20]}"
            )
    planning_metadata = dict(input_payload.get("planning_metadata") or {})
    for field in ("coverage_focus", "risks"):
        values: list[str] = []
        metadata_values = planning_metadata.get(field)
        if metadata_values is not None:
            candidates = (
                [metadata_values]
                if isinstance(metadata_values, str)
                else list(metadata_values or [])
            )
            for candidate in candidates:
                value = _summary_item_text(candidate)
                if value and value not in values:
                    values.append(value)
        else:
            for raw_partial in partial_plans:
                draft = dict(dict(raw_partial or {}).get("draft") or {})
                raw_value = draft.get(field)
                candidates = [raw_value] if isinstance(raw_value, str) else list(raw_value or [])
                for candidate in candidates:
                    value = _summary_item_text(candidate)
                    if value and value not in values:
                        values.append(value)
        if not values and field == "coverage_focus":
            raise ValueError(f"分批规划草案缺少可编译字段: {field}")
        normalized[field] = values
    planning_limits = dict(input_payload.get("planning_limits") or {})
    required_limit_keys = {
        "max_business_modules",
        "max_test_points",
        "max_test_designs",
        "max_coverage_items",
    }
    if set(planning_limits) != required_limit_keys or any(
        not isinstance(planning_limits[key], int) or planning_limits[key] < 1
        for key in required_limit_keys
    ):
        raise ValueError("业务规划缺少平台计算的 planning_limits")
    module_names: set[str] = set()
    test_point_count = 0
    test_design_count = 0
    design_item_count = 0
    for module_index, raw_module in enumerate(normalized["business_modules"]):
        if not isinstance(raw_module, dict):
            raise ValueError(f"业务规划模块必须是对象: index={module_index}")
        module = dict(raw_module)
        module_name = _required_text(module.get("name"), "business_module.name")
        if module_name in module_names:
            raise ValueError(f"业务规划包含重复模块名称: {module_name}")
        module_names.add(module_name)
        test_points = list(module.get("test_points") or [])
        point_names: set[str] = set()
        for point_index, raw_point in enumerate(test_points):
            test_point_count += 1
            if not isinstance(raw_point, dict):
                raise ValueError(
                    f"业务规划测试点必须是对象: module={module_name}, index={point_index}"
                )
            point = dict(raw_point)
            point_name = _required_text(point.get("name"), "test_point.name")
            if point_name in point_names:
                raise ValueError(f"业务模块包含重复测试点: module={module_name}, point={point_name}")
            point_names.add(point_name)
            for design_index, raw_design in enumerate(list(point.get("test_designs") or [])):
                test_design_count += 1
                if not isinstance(raw_design, dict):
                    raise ValueError(
                        "测试点的 test_designs 每项必须是对象: "
                        f"module={module_name}, point={point_name}, index={design_index}"
                    )
                design = dict(raw_design)
                _required_text(design.get("technique"), "test_design.technique")
                _required_text(design.get("rationale"), "test_design.rationale")
                for coverage_intent in list(design.get("coverage_items") or []):
                    _required_text(coverage_intent, "test_design.coverage_item")
                    design_item_count += 1
    if design_item_count < 1:
        raise ValueError("业务规划没有形成任何测试设计覆盖项")
    actual_counts = {
        "max_business_modules": len(normalized["business_modules"]),
        "max_test_points": test_point_count,
        "max_test_designs": test_design_count,
        "max_coverage_items": design_item_count,
    }
    exceeded = [
        f"{key}={actual_counts[key]}/{planning_limits[key]}"
        for key in required_limit_keys
        if actual_counts[key] > planning_limits[key]
    ]
    if exceeded:
        raise ValueError(f"业务规划超过动态容量: {', '.join(sorted(exceeded))}")
    return normalized


def _serialized_json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _business_planning_limits(
    *,
    module_candidate_count: int,
    coverage_topic_count: int,
    covered_fact_count: int,
) -> dict[str, int]:
    """按已校验草案和事实目录计算规划容量，不假设覆盖项与用例一一对应。"""

    module_capacity = max(1, int(module_candidate_count))
    topic_capacity = max(module_capacity, int(coverage_topic_count))
    fact_capacity = max(topic_capacity, int(covered_fact_count))
    return {
        "max_business_modules": module_capacity,
        "max_test_points": topic_capacity,
        "max_test_designs": topic_capacity,
        "max_coverage_items": fact_capacity,
    }


def _planning_scope_fragments(planning_scopes: list[Any]) -> list[dict[str, Any]]:
    """按事实数和真实 JSON 体积拆分来源范围，保留事实原始顺序。"""

    fragments: list[dict[str, Any]] = []
    seen_scope_ids: set[str] = set()
    seen_fact_ids: set[str] = set()
    for raw_scope in planning_scopes:
        if not isinstance(raw_scope, dict):
            raise ValueError("业务规划输入的 planning_scopes 每项必须是对象")
        scope = dict(raw_scope)
        scope_id = _required_text(scope.get("scope_id"), "planning_scope.scope_id")
        if scope_id in seen_scope_ids:
            raise ValueError(f"业务规划输入包含重复 scope_id: {scope_id}")
        seen_scope_ids.add(scope_id)
        facts = list(scope.get("facts") or [])
        if not facts:
            raise ValueError(f"业务规划输入包含空事实范围: scope_id={scope_id}")
        fragment_facts: list[dict[str, Any]] = []
        for raw_fact in facts:
            if not isinstance(raw_fact, dict):
                raise ValueError(f"业务规划事实必须是对象: scope_id={scope_id}")
            fact = deepcopy(raw_fact)
            fact_id = _required_text(fact.get("fact_id"), "planning_scope.fact_id")
            if fact_id in seen_fact_ids:
                raise ValueError(f"业务规划输入包含重复 fact_id: {fact_id}")
            seen_fact_ids.add(fact_id)
            candidate = {
                "scope_id": scope_id,
                "facts": [*fragment_facts, fact],
            }
            if fragment_facts and (
                len(fragment_facts) >= BUSINESS_PLANNING_BATCH_MAX_FACTS
                or _serialized_json_chars(candidate) > BUSINESS_PLANNING_BATCH_MAX_JSON_CHARS
            ):
                fragments.append({"scope_id": scope_id, "facts": fragment_facts})
                fragment_facts = []
            fragment_facts.append(fact)
        if fragment_facts:
            fragments.append({"scope_id": scope_id, "facts": fragment_facts})
    if not fragments:
        raise ValueError("业务规划缺少有效 planning_scopes")
    return fragments


def _planning_fact_model_view(fact: dict[str, Any]) -> dict[str, Any]:
    """生成规划/路由模型真正需要的事实投影。

    来源锚点、治理关系和状态字段仍由 source_semantics 及审计链路保留；
    规划模型只需要稳定 ID 与可读断言，重复传输其余字段会放大长请求。
    """

    fact_id = _required_text(fact.get("fact_id"), "planning_scope.fact_id")
    assertion = str(fact.get("assertion") or "").strip()
    return {"fact_id": fact_id, "assertion": assertion}


def prepare_business_plan_batches(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """把完整事实目录切成有界规划批次，避免单次模型请求跨越网关时限。"""

    planning_scopes = list(arguments.get("planning_scopes") or [])
    case_budget = int(arguments.get("case_budget") or 0)
    if case_budget < 1:
        raise ValueError("业务规划缺少有效 case_budget")
    if not planning_scopes or not all(isinstance(scope, dict) for scope in planning_scopes):
        raise ValueError("业务规划输入的 planning_scopes 每项必须是对象")
    raw_payload_chars = _serialized_json_chars({"planning_scopes": planning_scopes})
    model_scopes = [
        {
            "scope_id": _required_text(scope.get("scope_id"), "planning_scope.scope_id"),
            "facts": [
                _planning_fact_model_view(dict(fact))
                for fact in list(scope.get("facts") or [])
            ],
        }
        for scope in planning_scopes
    ]
    model_payload_chars = _serialized_json_chars({"planning_scopes": model_scopes})
    fragments = _planning_scope_fragments(model_scopes)
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_fact_count = 0
    for fragment in fragments:
        fragment_fact_count = len(list(fragment.get("facts") or []))
        candidate = [*current, fragment]
        if current and (
            current_fact_count + fragment_fact_count > BUSINESS_PLANNING_BATCH_MAX_FACTS
            or _serialized_json_chars({"planning_scopes": candidate})
            > BUSINESS_PLANNING_BATCH_MAX_JSON_CHARS
        ):
            batches.append(current)
            current = []
            current_fact_count = 0
        current.append(fragment)
        current_fact_count += fragment_fact_count
    if current:
        batches.append(current)

    fact_count = sum(
        len(list(fragment.get("facts") or []))
        for batch in batches
        for fragment in batch
    )
    items = [
        {
            "planning_scopes": deepcopy(batch),
            "case_budget": case_budget,
            "planning_batch": {
                "batch_number": index + 1,
                "batch_count": len(batches),
                "scope_fragment_count": len(batch),
                "fact_count": sum(len(list(item.get("facts") or [])) for item in batch),
            },
        }
        for index, batch in enumerate(batches)
    ]
    context.artifacts["business_planning_batch_plan"] = {
        "batch_count": len(items),
        "scope_count": len(planning_scopes),
        "scope_fragment_count": len(fragments),
        "fact_count": fact_count,
        "max_facts_per_batch": BUSINESS_PLANNING_BATCH_MAX_FACTS,
        "max_json_chars_per_batch": BUSINESS_PLANNING_BATCH_MAX_JSON_CHARS,
        "raw_model_input_chars": raw_payload_chars,
        "projected_model_input_chars": model_payload_chars,
        "model_input_reduction_ratio": round(
            (raw_payload_chars - model_payload_chars) / raw_payload_chars,
            6,
        ) if raw_payload_chars else 0.0,
        "model_fact_fields": ["fact_id", "assertion"],
        "removed_fact_fields": sorted(
            {
                str(key)
                for scope in planning_scopes
                if isinstance(scope, dict)
                for raw_fact in list(scope.get("facts") or [])
                if isinstance(raw_fact, dict)
                for key in raw_fact
                if key not in {"fact_id", "assertion"}
            }
        ),
    }
    return {
        "items": items,
        "batch_count": len(items),
        "scope_count": len(planning_scopes),
        "fact_count": fact_count,
    }


def validate_business_plan_draft_output(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """确保每个规划草案完整承接当前批次的全部真实事实。"""

    del context
    input_payload = dict(arguments.get("input_payload") or {})
    output = arguments.get("output")
    if not isinstance(output, dict):
        raise ValueError("业务规划批次未返回对象")
    expected_fact_ids = [
        _required_text(dict(fact).get("fact_id"), "planning_scope.fact_id")
        for raw_scope in list(input_payload.get("planning_scopes") or [])
        for fact in list(dict(raw_scope).get("facts") or [])
    ]
    if not expected_fact_ids or len(expected_fact_ids) != len(set(expected_fact_ids)):
        raise ValueError("业务规划批次事实为空或 fact_id 重复")
    candidates = output.get("module_candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("业务规划批次缺少 module_candidates")
    candidate_names: set[str] = set()
    routed_fact_ids: list[str] = []
    for raw_candidate in candidates:
        if not isinstance(raw_candidate, dict):
            raise ValueError("业务规划批次 module_candidates 每项必须是对象")
        candidate = dict(raw_candidate)
        name = _required_text(candidate.get("name"), "module_candidate.name")
        if name in candidate_names:
            raise ValueError(f"业务规划批次包含重复模块候选: {name}")
        candidate_names.add(name)
        fact_ids = list(candidate.get("fact_ids") or [])
        if not fact_ids or len(fact_ids) != len(set(fact_ids)):
            raise ValueError(f"模块候选 fact_ids 为空或重复: module={name}")
        routed_fact_ids.extend(str(value) for value in fact_ids)
    expected_set = set(expected_fact_ids)
    routed_set = set(routed_fact_ids)
    if routed_set != expected_set:
        missing = sorted(expected_set - routed_set)
        unknown = sorted(routed_set - expected_set)
        raise ValueError(
            "业务规划批次没有完整承接真实事实: "
            f"missing={missing[:20]}, unknown={unknown[:20]}"
        )
    return deepcopy(output)


def prepare_business_plan_consolidation(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """校验批次草案并移除仅用于完整性审计的 fact_id，压缩全局汇总输入。"""

    prepared_items = list(arguments.get("prepared_items") or [])
    plan_records = list(arguments.get("plan_records") or [])
    case_budget = int(arguments.get("case_budget") or 0)
    if case_budget < 1:
        raise ValueError("业务规划汇总缺少有效 case_budget")
    if not prepared_items or len(prepared_items) != len(plan_records):
        raise ValueError("业务规划批次输入与结果数量不一致")
    partial_plans: list[dict[str, Any]] = []
    covered_fact_ids: set[str] = set()
    module_candidate_count = 0
    coverage_group_catalog: list[dict[str, Any]] = []
    assigned_topic_objectives: set[str] = set()
    coverage_topic_count = 0
    planning_metadata = {"coverage_focus": [], "risks": []}
    for index, (raw_prepared, raw_record) in enumerate(
        zip(prepared_items, plan_records, strict=True)
    ):
        prepared = dict(raw_prepared or {})
        record = dict(raw_record or {})
        if int(record.get("item_index", index)) != index:
            raise ValueError(f"业务规划批次记录顺序不一致: index={index}")
        output = validate_business_plan_draft_output(
            context,
            {"input_payload": prepared, "output": dict(record.get("output") or {})},
        )
        compact = deepcopy(output)
        for field in planning_metadata:
            raw_values = compact.pop(field, [])
            candidates = [raw_values] if isinstance(raw_values, str) else list(raw_values or [])
            for candidate in candidates:
                value = _summary_item_text(candidate)
                if value and value not in planning_metadata[field]:
                    planning_metadata[field].append(value)
        compact.pop("batch_summary", None)
        compact_candidates: list[dict[str, Any]] = []
        for candidate in list(compact.get("module_candidates") or []):
            coverage_items: list[str] = []
            for raw_topic in list(candidate.get("coverage_topics") or []):
                topic = dict(raw_topic or {})
                _required_text(topic.get("name"), "coverage_topic.name")
                objective = _required_text(
                    topic.get("objective"),
                    "coverage_topic.objective",
                )
                # 同一原子意图只保留第一次出现的候选归属。
                if objective not in assigned_topic_objectives:
                    coverage_items.append(objective)
                    assigned_topic_objectives.add(objective)
            covered_fact_ids.update(str(value) for value in list(candidate.get("fact_ids") or []))
            candidate.pop("fact_ids", None)
            if coverage_items:
                group_id = f"CG-{len(coverage_group_catalog) + 1:04d}"
                coverage_group_catalog.append(
                    {
                        "coverage_group_id": group_id,
                        "name": _required_text(candidate.get("name"), "module_candidate.name"),
                        "objective": _required_text(
                            candidate.get("objective"),
                            "module_candidate.objective",
                        ),
                        "coverage_items": coverage_items,
                    }
                )
                candidate["coverage_topics"] = [group_id]
                compact_candidates.append(candidate)
                module_candidate_count += 1
                coverage_topic_count += len(coverage_items)
        compact["module_candidates"] = compact_candidates
        partial_plans.append(
            {
                "batch_number": index + 1,
                "batch_count": len(plan_records),
                "draft": compact,
            }
        )
    context.artifacts["business_planning_batch_results"] = {
        "batch_count": len(partial_plans),
        "covered_fact_count": len(covered_fact_ids),
    }
    planning_limits = _business_planning_limits(
        module_candidate_count=module_candidate_count,
        coverage_topic_count=coverage_topic_count,
        covered_fact_count=len(covered_fact_ids),
    )
    return {
        "partial_plans": partial_plans,
        "planning_metadata": planning_metadata,
        "coverage_group_catalog": coverage_group_catalog,
        "batch_count": len(partial_plans),
        "covered_fact_count": len(covered_fact_ids),
        "case_budget": case_budget,
        "planning_limits": planning_limits,
    }


def _planning_module_design_catalog(module: dict[str, Any]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for raw_point in list(module.get("test_points") or []):
        point = dict(raw_point or {})
        for raw_design in list(point.get("test_designs") or []):
            design = dict(raw_design or {})
            for coverage_intent in list(design.get("coverage_items") or []):
                catalog.append(
                    {
                        "test_design_item_index": len(catalog),
                        "test_point": str(point.get("name") or ""),
                        "technique": str(design.get("technique") or ""),
                        "coverage_intent": str(coverage_intent or ""),
                    }
                )
    if not catalog:
        raise ValueError(f"业务模块缺少测试设计项: module={module.get('name')}")
    return catalog


def prepare_planning_scope_routes(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """把每个有效 scope 变成模块与测试设计联合路由任务。"""

    plan = dict(arguments.get("plan") or {})
    modules = list(plan.get("business_modules") or [])
    scopes = list(arguments.get("planning_scopes") or [])
    if not modules or not all(isinstance(module, dict) for module in modules):
        raise ValueError("规划路由缺少有效 business_modules")
    if not scopes or not all(isinstance(scope, dict) for scope in scopes):
        raise ValueError("规划路由缺少有效 planning_scopes")
    artifacts = getattr(context, "artifacts", None)
    if not isinstance(artifacts, dict):
        artifacts = {}
        try:
            context.artifacts = artifacts
        except AttributeError:
            pass
    module_catalog = [
        {
            "module_index": index,
            "name": str(module.get("name") or ""),
            "objective": str(module.get("objective") or ""),
            "test_design_items": _planning_module_design_catalog(module),
        }
        for index, module in enumerate(modules)
    ]
    items = [
        {
            "scope_id": str(scope.get("scope_id") or ""),
            "facts": deepcopy(list(scope.get("facts") or [])),
            "business_modules": deepcopy(module_catalog),
        }
        for scope in scopes
    ]
    if any(not item["scope_id"] or not item["facts"] for item in items):
        raise ValueError("planning_scopes 包含空 scope_id 或空事实集")
    if len({item["scope_id"] for item in items}) != len(items):
        raise ValueError("planning_scopes 包含重复 scope_id")
    model_items = [
        {
            "scope_id": item["scope_id"],
            "facts": [
                {
                    **_planning_fact_model_view(dict(fact)),
                    "fact_ref": f"RF-{fact_index + 1:03d}",
                }
                for fact_index, fact in enumerate(item["facts"])
            ],
        }
        for item in items
    ]

    batch_indexes: list[list[int]] = []
    current_indexes: list[int] = []
    for item_index, model_item in enumerate(model_items):
        candidate_indexes = [*current_indexes, item_index]
        candidate_chars = _serialized_json_chars(
            {
                "scopes": [model_items[index] for index in candidate_indexes],
                "business_modules": module_catalog,
            }
        )
        if current_indexes and (
            len(candidate_indexes) > PLANNING_SCOPE_ROUTE_BATCH_SIZE
            or candidate_chars > PLANNING_SCOPE_ROUTE_MAX_MODEL_INPUT_CHARS
        ):
            batch_indexes.append(current_indexes)
            current_indexes = [item_index]
            continue
        current_indexes = candidate_indexes
    if current_indexes:
        batch_indexes.append(current_indexes)

    raw_batch_payload_chars = sum(
        _serialized_json_chars(
            {
                "scopes": [items[index] for index in indexes],
                "business_modules": module_catalog,
            }
        )
        for indexes in batch_indexes
    )
    model_batch_payload_chars = sum(
        _serialized_json_chars(
            {
                "scopes": [model_items[index] for index in indexes],
                "business_modules": module_catalog,
            }
        )
        for indexes in batch_indexes
    )
    batch_items = [
        {
            "scopes": [
                {
                    "scope_id": model_items[index]["scope_id"],
                    "facts": deepcopy(model_items[index]["facts"]),
                }
                for index in indexes
            ],
            "business_modules": deepcopy(module_catalog),
        }
        for indexes in batch_indexes
    ]
    artifacts["planning_scope_route_plan"] = {
        "scope_count": len(items),
        "batch_count": len(batch_items),
        "module_count": len(modules),
        "max_model_input_chars": PLANNING_SCOPE_ROUTE_MAX_MODEL_INPUT_CHARS,
        "max_scopes_per_batch": PLANNING_SCOPE_ROUTE_BATCH_SIZE,
        "oversized_single_scope_count": sum(
            len(indexes) == 1
            and _serialized_json_chars(
                {
                    "scopes": [model_items[indexes[0]]],
                    "business_modules": module_catalog,
                }
            ) > PLANNING_SCOPE_ROUTE_MAX_MODEL_INPUT_CHARS
            for indexes in batch_indexes
        ),
        "raw_batch_model_input_chars": raw_batch_payload_chars,
        "projected_batch_model_input_chars": model_batch_payload_chars,
        "model_input_reduction_ratio": round(
            (raw_batch_payload_chars - model_batch_payload_chars) / raw_batch_payload_chars,
            6,
        ) if raw_batch_payload_chars else 0.0,
        "removed_module_fields": ["test_points"],
        "model_fact_fields": ["fact_ref", "fact_id", "assertion"],
    }
    return {
        "items": items,
        "batch_items": batch_items,
        "scope_count": len(items),
        "batch_count": len(batch_items),
        "module_count": len(modules),
    }


def _normalize_planning_scope_route(
    *,
    prepared: dict[str, Any],
    raw_output: dict[str, Any],
) -> dict[str, Any]:
    """将当前范围的校验差异绑定到真实 scope，供修复策略准确定位。"""

    try:
        return _validate_planning_scope_route(prepared=prepared, raw_output=raw_output)
    except ValueError as exc:
        raise OutputRepairError(
            str(exc),
            strategy_key=PLANNING_REPAIR_STRATEGY,
            details={"scope_ids": [str(prepared.get("scope_id") or "")]},
        ) from exc


def _validate_planning_scope_route(
    *,
    prepared: dict[str, Any],
    raw_output: dict[str, Any],
) -> dict[str, Any]:
    """依据单项真实输入规范化路由，并完成可在当前项修复的确定性校验。"""

    output = deepcopy(raw_output)
    expected_scope_id = str(prepared.get("scope_id") or "")
    if str(output.get("scope_id") or "") != expected_scope_id:
        raise ValueError(f"规划路由结果篡改 scope_id: scope_id={expected_scope_id}")
    module_catalog = prepared.get("business_modules")
    if not isinstance(module_catalog, list) or not module_catalog:
        raise ValueError(f"规划路由输入缺少模块目录: scope_id={expected_scope_id}")
    module_indexes = [
        module.get("module_index") if isinstance(module, dict) else None
        for module in module_catalog
    ]
    if module_indexes != list(range(len(module_catalog))):
        raise ValueError(f"规划路由输入模块目录下标不连续: scope_id={expected_scope_id}")
    prepared_facts = [dict(fact) for fact in list(prepared.get("facts") or [])]
    prepared_fact_ids = [
        str(fact.get("fact_id") or "").strip() for fact in prepared_facts
    ]
    if not all(prepared_fact_ids) or len(prepared_fact_ids) != len(set(prepared_fact_ids)):
        raise ValueError(f"规划路由输入 fact_id 为空或重复: scope_id={expected_scope_id}")
    prepared_fact_refs = [
        str(fact.get("fact_ref") or "").strip() for fact in prepared_facts
    ]
    uses_fact_refs = all(prepared_fact_refs)
    if uses_fact_refs and len(prepared_fact_refs) != len(set(prepared_fact_refs)):
        raise ValueError(f"规划路由输入 fact_ref 重复: scope_id={expected_scope_id}")
    fact_id_by_ref = dict(zip(prepared_fact_refs, prepared_fact_ids, strict=True))
    assignments = output.get("assignments")
    if not isinstance(assignments, list):
        raise ValueError("规划路由 assignments 必须是数组")
    assignments_by_fact_id: dict[str, dict[str, Any]] = {}
    for assignment_index, raw_assignment in enumerate(assignments):
        if not isinstance(raw_assignment, dict):
            raise ValueError(
                f"规划路由 assignment 必须是对象: index={assignment_index}"
            )
        assignment = dict(raw_assignment)
        fact_ref = str(assignment.get("fact_ref") or "").strip()
        submitted_fact_id = str(assignment.get("fact_id") or "").strip()
        if uses_fact_refs and fact_ref:
            fact_id = fact_id_by_ref.get(fact_ref, "")
        elif uses_fact_refs and submitted_fact_id in prepared_fact_ids:
            # agent_map 后处理结果会在后续合并工具中再次经过本函数；
            # 已恢复且仍属于当前 scope 的真实 fact_id 应保持幂等。
            fact_id = submitted_fact_id
        else:
            fact_id = submitted_fact_id
        if not fact_id or fact_id in assignments_by_fact_id:
            raise ValueError(
                "规划路由必须逐条且仅路由当前 scope 的全部 fact_id: "
                f"scope_id={expected_scope_id}; fact_ref={fact_ref or None}"
            )
        assignment.pop("fact_ref", None)
        assignment["fact_id"] = fact_id
        raw_module_routes = assignment.get("module_routes")
        if not isinstance(raw_module_routes, list) or not raw_module_routes:
            raise ValueError(f"规划路由缺少模块与测试设计映射: fact_id={fact_id}")
        module_routes_by_index: dict[int, dict[str, Any]] = {}
        primary_count = 0
        for raw_module_route in raw_module_routes:
            if not isinstance(raw_module_route, dict):
                raise ValueError(f"规划路由模块映射必须是对象: fact_id={fact_id}")
            module_route = dict(raw_module_route)
            module_index = module_route.get("module_index")
            relation = str(module_route.get("relation") or "")
            design_indexes = module_route.get("test_design_item_indexes")
            if (
                not isinstance(module_index, int)
                or isinstance(module_index, bool)
                or not 0 <= module_index < len(module_catalog)
                or relation not in {"primary", "shared"}
                or not isinstance(design_indexes, list)
                or len(design_indexes) != len(set(design_indexes))
            ):
                raise ValueError(f"规划路由模块或测试设计映射无效: fact_id={fact_id}")
            design_catalog = list(
                dict(module_catalog[module_index]).get("test_design_items") or []
            )
            if any(
                not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index < len(design_catalog)
                for index in design_indexes
            ):
                raise ValueError(
                    f"规划路由测试设计项下标越界: fact_id={fact_id}, module_index={module_index}"
                )
            primary_count += int(relation == "primary")
            existing_route = module_routes_by_index.get(module_index)
            if existing_route is None:
                module_routes_by_index[module_index] = {
                    "module_index": module_index,
                    "relation": relation,
                    "test_design_item_indexes": list(design_indexes),
                }
                continue
            existing_route["relation"] = (
                "primary"
                if "primary" in {existing_route["relation"], relation}
                else "shared"
            )
            existing_route["test_design_item_indexes"] = sorted(
                set(existing_route["test_design_item_indexes"]) | set(design_indexes)
            )
        if primary_count != 1:
            raise ValueError(f"规划路由必须且只能包含一个主模块: fact_id={fact_id}")
        module_routes = list(module_routes_by_index.values())
        assignment["module_routes"] = sorted(
            module_routes,
            key=lambda item: (item["relation"] != "primary", item["module_index"]),
        )
        assignments_by_fact_id[fact_id] = assignment
    missing_fact_ids = sorted(set(prepared_fact_ids) - set(assignments_by_fact_id))
    unknown_fact_ids = sorted(set(assignments_by_fact_id) - set(prepared_fact_ids))
    if missing_fact_ids or unknown_fact_ids:
        raise ValueError(
            "规划路由必须逐条且仅路由当前 scope 的全部 fact_id: "
            f"scope_id={expected_scope_id}; missing={missing_fact_ids}; "
            f"unknown={unknown_fact_ids}"
        )
    output["assignments"] = [
        assignments_by_fact_id[fact_id] for fact_id in prepared_fact_ids
    ]
    return output


def _normalize_planning_scope_route_batch(
    *,
    prepared: dict[str, Any],
    raw_output: dict[str, Any],
) -> dict[str, Any]:
    """按输入顺序拆解批量路由，并复用逐 scope 的严格校验。"""

    scopes = list(prepared.get("scopes") or [])
    module_catalog = list(prepared.get("business_modules") or [])
    routes = raw_output.get("routes")
    if (
        not scopes
        or len(scopes) > PLANNING_SCOPE_ROUTE_BATCH_SIZE
        or not isinstance(routes, list)
        or len(routes) != len(scopes)
    ):
        raise ValueError("规划路由批次必须逐项返回全部 scope")
    routes_by_scope_id: dict[str, dict[str, Any]] = {}
    for route in routes:
        if not isinstance(route, dict):
            raise ValueError("规划路由批次 routes 只能包含对象")
        scope_id = str(route.get("scope_id") or "")
        if not scope_id or scope_id in routes_by_scope_id:
            raise ValueError("规划路由批次包含空或重复 scope_id")
        routes_by_scope_id[scope_id] = route

    normalized_routes: list[dict[str, Any]] = []
    route_errors: list[OutputRepairError] = []
    for raw_scope in scopes:
        scope = dict(raw_scope or {})
        scope_id = str(scope.get("scope_id") or "")
        route = routes_by_scope_id.get(scope_id)
        if route is None:
            raise ValueError(f"规划路由批次遗漏 scope_id: {scope_id}")
        try:
            normalized_routes.append(_normalize_planning_scope_route(
                prepared={
                    "scope_id": scope_id,
                    "facts": deepcopy(list(scope.get("facts") or [])),
                    "business_modules": deepcopy(module_catalog),
                },
                raw_output=route,
            ))
        except OutputRepairError as exc:
            route_errors.append(exc)
    if set(routes_by_scope_id) != {
        str(dict(scope).get("scope_id") or "") for scope in scopes
    }:
        raise ValueError("规划路由批次引用输入外 scope_id")
    if route_errors:
        raise OutputRepairError(
            "；".join(str(error) for error in route_errors),
            strategy_key=PLANNING_REPAIR_STRATEGY,
            details={
                "scope_ids": [scope_id for error in route_errors for scope_id in error.details["scope_ids"]],
            },
        )
    return {"routes": normalized_routes}


@repairable_output(PLANNING_REPAIR_STRATEGY)
def postprocess_planning_scope_routing_item(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """在单个路由实例完成时规范化输出并校验其真实输入边界。"""

    del context
    prepared = dict(arguments.get("item_input") or {})
    raw_output = dict(arguments.get("item_output") or {})
    if "scopes" in prepared:
        return _normalize_planning_scope_route_batch(
            prepared=prepared,
            raw_output=raw_output,
        )
    return _normalize_planning_scope_route(
        prepared=prepared,
        raw_output=raw_output,
    )


def _expand_planning_scope_route_batches(
    *,
    prepared_items: list[Any],
    route_records: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把模型批次恢复为既有的逐 scope 路由数据流。"""

    if not prepared_items or "scopes" not in dict(prepared_items[0] or {}):
        return (
            [dict(item) for item in prepared_items],
            [dict(record) for record in route_records],
        )
    if len(prepared_items) != len(route_records):
        raise ValueError("规划路由批次结果数量不一致")

    expanded_items: list[dict[str, Any]] = []
    expanded_records: list[dict[str, Any]] = []
    for batch_index, (raw_prepared, raw_record) in enumerate(
        zip(prepared_items, route_records, strict=True)
    ):
        prepared = dict(raw_prepared or {})
        record = dict(raw_record or {})
        if int(record.get("item_index", batch_index)) != batch_index:
            raise ValueError(f"规划路由批次记录顺序不一致: index={batch_index}")
        normalized = _normalize_planning_scope_route_batch(
            prepared=prepared,
            raw_output=dict(record.get("output") or {}),
        )
        module_catalog = list(prepared.get("business_modules") or [])
        for raw_scope, route in zip(
            list(prepared.get("scopes") or []),
            normalized["routes"],
            strict=True,
        ):
            scope = dict(raw_scope or {})
            expanded_items.append(
                {
                    "scope_id": str(scope.get("scope_id") or ""),
                    "facts": deepcopy(list(scope.get("facts") or [])),
                    "business_modules": deepcopy(module_catalog),
                }
            )
            expanded_records.append(
                {
                    "item_index": len(expanded_records),
                    "output": deepcopy(route),
                }
            )
    return expanded_items, expanded_records


def _collect_planning_scope_route_state(
    *,
    plan: dict[str, Any],
    prepared_items: list[Any],
    route_records: list[Any],
) -> dict[str, Any]:
    """统一解析初始路由，供缺口审计和最终合并复用。"""

    prepared_items, route_records = _expand_planning_scope_route_batches(
        prepared_items=prepared_items,
        route_records=route_records,
    )
    draft_plan = deepcopy(plan)
    modules = [dict(module) for module in list(draft_plan.get("business_modules") or [])]
    if len(prepared_items) != len(route_records):
        raise ValueError("规划路由结果数量与有效 scope 数量不一致")

    facts_by_scope: dict[str, list[dict[str, Any]]] = {}
    fact_scope_by_id: dict[str, str] = {}
    for item_index, prepared in enumerate(prepared_items):
        if not isinstance(prepared, dict):
            raise ValueError(f"规划路由输入必须是对象: index={item_index}")
        scope_id = str(prepared.get("scope_id") or "").strip()
        facts = list(prepared.get("facts") or [])
        if not scope_id or not facts:
            raise ValueError(f"规划路由输入缺少 scope 或事实: index={item_index}")
        normalized_facts: list[dict[str, Any]] = []
        for raw_fact in facts:
            if not isinstance(raw_fact, dict):
                raise ValueError(f"规划路由事实必须是对象: scope_id={scope_id}")
            fact = deepcopy(raw_fact)
            fact_id = str(fact.get("fact_id") or "").strip()
            if not fact_id or fact_id in fact_scope_by_id:
                raise ValueError(f"规划路由事实 ID 为空或重复: fact_id={fact_id}")
            fact_scope_by_id[fact_id] = scope_id
            normalized_facts.append(fact)
        facts_by_scope[scope_id] = normalized_facts

    evidence_by_module: list[list[str]] = [[] for _ in modules]
    facts_by_module: list[list[str]] = [[] for _ in modules]
    fact_design_routes_by_module: list[list[dict[str, Any]]] = [
        [] for _ in modules
    ]
    for item_index, (prepared, record) in enumerate(zip(prepared_items, route_records)):
        if not isinstance(record, dict):
            raise ValueError(f"规划路由记录必须是对象: index={item_index}")
        if int(record.get("item_index", item_index)) != item_index:
            raise ValueError(f"规划路由记录顺序不一致: index={item_index}")
        output = _normalize_planning_scope_route(
            prepared=dict(prepared),
            raw_output=dict(record.get("output") or {}),
        )
        scope_id = str(output["scope_id"])
        for assignment in output["assignments"]:
            fact_id = str(assignment["fact_id"])
            for module_route in list(assignment["module_routes"]):
                module_index = int(module_route["module_index"])
                if scope_id not in evidence_by_module[module_index]:
                    evidence_by_module[module_index].append(scope_id)
                if fact_id not in facts_by_module[module_index]:
                    facts_by_module[module_index].append(fact_id)
                fact_design_routes_by_module[module_index].append(
                    {
                        "fact_id": fact_id,
                        "test_design_item_indexes": list(
                            module_route["test_design_item_indexes"]
                        ),
                    }
                )

    return {
        "draft_plan": draft_plan,
        "modules": modules,
        "facts_by_scope": facts_by_scope,
        "fact_scope_by_id": fact_scope_by_id,
        "evidence_by_module": evidence_by_module,
        "facts_by_module": facts_by_module,
        "fact_design_routes_by_module": fact_design_routes_by_module,
    }


def _missing_planning_design_indexes(state: dict[str, Any]) -> dict[int, list[int]]:
    modules = list(state["modules"])
    facts_by_module = list(state["facts_by_module"])
    routes_by_module = list(state["fact_design_routes_by_module"])
    missing_by_module: dict[int, list[int]] = {}
    for module_index, module_fact_ids in enumerate(facts_by_module):
        if not module_fact_ids:
            continue
        expected = set(range(len(_planning_module_design_catalog(modules[module_index]))))
        routed = {
            int(design_index)
            for route in routes_by_module[module_index]
            for design_index in list(route["test_design_item_indexes"])
        }
        missing = sorted(expected - routed)
        if missing:
            missing_by_module[module_index] = missing
    return missing_by_module


def prepare_planning_route_repairs(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """把全局路由缺口收敛为按模块复核的最小真实事实集。"""

    del context
    state = _collect_planning_scope_route_state(
        plan=dict(arguments.get("plan") or {}),
        prepared_items=list(arguments.get("prepared_items") or []),
        route_records=list(arguments.get("route_records") or []),
    )
    modules = list(state["modules"])
    facts_by_scope = dict(state["facts_by_scope"])
    evidence_by_module = list(state["evidence_by_module"])
    routes_by_module = list(state["fact_design_routes_by_module"])
    missing_by_module = _missing_planning_design_indexes(state)
    items: list[dict[str, Any]] = []
    for module_index, missing_indexes in missing_by_module.items():
        design_catalog = _planning_module_design_catalog(modules[module_index])
        routed_indexes_by_fact = {
            str(route["fact_id"]): list(route["test_design_item_indexes"])
            for route in routes_by_module[module_index]
        }
        candidate_facts: list[dict[str, Any]] = []
        for scope_id in evidence_by_module[module_index]:
            for fact in facts_by_scope[scope_id]:
                fact_id = str(fact["fact_id"])
                candidate_facts.append(
                    {
                        "scope_id": scope_id,
                        "fact_id": fact_id,
                        "assertion": str(fact.get("assertion") or ""),
                        "current_test_design_item_indexes": routed_indexes_by_fact.get(
                            fact_id, []
                        ),
                    }
                )
        if not candidate_facts:
            raise ValueError(
                "规划路由缺口没有可复核的真实事实: "
                f"module={modules[module_index].get('name')}"
            )
        items.append(
            {
                "module_index": module_index,
                "module_name": str(modules[module_index].get("name") or ""),
                "module_objective": str(modules[module_index].get("objective") or ""),
                "missing_test_design_items": [
                    deepcopy(design_catalog[index]) for index in missing_indexes
                ],
                "candidate_facts": candidate_facts,
            }
        )
    return {
        "items": items,
        "gap_module_count": len(items),
        "gap_design_item_count": sum(len(indexes) for indexes in missing_by_module.values()),
    }


def _normalize_planning_route_repair(
    *,
    prepared: dict[str, Any],
    raw_output: dict[str, Any],
) -> dict[str, Any]:
    output = deepcopy(raw_output)
    expected_module_index = int(prepared.get("module_index", -1))
    if output.get("module_index") != expected_module_index:
        raise ValueError(
            f"规划路由缺口复核篡改 module_index: module_index={expected_module_index}"
        )
    expected_design_indexes = {
        int(item["test_design_item_index"])
        for item in list(prepared.get("missing_test_design_items") or [])
        if isinstance(item, dict)
    }
    candidate_fact_ids = {
        str(fact.get("fact_id") or "")
        for fact in list(prepared.get("candidate_facts") or [])
        if isinstance(fact, dict)
    }
    decisions = output.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise ValueError("规划路由缺口复核 decisions 必须是非空数组")
    normalized_by_index: dict[int, dict[str, Any]] = {}
    for raw_decision in decisions:
        if not isinstance(raw_decision, dict):
            raise ValueError("规划路由缺口复核 decision 必须是对象")
        decision = dict(raw_decision)
        design_index = decision.get("test_design_item_index")
        if (
            not isinstance(design_index, int)
            or isinstance(design_index, bool)
            or design_index not in expected_design_indexes
            or design_index in normalized_by_index
        ):
            raise ValueError("规划路由缺口复核包含未知或重复测试设计项")
        disposition = str(decision.get("disposition") or "")
        fact_ids = decision.get("fact_ids")
        reason = str(decision.get("reason") or "").strip()
        if (
            disposition not in {"supported", "unsupported"}
            or not isinstance(fact_ids, list)
            or len(fact_ids) != len(set(fact_ids))
            or any(str(fact_id) not in candidate_fact_ids for fact_id in fact_ids)
            or not reason
        ):
            raise ValueError(
                f"规划路由缺口复核结论无效: test_design_item_index={design_index}"
            )
        if disposition == "supported" and not fact_ids:
            raise ValueError("规划路由缺口复核支持结论必须引用真实 fact_id")
        if disposition == "unsupported" and fact_ids:
            raise ValueError("规划路由缺口复核不支持结论不能引用 fact_id")
        normalized_by_index[design_index] = {
            "test_design_item_index": design_index,
            "disposition": disposition,
            "fact_ids": [str(fact_id) for fact_id in fact_ids],
            "reason": reason,
        }
    if set(normalized_by_index) != expected_design_indexes:
        raise ValueError("规划路由缺口复核未逐项审查全部缺失测试设计项")
    output["decisions"] = [
        normalized_by_index[index] for index in sorted(normalized_by_index)
    ]
    return output


def postprocess_planning_route_repair_item(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """校验缺口复核只能引用当前输入中的模块、设计项和真实事实。"""

    del context
    return _normalize_planning_route_repair(
        prepared=dict(arguments.get("item_input") or {}),
        raw_output=dict(arguments.get("item_output") or {}),
    )


def _prune_unsupported_planning_design_items(
    *,
    modules: list[dict[str, Any]],
    routes_by_module: list[list[dict[str, Any]]],
    unsupported_by_module: dict[int, set[int]],
) -> int:
    """删除无事实支持的原子覆盖项，并重排同模块内的确定性索引。"""

    removed_count = 0
    for module_index, unsupported_indexes in unsupported_by_module.items():
        module = modules[module_index]
        old_index = 0
        new_index = 0
        index_mapping: dict[int, int] = {}
        retained_points: list[dict[str, Any]] = []
        for raw_point in list(module.get("test_points") or []):
            point = deepcopy(dict(raw_point or {}))
            retained_designs: list[dict[str, Any]] = []
            for raw_design in list(point.get("test_designs") or []):
                design = deepcopy(dict(raw_design or {}))
                retained_coverage: list[Any] = []
                for coverage_intent in list(design.get("coverage_items") or []):
                    if old_index in unsupported_indexes:
                        removed_count += 1
                    else:
                        index_mapping[old_index] = new_index
                        new_index += 1
                        retained_coverage.append(coverage_intent)
                    old_index += 1
                if retained_coverage:
                    design["coverage_items"] = retained_coverage
                    retained_designs.append(design)
            if retained_designs:
                point["test_designs"] = retained_designs
                retained_points.append(point)
        if not retained_points:
            raise ValueError(
                "业务模块的全部测试设计项均缺少真实事实支持: "
                f"module={module.get('name')}"
            )
        module["test_points"] = retained_points
        for route in routes_by_module[module_index]:
            route["test_design_item_indexes"] = sorted(
                {
                    index_mapping[index]
                    for index in list(route.get("test_design_item_indexes") or [])
                    if index in index_mapping
                }
            )
    return removed_count


def merge_planning_scope_routes(
    context: ToolExecutionContext,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """严格合并逐事实路由，并为业务模块写入确定性的事实与证据 ID。"""

    prepared_items = list(arguments.get("prepared_items") or [])
    route_records = list(arguments.get("route_records") or [])
    state = _collect_planning_scope_route_state(
        plan=dict(arguments.get("plan") or {}),
        prepared_items=prepared_items,
        route_records=route_records,
    )
    draft_plan = state["draft_plan"]
    modules = state["modules"]
    fact_scope_by_id = state["fact_scope_by_id"]
    evidence_by_module = state["evidence_by_module"]
    facts_by_module = state["facts_by_module"]
    fact_design_routes_by_module = state["fact_design_routes_by_module"]
    repair_input = prepare_planning_route_repairs(
        context,
        {
            "plan": arguments.get("plan"),
            "prepared_items": prepared_items,
            "route_records": route_records,
        },
    )
    repair_items = list(repair_input["items"])
    if repair_items and "repair_records" not in arguments:
        first_gap = repair_items[0]
        raise ValueError(
            "规划路由没有承接模块的全部测试设计项: "
            f"module={first_gap.get('module_name')}, "
            "missing="
            f"{[item['test_design_item_index'] for item in first_gap['missing_test_design_items']]}"
        )
    repair_records = list(arguments.get("repair_records") or [])
    if len(repair_items) != len(repair_records):
        raise ValueError("规划路由缺口复核结果数量不一致")
    repaired_design_item_count = 0
    unsupported_by_module: dict[int, set[int]] = {}
    unsupported_decisions: list[dict[str, Any]] = []
    for item_index, (prepared, record) in enumerate(zip(repair_items, repair_records)):
        if not isinstance(record, dict) or int(record.get("item_index", item_index)) != item_index:
            raise ValueError(f"规划路由缺口复核记录顺序不一致: index={item_index}")
        output = _normalize_planning_route_repair(
            prepared=prepared,
            raw_output=dict(record.get("output") or {}),
        )
        module_index = int(output["module_index"])
        for decision in output["decisions"]:
            design_index = int(decision["test_design_item_index"])
            if decision["disposition"] == "unsupported":
                unsupported_by_module.setdefault(module_index, set()).add(design_index)
                unsupported_decisions.append(
                    {
                        "module": str(modules[module_index].get("name") or ""),
                        "design_index": design_index,
                        "reason": str(decision["reason"]),
                    }
                )
                continue
            repaired_design_item_count += 1
            for fact_id in decision["fact_ids"]:
                scope_id = str(fact_scope_by_id[fact_id])
                if scope_id not in evidence_by_module[module_index]:
                    evidence_by_module[module_index].append(scope_id)
                if fact_id not in facts_by_module[module_index]:
                    facts_by_module[module_index].append(fact_id)
                route = next(
                    (
                        item for item in fact_design_routes_by_module[module_index]
                        if item["fact_id"] == fact_id
                    ),
                    None,
                )
                if route is None:
                    route = {"fact_id": fact_id, "test_design_item_indexes": []}
                    fact_design_routes_by_module[module_index].append(route)
                if design_index not in route["test_design_item_indexes"]:
                    route["test_design_item_indexes"].append(design_index)
                    route["test_design_item_indexes"].sort()
    removed_unsupported_design_item_count = _prune_unsupported_planning_design_items(
        modules=modules,
        routes_by_module=fact_design_routes_by_module,
        unsupported_by_module=unsupported_by_module,
    )
    remaining_gaps = _missing_planning_design_indexes(state)
    if remaining_gaps:
        module_index = next(iter(remaining_gaps))
        raise ValueError(
            "规划路由没有承接模块的全部测试设计项: "
            f"module={modules[module_index].get('name')}, "
            f"missing={remaining_gaps[module_index]}"
        )
    active_module_indexes = [
        index for index, module_fact_ids in enumerate(facts_by_module) if module_fact_ids
    ]
    routed_modules = [
        {
            **modules[index],
            "evidence_ids": evidence_by_module[index],
            "fact_ids": facts_by_module[index],
            "fact_design_routes": fact_design_routes_by_module[index],
        }
        for index in active_module_indexes
    ]
    if not routed_modules:
        raise ValueError("规划路由没有形成任何受真实证据支持的业务模块")
    draft_plan["business_modules"] = routed_modules
    assignment_counts = {
        fact_id: sum(fact_id in module_fact_ids for module_fact_ids in facts_by_module)
        for fact_id in {
            fact_id for module_fact_ids in facts_by_module for fact_id in module_fact_ids
        }
    }
    facts_with_design_routes = {
        str(route["fact_id"])
        for routes in fact_design_routes_by_module
        for route in routes
        if route["test_design_item_indexes"]
    }
    unmatched_test_design_fact_ids = [
        fact_id
        for fact_id in fact_scope_by_id
        if fact_id in assignment_counts and fact_id not in facts_with_design_routes
    ]
    context.artifacts["planning_fact_routes"] = {
        "input_module_count": len(modules),
        "routed_module_count": len(routed_modules),
        "fact_count": len(assignment_counts),
        "fact_assignment_count": sum(assignment_counts.values()),
        "shared_fact_count": sum(count > 1 for count in assignment_counts.values()),
        "max_fact_reuse": max(assignment_counts.values(), default=0),
        "fact_design_assignment_count": sum(
            len(route["test_design_item_indexes"])
            for routes in fact_design_routes_by_module
            for route in routes
        ),
        "multi_design_fact_route_count": sum(
            len(route["test_design_item_indexes"]) > 1
            for routes in fact_design_routes_by_module
            for route in routes
        ),
        "initial_gap_module_count": int(repair_input["gap_module_count"]),
        "initial_gap_design_item_count": int(repair_input["gap_design_item_count"]),
        "route_repair_record_count": len(repair_records),
        "repaired_design_item_count": repaired_design_item_count,
        "removed_unsupported_design_item_count": removed_unsupported_design_item_count,
        "unsupported_design_items": unsupported_decisions,
        "unmatched_test_design_fact_count": len(unmatched_test_design_fact_ids),
        "unmatched_test_design_fact_ids": unmatched_test_design_fact_ids,
    }
    return draft_plan



__all__ = ['_required_text', '_required_source_text', '_identity', '_content_hash', 'submit_source_semantics', 'submit_generation_batch', 'submit_scenario_design_guidance', 'submit_business_plan', 'resolve_requirement_evidence', '_summary_item_text', 'validate_business_plan_output', '_serialized_json_chars', '_business_planning_limits', '_planning_scope_fragments', '_planning_fact_model_view', 'prepare_business_plan_batches', 'validate_business_plan_draft_output', 'prepare_business_plan_consolidation', '_planning_module_design_catalog', 'prepare_planning_scope_routes', '_normalize_planning_scope_route', '_validate_planning_scope_route', '_normalize_planning_scope_route_batch', 'postprocess_planning_scope_routing_item', '_expand_planning_scope_route_batches', '_collect_planning_scope_route_state', '_missing_planning_design_indexes', 'prepare_planning_route_repairs', '_normalize_planning_route_repair', 'postprocess_planning_route_repair_item', '_prune_unsupported_planning_design_items', 'merge_planning_scope_routes']
