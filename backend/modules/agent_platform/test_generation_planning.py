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
from .output_repair import OutputRepairError, OutputValidationError, repairable_output
from .test_generation_facts import fact_source_context_fields, required_text
from .action_journal import action_state_hash
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

def _required_source_text(value: Any, field_name: str) -> str:
    """校验事实源非空，并清理模型不可见控制字符与 OCR 兼容字符。"""

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+", " ", text)
    if not text.strip():
        raise ValueError(f"{field_name}不能为空")
    return text


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
        requirement = required_text(inline_requirement, "真实需求")
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


def _business_plan_source(input_payload: Any) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], dict[str, int]]:
    """先校验并编译平台来源；这些错误不能作为模型候选反馈继续执行。"""
    if not isinstance(input_payload, dict):
        raise ValueError("业务规划缺少完整平台输入")
    partial_plans = input_payload.get("partial_plans")
    if not isinstance(partial_plans, list) or not partial_plans:
        raise ValueError("业务规划缺少已校验的分批规划草案")
    drafts = []
    for index, partial in enumerate(partial_plans):
        if not isinstance(partial, dict) or not isinstance(partial.get("draft"), dict):
            raise ValueError(f"分批规划草案结构无效: index={index}")
        draft = partial["draft"]
        if not isinstance(draft.get("module_candidates"), list) or any(
            not isinstance(item, dict) for item in draft["module_candidates"]
        ):
            raise ValueError(f"分批规划候选目录结构无效: index={index}")
        drafts.append(draft)
    raw_catalog = input_payload.get("coverage_group_catalog")
    if not isinstance(raw_catalog, list) or not raw_catalog:
        raise ValueError("业务规划缺少覆盖语义组目录")
    catalog = {}
    for group in raw_catalog:
        if not isinstance(group, dict):
            raise ValueError("覆盖语义组目录项必须是对象")
        group_id = required_text(group.get("coverage_group_id"), "coverage_group_id")
        if group_id in catalog:
            raise ValueError(f"覆盖语义组目录包含重复ID: {group_id}")
        raw_items = group.get("coverage_items")
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError(f"覆盖语义组缺少原子覆盖项: {group_id}")
        catalog[group_id] = {
            **deepcopy(group), "coverage_group_id": group_id,
            "coverage_items": [required_text(item, "coverage_group.coverage_item") for item in raw_items],
        }
    draft_group_ids = []
    for draft in drafts:
        for candidate in draft["module_candidates"]:
            groups = candidate.get("coverage_topics")
            if not isinstance(groups, list) or not groups:
                raise ValueError("分批规划候选缺少平台覆盖语义组引用")
            draft_group_ids.extend(required_text(value, "draft.coverage_group_id") for value in groups)
    if len(draft_group_ids) != len(set(draft_group_ids)) or set(draft_group_ids) != set(catalog):
        raise ValueError("分批规划草案与覆盖语义组目录不一致")
    planning_metadata = input_payload.get("planning_metadata")
    if not isinstance(planning_metadata, dict):
        raise ValueError("业务规划 planning_metadata 必须是对象")
    metadata = {}
    for field in ("coverage_focus", "risks"):
        source = planning_metadata.get(field)
        if not isinstance(source, (str, list)):
            raise ValueError(f"业务规划元数据字段类型无效: {field}")
        values = []
        for candidate in [source] if isinstance(source, str) else source:
            value = _summary_item_text(candidate)
            if value and value not in values:
                values.append(value)
        if not values and field == "coverage_focus":
            raise ValueError(f"分批规划草案缺少可编译字段: {field}")
        metadata[field] = values
    limits = input_payload.get("planning_limits")
    required_keys = {"max_business_modules", "max_test_points", "max_test_designs", "max_coverage_items"}
    if not isinstance(limits, dict) or set(limits) != required_keys or any(
        type(limits[key]) is not int or limits[key] < 1 for key in required_keys
    ):
        raise ValueError("业务规划缺少平台计算的 planning_limits")
    if type(input_payload.get("case_budget")) is not int or input_payload["case_budget"] < 1:
        raise ValueError("业务规划来源 case_budget 无效")
    return catalog, metadata, deepcopy(limits)


def _business_plan_candidate_text(value: Any, field: str, path: str) -> str:
    try:
        return required_text(value, field)
    except ValueError as error:
        raise OutputValidationError(str(error), details={"field": field, "path": path}) from error


def _business_plan_candidate_list(value: Any, path: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise OutputValidationError("业务规划候选字段必须是数组", details={"path": path})
    return value


def _validate_business_plan_candidate(
    output: Any, *, catalog: dict[str, dict[str, Any]],
    metadata: dict[str, list[str]], limits: dict[str, int],
) -> dict[str, Any]:
    """候选错误带原始 CG 归属位置返回；平台不替模型去重、搬移或选择唯一归属。"""
    if not isinstance(output, dict) or not isinstance(output.get("business_modules"), list):
        raise OutputValidationError("业务规划缺少 business_modules", details={"path": "$.business_modules"})
    normalized = deepcopy(output)
    module_names: set[str] = set()
    group_locations: dict[str, list[dict[str, Any]]] = {}
    used_group_ids = []
    test_point_count = 0
    designs = []
    for module_index, module in enumerate(normalized["business_modules"]):
        module_path = f"$.business_modules[{module_index}]"
        if not isinstance(module, dict):
            raise OutputValidationError("业务规划模块必须是对象", details={"path": module_path})
        module_name = _business_plan_candidate_text(module.get("name"), "business_module.name", module_path + ".name")
        if module_name in module_names:
            raise OutputValidationError(f"业务规划包含重复模块名称: {module_name}", details={"path": module_path + ".name"})
        module_names.add(module_name)
        point_names: set[str] = set()
        for point_index, point in enumerate(_business_plan_candidate_list(module.get("test_points"), module_path + ".test_points")):
            test_point_count += 1
            point_path = f"{module_path}.test_points[{point_index}]"
            if not isinstance(point, dict):
                raise OutputValidationError("业务规划测试点必须是对象", details={"path": point_path})
            point_name = _business_plan_candidate_text(point.get("name"), "test_point.name", point_path + ".name")
            if point_name in point_names:
                raise OutputValidationError(f"业务模块包含重复测试点: module={module_name}, point={point_name}", details={"path": point_path + ".name"})
            point_names.add(point_name)
            for design_index, design in enumerate(_business_plan_candidate_list(point.get("test_designs"), point_path + ".test_designs")):
                design_path = f"{point_path}.test_designs[{design_index}]"
                if not isinstance(design, dict):
                    raise OutputValidationError("测试点的 test_designs 每项必须是对象", details={"path": design_path})
                technique = _business_plan_candidate_text(design.get("technique"), "test_design.technique", design_path + ".technique")
                _business_plan_candidate_text(design.get("rationale"), "test_design.rationale", design_path + ".rationale")
                coverage_ids = [_business_plan_candidate_text(item, "coverage_group_id", f"{design_path}.coverage_items[{index}]")
                                for index, item in enumerate(_business_plan_candidate_list(design.get("coverage_items"), design_path + ".coverage_items"))]
                location = {"module_index": module_index, "module_name": module_name,
                            "point_index": point_index, "point_name": point_name,
                            "design_index": design_index, "technique": technique}
                designs.append((design, coverage_ids))
                used_group_ids.extend(coverage_ids)
                for index, group_id in enumerate(coverage_ids):
                    group_locations.setdefault(group_id, []).append({
                        **location, "coverage_item_index": index, "path": f"{design_path}.coverage_items[{index}]",
                    })
    duplicates = sorted(group_id for group_id, count in Counter(used_group_ids).items() if count > 1)
    missing = sorted(set(catalog) - set(used_group_ids))
    unknown = sorted(set(used_group_ids) - set(catalog))
    if duplicates or missing or unknown:
        raise OutputValidationError(
            (f"业务规划引用未知覆盖语义组: {unknown}; " if unknown else "")
            + "业务规划必须且只能承接一次全部覆盖语义组: "
            + f"missing={missing[:20]}, duplicate={duplicates[:20]}",
            details={"reason": "coverage_group_assignment", "original_group_ids": list(catalog),
                     "missing_group_ids": missing, "duplicate_group_ids": duplicates, "unknown_group_ids": unknown,
                     "group_locations": {key: group_locations[key] for key in dict.fromkeys([*duplicates, *unknown])},
                     "instruction": "依据原始覆盖组语义决定唯一归属；每个 CG ID 在整个输出的所有模块、测试点和测试设计中合计只出现一次。不得改写、丢弃或新造 ID。"},
        )
    for design, coverage_ids in designs:
        design["coverage_items"] = [item for group_id in coverage_ids for item in catalog[group_id]["coverage_items"]]
    item_count = sum(len(design.get("coverage_items") or []) for design, _ in designs)
    if item_count < 1:
        raise OutputValidationError("业务规划没有形成任何测试设计覆盖项")
    actual_counts = {"max_business_modules": len(normalized["business_modules"]), "max_test_points": test_point_count,
                     "max_test_designs": len(designs), "max_coverage_items": item_count}
    exceeded = [f"{key}={actual_counts[key]}/{limits[key]}" for key in limits if actual_counts[key] > limits[key]]
    if exceeded:
        raise OutputValidationError(f"业务规划超过动态容量: {', '.join(sorted(exceeded))}",
                                    details={"actual_counts": actual_counts, "planning_limits": limits})
    normalized.update(deepcopy(metadata))
    return normalized


def validate_business_plan_output(
    context: ToolExecutionContext, arguments: dict[str, Any],
) -> dict[str, Any]:
    """平台来源先硬校验，只有模型候选问题可回到当前专业会话修正。"""
    catalog, metadata, limits = _business_plan_source(arguments.get("input_payload"))
    return _validate_business_plan_candidate(arguments.get("output"), catalog=catalog, metadata=metadata, limits=limits)


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
        scope_id = required_text(scope.get("scope_id"), "planning_scope.scope_id")
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
            fact_id = required_text(fact.get("fact_id"), "planning_scope.fact_id")
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

    来源坐标仍由审计链路保留；表格首行及所属行是断言的适用条件，不能压掉。
    """

    fact_id = required_text(fact.get("fact_id"), "planning_scope.fact_id")
    assertion = str(fact.get("assertion") or "").strip()
    return {"fact_id": fact_id, "assertion": assertion, **fact_source_context_fields(fact)}


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
            "scope_id": required_text(scope.get("scope_id"), "planning_scope.scope_id"),
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
    # 平台事实输入损坏必须硬失败，不能要求模型修复它未提供的数据。
    expected_fact_ids = [fact["fact_id"] for scope in _planning_scope_fragments(
        list(input_payload.get("planning_scopes") or [])
    ) for fact in scope["facts"]]
    if not expected_fact_ids or len(expected_fact_ids) != len(set(expected_fact_ids)):
        raise ValueError("业务规划批次事实为空或 fact_id 重复")
    output = arguments.get("output")
    if not isinstance(output, dict):
        raise OutputValidationError("业务规划批次未返回对象")
    candidates = output.get("module_candidates")
    if not isinstance(candidates, list) or not candidates:
        raise OutputValidationError("业务规划批次缺少 module_candidates")
    candidate_names: set[str] = set()
    routed_fact_ids: list[str] = []
    for raw_candidate in candidates:
        if not isinstance(raw_candidate, dict):
            raise OutputValidationError("业务规划批次 module_candidates 每项必须是对象")
        candidate = dict(raw_candidate)
        name = str(candidate.get("name") or "").strip()
        if not name:
            raise OutputValidationError("module_candidate.name 不能为空")
        if name in candidate_names:
            raise OutputValidationError(f"业务规划批次包含重复模块候选: {name}")
        candidate_names.add(name)
        fact_ids = list(candidate.get("fact_ids") or [])
        if not fact_ids or len(fact_ids) != len(set(fact_ids)):
            raise OutputValidationError(f"模块候选 fact_ids 为空或重复: module={name}")
        routed_fact_ids.extend(str(value) for value in fact_ids)
    expected_set = set(expected_fact_ids)
    routed_set = set(routed_fact_ids)
    if routed_set != expected_set:
        missing = sorted(expected_set - routed_set)
        unknown = sorted(routed_set - expected_set)
        raise OutputValidationError(
            "业务规划批次没有完整承接真实事实: "
            f"missing={missing[:20]}, unknown={unknown[:20]}",
            details={"missing_fact_ids": missing, "unknown_fact_ids": unknown},
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
                required_text(topic.get("name"), "coverage_topic.name")
                objective = required_text(
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
                        "name": required_text(candidate.get("name"), "module_candidate.name"),
                        "objective": required_text(
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
            # 路由依据完整覆盖意图判断事实归属；测试技法和重复的测试点名留在原计划。
            "test_design_items": [
                {key: item[key] for key in ("test_design_item_index", "coverage_intent")}
                for item in _planning_module_design_catalog(module)
            ],
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
        "removed_design_fields": ["test_point", "technique"],
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
    """逐模块审查完整覆盖意图；初始相关路由不代表已经充分支持。"""

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
    selected = arguments.get("design_selections")
    additional_scopes = arguments.get("additional_scope_ids", [])
    if (not isinstance(additional_scopes, list)
            or any(not isinstance(value, str) or value not in facts_by_scope for value in additional_scopes)
            or len(set(additional_scopes)) != len(additional_scopes)
            or (additional_scopes and selected is None)):
        raise ValueError("补充支持证据必须是定向复核中当前来源的有效且不重复 scope_id")
    additional_scopes = sorted(additional_scopes)
    if selected is None:
        missing_by_module = {index: list(range(len(_planning_module_design_catalog(module))))
                             for index, module in enumerate(modules) if state["facts_by_module"][index]}
    else:
        if not isinstance(selected, list) or not selected:
            raise ValueError("支持复核必须选择当前模块及覆盖意图")
        missing_by_module = {}
        for selection_position, selection in enumerate(selected):
            available_module_indexes = [index for index in range(len(modules)) if state["facts_by_module"][index]]
            if not isinstance(selection, dict):
                raise OutputValidationError("支持复核选择必须是模块及意图索引对象", details={
                    "selection_position": selection_position, "invalid_selection": deepcopy(selection),
                    "available_module_indexes": available_module_indexes,
                })
            index = selection.get("module_index")
            indexes = selection.get("test_design_item_indexes")
            if (type(index) is not int or not 0 <= index < len(modules) or index in missing_by_module
                    or not state["facts_by_module"][index] or not isinstance(indexes, list) or not indexes
                    or any(type(value) is not int or not 0 <= value < len(_planning_module_design_catalog(modules[index])) for value in indexes)
                    or len(set(indexes)) != len(indexes)):
                available_indexes = list(range(len(_planning_module_design_catalog(modules[index])))) if (
                    type(index) is int and index in available_module_indexes) else []
                requested_indexes = indexes if isinstance(indexes, list) else []
                raise OutputValidationError("支持复核选择了无效或重复的模块及覆盖意图", details={
                    "selection_position": selection_position, "module_index": deepcopy(index),
                    "requested_test_design_item_indexes": deepcopy(indexes),
                    "invalid_test_design_item_indexes": [deepcopy(value) for value in requested_indexes
                        if type(value) is not int or value not in available_indexes],
                    "duplicate_test_design_item_indexes": sorted({value for value in requested_indexes
                        if type(value) is int and requested_indexes.count(value) > 1}),
                    "module_already_selected": type(index) is int and index in missing_by_module,
                    "available_module_indexes": available_module_indexes,
                    "available_test_design_item_indexes": available_indexes,
                })
            missing_by_module[index] = sorted(indexes)
    items: list[dict[str, Any]] = []
    for module_index, missing_indexes in missing_by_module.items():
        design_catalog = _planning_module_design_catalog(modules[module_index])
        routed_indexes_by_fact = {
            str(route["fact_id"]): list(route["test_design_item_indexes"])
            for route in routes_by_module[module_index]
        }
        candidate_facts: list[dict[str, Any]] = []
        for scope_id in dict.fromkeys([*evidence_by_module[module_index], *additional_scopes]):
            for fact in facts_by_scope[scope_id]:
                fact_id = str(fact["fact_id"])
                candidate_facts.append(
                    {
                        **deepcopy(fact),
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
                "additional_scope_ids": list(additional_scopes),
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
    expected_module_index = prepared.get("module_index")
    designs = prepared.get("missing_test_design_items")
    facts = prepared.get("candidate_facts")
    if (type(expected_module_index) is not int or expected_module_index < 0
            or not isinstance(designs, list) or not designs
            or not isinstance(facts, list) or not facts):
        raise ValueError("规划路由缺口复核输入缺少有效模块、设计项或事实")
    design_indexes = [item.get("test_design_item_index") if isinstance(item, dict) else None for item in designs]
    if (any(type(index) is not int or index < 0 for index in design_indexes)
            or len(design_indexes) != len(set(design_indexes))):
        raise ValueError("规划路由缺口复核输入设计索引无效或重复")
    fact_ids = [required_text(fact.get("fact_id"), "candidate_fact.fact_id") for fact in facts]
    if len(fact_ids) != len(set(fact_ids)):
        raise ValueError("规划路由缺口复核输入事实 ID 重复")
    expected_design_indexes, evidence_fact_ids = set(design_indexes), set(fact_ids)
    candidate_fact_ids = set(evidence_fact_ids)
    intent_by_index = {item["test_design_item_index"]: item["coverage_intent"] for item in designs}
    # 由父领域的当前问题传入真实权威结论；保留原事实供取证，但不得再次把已排除事实批准为支持。
    unavailable_fact_ids = {fact_id
        for problem in prepared.get("_planning_feedback", {}).get("downstream_problems", [])
        if problem.get("origin") == "preparation" and problem.get("payload", {}).get("kind") == "authority_support_gap"
        for fact_id in problem["payload"]["authority_inactive_fact_ids"]}
    candidate_fact_ids -= unavailable_fact_ids
    output = deepcopy(raw_output)
    if type(output.get("module_index")) is not int or output["module_index"] != expected_module_index:
        raise OutputValidationError(
            f"规划路由缺口复核篡改 module_index: module_index={expected_module_index}",
            details={"expected_module_index": expected_module_index},
        )
    decisions = output.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise OutputValidationError("规划路由缺口复核 decisions 必须是非空数组")
    normalized_by_index: dict[int, dict[str, Any]] = {}
    for raw_decision in decisions:
        if not isinstance(raw_decision, dict):
            raise OutputValidationError("规划路由缺口复核 decision 必须是对象")
        decision = dict(raw_decision)
        design_index = decision.get("test_design_item_index")
        if (
            not isinstance(design_index, int)
            or isinstance(design_index, bool)
            or design_index not in expected_design_indexes
            or design_index in normalized_by_index
        ):
            raise OutputValidationError("规划路由缺口复核包含未知或重复测试设计项",
                                        details={"expected_design_indexes": sorted(expected_design_indexes)})
        fact_ids = decision.get("fact_ids")
        reason = str(decision.get("reason") or "").strip()
        gaps = decision.get("support_gaps")
        if (
            not isinstance(fact_ids, list)
            or any(not isinstance(fact_id, str) for fact_id in fact_ids)
            or len(fact_ids) != len(set(fact_ids))
            or any(str(fact_id) not in candidate_fact_ids for fact_id in fact_ids)
            or not reason
            or not isinstance(gaps, list)
        ):
            raise OutputValidationError(
                f"规划路由缺口复核结论无效: test_design_item_index={design_index}",
                details={"test_design_item_index": design_index,
                         "allowed_fact_ids": sorted(candidate_fact_ids)},
            )
        normalized_gaps = []
        for gap in gaps:
            if not isinstance(gap, dict):
                raise OutputValidationError("支持缺口必须逐项说明原意图片段及真实依据")
            fragment = str(gap.get("coverage_fragment") or "").strip()
            kind, cited = gap.get("kind"), gap.get("evidence_fact_ids")
            gap_reason = str(gap.get("reason") or "").strip()
            if (not fragment or fragment not in intent_by_index[design_index]
                    or kind not in {"missing_support", "source_conflict"}
                    or not isinstance(cited, list) or any(not isinstance(value, str) or value not in evidence_fact_ids for value in cited)
                    or len(set(cited)) != len(cited) or not gap_reason
                    or (kind == "source_conflict" and not cited)):
                raise OutputValidationError("支持缺口必须引用原覆盖意图片段；来源冲突须引用承载冲突内容的真实事实",
                    details={"test_design_item_index": design_index, "coverage_intent": intent_by_index[design_index]})
            normalized_gaps.append({"coverage_fragment": fragment, "kind": kind,
                                    "evidence_fact_ids": list(cited), "reason": gap_reason})
        if not fact_ids and not normalized_gaps:
            raise OutputValidationError("支持结论既无事实依据也无明确缺口，不能完成复核")
        disposition = "unsupported" if normalized_gaps else "supported"
        # 标准化回执可再次验收；其派生标签必须仍与完整支持结构一致。
        if "disposition" in decision and decision["disposition"] != disposition:
            raise OutputValidationError("支持回执的派生状态与事实及缺口不一致")
        normalized_by_index[design_index] = {
            "test_design_item_index": design_index,
            "disposition": disposition,
            "fact_ids": [str(fact_id) for fact_id in fact_ids],
            "reason": reason,
            "support_gaps": normalized_gaps,
        }
    if set(normalized_by_index) != expected_design_indexes:
        raise OutputValidationError("规划路由缺口复核未逐项审查全部缺失测试设计项",
                                    details={"missing_design_indexes": sorted(expected_design_indexes - set(normalized_by_index))})
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


def planning_support_request_hash(item: dict[str, Any]) -> str:
    """同意图及完整证据只能定向复核一次；问法、历史裁决和来源选择标签不构成新证据。"""
    request = deepcopy(item)
    request.pop("additional_scope_ids", None)
    request.get("_planning_feedback", {}).pop("previous_decisions", None)
    request.get("_planning_feedback", {}).pop("question", None)
    for fact in request.get("candidate_facts", []):
        fact.pop("current_test_design_item_indexes", None)
    return action_state_hash(request)


def planning_support_design_hashes(item: dict[str, Any]) -> set[str]:
    """按意图核对复核身份，改变批次组合不能再次抽取同一证据的裁决。"""
    return {planning_support_request_hash({**item, "missing_test_design_items": [design]})
            for design in item["missing_test_design_items"]}


def current_planning_support_decisions(
    context: ToolExecutionContext, arguments: dict[str, Any],
) -> dict[int, dict[int, dict[str, Any]]]:
    """保留原始复核回执，按真实顺序投影每个意图的当前支持结论。"""
    base = {key: arguments[key] for key in ("plan", "prepared_items", "route_records")}
    prepared = prepare_planning_route_repairs(context, base)["items"]
    records = arguments.get("repair_records") or []
    if len(prepared) != len(records):
        raise ValueError("完整意图支持复核记录数量不一致")
    decisions: dict[int, dict[int, dict[str, Any]]] = {}

    def accept(item, record):
        input_hash = action_state_hash(item)
        if record.get("input_hash") != input_hash:
            raise ValueError("支持复核回执与完整输入不一致")
        output = _normalize_planning_route_repair(prepared=item, raw_output=record["output"])
        current = decisions.setdefault(item["module_index"], {})
        for decision in output["decisions"]:
            current[decision["test_design_item_index"]] = {
                **deepcopy(decision), "review_input_sha256": input_hash,
            }

    for index, (item, record) in enumerate(zip(prepared, records, strict=True)):
        if record is None:
            continue
        if not isinstance(record, dict) or record.get("item_index") != index:
            raise ValueError("支持复核回执顺序无效")
        accept(item, record)
    seen_requests = set()
    for record in arguments.get("review_revisions") or []:
        item = record["input"]
        selection = [{"module_index": item["module_index"],
                      "test_design_item_indexes": [value["test_design_item_index"] for value in item["missing_test_design_items"]]}]
        expected = prepare_planning_route_repairs(context, {**base, "design_selections": selection,
            "additional_scope_ids": item["additional_scope_ids"]})["items"][0]
        feedback = item.get("_planning_feedback")
        previous_decisions = [deepcopy(decisions[item["module_index"]][design["test_design_item_index"]])
                              for design in item["missing_test_design_items"]]
        if (not isinstance(feedback, dict) or not isinstance(feedback.get("question"), str)
                or not feedback["question"].strip()
                or feedback.get("previous_decisions") != previous_decisions
                or {key: value for key, value in item.items() if key != "_planning_feedback"} != expected):
            raise ValueError("定向支持复核没有对应当前规划、完整事实或真实问题输入")
        request_hashes = planning_support_design_hashes(item)
        if request_hashes & seen_requests:
            raise ValueError("不能通过改变意图组合重复抽取相同证据的支持结论")
        seen_requests.update(request_hashes)
        accept(item, record)
    return decisions


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
    repair_input = prepare_planning_route_repairs(context, {
        "plan": arguments["plan"], "prepared_items": prepared_items, "route_records": route_records,
    })
    repair_records = list(arguments.get("repair_records") or [])
    decisions = current_planning_support_decisions(context, {
        "plan": arguments["plan"], "prepared_items": prepared_items, "route_records": route_records,
        "repair_records": repair_records, "review_revisions": arguments.get("review_revisions") or [],
    })
    repaired_design_item_count = 0
    unsupported_decisions = []
    supports_by_module = [[] for _ in modules]
    for module_index, module in enumerate(modules):
        if not facts_by_module[module_index]:
            continue
        expected_indexes = set(range(len(_planning_module_design_catalog(module))))
        current = decisions.get(module_index, {})
        if set(current) != expected_indexes:
            raise OutputValidationError("规划意图尚未全部完成独立支持审查", details={
                "module_index": module_index, "pending_design_indexes": sorted(expected_indexes - set(current)),
            })
        for design_index, decision in sorted(current.items()):
            if decision["disposition"] != "supported":
                unsupported_decisions.append({"module": module["name"], "design_index": design_index,
                                              "reason": decision["reason"]})
                continue
            repaired_design_item_count += 1
            supports_by_module[module_index].append({key: deepcopy(decision[key]) for key in (
                "test_design_item_index", "fact_ids", "review_input_sha256",
            )})
            # 独立审查替换相关性候选边，避免旧的错误绑定混入完整支持集合。
            for route in fact_design_routes_by_module[module_index]:
                route["test_design_item_indexes"] = [value for value in route["test_design_item_indexes"] if value != design_index]
            for fact_id in decision["fact_ids"]:
                scope_id = fact_scope_by_id[fact_id]
                if scope_id not in evidence_by_module[module_index]:
                    evidence_by_module[module_index].append(scope_id)
                if fact_id not in facts_by_module[module_index]:
                    facts_by_module[module_index].append(fact_id)
                route = next((value for value in fact_design_routes_by_module[module_index] if value["fact_id"] == fact_id), None)
                if route is None:
                    route = {"fact_id": fact_id, "test_design_item_indexes": []}
                    fact_design_routes_by_module[module_index].append(route)
                route["test_design_item_indexes"].append(design_index)
    if unsupported_decisions:
        raise OutputValidationError(
            "规划仍含无真实事实支持的覆盖意图，必须回查来源并修正草案和目标后重新规划，不能直接剪掉覆盖项",
            details={"unsupported_design_items": unsupported_decisions},
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
            "design_supports": supports_by_module[index],
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
        "unmatched_test_design_fact_count": len(unmatched_test_design_fact_ids),
        "unmatched_test_design_fact_ids": unmatched_test_design_fact_ids,
    }
    return draft_plan
