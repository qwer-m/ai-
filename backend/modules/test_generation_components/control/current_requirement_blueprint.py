from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from typing import Any, Callable

from .actor_roles import normalize_actor_role
from .feedback_control_state import FeedbackControlState
from .requirement_fact_ledger_compiler import (
    compile_requirement_atomic_fact_ledger,
)
from .requirement_graph_stage_compiler import compile_requirement_graph_stage
from .requirement_scope_ledger_compiler import compile_requirement_scope_ledger
from .model_envelope_call import safe_error_preview as _safe_error_preview
from .semantic_contract import (
    MAX_WORKFLOW_STEPS,
    REQUIREMENT_SEMANTIC_CONTRACT_VERSION,
    WORKFLOW_STAGE_KIND_VALUES,
    canonicalize_requirement_semantic_candidate,
    empty_requirement_semantic_contract,
    evidence_supported,
    graph_typed_state_identity_rejections,
    normalize_functional_architecture,
    normalize_module_candidates,
    normalize_requirement_semantic_contract,
    normalize_typed_states,
)
from .requirement_semantic_graph import UNREPAIRABLE_PRIMARY_FLOW_ERROR_CODES
from .requirement_evidence_view import build_requirement_business_evidence_view
from .workflow_typed_state_chain import validate_typed_state_chain


CURRENT_REQUIREMENT_BLUEPRINT_REPOSITORY_SOURCE = "current_requirement_blueprint"
CURRENT_REQUIREMENT_BLUEPRINT_SOURCE_TYPE = "current_requirement_extracted"

_MAX_BLUEPRINTS = 1
_MAX_STEPS = MAX_WORKFLOW_STEPS
_MAX_KEYWORDS_PER_STEP = 8
_DEFAULT_BLUEPRINT_MAX_TOKENS = 8192
_MIN_BLUEPRINT_MAX_TOKENS = 1200
_BLUEPRINT_MAX_TOKENS_ENV = "GENERATION_CURRENT_REQUIREMENT_BLUEPRINT_MAX_TOKENS"
_DEFAULT_SEMANTIC_COMPILATION_REQUEST_TIMEOUT_SECONDS = 180
_SEMANTIC_COMPILATION_REQUEST_TIMEOUT_ENV = (
    "GENERATION_SEMANTIC_COMPILATION_REQUEST_TIMEOUT_SECONDS"
)
_RETRY_SOURCE_QUOTE_MAX_CHARS = 220
_SOURCE_EVIDENCE_CATALOG_VERSION = "source-evidence-catalog-v2"
_SOURCE_STRUCTURAL_PARAGRAPH_SEPARATOR_RE = re.compile(
    r"\x01[ \t\u00a0]*(?:\n|$)"
)

SEMANTIC_COMPILATION_ABORT_CODE = "SEMANTIC_COMPILATION_FAILED"
WORKFLOW_DECLARATION_APPLIED = "applied_with_workflows"
WORKFLOW_DECLARATION_INDEPENDENT_ONLY = "applied_independent_only"
WORKFLOW_DECLARATION_INVALID = "invalid_workflow_contract"
WORKFLOW_DECLARATION_MISSING = "missing_workflow_declaration"
_SUCCESSFUL_WORKFLOW_DECLARATION_STATUSES = {
    WORKFLOW_DECLARATION_APPLIED,
    WORKFLOW_DECLARATION_INDEPENDENT_ONLY,
}

_ALLOWED_STAGE_KINDS = set(WORKFLOW_STAGE_KIND_VALUES)

def _text(value: Any) -> str:
    return str(value or "").strip()


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        value = int(str(raw).strip())
    except ValueError:
        return int(default)
    return max(int(minimum), int(value))


def current_requirement_blueprint_max_tokens() -> int:
    return _env_int(
        _BLUEPRINT_MAX_TOKENS_ENV,
        _DEFAULT_BLUEPRINT_MAX_TOKENS,
        minimum=_MIN_BLUEPRINT_MAX_TOKENS,
    )


def semantic_compilation_request_timeout_seconds() -> int:
    """语义编译是完整 JSON 非流式任务，使用独立且有界的读取预算。"""

    return min(
        360,
        _env_int(
            _SEMANTIC_COMPILATION_REQUEST_TIMEOUT_ENV,
            _DEFAULT_SEMANTIC_COMPILATION_REQUEST_TIMEOUT_SECONDS,
            minimum=30,
        ),
    )


def _slug(value: Any, *, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", _text(value)).strip("_")
    return (text or fallback)[:80]


def _fingerprint(value: Any) -> str:
    normalized = re.sub(r"\s+", " ", _text(value))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _list_text(value: Any, *, limit: int = 12) -> list[str]:
    values = value if isinstance(value, list) else ([value] if _text(value) else [])
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = re.sub(r"\s+", " ", _text(raw)).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        output.append(text[:240])
        if len(output) >= max(1, int(limit)):
            break
    return output


def _normalize_stage_kind(value: Any) -> str:
    raw = re.sub(r"\s+", "_", _text(value).lower()).strip("_")
    return raw if raw in _ALLOWED_STAGE_KINDS else ""


def _normalize_keywords(step: dict[str, Any]) -> list[str]:
    raw_values = step.get("match_keywords")
    values = list(raw_values) if isinstance(raw_values, list) else []
    keywords: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = re.sub(r"\s+", " ", _text(raw)).strip()
        key = text.lower()
        if len(text) < 2 or key in seen:
            continue
        seen.add(key)
        keywords.append(text[:80])
        if len(keywords) >= _MAX_KEYWORDS_PER_STEP:
            break
    return keywords


def _payload_to_blueprint_candidates(payload: Any) -> list[Any]:
    if isinstance(payload, dict):
        nested = payload.get("requirement_semantic_contract")
        if isinstance(nested, dict):
            payload = nested
        value = payload.get("workflow_blueprints")
        if isinstance(value, list):
            return list(value)
    return []


def _contract_payload(payload: Any) -> dict[str, Any]:
    contract, _ = canonicalize_requirement_semantic_candidate(payload)
    return contract


def _confidence(value: Any, *, default: float = 0.72) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return round(max(0.0, min(1.0, parsed)), 4)


def _safe_typed_state_rejections(
    items: list[dict[str, Any]],
    *,
    workflow_index: int,
    step_index: int,
    collection: str,
) -> list[dict[str, Any]]:
    """只透传类型、位置和字段级错误，不落库模型整段原文。"""
    safe_keys = (
        "item_type",
        "item_index",
        "reason",
        "missing_or_invalid_fields",
        "invalid_enum_fields",
        "invalid_enum_values",
        "incompatible_role_fields",
    )
    output: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        output.append(
            {
                "workflow_index": int(workflow_index),
                "step_index": int(step_index),
                "collection": collection,
                **{key: item.get(key) for key in safe_keys if key in item},
            }
        )
    return output


def _semantic_identity_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _text(value)).casefold()
    return re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "", text)


def _state_entity_module_keys(
    states: list[dict[str, Any]],
    *,
    module_catalog: list[dict[str, Any]],
) -> set[str]:
    """只把与模块 key、名称或别名精确一致的 module-scope 状态实体映射为模块。"""
    module_keys_by_identity: dict[str, set[str]] = {}
    for module in module_catalog:
        if not isinstance(module, dict):
            continue
        module_key = _text(module.get("module_key"))
        if not module_key:
            continue
        identities = [
            module_key,
            _text(module.get("module_name")),
            *[
                _text(alias)
                for alias in (module.get("aliases") or [])
                if _text(alias)
            ],
        ]
        for identity in identities:
            marker = _semantic_identity_key(identity)
            if marker:
                module_keys_by_identity.setdefault(marker, set()).add(module_key)

    resolved: set[str] = set()
    for state in states:
        if not isinstance(state, dict) or _text(state.get("scope")) != "module":
            continue
        matches = module_keys_by_identity.get(
            _semantic_identity_key(state.get("entity")),
            set(),
        )
        if len(matches) == 1:
            resolved.update(matches)
    return resolved


def _source_quote_identity(value: Any) -> str:
    """生成保留语义标点的稳定身份，只统一 Unicode、控制符与空白。"""

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _source_quote_match_key(value: Any) -> str:
    """仅为跨 PDF 换行定位移除空白，比较符和标点必须原样参与匹配。"""

    return re.sub(r"\s+", "", _source_quote_identity(value))


def _split_source_quote_line(value: str) -> list[str]:
    """按句末或分号切分单行，保留标点并避免把小数点当成句号。"""

    text = str(value or "").strip()
    if not text:
        return []
    output: list[str] = []
    start = 0
    cursor = 0
    closing_chars = frozenset({'"', "'", "”", "’", "）", ")", "]", "}"})
    while cursor < len(text):
        token = text[cursor]
        is_boundary = token in {"。", "！", "？", "；", ";", "!", "?"}
        if token == ".":
            previous = text[cursor - 1] if cursor > 0 else ""
            following = text[cursor + 1] if cursor + 1 < len(text) else ""
            prefix = text[:cursor].strip()
            is_outline_prefix = bool(
                following.isspace()
                and re.fullmatch(r"(?:\d{1,3}|[A-Za-z])", prefix)
            )
            is_boundary = (
                not (previous.isdigit() and following.isdigit())
                and not is_outline_prefix
                and (
                not following
                or following.isspace()
                or following in closing_chars
                )
            )
        if not is_boundary:
            cursor += 1
            continue
        end = cursor + 1
        while end < len(text) and text[end] in closing_chars:
            end += 1
        piece = text[start:end].strip()
        if piece:
            output.append(piece)
        start = end
        cursor = end
    remainder = text[start:].strip()
    if remainder:
        output.append(remainder)
    return output


def _clean_source_physical_line(value: str) -> str:
    """清理单个物理行，同时保留文字、语义标点和原有可见空格。"""

    # 裸 \x01 是 PDF 行内样式 span，不代表可见空格；删除它以保持同文同 ID。
    cleaned = str(value or "").replace("\x01", "")
    cleaned = re.sub(r"[\x00\x02-\x09\x0b-\x1f\x7f]+", " ", cleaned)
    return re.sub(r"[ \t\u00a0]+", " ", cleaned).strip()


def _source_physical_line_joiner(previous: str, following: str) -> str:
    """按字符边界恢复段内软换行，不依赖文档类型或业务词。"""

    left = str(previous or "").rstrip()
    right = str(following or "").lstrip()
    if not left or not right:
        return ""
    left_char = left[-1]
    right_char = right[0]
    if len(_source_quote_match_key(left)) == 1 and not left_char.isalnum():
        return " "
    if (
        left_char.isascii()
        and right_char.isascii()
        and left_char.isalnum()
        and right_char.isalnum()
    ):
        return " "
    return ""


def _rebuild_source_physical_lines(value: str) -> str:
    """把一个结构段落内部的 PDF 视觉折行恢复为连续正文。"""

    lines = [
        cleaned
        for cleaned in (
            _clean_source_physical_line(raw_line)
            for raw_line in str(value or "").split("\n")
        )
        if cleaned
    ]
    if not lines:
        return ""
    rebuilt = lines[0]
    for line in lines[1:]:
        rebuilt += _source_physical_line_joiner(rebuilt, line) + line
    return rebuilt.strip()


def _source_line_starts_semantic_group(value: str) -> bool:
    """识别通用标题或列表前缀，防止软换行重建跨过新的语义条目。"""

    text = str(value or "").strip()
    if not text:
        return False
    if text[0] in {"•", "◦", "▪", "●", "○"}:
        return True
    return bool(
        re.match(r"^#{1,6}\s+\S", text)
        or re.match(r"^\d{1,3}(?:\.\d{1,3})+(?:\s+|(?=[^\d.]))", text)
        or re.match(r"^(?:\d{1,3}|[A-Za-z])[.、)](?!\d)", text)
        or re.match(r"^[（(]\d{1,3}[）)]", text)
        or re.match(r"^[一二三四五六七八九十百]+[、.]", text)
    )


def _source_physical_line_groups(value: str) -> list[str]:
    """在一个强结构段内按新列表/标题起点分组，其余行作为软折行重建。"""

    lines = [
        cleaned
        for cleaned in (
            _clean_source_physical_line(raw_line)
            for raw_line in str(value or "").split("\n")
        )
        if cleaned
    ]
    if not lines:
        return []

    groups: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if current and _source_line_starts_semantic_group(line):
            groups.append(current)
            current = []
        current.append(line)
    if current:
        groups.append(current)
    return [
        rebuilt
        for rebuilt in (
            _rebuild_source_physical_lines("\n".join(group)) for group in groups
        )
        if rebuilt
    ]


def _source_semantic_paragraphs(source_text: str) -> list[str]:
    """优先使用解析器保留的结构段落；普通文本则保守保留显式换行。"""

    text = str(source_text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text:
        return []

    paragraphs: list[str] = []
    structural_terminators = list(
        _SOURCE_STRUCTURAL_PARAGRAPH_SEPARATOR_RE.finditer(text)
    )
    if structural_terminators:
        # PDF 文本层也会用裸 \x01 分隔同一行内的样式 span；只有 \x01+换行
        # 或位于 EOF 的 \x01 表示结构段落终止。未终止的尾部仍按普通文本处理。
        cursor = 0
        for terminator in structural_terminators:
            structural_paragraph = text[cursor : terminator.start()]
            for block in re.split(r"\n[ \t\u00a0]*\n+", structural_paragraph):
                paragraphs.extend(_source_physical_line_groups(block))
            cursor = terminator.end()
        for raw_line in text[cursor:].split("\n"):
            cleaned = _clean_source_physical_line(raw_line)
            if cleaned:
                paragraphs.append(cleaned)
        return paragraphs

    # 没有结构信息时无法通用判断软换行，保留调用方提供的每个语义行。
    for raw_line in text.split("\n"):
        cleaned = _clean_source_physical_line(raw_line)
        if cleaned:
            paragraphs.append(cleaned)
    return paragraphs


def _split_long_source_clause(value: str) -> list[str]:
    """仅对超过单条证据上限的连续原文做保序切片。"""

    clause = str(value or "").strip()
    if not clause:
        return []
    if len(clause) <= _RETRY_SOURCE_QUOTE_MAX_CHARS:
        return [clause]

    pieces: list[str] = []
    start = 0
    chunk_size = _RETRY_SOURCE_QUOTE_MAX_CHARS
    while start < len(clause):
        hard_end = min(len(clause), start + chunk_size)
        end = hard_end
        if hard_end < len(clause):
            search_start = start + max(40, chunk_size // 2)
            boundary = max(
                clause.rfind(token, search_start, hard_end)
                for token in ("，", ",", " ")
            )
            if boundary >= search_start:
                end = boundary + 1
        piece = clause[start:end].strip()
        if piece:
            pieces.append(piece)
        start = max(end, start + 1)
    return pieces


def _source_quote_units(source_text: str) -> list[tuple[str, str, int]]:
    """按结构段落和句级边界生成稳定证据单元，不把物理换行当事实边界。"""

    source_match_key = _source_quote_match_key(source_text)
    units: list[tuple[str, str, int]] = []
    for paragraph_index, paragraph in enumerate(
        _source_semantic_paragraphs(source_text)
    ):
        for clause in _split_source_quote_line(paragraph):
            for quote in _split_long_source_clause(clause):
                identity = _source_quote_identity(quote)
                match_key = _source_quote_match_key(quote)
                if not identity or not match_key:
                    continue
                if match_key not in source_match_key:
                    continue
                units.append((quote, identity, paragraph_index))
    return units


def _source_quote_ref(identity: str) -> str:
    return "EV_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def _source_outline_marker_kind(value: Any) -> str:
    """提取通用大纲前缀类型，仅用于保留父项与连续子项的分片原子性。"""

    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return ""
    if re.match(r"^\d{1,3}(?:\.\d{1,3})+(?:\s+|(?=[^\d.])|$)", text):
        return "dotted_numeric"
    if re.match(r"^\d{1,3}[.、)](?!\d)", text):
        return "numeric"
    if re.match(r"^[A-Za-z][.、)]", text):
        return "letter"
    if text[0] in {"•", "◦", "▪", "●", "○"}:
        return "bullet"
    if re.match(r"^#{1,6}\s+\S", text) or re.match(
        r"^[一二三四五六七八九十百]+[、.]",
        text,
    ):
        return "section"
    return ""


def _assign_source_partition_groups(
    catalog: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """为目录附加稳定分组，预算分片不得切断一个编号项及其连续子项。"""

    paragraph_runs: list[list[dict[str, Any]]] = []
    for item in catalog:
        paragraph_index = int(item.get("_paragraph_index") or 0)
        if (
            not paragraph_runs
            or int(paragraph_runs[-1][0].get("_paragraph_index") or 0)
            != paragraph_index
        ):
            paragraph_runs.append([])
        paragraph_runs[-1].append(item)

    grouped_runs: list[list[dict[str, Any]]] = []
    cursor = 0
    stop_kinds = {"numeric", "dotted_numeric", "section"}
    while cursor < len(paragraph_runs):
        current_run = paragraph_runs[cursor]
        current_kind = _source_outline_marker_kind(current_run[0].get("quote"))
        end = cursor + 1
        if current_kind in {"numeric", "letter"}:
            while end < len(paragraph_runs):
                next_kind = _source_outline_marker_kind(
                    paragraph_runs[end][0].get("quote")
                )
                if next_kind in stop_kinds:
                    break
                end += 1
        grouped_runs.append(
            [item for run in paragraph_runs[cursor:end] for item in run]
        )
        cursor = end

    output: list[dict[str, str]] = []
    for group in grouped_runs:
        refs = [str(item.get("ref") or "") for item in group]
        group_identity = "\x1f".join(refs)
        group_id = (
            "PG_"
            + hashlib.sha256(group_identity.encode("utf-8")).hexdigest()[:12].upper()
        )
        for item in group:
            output.append(
                {
                    "ref": str(item.get("ref") or ""),
                    "quote": str(item.get("quote") or ""),
                    "partition_group_id": group_id,
                }
            )
    return output


def _build_source_quote_catalog(source_text: str) -> list[dict[str, str]]:
    """把原文保序拆为结构段落内的句级目录。"""

    source_units = _source_quote_units(source_text)

    catalog: list[dict[str, str]] = []
    seen_identities: set[str] = set()
    for quote, _window_identity, paragraph_index in source_units:
        identity = _source_quote_identity(quote)
        if not identity or identity in seen_identities:
            continue
        seen_identities.add(identity)
        catalog.append(
            {
                "ref": _source_quote_ref(identity),
                "quote": quote,
                "_paragraph_index": paragraph_index,
            }
        )
    return _assign_source_partition_groups(catalog)


def _source_quote_catalog_coverage(
    source_text: str,
    quote_catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    """按目录顺序核对无损匹配表示的覆盖率，不记录任何业务文本。"""

    source_match_key = _source_quote_match_key(source_text)
    covered = bytearray(len(source_match_key))

    def _mark(position: int, marker: str) -> None:
        end = min(len(source_match_key), position + len(marker))
        covered[position:end] = b"\x01" * max(0, end - position)

    ordered_cursor = 0
    for item in quote_catalog:
        if not isinstance(item, dict):
            continue
        marker = _source_quote_match_key(item.get("quote"))
        if not marker:
            continue
        # 重复长片段可能总是先匹配到前文；顺序匹配保留本次切片的真实位置。
        ordered_position = source_match_key.find(marker, ordered_cursor)
        if ordered_position >= 0:
            _mark(ordered_position, marker)
            ordered_cursor = ordered_position + len(marker)

        # 同一原文单元可能真实重复出现，所有出现位置都必须计入覆盖。
        cursor = 0
        while cursor < len(source_match_key):
            position = source_match_key.find(marker, cursor)
            if position < 0:
                break
            _mark(position, marker)
            cursor = position + 1
    covered_chars = int(sum(covered))
    return {
        "source_key_chars": int(len(source_match_key)),
        "covered_key_chars": covered_chars,
        "complete": bool(covered_chars == len(source_match_key)),
    }


def _source_evidence_catalog_diagnostic(
    quote_catalog: list[dict[str, Any]],
    *,
    injected: bool,
) -> dict[str, Any]:
    entries = [
        {
            "ref": _text(item.get("ref")),
            "identity": _source_quote_identity(item.get("quote")),
            "partition_group_id": _text(item.get("partition_group_id")),
        }
        for item in quote_catalog
        if isinstance(item, dict) and _text(item.get("ref"))
    ]
    canonical = json.dumps(entries, ensure_ascii=True, separators=(",", ":"))
    return {
        "version": _SOURCE_EVIDENCE_CATALOG_VERSION,
        "count": int(len(entries)),
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "injected": bool(injected),
    }


def _normalize_current_requirement_workflow_step(
    raw_step: Any,
    *,
    workflow_index: int,
    workflow_id: str,
    step_index: int,
    requirement_text: str,
    module_catalog: list[dict[str, Any]],
    active_interactions: dict[str, dict[str, Any]],
    seen_step_ids: set[str],
    semantic_graph_mode: bool = False,
    evidence_validator: Callable[[list[str], str], bool] | None = None,
) -> tuple[
    dict[str, Any] | None,
    list[str],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """一次收集单个步骤的全部契约错误，避免每轮只暴露第一个局部错误。"""
    prefix = f"step_{step_index}:"
    if not isinstance(raw_step, dict):
        return None, [f"{prefix}not_object"], [], []

    reasons: list[str] = []
    typed_state_rejections: list[dict[str, Any]] = []
    workflow_consistency_rejections: list[dict[str, Any]] = []
    stage_kind = _normalize_stage_kind(raw_step.get("stage_kind"))
    label = _text(raw_step.get("label"))
    action = _text(raw_step.get("action"))
    raw_actor = _text(raw_step.get("actor"))
    raw_step_id = _text(raw_step.get("id"))
    raw_state_in = _text(raw_step.get("state_in"))
    raw_state_out = _text(raw_step.get("state_out"))
    raw_module_candidates = raw_step.get("module_candidates")
    raw_interaction_ids = raw_step.get("interaction_ids")

    field_validity = (
        ("stage_kind", bool(stage_kind)),
        ("label", bool(label)),
        ("action", bool(action)),
        ("actor", bool(raw_actor)),
        ("id", bool(raw_step_id)),
        ("state_in", bool(raw_state_in)),
        ("state_out", bool(raw_state_out)),
        ("required", isinstance(raw_step.get("required"), bool)),
        ("terminal", isinstance(raw_step.get("terminal"), bool)),
        ("critical", isinstance(raw_step.get("critical"), bool)),
        ("blocking", isinstance(raw_step.get("blocking"), bool)),
        ("destructive", isinstance(raw_step.get("destructive"), bool)),
        (
            "module_candidates",
            isinstance(raw_module_candidates, list) and bool(raw_module_candidates),
        ),
        ("interaction_ids", isinstance(raw_interaction_ids, list)),
    )
    reasons.extend(
        f"{prefix}{field}_missing_or_invalid"
        for field, valid in field_validity
        if not valid
    )

    step_id = _slug(raw_step_id, fallback="") if raw_step_id else ""
    if raw_step_id and not step_id:
        reasons.append(f"{prefix}id_invalid")
    elif step_id:
        if step_id in seen_step_ids:
            reasons.append(f"{prefix}id_duplicate")
        else:
            seen_step_ids.add(step_id)

    state_in = _slug(raw_state_in, fallback="") if raw_state_in else ""
    state_out = _slug(raw_state_out, fallback="") if raw_state_out else ""
    if raw_state_in and raw_state_out and (not state_in or not state_out):
        reasons.append(f"{prefix}state_invalid")

    evidence = _list_text(raw_step.get("evidence"), limit=6)
    if not (evidence_validator or evidence_supported)(evidence, requirement_text):
        reasons.append(f"{prefix}evidence_unverified")

    module_candidates: list[dict[str, Any]] = []
    module_collection_valid = isinstance(raw_module_candidates, list) and bool(
        raw_module_candidates
    )
    if module_collection_valid:
        module_candidates = normalize_module_candidates(
            raw_module_candidates,
            source_text=requirement_text,
            module_catalog=module_catalog,
            evidence_validator=evidence_validator,
        )
        if (
            len(module_candidates) != len(raw_module_candidates)
            or any(item.get("evidence_verified") is not True for item in module_candidates)
        ):
            reasons.append(f"{prefix}module_candidates_invalid_or_unverified")

    interaction_ids: list[str] = []
    interactions_valid = isinstance(raw_interaction_ids, list)
    if interactions_valid:
        interaction_ids = _list_text(raw_interaction_ids, limit=16)
        interactions_valid = bool(
            len(interaction_ids) == len(raw_interaction_ids)
            and all(
                interaction_id in active_interactions
                for interaction_id in interaction_ids
            )
        )
        if not interactions_valid:
            reasons.append(f"{prefix}interaction_ids_invalid_or_unknown")

    if module_collection_valid and len(module_candidates) == len(raw_module_candidates):
        candidate_module_keys = {
            str(item.get("module_key") or "").strip()
            for item in module_candidates
            if str(item.get("module_key") or "").strip()
        }
        candidate_roles = {
            str(item.get("role") or "").strip()
            for item in module_candidates
            if str(item.get("role") or "").strip()
        }
        candidate_roles_by_module: dict[str, set[str]] = {}
        for item in module_candidates:
            module_key = _text(item.get("module_key"))
            role = _text(item.get("role"))
            if module_key and role:
                candidate_roles_by_module.setdefault(module_key, set()).add(role)
        structured_cross_module = bool(
            len(candidate_module_keys) > 1
            and "source" in candidate_roles
            and "target" in candidate_roles
        )
        if structured_cross_module and interactions_valid and not interaction_ids:
            reasons.append(f"{prefix}cross_module_interaction_id_missing")
            source_module_keys = {
                _text(item.get("module_key"))
                for item in module_candidates
                if _text(item.get("role")) == "source"
                and _text(item.get("module_key"))
            }
            target_module_keys = {
                _text(item.get("module_key"))
                for item in module_candidates
                if _text(item.get("role")) == "target"
                and _text(item.get("module_key"))
            }
            matching_interaction_ids = sorted(
                interaction_id
                for interaction_id, interaction in active_interactions.items()
                if _text(interaction.get("source_module_key")) in source_module_keys
                and _text(interaction.get("target_module_key")) in target_module_keys
            )
            consistency_rejection = {
                "workflow_index": int(workflow_index),
                "step_index": int(step_index),
                "reason": "cross_module_interaction_id_missing",
                "declared_module_keys": sorted(candidate_module_keys),
                "source_module_key": next(iter(sorted(source_module_keys)), ""),
                "target_module_key": next(iter(sorted(target_module_keys)), ""),
                "expected_interaction_ids": matching_interaction_ids,
            }
            if not matching_interaction_ids:
                consistency_rejection["field_path"] = (
                    "$.semantic_graph.edges"
                    if semantic_graph_mode
                    else "$.functional_architecture.module_interactions"
                )
                if semantic_graph_mode:
                    consistency_rejection["source_node_id"] = next(
                        iter(sorted(source_module_keys)), ""
                    )
                    consistency_rejection["target_node_id"] = next(
                        iter(sorted(target_module_keys)), ""
                    )
            workflow_consistency_rejections.append(consistency_rejection)
        if interactions_valid:
            for interaction_id in interaction_ids:
                interaction = active_interactions[interaction_id]
                source_module_key = _text(interaction.get("source_module_key"))
                target_module_key = _text(interaction.get("target_module_key"))
                required_module_keys = {source_module_key, target_module_key}
                required_module_keys.discard("")
                missing_module_keys = required_module_keys - candidate_module_keys
                if missing_module_keys:
                    reasons.append(f"{prefix}interaction_modules_not_declared")
                    workflow_consistency_rejections.append(
                        {
                            "workflow_index": int(workflow_index),
                            "step_index": int(step_index),
                            "reason": "interaction_modules_not_declared",
                            "interaction_id": interaction_id,
                            "declared_module_keys": sorted(candidate_module_keys),
                            "required_module_keys": sorted(required_module_keys),
                            "missing_module_keys": sorted(missing_module_keys),
                            "candidate_field": (
                                "scope_candidates"
                                if semantic_graph_mode
                                else "module_candidates"
                            ),
                        }
                    )
                role_mismatches: list[dict[str, Any]] = []
                for module_key, expected_role in (
                    (source_module_key, "source"),
                    (target_module_key, "target"),
                ):
                    if not module_key or expected_role in candidate_roles_by_module.get(
                        module_key, set()
                    ):
                        continue
                    role_mismatches.append(
                        {
                            "module_key": module_key,
                            "expected_role": expected_role,
                            "declared_roles": sorted(
                                candidate_roles_by_module.get(module_key, set())
                            ),
                        }
                    )
                if role_mismatches:
                    reasons.append(f"{prefix}interaction_direction_roles_mismatch")
                    workflow_consistency_rejections.append(
                        {
                            "workflow_index": int(workflow_index),
                            "step_index": int(step_index),
                            "reason": "interaction_direction_roles_mismatch",
                            "interaction_id": interaction_id,
                            "source_module_key": source_module_key,
                            "target_module_key": target_module_key,
                            "role_mismatches": role_mismatches,
                            "candidate_field": (
                                "scope_candidates"
                                if semantic_graph_mode
                                else "module_candidates"
                            ),
                        }
                    )

    normalized_state_collections: dict[str, list[dict[str, Any]]] = {}
    for collection, item_type, state_role in (
        ("required_states", "required_state", "precondition"),
        ("produced_states", "produced_state", "produced"),
    ):
        raw_states = raw_step.get(collection)
        if raw_states is not None and not isinstance(raw_states, list):
            reasons.append(f"{prefix}{collection}_invalid_or_unverified")
            typed_state_rejections.append(
                {
                    "workflow_index": int(workflow_index),
                    "step_index": int(step_index),
                    "collection": collection,
                    "item_type": item_type,
                    "reason": "collection_not_list",
                }
            )
            normalized_state_collections[collection] = []
            continue
        state_rejections: list[dict[str, Any]] = []
        normalized_states = normalize_typed_states(
            raw_states or [],
            source_text=requirement_text,
            rejected_semantic_items=state_rejections,
            item_type=item_type,
            state_role=state_role,
            evidence_validator=evidence_validator,
        )
        typed_state_rejections.extend(
            _safe_typed_state_rejections(
                state_rejections,
                workflow_index=workflow_index,
                step_index=step_index,
                collection=collection,
            )
        )
        if len(normalized_states) != len(raw_states or []):
            reasons.append(f"{prefix}{collection}_invalid_or_unverified")
        normalized_state_collections[collection] = normalized_states

    if module_collection_valid and len(module_candidates) == len(raw_module_candidates):
        candidate_module_keys = {
            _text(item.get("module_key"))
            for item in module_candidates
            if _text(item.get("module_key"))
        }
        state_module_keys = _state_entity_module_keys(
            [
                *normalized_state_collections.get("required_states", []),
                *normalized_state_collections.get("produced_states", []),
            ],
            module_catalog=module_catalog,
        )
        missing_state_module_keys = state_module_keys - candidate_module_keys
        if missing_state_module_keys:
            reasons.append(f"{prefix}state_modules_not_declared")
            workflow_consistency_rejections.append(
                {
                    "workflow_index": int(workflow_index),
                    "step_index": int(step_index),
                    "reason": "state_modules_not_declared",
                    "declared_module_keys": sorted(candidate_module_keys),
                    "required_module_keys": sorted(state_module_keys),
                    "missing_module_keys": sorted(missing_state_module_keys),
                    "candidate_field": (
                        "scope_candidates"
                        if semantic_graph_mode
                        else "module_candidates"
                    ),
                }
            )

    reasons = list(dict.fromkeys(reasons))
    if reasons:
        return (
            None,
            reasons,
            typed_state_rejections,
            workflow_consistency_rejections,
        )

    graph_fact_ids = (
        _list_text(raw_step.get("fact_ids"), limit=32)
        if semantic_graph_mode
        else []
    )
    graph_scope_candidates = (
        [
            {
                "scope_id": _text(item.get("scope_id")),
                "role": _text(item.get("role")),
                "fact_ids": _list_text(item.get("fact_ids"), limit=32),
                "confidence": _confidence(item.get("confidence")),
            }
            for item in (raw_step.get("scope_candidates") or [])
            if isinstance(item, dict) and _text(item.get("scope_id"))
        ]
        if semantic_graph_mode
        else []
    )
    graph_relation_ids = (
        _list_text(
            raw_step.get("graph_relation_ids") or raw_step.get("relation_ids"),
            limit=32,
        )
        if semantic_graph_mode
        else []
    )
    return (
        {
            "id": step_id,
            "workflow_id": workflow_id,
            "label": label[:120],
            "action": action[:160],
            "assertion": _text(
                raw_step.get("assertion") or raw_step.get("expected_result")
            )[:240],
            "test_steps": (
                list(raw_step.get("test_steps") or [])
                if isinstance(raw_step.get("test_steps"), list)
                else []
            ),
            "state_in": state_in,
            "state_out": state_out,
            "source_state": state_in,
            "target_state": state_out,
            "stage_kind": stage_kind,
            "actor": normalize_actor_role(raw_actor),
            "source_actor_role": raw_actor,
            "path_type": "positive",
            "required": raw_step["required"],
            "terminal": raw_step["terminal"],
            "critical": raw_step["critical"],
            "blocking": raw_step["blocking"],
            "destructive": raw_step["destructive"],
            "can_advance_main_flow": True,
            "match_keywords": _normalize_keywords(raw_step),
            "graph_node_id": (
                _text(raw_step.get("graph_node_id"))
                if semantic_graph_mode
                else ""
            ),
            "fact_ids": graph_fact_ids,
            "scope_candidates": graph_scope_candidates,
            "relation_ids": list(graph_relation_ids),
            "graph_relation_ids": list(graph_relation_ids),
            "module_candidates": module_candidates,
            "interaction_ids": interaction_ids,
            "required_states": normalized_state_collections["required_states"],
            "produced_states": normalized_state_collections["produced_states"],
            "evidence": evidence,
            "evidence_verified": True,
            "source": CURRENT_REQUIREMENT_BLUEPRINT_REPOSITORY_SOURCE,
        },
        [],
        typed_state_rejections,
        workflow_consistency_rejections,
    )


def normalize_current_requirement_blueprint_payload(
    payload: Any,
    *,
    requirement_text: str,
    project_id: int | None = None,
    user_id: int | None = None,
    normalization_diagnostics: dict[str, Any] | None = None,
    evidence_validator: Callable[[list[str], str], bool] | None = None,
) -> list[dict[str, Any]]:
    contract_payload = _contract_payload(payload)
    semantic_graph_mode = bool(
        "evidence_facts" in contract_payload or "semantic_graph" in contract_payload
    )
    if semantic_graph_mode:
        graph_contract = normalize_requirement_semantic_contract(
            contract_payload,
            requirement_text=requirement_text,
            workflow_blueprints=(
                list(contract_payload.get("workflow_blueprints") or [])
                if isinstance(contract_payload.get("workflow_blueprints"), list)
                else []
            ),
            evidence_validator=evidence_validator,
        )
        contract_payload = {
            **contract_payload,
            "functional_architecture": dict(
                graph_contract.get("functional_architecture") or {}
            ),
            "workflow_blueprints": list(
                graph_contract.get("workflow_blueprints") or []
            ),
        }
    architecture = contract_payload.get("functional_architecture")
    if not isinstance(architecture, dict):
        architecture = {}
    normalized_architecture = normalize_functional_architecture(
        architecture,
        source_text=requirement_text,
        evidence_validator=evidence_validator,
    )
    module_catalog = [
        dict(item)
        for item in (normalized_architecture.get("functional_modules") or [])
        if isinstance(item, dict)
    ]
    active_interactions = {
        str(item.get("interaction_id") or "").strip(): dict(item)
        for item in (normalized_architecture.get("module_interactions") or [])
        if isinstance(item, dict) and str(item.get("interaction_id") or "").strip()
    }

    blueprints: list[dict[str, Any]] = []
    workflow_rejections: list[dict[str, Any]] = []
    typed_state_rejections: list[dict[str, Any]] = []
    workflow_consistency_rejections: list[dict[str, Any]] = []
    candidates = _payload_to_blueprint_candidates(contract_payload)
    requirement_hash = _fingerprint(requirement_text)
    for bp_index, raw_blueprint in enumerate(candidates, start=1):
        rejection_reasons: list[str] = []
        if bp_index > _MAX_BLUEPRINTS:
            workflow_rejections.append(
                {"workflow_index": bp_index, "reasons": ["workflow_count_exceeds_limit"]}
            )
            continue
        if not isinstance(raw_blueprint, dict):
            workflow_rejections.append(
                {"workflow_index": bp_index, "reasons": ["workflow_not_object"]}
            )
            continue
        raw_steps = raw_blueprint.get("steps")
        if not isinstance(raw_steps, list):
            workflow_rejections.append(
                {"workflow_index": bp_index, "reasons": ["steps_not_list"]}
            )
            continue
        if not raw_steps:
            workflow_rejections.append(
                {"workflow_index": bp_index, "reasons": ["steps_empty"]}
            )
            continue
        if len(raw_steps) > _MAX_STEPS:
            workflow_rejections.append(
                {"workflow_index": bp_index, "reasons": ["step_count_exceeds_limit"]}
            )
            continue
        candidate_steps = list(raw_steps)
        workflow_name = _text(raw_blueprint.get("name"))
        raw_workflow_id = _text(raw_blueprint.get("workflow_id"))
        if not workflow_name or not raw_workflow_id:
            workflow_rejections.append(
                {
                    "workflow_index": bp_index,
                    "reasons": [
                        field
                        for field, missing in (
                            ("workflow_name_missing", not workflow_name),
                            ("workflow_id_missing", not raw_workflow_id),
                        )
                        if missing
                    ],
                }
            )
            continue
        workflow_id = _slug(raw_workflow_id, fallback="")
        if not workflow_id:
            workflow_rejections.append(
                {"workflow_index": bp_index, "reasons": ["workflow_id_invalid"]}
            )
            continue
        raw_initial_state = _text(raw_blueprint.get("initial_state"))
        if raw_blueprint.get("primary") is not True:
            workflow_rejections.append(
                {"workflow_index": bp_index, "reasons": ["primary_workflow_not_declared"]}
            )
            continue
        if not raw_initial_state:
            workflow_rejections.append(
                {"workflow_index": bp_index, "reasons": ["initial_state_missing"]}
            )
            continue
        initial_state = _slug(raw_initial_state, fallback="")
        if not initial_state:
            workflow_rejections.append(
                {"workflow_index": bp_index, "reasons": ["initial_state_invalid"]}
            )
            continue
        normalized_steps: list[dict[str, Any]] = []
        seen_step_ids: set[str] = set()
        for step_index, raw_step in enumerate(candidate_steps, start=1):
            (
                normalized_step,
                step_reasons,
                step_typed_state_rejections,
                step_consistency_rejections,
            ) = _normalize_current_requirement_workflow_step(
                raw_step,
                workflow_index=bp_index,
                workflow_id=workflow_id,
                step_index=step_index,
                requirement_text=requirement_text,
                module_catalog=module_catalog,
                active_interactions=active_interactions,
                seen_step_ids=seen_step_ids,
                semantic_graph_mode=semantic_graph_mode,
                evidence_validator=evidence_validator,
            )
            rejection_reasons.extend(step_reasons)
            typed_state_rejections.extend(step_typed_state_rejections)
            workflow_consistency_rejections.extend(step_consistency_rejections)
            if normalized_step is not None:
                normalized_steps.append(normalized_step)
        if rejection_reasons or len(normalized_steps) != len(candidate_steps):
            workflow_rejections.append(
                {
                    "workflow_index": bp_index,
                    "workflow_id": workflow_id,
                    "reasons": list(dict.fromkeys(rejection_reasons or ["step_normalization_incomplete"])),
                }
            )
            continue

        typed_state_chain_issues = validate_typed_state_chain(normalized_steps)
        if typed_state_chain_issues:
            rejection_reasons.append("typed_state_chain_invalid")
            workflow_consistency_rejections.extend(
                {
                    "workflow_index": int(bp_index),
                    "reason": str(issue.get("reason") or "typed_state_chain_invalid"),
                    "field_path": (
                        f"$.workflow_blueprints[{bp_index - 1}].steps["
                        f"{int(issue.get('step_index') or 0)}].required_states["
                        f"{int(issue.get('state_index') or 0)}]"
                    ),
                    **issue,
                }
                for issue in typed_state_chain_issues
            )

        explicit_required_ids = [str(step.get("id") or "") for step in normalized_steps if step["required"]]
        raw_required_stage_ids = raw_blueprint.get("required_stage_ids")
        raw_terminal_states = raw_blueprint.get("terminal_states")
        if not isinstance(raw_required_stage_ids, list) or not raw_required_stage_ids:
            rejection_reasons.append("required_stage_ids_missing_or_invalid")
        if not isinstance(raw_terminal_states, list) or not raw_terminal_states:
            rejection_reasons.append("terminal_states_missing_or_invalid")
        required_stage_ids = [
            _slug(item, fallback="")
            for item in _list_text(raw_required_stage_ids, limit=_MAX_STEPS)
        ]
        explicit_terminal_states = [
            str(step.get("state_out") or "") for step in normalized_steps if step["terminal"]
        ]
        terminal_states = [
            _slug(item, fallback="")
            for item in _list_text(raw_terminal_states, limit=8)
        ]
        if required_stage_ids and set(required_stage_ids) != set(explicit_required_ids):
            rejection_reasons.append("required_stage_ids_mismatch")
            workflow_consistency_rejections.append(
                {
                    "workflow_index": int(bp_index),
                    "reason": "required_stage_ids_mismatch",
                    "declared_values": required_stage_ids,
                    "expected_values": explicit_required_ids,
                }
            )
        if terminal_states and set(terminal_states) != set(explicit_terminal_states):
            rejection_reasons.append("terminal_states_mismatch")
            workflow_consistency_rejections.append(
                {
                    "workflow_index": int(bp_index),
                    "reason": "terminal_states_mismatch",
                    "declared_values": terminal_states,
                    "expected_values": explicit_terminal_states,
                }
            )
        if initial_state != str(normalized_steps[0].get("state_in") or ""):
            rejection_reasons.append("initial_state_mismatch")
            workflow_consistency_rejections.append(
                {
                    "workflow_index": int(bp_index),
                    "reason": "initial_state_mismatch",
                    "declared_values": [initial_state],
                    "expected_values": [str(normalized_steps[0].get("state_in") or "")],
                }
            )
        for previous_index, (previous_step, current_step) in enumerate(
            zip(normalized_steps, normalized_steps[1:]),
            start=1,
        ):
            previous_state_out = str(previous_step.get("state_out") or "")
            current_state_in = str(current_step.get("state_in") or "")
            if previous_state_out == current_state_in:
                continue
            rejection_reasons.append("adjacent_state_discontinuity")
            workflow_consistency_rejections.append(
                {
                    "workflow_index": int(bp_index),
                    "step_index": int(previous_index + 1),
                    "reason": "adjacent_state_discontinuity",
                    "field_path": (
                        f"$.workflow_blueprints[{bp_index - 1}].steps[{previous_index}].state_in"
                    ),
                    "declared_values": [current_state_in],
                    "expected_values": [previous_state_out],
                }
            )
        if rejection_reasons:
            workflow_rejections.append(
                {
                    "workflow_index": bp_index,
                    "workflow_id": workflow_id,
                    "reasons": list(dict.fromkeys(rejection_reasons)),
                }
            )
            continue

        blueprints.append(
            {
                **raw_blueprint,
                "id": workflow_id,
                "workflow_id": workflow_id,
                "name": workflow_name[:120],
                "primary": True,
                "project_id": int(project_id or 0),
                "user_id": int(user_id or 0),
                "source_type": CURRENT_REQUIREMENT_BLUEPRINT_SOURCE_TYPE,
                "repository_source": CURRENT_REQUIREMENT_BLUEPRINT_REPOSITORY_SOURCE,
                "trusted": False,
                "confidence": _confidence(raw_blueprint.get("confidence")),
                "source_content_hash": requirement_hash,
                "initial_state": initial_state,
                "required_stage_ids": required_stage_ids,
                "terminal_states": terminal_states,
                "closure_declaration_complete": True,
                "closure_declaration_errors": [],
                "steps": normalized_steps,
                "edges": normalized_steps,
            }
        )
    if normalization_diagnostics is not None:
        normalization_diagnostics.update(
            {
                "raw_workflow_candidate_count": int(len(candidates)),
                "normalized_workflow_count": int(len(blueprints)),
                "rejected_workflow_count": int(len(workflow_rejections)),
                "workflow_rejections": workflow_rejections,
                "workflow_rejection_reasons": [
                    f"workflow_{item.get('workflow_index')}:{reason}"
                    for item in workflow_rejections
                    for reason in (item.get("reasons") or [])
                ],
                "typed_state_rejections": typed_state_rejections[:64],
                "workflow_consistency_rejections": workflow_consistency_rejections[:64],
            }
        )
    return blueprints


def _extract_requirement_understanding_stats(requirement: str) -> dict[str, Any]:
    marker = "[Requirement Understanding]"
    text = _text(requirement)
    marker_index = text.find(marker)
    if marker_index < 0:
        return {"requirement_understanding_used": False}
    section = text[marker_index + len(marker) :].strip()
    next_section = section.find("\n\n[")
    if next_section >= 0:
        section = section[:next_section].strip()
    stats: dict[str, Any] = {"requirement_understanding_used": True}
    try:
        payload = json.loads(section)
    except Exception:
        payload = {}
    if isinstance(payload, dict):
        stats.update(
            {
                "requirement_understanding_visual_fact_count": int(payload.get("visual_fact_count") or 0),
                "requirement_understanding_invalid_visual_block_count": int(
                    payload.get("invalid_visual_block_count") or 0
                ),
            }
        )
    return stats


def _canonical_workflow_declaration(payload: Any) -> tuple[bool, Any]:
    contract = _contract_payload(payload)
    if "workflow_blueprints" not in contract:
        return False, None
    return True, contract.get("workflow_blueprints")


def _is_live_v2_graph_candidate(payload: Any) -> bool:
    contract = _contract_payload(payload)
    return bool(
        contract.get("semantic_contract_version")
        == REQUIREMENT_SEMANTIC_CONTRACT_VERSION
        and isinstance(contract.get("evidence_facts"), list)
        and isinstance(contract.get("semantic_graph"), dict)
    )


def _graph_workflow_consistency_rejections(
    payload: Any,
    semantic_contract: dict[str, Any],
) -> list[dict[str, Any]]:
    """校验工作流确实是语义图中的一条可执行路径，而不是第二份自由声明。"""

    contract = _contract_payload(payload)
    workflows = contract.get("workflow_blueprints")
    if not isinstance(workflows, list):
        return []
    graph = dict(semantic_contract.get("semantic_graph") or {})
    graph_validation = dict(
        semantic_contract.get("semantic_graph_validation") or {}
    )
    graph_diagnostics = dict(graph_validation.get("diagnostics") or {})
    workflow_topology_status = _text(
        graph_diagnostics.get("workflow_topology_status")
    )
    workflow_topology_error_codes = sorted(
        {
            _text(item)
            for item in (
                graph_diagnostics.get("workflow_topology_error_codes") or []
            )
            if _text(item)
        }
    )
    nodes = [
        dict(item)
        for item in (graph.get("nodes") or [])
        if isinstance(item, dict) and _text(item.get("node_id"))
    ]
    edges = [
        dict(item)
        for item in (graph.get("edges") or [])
        if isinstance(item, dict) and _text(item.get("edge_id"))
    ]
    facts = {
        _text(item.get("fact_id"))
        for item in (semantic_contract.get("evidence_facts") or [])
        if isinstance(item, dict) and _text(item.get("fact_id"))
    }
    nodes_by_id = {_text(item.get("node_id")): item for item in nodes}
    edges_by_id = {_text(item.get("edge_id")): item for item in edges}
    primary_flow_value = graph.get("primary_flow")
    primary_flow = (
        dict(primary_flow_value) if isinstance(primary_flow_value, dict) else {}
    )
    primary_flow_node_ids = [
        _text(item)
        for item in (primary_flow.get("node_ids") or [])
        if _text(item)
    ]
    primary_flow_edge_ids = [
        _text(item)
        for item in (primary_flow.get("edge_ids") or [])
        if _text(item)
    ]
    primary_flow_edge_id_set = set(primary_flow_edge_ids)
    owner_scope_ids_by_node: dict[str, set[str]] = {}
    interaction_scope_ids_by_edge: dict[str, set[str]] = {}
    for edge in edges:
        edge_type = _text(edge.get("type"))
        if edge_type == "interacts_with":
            scope_ids = {
                _text(edge.get("source_scope_id")),
                _text(edge.get("target_scope_id")),
            }
            if not all(scope_ids):
                scope_ids = {
                    endpoint_id
                    for endpoint_id in (
                        _text(edge.get("source_node_id")),
                        _text(edge.get("target_node_id")),
                    )
                    if _text((nodes_by_id.get(endpoint_id) or {}).get("kind"))
                    == "scope"
                }
            scope_ids.discard("")
            interaction_scope_ids_by_edge[_text(edge.get("edge_id"))] = scope_ids
        if edge_type != "owns":
            continue
        source_node_id = _text(edge.get("source_node_id"))
        target_node_id = _text(edge.get("target_node_id"))
        if (
            _text((nodes_by_id.get(source_node_id) or {}).get("kind")) == "scope"
            and target_node_id
        ):
            owner_scope_ids_by_node.setdefault(target_node_id, set()).add(
                source_node_id
            )

    def _is_primary_required_control_edge(edge: dict[str, Any]) -> bool:
        if (
            _text(edge.get("type")) not in {"triggers", "transitions"}
            or edge.get("required") is not True
        ):
            return False
        source = nodes_by_id.get(_text(edge.get("source_node_id"))) or {}
        target = nodes_by_id.get(_text(edge.get("target_node_id"))) or {}
        if source.get("required") is not True or target.get("required") is not True:
            return False
        return _text(edge.get("edge_id")) in primary_flow_edge_id_set

    def _binding_scope_ids(node_id: str) -> set[str]:
        """仅解析节点的直接职责范围；控制边不会转移模块归属。"""

        scope_ids = set(owner_scope_ids_by_node.get(node_id) or set())
        node = nodes_by_id.get(node_id) or {}
        if (
            _text(node.get("kind")) == "scope"
            and _text(node.get("scope_status")) == "in_scope"
        ):
            scope_ids.add(node_id)
        if scope_ids:
            return scope_ids

        # 非 capability 流程节点没有 owns；仅从 required 主链方向上的相邻 capability
        # 继承唯一职责范围。跨范围或 optional 邻接保持不确定并在后续 fail-close。
        workflow_role = _text(node.get("workflow_role"))
        node_kind = _text(node.get("kind"))
        if node.get("required") is not True or workflow_role == "none":
            return scope_ids
        inherited_scope_ids: set[str] = set()
        for edge in edges:
            edge_type = _text(edge.get("type"))
            if not _is_primary_required_control_edge(edge):
                continue
            if node_kind == "trigger" and edge_type != "triggers":
                continue
            source_node_id = _text(edge.get("source_node_id"))
            target_node_id = _text(edge.get("target_node_id"))
            neighbor_node_id = ""
            if node_kind == "trigger":
                if source_node_id != node_id:
                    continue
                neighbor_node_id = target_node_id
            elif workflow_role == "entry" and source_node_id == node_id:
                neighbor_node_id = target_node_id
            elif workflow_role == "terminal" and target_node_id == node_id:
                neighbor_node_id = source_node_id
            elif workflow_role == "intermediate":
                if source_node_id == node_id:
                    neighbor_node_id = target_node_id
                elif target_node_id == node_id:
                    neighbor_node_id = source_node_id
            neighbor_node = nodes_by_id.get(neighbor_node_id) or {}
            if (
                neighbor_node.get("required") is not True
                or _text(neighbor_node.get("kind")) != "capability"
            ):
                continue
            inherited_scope_ids.update(
                owner_scope_ids_by_node.get(neighbor_node_id) or set()
            )
        if len(inherited_scope_ids) == 1:
            scope_ids.update(inherited_scope_ids)
        return scope_ids
    flow_nodes = {
        node_id: nodes_by_id[node_id]
        for node_id in primary_flow_node_ids
        if node_id in nodes_by_id
    }
    required_flow_node_ids = {
        node_id for node_id, node in flow_nodes.items() if node.get("required") is True
    }
    required_transition_pairs = {
        (_text(edge.get("source_node_id")), _text(edge.get("target_node_id")))
        for edge in edges
        if _is_primary_required_control_edge(edge)
    }
    required_transition_edge_ids_by_pair: dict[tuple[str, str], set[str]] = {}
    for edge in edges:
        if _text(edge.get("type")) not in {"triggers", "transitions"}:
            continue
        pair = (
            _text(edge.get("source_node_id")),
            _text(edge.get("target_node_id")),
        )
        if _is_primary_required_control_edge(edge):
            required_transition_edge_ids_by_pair.setdefault(pair, set()).add(
                _text(edge.get("edge_id"))
            )
    rejections: list[dict[str, Any]] = []
    rejections.extend(
        graph_typed_state_identity_rejections(workflows, semantic_contract)
    )

    if workflow_topology_status == "declaration_invalid":
        rejections.append(
            {
                "reason": "workflow_topology_declaration_invalid",
                "field_path": "$.semantic_graph.nodes",
                "workflow_topology_error_codes": workflow_topology_error_codes,
            }
        )
        return rejections

    if workflow_topology_status == "not_linearizable":
        rejections.append(
            {
                "reason": (
                    "workflow_forbidden_by_invalid_primary_flow"
                    if workflows
                    else "primary_flow_declaration_invalid"
                ),
                "field_path": "$.semantic_graph.primary_flow",
                "workflow_topology_error_codes": workflow_topology_error_codes,
            }
        )
        return rejections

    if workflows and not primary_flow_node_ids:
        rejections.append(
            {
                "reason": "workflow_forbidden_without_primary_flow",
                "field_path": "$.workflow_blueprints",
            }
        )
        return rejections

    if primary_flow_node_ids and len(workflows) != 1:
        rejections.append(
            {
                "reason": "primary_flow_requires_exactly_one_workflow",
                "field_path": "$.workflow_blueprints",
                "declared_workflow_count": len(workflows),
            }
        )
        return rejections

    if not workflows and required_flow_node_ids:
        rejections.append(
            {
                "reason": "workflow_required_by_graph",
                "field_path": "$.workflow_blueprints",
                "required_graph_node_ids": sorted(required_flow_node_ids),
            }
        )
        return rejections

    for workflow_index, workflow in enumerate(workflows, start=1):
        if not isinstance(workflow, dict) or not isinstance(workflow.get("steps"), list):
            continue
        mapped_node_ids: list[str] = []
        mapped_required_node_ids: set[str] = set()
        step_relation_id_sets: list[set[str]] = []
        steps = workflow.get("steps") or []
        for step_index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                mapped_node_ids.append("")
                step_relation_id_sets.append(set())
                continue
            step_path = f"$.workflow_blueprints[{workflow_index - 1}].steps[{step_index - 1}]"
            fact_ids = _list_text(step.get("fact_ids"), limit=32)
            unknown_fact_ids = sorted(set(fact_ids) - facts)
            if not fact_ids or unknown_fact_ids:
                rejections.append(
                    {
                        "workflow_index": workflow_index,
                        "step_index": step_index,
                        "reason": "graph_step_fact_ids_invalid",
                        "field_path": f"{step_path}.fact_ids",
                        "unknown_fact_ids": unknown_fact_ids,
                    }
                )
            relation_ids = _list_text(step.get("relation_ids"), limit=32)
            step_relation_id_sets.append(set(relation_ids))
            unknown_relation_ids = sorted(set(relation_ids) - set(edges_by_id))
            if not isinstance(step.get("relation_ids"), list) or unknown_relation_ids:
                rejections.append(
                    {
                        "workflow_index": workflow_index,
                        "step_index": step_index,
                        "reason": "graph_step_relation_ids_invalid",
                        "field_path": f"{step_path}.relation_ids",
                        "unknown_relation_ids": unknown_relation_ids,
                    }
                )
            scope_candidates = step.get("scope_candidates")
            invalid_scope_ids: list[str] = []
            candidate_scope_ids: set[str] = set()
            candidate_fact_ids_by_scope: dict[str, set[str]] = {}
            candidate_roles_by_scope: dict[str, set[str]] = {}
            if isinstance(scope_candidates, list):
                for item in scope_candidates:
                    if not isinstance(item, dict):
                        invalid_scope_ids.append("<non_object>")
                        continue
                    scope_id = _text(item.get("scope_id"))
                    scope_node = nodes_by_id.get(scope_id) or {}
                    if (
                        not scope_id
                        or _text(scope_node.get("kind")) != "scope"
                        or _text(scope_node.get("scope_status")) != "in_scope"
                    ):
                        invalid_scope_ids.append(scope_id or "<missing>")
                        continue
                    candidate_scope_ids.add(scope_id)
                    role = _text(item.get("role"))
                    if role:
                        candidate_roles_by_scope.setdefault(scope_id, set()).add(role)
                    candidate_fact_ids_by_scope.setdefault(scope_id, set()).update(
                        _list_text(item.get("fact_ids"), limit=32)
                    )
                invalid_scope_ids = sorted(set(invalid_scope_ids))
            if not isinstance(scope_candidates, list) or not scope_candidates or invalid_scope_ids:
                rejections.append(
                    {
                        "workflow_index": workflow_index,
                        "step_index": step_index,
                        "reason": "graph_step_scope_candidates_invalid",
                        "field_path": f"{step_path}.scope_candidates",
                        "invalid_scope_ids": invalid_scope_ids,
                    }
                )

            matching_flow_nodes = sorted(
                node_id
                for node_id, node in flow_nodes.items()
                if set(fact_ids) & set(node.get("fact_ids") or [])
            )
            if len(matching_flow_nodes) != 1:
                rejections.append(
                    {
                        "workflow_index": workflow_index,
                        "step_index": step_index,
                        "reason": "graph_step_flow_node_unresolved",
                        "field_path": f"{step_path}.fact_ids",
                        "matching_graph_node_ids": matching_flow_nodes,
                    }
                )
                mapped_node_ids.append("")
                continue
            node_id = matching_flow_nodes[0]
            mapped_node_ids.append(node_id)
            graph_node = flow_nodes[node_id]
            if required_flow_node_ids and graph_node.get("required") is not True:
                rejections.append(
                    {
                        "workflow_index": workflow_index,
                        "step_index": step_index,
                        "reason": "graph_optional_node_in_required_workflow",
                        "field_path": f"{step_path}.fact_ids",
                        "graph_node_id": node_id,
                    }
                )
            referenced_edges = [
                edges_by_id[relation_id]
                for relation_id in relation_ids
                if relation_id in edges_by_id
            ]
            non_primary_control_relation_ids = sorted(
                _text(edge.get("edge_id"))
                for edge in referenced_edges
                if _text(edge.get("type")) in {"triggers", "transitions"}
                and _text(edge.get("edge_id")) not in primary_flow_edge_id_set
            )
            if non_primary_control_relation_ids:
                rejections.append(
                    {
                        "workflow_index": workflow_index,
                        "step_index": step_index,
                        "reason": "graph_step_non_primary_control_relation",
                        "field_path": f"{step_path}.relation_ids",
                        "non_primary_relation_ids": (
                            non_primary_control_relation_ids
                        ),
                    }
                )
            owner_scope_ids = _binding_scope_ids(node_id)
            supported_scope_ids = set(owner_scope_ids)

            def _interaction_applies_to_step(edge: dict[str, Any]) -> bool:
                edge_id = _text(edge.get("edge_id"))
                if _text(edge.get("type")) != "interacts_with":
                    return False
                interaction_scope_ids = set(
                    interaction_scope_ids_by_edge.get(edge_id) or set()
                )
                endpoint_ids = {
                    _text(edge.get("source_node_id")),
                    _text(edge.get("target_node_id")),
                }
                return bool(
                    owner_scope_ids & interaction_scope_ids
                    and (
                        node_id in endpoint_ids
                        or bool(owner_scope_ids & endpoint_ids)
                    )
                )

            step_interaction_edges: list[dict[str, Any]] = []
            for edge in referenced_edges:
                edge_id = _text(edge.get("edge_id"))
                if not _interaction_applies_to_step(edge):
                    continue
                step_interaction_edges.append(edge)
                supported_scope_ids.update(
                    interaction_scope_ids_by_edge.get(edge_id) or set()
                )
            step_interaction_edge_ids = {
                _text(edge.get("edge_id")) for edge in step_interaction_edges
            }
            binding_interaction_edges = list(step_interaction_edges)
            direction_rejection_edge_ids: set[str] = set()

            def _append_interaction_direction_rejection(
                edge: dict[str, Any],
            ) -> None:
                edge_id = _text(edge.get("edge_id"))
                if edge_id in direction_rejection_edge_ids:
                    return
                source_scope_id = _text(edge.get("source_scope_id"))
                target_scope_id = _text(edge.get("target_scope_id"))
                role_mismatches: list[dict[str, Any]] = []
                for scope_id, expected_role in (
                    (source_scope_id, "source"),
                    (target_scope_id, "target"),
                ):
                    if not scope_id or expected_role in candidate_roles_by_scope.get(
                        scope_id, set()
                    ):
                        continue
                    role_mismatches.append(
                        {
                            "module_key": scope_id,
                            "expected_role": expected_role,
                            "declared_roles": sorted(
                                candidate_roles_by_scope.get(scope_id, set())
                            ),
                        }
                    )
                if role_mismatches:
                    direction_rejection_edge_ids.add(edge_id)
                    rejections.append(
                        {
                            "workflow_index": workflow_index,
                            "step_index": step_index,
                            "reason": "interaction_direction_roles_mismatch",
                            "interaction_id": edge_id,
                            "source_module_key": source_scope_id,
                            "target_module_key": target_scope_id,
                            "role_mismatches": role_mismatches,
                            "candidate_field": "scope_candidates",
                        }
                    )

            for edge in step_interaction_edges:
                _append_interaction_direction_rejection(edge)
            typed_state_fact_ids = {
                fact_id
                for collection in ("required_states", "produced_states")
                for state in (step.get(collection) or [])
                if isinstance(state, dict)
                for fact_id in _list_text(state.get("fact_ids"), limit=32)
            }
            if owner_scope_ids and not (owner_scope_ids & candidate_scope_ids):
                rejections.append(
                    {
                        "workflow_index": workflow_index,
                        "step_index": step_index,
                        "reason": "graph_step_owner_scope_missing",
                        "field_path": f"{step_path}.scope_candidates",
                        "graph_node_id": node_id,
                        "expected_scope_ids": sorted(owner_scope_ids),
                        "declared_scope_ids": sorted(candidate_scope_ids),
                    }
                )
            source_scope_ids = {
                scope_id
                for scope_id, roles in candidate_roles_by_scope.items()
                if "source" in roles
            }
            target_scope_ids = {
                scope_id
                for scope_id, roles in candidate_roles_by_scope.items()
                if "target" in roles
            }
            remote_scope_ids = candidate_scope_ids - owner_scope_ids
            for source_scope_id in sorted(source_scope_ids):
                for target_scope_id in sorted(target_scope_ids):
                    pair_scope_ids = {source_scope_id, target_scope_id}
                    if (
                        source_scope_id == target_scope_id
                        or not (pair_scope_ids & owner_scope_ids)
                        or not (pair_scope_ids & remote_scope_ids)
                    ):
                        continue
                    referenced_pair_edges = [
                        edge
                        for edge in step_interaction_edges
                        if set(
                            interaction_scope_ids_by_edge.get(
                                _text(edge.get("edge_id")), set()
                            )
                        )
                        == pair_scope_ids
                    ]
                    if referenced_pair_edges:
                        continue
                    pair_interaction_edges = [
                        edge
                        for edge in edges
                        if _interaction_applies_to_step(edge)
                        and set(
                            interaction_scope_ids_by_edge.get(
                                _text(edge.get("edge_id")), set()
                            )
                        )
                        == pair_scope_ids
                    ]
                    declared_direction_edges = [
                        edge
                        for edge in pair_interaction_edges
                        if _text(edge.get("source_scope_id")) == source_scope_id
                        and _text(edge.get("target_scope_id")) == target_scope_id
                    ]
                    expected_edges = declared_direction_edges or pair_interaction_edges
                    if pair_interaction_edges:
                        supported_scope_ids.update(pair_scope_ids)
                        known_binding_edge_ids = {
                            _text(edge.get("edge_id"))
                            for edge in binding_interaction_edges
                        }
                        binding_interaction_edges.extend(
                            edge
                            for edge in pair_interaction_edges
                            if _text(edge.get("edge_id"))
                            not in known_binding_edge_ids
                        )
                    if pair_interaction_edges and not declared_direction_edges:
                        for edge in pair_interaction_edges:
                            _append_interaction_direction_rejection(edge)
                    matching_interaction_ids = sorted(
                        _text(edge.get("edge_id")) for edge in expected_edges
                    )
                    missing_interaction = {
                        "workflow_index": workflow_index,
                        "step_index": step_index,
                        "reason": "cross_module_interaction_id_missing",
                        "declared_module_keys": sorted(candidate_scope_ids),
                        "source_module_key": source_scope_id,
                        "target_module_key": target_scope_id,
                        "expected_interaction_ids": matching_interaction_ids,
                    }
                    if not matching_interaction_ids:
                        missing_interaction.update(
                            {
                                "field_path": "$.semantic_graph.edges",
                                "source_node_id": source_scope_id,
                                "target_node_id": target_scope_id,
                            }
                        )
                    rejections.append(missing_interaction)
            unsupported_scope_ids = sorted(
                candidate_scope_ids - supported_scope_ids
            )
            if unsupported_scope_ids:
                rejections.append(
                    {
                        "workflow_index": workflow_index,
                        "step_index": step_index,
                        "reason": "graph_step_scope_binding_unsupported",
                        "field_path": f"{step_path}.scope_candidates",
                        "graph_node_id": node_id,
                        "unsupported_scope_ids": unsupported_scope_ids,
                        "declared_values": sorted(candidate_scope_ids),
                        "expected_values": sorted(supported_scope_ids),
                    }
                )
            relevant_endpoint_ids = {node_id, *candidate_scope_ids}
            irrelevant_relation_ids = sorted(
                _text(edge.get("edge_id"))
                for edge in referenced_edges
                if not (
                    (
                        _text(edge.get("type")) == "interacts_with"
                        and _text(edge.get("edge_id"))
                        in step_interaction_edge_ids
                    )
                    or (
                        _text(edge.get("type")) != "interacts_with"
                        and (
                            {
                                _text(edge.get("source_node_id")),
                                _text(edge.get("target_node_id")),
                                _text(edge.get("source_scope_id")),
                                _text(edge.get("target_scope_id")),
                            }
                            & relevant_endpoint_ids
                            or set(edge.get("fact_ids") or [])
                            & typed_state_fact_ids
                        )
                    )
                )
            )
            if irrelevant_relation_ids:
                rejections.append(
                    {
                        "workflow_index": workflow_index,
                        "step_index": step_index,
                        "reason": "graph_step_relation_unrelated",
                        "field_path": f"{step_path}.relation_ids",
                        "graph_node_id": node_id,
                        "irrelevant_relation_ids": irrelevant_relation_ids,
                        "declared_values": irrelevant_relation_ids,
                    }
                )
            step_fact_id_set = set(fact_ids)
            for scope_id, candidate_fact_ids in sorted(
                candidate_fact_ids_by_scope.items()
            ):
                supported_scope_fact_ids: set[str] = set()
                if scope_id in owner_scope_ids:
                    supported_scope_fact_ids.update(
                        (nodes_by_id.get(scope_id) or {}).get("fact_ids") or []
                    )
                    supported_scope_fact_ids.update(graph_node.get("fact_ids") or [])
                    for edge in edges:
                        if _text(edge.get("type")) == "owns" and (
                            _text(edge.get("source_node_id")) == scope_id
                            and _text(edge.get("target_node_id")) == node_id
                        ):
                            supported_scope_fact_ids.update(
                                edge.get("fact_ids") or []
                            )
                for edge in binding_interaction_edges:
                    if scope_id in interaction_scope_ids_by_edge.get(
                        _text(edge.get("edge_id")), set()
                    ):
                        supported_scope_fact_ids.update(edge.get("fact_ids") or [])
                invalid_candidate_fact_ids = sorted(
                    fact_id
                    for fact_id in candidate_fact_ids
                    if fact_id not in step_fact_id_set
                    or fact_id not in supported_scope_fact_ids
                )
                if not candidate_fact_ids or invalid_candidate_fact_ids:
                    rejections.append(
                        {
                            "workflow_index": workflow_index,
                            "step_index": step_index,
                            "reason": "graph_step_scope_fact_binding_invalid",
                            "field_path": f"{step_path}.scope_candidates",
                            "graph_node_id": node_id,
                            "scope_id": scope_id,
                            "invalid_fact_ids": invalid_candidate_fact_ids,
                        }
                    )
            if step.get("required") is True:
                mapped_required_node_ids.add(node_id)
            expected_terminal = _text(graph_node.get("workflow_role")) == "terminal"
            if bool(step.get("terminal")) != expected_terminal:
                rejections.append(
                    {
                        "workflow_index": workflow_index,
                        "step_index": step_index,
                        "reason": "graph_step_terminal_role_mismatch",
                        "field_path": f"{step_path}.terminal",
                        "graph_node_id": node_id,
                    }
                )

        if mapped_node_ids != primary_flow_node_ids:
            rejections.append(
                {
                    "workflow_index": workflow_index,
                    "reason": "graph_workflow_primary_flow_order_mismatch",
                    "field_path": f"$.workflow_blueprints[{workflow_index - 1}].steps",
                    "expected_graph_node_ids": primary_flow_node_ids,
                    "declared_graph_node_ids": mapped_node_ids,
                }
            )
        if mapped_node_ids:
            first_node = flow_nodes.get(mapped_node_ids[0]) or {}
            last_node = flow_nodes.get(mapped_node_ids[-1]) or {}
            if _text(first_node.get("workflow_role")) != "entry":
                rejections.append(
                    {
                        "workflow_index": workflow_index,
                        "reason": "graph_workflow_entry_mismatch",
                        "field_path": f"$.workflow_blueprints[{workflow_index - 1}].steps[0].fact_ids",
                    }
                )
            if _text(last_node.get("workflow_role")) != "terminal":
                rejections.append(
                    {
                        "workflow_index": workflow_index,
                        "reason": "graph_workflow_terminal_mismatch",
                        "field_path": (
                            f"$.workflow_blueprints[{workflow_index - 1}].steps[{len(steps) - 1}].fact_ids"
                        ),
                    }
                )
        for step_index, (source_node_id, target_node_id) in enumerate(
            zip(mapped_node_ids, mapped_node_ids[1:]),
            start=1,
        ):
            if not source_node_id or not target_node_id:
                continue
            if (source_node_id, target_node_id) not in required_transition_pairs:
                rejections.append(
                    {
                        "workflow_index": workflow_index,
                        "step_index": step_index + 1,
                        "reason": "graph_workflow_transition_missing",
                        "field_path": (
                            f"$.workflow_blueprints[{workflow_index - 1}].steps[{step_index}].fact_ids"
                        ),
                        "source_graph_node_id": source_node_id,
                        "target_graph_node_id": target_node_id,
                        "required_transition": True,
                    }
                )
                continue
            transition_edge_ids = required_transition_edge_ids_by_pair.get(
                (source_node_id, target_node_id),
                set(),
            )
            adjacent_relation_ids = (
                step_relation_id_sets[step_index - 1]
                | step_relation_id_sets[step_index]
            )
            missing_transition_edge_ids = sorted(
                transition_edge_ids - adjacent_relation_ids
            )
            if missing_transition_edge_ids:
                rejections.append(
                    {
                        "workflow_index": workflow_index,
                        "step_index": step_index + 1,
                        "reason": "graph_workflow_transition_relation_unreferenced",
                        "field_path": (
                            f"$.workflow_blueprints[{workflow_index - 1}].steps[{step_index}].relation_ids"
                        ),
                        "source_graph_node_id": source_node_id,
                        "target_graph_node_id": target_node_id,
                        "expected_relation_ids": sorted(transition_edge_ids),
                        "missing_relation_ids": missing_transition_edge_ids,
                        "required_transition": True,
                    }
                )
        missing_required_node_ids = sorted(
            required_flow_node_ids - mapped_required_node_ids
        )
        if missing_required_node_ids:
            rejections.append(
                {
                    "workflow_index": workflow_index,
                    "reason": "graph_required_flow_nodes_missing",
                    "field_path": f"$.workflow_blueprints[{workflow_index - 1}].steps",
                    "missing_graph_node_ids": missing_required_node_ids,
                }
            )
    return rejections


def _verified_functional_module_count(semantic_contract: dict[str, Any]) -> int:
    architecture = semantic_contract.get("functional_architecture")
    if not isinstance(architecture, dict):
        return 0
    return sum(
        1
        for item in (architecture.get("functional_modules") or [])
        if isinstance(item, dict)
        and item.get("scope_status") != "out_of_scope"
        and item.get("evidence_verified") is True
    )


def _evaluate_parsed_semantic_candidate(
    parsed: dict[str, Any],
    *,
    evidence_source: str,
    project_id: int | None,
    user_id: int | None,
) -> dict[str, Any]:
    live_contract = _contract_payload(parsed)
    if not _is_live_v2_graph_candidate(live_contract):
        version = _text(live_contract.get("semantic_contract_version"))
        graph_rejections: list[dict[str, Any]] = []
        if version != REQUIREMENT_SEMANTIC_CONTRACT_VERSION:
            graph_rejections.append(
                {
                    "reason": "semantic_contract_version_mismatch",
                    "path": "$.semantic_contract_version",
                    "expected": REQUIREMENT_SEMANTIC_CONTRACT_VERSION,
                    "actual": version,
                }
            )
        if not isinstance(live_contract.get("evidence_facts"), list):
            graph_rejections.append(
                {
                    "reason": "evidence_facts_missing_or_invalid",
                    "path": "$.evidence_facts",
                }
            )
        if not isinstance(live_contract.get("semantic_graph"), dict):
            graph_rejections.append(
                {
                    "reason": "semantic_graph_missing_or_invalid",
                    "path": "$.semantic_graph",
                }
            )
        return {
            "valid": False,
            "status": WORKFLOW_DECLARATION_INVALID,
            "workflow_declaration_status": WORKFLOW_DECLARATION_INVALID,
            "blueprints": [],
            "semantic_contract": empty_requirement_semantic_contract(
                status="live_v2_graph_contract_required"
            ),
            "normalization_diagnostics": {
                "workflow_rejection_reasons": [
                    str(item.get("reason") or "live_v2_graph_contract_required")
                    for item in graph_rejections
                ],
                "semantic_graph_rejections": graph_rejections,
            },
        }
    # A1 已经基于完整 source catalog 验证并冻结事实证据。B 阶段只规范化
    # Graph/workflow，不再用压缩后的 evidence_source 删除冻结事实；调用方随后
    # 会对 evidence_facts 做完整等值校验，模型仍无权增删或改写事实。
    frozen_fact_evidence_validator = lambda evidence, _source: bool(evidence)
    declaration_present, raw_workflow_declaration = _canonical_workflow_declaration(parsed)
    normalization_diagnostics: dict[str, Any] = {}
    if not declaration_present:
        semantic_contract = normalize_requirement_semantic_contract(
            _contract_payload(parsed),
            requirement_text=evidence_source,
            workflow_blueprints=[],
            evidence_validator=frozen_fact_evidence_validator,
        )
        return {
            "valid": False,
            "status": WORKFLOW_DECLARATION_MISSING,
            "workflow_declaration_status": WORKFLOW_DECLARATION_MISSING,
            "blueprints": [],
            "semantic_contract": semantic_contract,
            "normalization_diagnostics": {
                "workflow_rejection_reasons": ["workflow_declaration_missing"],
            },
        }
    if not isinstance(raw_workflow_declaration, list):
        semantic_contract = normalize_requirement_semantic_contract(
            _contract_payload(parsed),
            requirement_text=evidence_source,
            workflow_blueprints=[],
            evidence_validator=frozen_fact_evidence_validator,
        )
        return {
            "valid": False,
            "status": WORKFLOW_DECLARATION_INVALID,
            "workflow_declaration_status": WORKFLOW_DECLARATION_INVALID,
            "blueprints": [],
            "semantic_contract": semantic_contract,
            "normalization_diagnostics": {
                "workflow_rejection_reasons": ["workflow_blueprints_not_list"],
            },
        }

    graph_declared = "evidence_facts" in parsed or "semantic_graph" in parsed
    graph_contract: dict[str, Any] = {}
    if graph_declared:
        graph_contract = normalize_requirement_semantic_contract(
            _contract_payload(parsed),
            requirement_text=evidence_source,
            workflow_blueprints=raw_workflow_declaration,
            evidence_validator=frozen_fact_evidence_validator,
        )
        graph_validation = dict(
            graph_contract.get("semantic_graph_validation") or {}
        )
        graph_diagnostics = dict(graph_validation.get("diagnostics") or {})
        workflow_topology_rejections = [
            dict(item)
            for item in (graph_diagnostics.get("workflow_topology_errors") or [])
            if isinstance(item, dict)
        ]
        if graph_validation.get("publishable") is not True:
            graph_rejections = [
                dict(item)
                for item in (graph_validation.get("errors") or [])
                if isinstance(item, dict)
            ]
            return {
                "valid": False,
                "status": WORKFLOW_DECLARATION_INVALID,
                "workflow_declaration_status": WORKFLOW_DECLARATION_INVALID,
                "blueprints": [],
                "semantic_contract": graph_contract,
                "normalization_diagnostics": {
                    "workflow_rejection_reasons": ["semantic_graph_invalid"],
                    "semantic_graph_rejections": graph_rejections[:128],
                    "semantic_graph_diagnostics": dict(
                        graph_validation.get("diagnostics") or {}
                    ),
                },
            }
        normalization_diagnostics["semantic_graph_diagnostics"] = graph_diagnostics
        graph_workflow_rejections = _graph_workflow_consistency_rejections(
            parsed,
            graph_contract,
        )
        if graph_workflow_rejections:
            return {
                "valid": False,
                "status": WORKFLOW_DECLARATION_INVALID,
                "workflow_declaration_status": WORKFLOW_DECLARATION_INVALID,
                "blueprints": [],
                "semantic_contract": graph_contract,
                "normalization_diagnostics": {
                    "workflow_rejection_reasons": list(
                        dict.fromkeys(
                            str(item.get("reason") or "graph_workflow_invalid")
                            for item in graph_workflow_rejections
                        )
                    ),
                    "workflow_consistency_rejections": graph_workflow_rejections[:128],
                    "semantic_graph_rejections": workflow_topology_rejections[:128],
                    "semantic_graph_diagnostics": graph_diagnostics,
                },
            }

    blueprints = normalize_current_requirement_blueprint_payload(
        parsed,
        requirement_text=evidence_source,
        project_id=project_id,
        user_id=user_id,
        normalization_diagnostics=normalization_diagnostics,
        evidence_validator=(
            frozen_fact_evidence_validator if graph_declared else None
        ),
    )
    raw_workflow_count = int(len(raw_workflow_declaration))
    normalized_workflow_count = int(len(blueprints))
    rejected_workflow_count = int(normalization_diagnostics.get("rejected_workflow_count") or 0)
    if graph_declared:
        semantic_contract = dict(graph_contract)
        semantic_contract["workflow_blueprints"] = (
            [dict(item) for item in blueprints]
            if not rejected_workflow_count
            else []
        )
    else:
        semantic_contract = normalize_requirement_semantic_contract(
            _contract_payload(parsed),
            requirement_text=evidence_source,
            workflow_blueprints=blueprints if not rejected_workflow_count else [],
        )
    verified_module_count = _verified_functional_module_count(semantic_contract)
    normalization_diagnostics.update(
        {
            "raw_workflow_candidate_count": raw_workflow_count,
            "normalized_workflow_count": normalized_workflow_count,
            "rejected_workflow_count": rejected_workflow_count,
            "verified_functional_module_count": verified_module_count,
        }
    )
    if raw_workflow_count and (
        rejected_workflow_count
        or normalized_workflow_count != raw_workflow_count
    ):
        return {
            "valid": False,
            "status": WORKFLOW_DECLARATION_INVALID,
            "workflow_declaration_status": WORKFLOW_DECLARATION_INVALID,
            "blueprints": [],
            "semantic_contract": semantic_contract,
            "normalization_diagnostics": normalization_diagnostics,
        }
    if not raw_workflow_count and verified_module_count < 1:
        normalization_diagnostics["workflow_rejection_reasons"] = list(
            dict.fromkeys(
                [
                    *(normalization_diagnostics.get("workflow_rejection_reasons") or []),
                    "independent_only_requires_verified_module",
                ]
            )
        )
        return {
            "valid": False,
            "status": "no_semantic_candidate",
            "workflow_declaration_status": WORKFLOW_DECLARATION_INVALID,
            "blueprints": [],
            "semantic_contract": semantic_contract,
            "normalization_diagnostics": normalization_diagnostics,
        }
    return {
        "valid": True,
        "status": WORKFLOW_DECLARATION_APPLIED if blueprints else WORKFLOW_DECLARATION_INDEPENDENT_ONLY,
        "workflow_declaration_status": (
            WORKFLOW_DECLARATION_APPLIED if blueprints else WORKFLOW_DECLARATION_INDEPENDENT_ONLY
        ),
        "blueprints": blueprints,
        "semantic_contract": semantic_contract,
        "normalization_diagnostics": normalization_diagnostics,
    }


def _evaluation_graph_diagnostics(evaluation: Any) -> dict[str, Any]:
    """统一读取本轮结构化图诊断，避免从截断的错误列表推断拓扑。"""

    data = dict(evaluation or {}) if isinstance(evaluation, dict) else {}
    semantic_contract = dict(data.get("semantic_contract") or {})
    graph_validation = dict(
        semantic_contract.get("semantic_graph_validation") or {}
    )
    graph_diagnostics = dict(graph_validation.get("diagnostics") or {})
    if graph_diagnostics:
        return graph_diagnostics
    normalization = dict(data.get("normalization_diagnostics") or {})
    return dict(normalization.get("semantic_graph_diagnostics") or {})


def _evaluation_unrepairable_topology_codes(evaluation: Any) -> list[str]:
    graph_diagnostics = _evaluation_graph_diagnostics(evaluation)
    if "workflow_topology_error_codes" in graph_diagnostics:
        topology_codes = graph_diagnostics.get("workflow_topology_error_codes") or []
    else:
        # 兼容尚未携带 workflow_topology_* 的旧诊断，但不扩大错误集合。
        topology_codes = (
            graph_diagnostics.get("unrepairable_required_component_error_codes")
            or []
        )
    return sorted(
        {
            _text(item)
            for item in topology_codes
            if _text(item) in UNREPAIRABLE_PRIMARY_FLOW_ERROR_CODES
        }
    )


def _evaluation_structural_recompile_codes(evaluation: Any) -> list[str]:
    """读取无法在稳定节点/边身份内局部修复的结构错误。"""

    graph_diagnostics = _evaluation_graph_diagnostics(evaluation)
    return sorted(
        {
            _text(item)
            for item in (
                graph_diagnostics.get("structural_recompile_error_codes") or []
            )
            if _text(item)
        }
    )


def _evaluation_independent_recompile_codes(evaluation: Any) -> list[str]:
    return sorted(
        {
            *_evaluation_unrepairable_topology_codes(evaluation),
            *_evaluation_structural_recompile_codes(evaluation),
        }
    )


def _revalidate_existing_semantic_contract(
    contract: Any,
    *,
    requirement_text: str,
) -> dict[str, Any]:
    """复用前重跑当前校验器，旧成功标志不能替代当前契约有效性。"""

    data = dict(contract or {}) if isinstance(contract, dict) else {}
    rejection_reasons: list[str] = []
    graph_rejections: list[dict[str, Any]] = []
    workflow_rejections: list[dict[str, Any]] = []
    if data.get("semantic_contract_version") != REQUIREMENT_SEMANTIC_CONTRACT_VERSION:
        rejection_reasons.append("semantic_contract_version_mismatch")
    if _text(data.get("source_content_hash")) != _fingerprint(requirement_text):
        rejection_reasons.append("semantic_contract_source_hash_mismatch")
    workflows = data.get("workflow_blueprints")
    if not isinstance(workflows, list):
        rejection_reasons.append("workflow_blueprints_not_list")
        workflows = []
    evidence_source, _ = build_requirement_business_evidence_view(
        requirement_text
    )
    if not evidence_source:
        rejection_reasons.append("business_evidence_source_empty")
    normalized = normalize_requirement_semantic_contract(
        data,
        requirement_text=evidence_source,
        workflow_blueprints=workflows,
    )
    validation = dict(normalized.get("semantic_graph_validation") or {})
    if validation.get("publishable") is not True:
        graph_rejections = [
            dict(item)
            for item in (validation.get("errors") or [])
            if isinstance(item, dict)
        ]
        rejection_reasons.append("semantic_graph_revalidation_failed")
    else:
        workflow_rejections = _graph_workflow_consistency_rejections(
            data,
            normalized,
        )
        if workflow_rejections:
            rejection_reasons.append("workflow_graph_revalidation_failed")
    return {
        "valid": not rejection_reasons,
        "rejection_reasons": list(dict.fromkeys(rejection_reasons)),
        "semantic_graph_rejections": graph_rejections[:128],
        "workflow_consistency_rejections": workflow_rejections[:128],
    }


def evaluate_current_requirement_semantic_compilation(
    source_meta: Any,
    *,
    requirement_text: str = "",
) -> dict[str, Any]:
    meta = dict(source_meta or {}) if isinstance(source_meta, dict) else {}
    pipeline_failed_stage = _text(meta.get("semantic_pipeline_failed_stage"))
    compile_status = _text(meta.get("semantic_compile_status")) or _text(
        meta.get("current_requirement_blueprint_status")
    )
    declaration_status = _text(meta.get("workflow_declaration_status"))
    revalidation = (
        _revalidate_existing_semantic_contract(
            meta.get("requirement_semantic_contract"),
            requirement_text=requirement_text,
        )
        if requirement_text
        else {"valid": True, "rejection_reasons": []}
    )
    passed = bool(
        meta.get("semantic_compile_success") is True
        and declaration_status in _SUCCESSFUL_WORKFLOW_DECLARATION_STATUSES
        and revalidation.get("valid") is True
    )
    result = {
        "passed": passed,
        "abort_code": "" if passed else SEMANTIC_COMPILATION_ABORT_CODE,
        "semantic_compile_status": compile_status or "missing_compile_status",
        "semantic_compile_mode": _text(meta.get("semantic_compile_mode")),
        "semantic_compile_attempt_count": int(meta.get("semantic_compile_attempt_count") or 0),
        "semantic_compile_physical_call_count": int(
            meta.get("semantic_compile_physical_call_count") or 0
        ),
        "semantic_compile_provider_call_count": int(
            meta.get("semantic_compile_provider_call_count") or 0
        ),
        "semantic_compile_cache_hit_count": int(
            meta.get("semantic_compile_cache_hit_count") or 0
        ),
        "semantic_compile_cache_miss_count": int(
            meta.get("semantic_compile_cache_miss_count") or 0
        ),
        "semantic_compile_cache_bypass_count": int(
            meta.get("semantic_compile_cache_bypass_count") or 0
        ),
        "semantic_compile_candidate_attempt_count": int(
            meta.get("semantic_compile_candidate_attempt_count") or 0
        ),
        "semantic_compile_candidate_attempt_limit": int(
            meta.get("semantic_compile_candidate_attempt_limit") or 0
        ),
        "semantic_compile_independent_recompile_limit": int(
            meta.get("semantic_compile_independent_recompile_limit") or 0
        ),
        "semantic_compile_independent_recompile_used": bool(
            meta.get("semantic_compile_independent_recompile_used")
        ),
        "semantic_compile_independent_recompile_attempt": int(
            meta.get("semantic_compile_independent_recompile_attempt") or 0
        ),
        "semantic_compile_independent_recompile_trigger_codes": [
            _text(item)
            for item in (
                meta.get("semantic_compile_independent_recompile_trigger_codes")
                or []
            )
            if _text(item)
        ],
        "semantic_compile_independent_recompile_outcome": _text(
            meta.get("semantic_compile_independent_recompile_outcome")
        )
        or "not_used",
        "semantic_compile_transport_retry_count": int(
            meta.get("semantic_compile_transport_retry_count") or 0
        ),
        "semantic_compile_transport_failure_count": int(
            meta.get("semantic_compile_transport_failure_count") or 0
        ),
        "semantic_compile_timeout_count": int(
            meta.get("semantic_compile_timeout_count") or 0
        ),
        "semantic_compile_stop_reason": _text(
            meta.get("semantic_compile_stop_reason")
        )[:120],
        "semantic_compile_final_gate_error_code": _text(
            meta.get("semantic_compile_final_gate_error_code")
        )[:120],
        "semantic_compile_final_gate_error_type": _text(
            meta.get("semantic_compile_final_gate_error_type")
        )[:120],
        "semantic_compile_final_gate_error_message": _text(
            meta.get("semantic_compile_final_gate_error_message")
        )[:300],
        "semantic_compile_retry_used": bool(meta.get("semantic_compile_retry_used")),
        "semantic_compile_attempts": [
            dict(item)
            for item in (meta.get("semantic_compile_attempts") or [])
            if isinstance(item, dict)
        ],
        "partition_compile_status": _text(
            meta.get("partition_compile_status")
        ),
        "partition_compile_success": bool(
            meta.get("partition_compile_success")
        ),
        "partition_compile_failed_phase": _text(
            meta.get("partition_compile_failed_phase")
        ),
        "partition_compile_failed_shard_id": _text(
            meta.get("partition_compile_failed_shard_id")
        ),
        "partition_compile_fact_shard_count": int(
            meta.get("partition_compile_fact_shard_count") or 0
        ),
        "partition_compile_completed_fact_shard_count": int(
            meta.get("partition_compile_completed_fact_shard_count") or 0
        ),
        "partition_compile_relation_fact_count": int(
            meta.get("partition_compile_relation_fact_count") or 0
        ),
        "partition_compile_relation_shard_count": int(
            meta.get("partition_compile_relation_shard_count") or 0
        ),
        "partition_compile_completed_relation_shard_count": int(
            meta.get("partition_compile_completed_relation_shard_count") or 0
        ),
        "partition_compile_workflow_called": bool(
            meta.get("partition_compile_workflow_called")
        ),
        "partition_compile_node_count": int(
            meta.get("partition_compile_node_count") or 0
        ),
        "partition_compile_edge_count": int(
            meta.get("partition_compile_edge_count") or 0
        ),
        "partition_compile_control_edge_count": int(
            meta.get("partition_compile_control_edge_count") or 0
        ),
        "partition_compile_provider_call_count": int(
            meta.get("partition_compile_provider_call_count") or 0
        ),
        "partition_compile_cache_hit_count": int(
            meta.get("partition_compile_cache_hit_count") or 0
        ),
        "partition_compile_cache_miss_count": int(
            meta.get("partition_compile_cache_miss_count") or 0
        ),
        "workflow_declaration_status": declaration_status or WORKFLOW_DECLARATION_MISSING,
        "workflow_absence_declared": bool(meta.get("workflow_absence_declared")) if passed else False,
        "raw_workflow_candidate_count": int(meta.get("raw_workflow_candidate_count") or 0),
        "normalized_workflow_count": int(meta.get("normalized_workflow_count") or 0),
        "rejected_workflow_count": int(meta.get("rejected_workflow_count") or 0),
        "workflow_rejection_reasons": list(
            meta.get("workflow_rejection_reasons")
            or (
                [f"semantic_pipeline_failed_stage:{pipeline_failed_stage}"]
                if pipeline_failed_stage
                else []
            )
        ),
        "typed_state_rejections": [
            dict(item)
            for item in (meta.get("typed_state_rejections") or [])
            if isinstance(item, dict)
        ][:64],
        "workflow_consistency_rejections": [
            dict(item)
            for item in (meta.get("workflow_consistency_rejections") or [])
            if isinstance(item, dict)
        ][:64],
        "semantic_graph_rejections": [
            dict(item)
            for item in (meta.get("semantic_graph_rejections") or [])
            if isinstance(item, dict)
        ][:128],
        "semantic_graph_diagnostics": dict(
            meta.get("semantic_graph_diagnostics") or {}
        ),
        "source_evidence_catalog": dict(
            meta.get("source_evidence_catalog") or {}
        ),
        "source_evidence_catalog_coverage": dict(
            meta.get("source_evidence_catalog_coverage") or {}
        ),
        "current_requirement_blueprint_error": _text(
            meta.get("current_requirement_blueprint_error")
        )[:300],
        "requirement_semantic_graph_fact_count": int(
            meta.get("requirement_semantic_graph_fact_count") or 0
        ),
        "requirement_semantic_graph_node_count": int(
            meta.get("requirement_semantic_graph_node_count") or 0
        ),
        "requirement_semantic_graph_edge_count": int(
            meta.get("requirement_semantic_graph_edge_count") or 0
        ),
        "verified_functional_module_count": int(meta.get("verified_functional_module_count") or 0),
        "semantic_pipeline_failed_stage": pipeline_failed_stage,
        "fact_ledger_compile_status": _text(
            meta.get("fact_ledger_compile_status")
        ),
        "fact_ledger_compile_success": bool(
            meta.get("fact_ledger_compile_success")
        ),
        "fact_ledger_compile_candidate_attempt_count": int(
            meta.get("fact_ledger_compile_candidate_attempt_count") or 0
        ),
        "fact_ledger_compile_candidate_attempt_limit": int(
            meta.get("fact_ledger_compile_candidate_attempt_limit") or 0
        ),
        "fact_ledger_compile_physical_call_count": int(
            meta.get("fact_ledger_compile_physical_call_count") or 0
        ),
        "fact_ledger_compile_provider_call_count": int(
            meta.get("fact_ledger_compile_provider_call_count") or 0
        ),
        "fact_ledger_compile_cache_hit_count": int(
            meta.get("fact_ledger_compile_cache_hit_count") or 0
        ),
        "fact_ledger_compile_cache_miss_count": int(
            meta.get("fact_ledger_compile_cache_miss_count") or 0
        ),
        "fact_ledger_compile_cache_bypass_count": int(
            meta.get("fact_ledger_compile_cache_bypass_count") or 0
        ),
        "fact_ledger_compile_transport_retry_count": int(
            meta.get("fact_ledger_compile_transport_retry_count") or 0
        ),
        "fact_ledger_compile_transport_failure_count": int(
            meta.get("fact_ledger_compile_transport_failure_count") or 0
        ),
        "fact_ledger_compile_fresh_candidate_used": bool(
            meta.get("fact_ledger_compile_fresh_candidate_used")
        ),
        "fact_ledger_compile_fresh_candidate_trigger_codes": _list_text(
            meta.get("fact_ledger_compile_fresh_candidate_trigger_codes"),
            limit=32,
        ),
        "fact_ledger_compile_last_parseable_candidate_attempt": int(
            meta.get("fact_ledger_compile_last_parseable_candidate_attempt")
            or 0
        ),
        "fact_ledger_compile_last_parseable_candidate_status": _text(
            meta.get("fact_ledger_compile_last_parseable_candidate_status")
        ),
        "fact_ledger_compile_last_parseable_candidate_error_codes": _list_text(
            meta.get("fact_ledger_compile_last_parseable_candidate_error_codes"),
            limit=64,
        ),
        "fact_ledger_compile_stop_reason": _text(
            meta.get("fact_ledger_compile_stop_reason")
        )[:120],
        "fact_ledger_compile_chunked": bool(
            meta.get("fact_ledger_compile_chunked")
        ),
        "fact_ledger_compile_chunk_count": int(
            meta.get("fact_ledger_compile_chunk_count") or 0
        ),
        "fact_ledger_compile_parallel_chunks_enabled": bool(
            meta.get("fact_ledger_compile_parallel_chunks_enabled")
        ),
        "fact_ledger_compile_chunk_max_workers": int(
            meta.get("fact_ledger_compile_chunk_max_workers") or 0
        ),
        "fact_ledger_compile_chunk_limit": int(
            meta.get("fact_ledger_compile_chunk_limit") or 0
        ),
        "fact_ledger_compile_chunk_budget_units": int(
            meta.get("fact_ledger_compile_chunk_budget_units") or 0
        ),
        "fact_ledger_compile_catalog_budget_units": int(
            meta.get("fact_ledger_compile_catalog_budget_units") or 0
        ),
        "fact_ledger_compile_partition_group_count": int(
            meta.get("fact_ledger_compile_partition_group_count") or 0
        ),
        "fact_ledger_compile_oversized_partition_group_count": int(
            meta.get("fact_ledger_compile_oversized_partition_group_count")
            or 0
        ),
        "fact_ledger_compile_completed_chunk_count": int(
            meta.get("fact_ledger_compile_completed_chunk_count") or 0
        ),
        "fact_ledger_compile_failed_chunk_index": int(
            meta.get("fact_ledger_compile_failed_chunk_index") or 0
        ),
        "fact_ledger_compile_global_status": _text(
            meta.get("fact_ledger_compile_global_status")
        ),
        "fact_ledger_compile_global_error_codes": _list_text(
            meta.get("fact_ledger_compile_global_error_codes"),
            limit=64,
        ),
        # 分片摘要只保留计数、状态和指纹，不发布候选正文。
        "fact_ledger_compile_chunk_summaries": [
            {
                key: item.get(key)
                for key in (
                    "chunk_index",
                    "status",
                    "target_source_evidence_count",
                    "budget_units",
                    "target_fingerprint",
                    "candidate_attempt_count",
                    "envelope_count",
                    "physical_call_count",
                    "provider_call_count",
                    "cache_hit_count",
                    "cache_miss_count",
                    "validated_attempt",
                    "ledger_fingerprint",
                    "fact_count",
                    "source_disposition_count",
                )
                if item.get(key) not in (None, "")
            }
            for item in (meta.get("fact_ledger_compile_chunk_summaries") or [])[:32]
            if isinstance(item, dict)
        ],
        "fact_ledger_fingerprint": _text(meta.get("fact_ledger_fingerprint")),
        "scope_ledger_compile_status": _text(
            meta.get("scope_ledger_compile_status")
        ),
        "scope_ledger_compile_success": bool(
            meta.get("scope_ledger_compile_success")
        ),
        "scope_ledger_compile_mode": _text(
            meta.get("scope_ledger_compile_mode")
        ),
        "scope_ledger_compile_envelope_count": int(
            meta.get("scope_ledger_compile_envelope_count") or 0
        ),
        "scope_ledger_compile_candidate_attempt_count": int(
            meta.get("scope_ledger_compile_candidate_attempt_count") or 0
        ),
        "scope_ledger_compile_candidate_attempt_limit": int(
            meta.get("scope_ledger_compile_candidate_attempt_limit") or 0
        ),
        "scope_ledger_compile_physical_call_count": int(
            meta.get("scope_ledger_compile_physical_call_count") or 0
        ),
        "scope_ledger_compile_provider_call_count": int(
            meta.get("scope_ledger_compile_provider_call_count") or 0
        ),
        "scope_ledger_compile_cache_hit_count": int(
            meta.get("scope_ledger_compile_cache_hit_count") or 0
        ),
        "scope_ledger_compile_cache_miss_count": int(
            meta.get("scope_ledger_compile_cache_miss_count") or 0
        ),
        "scope_ledger_compile_cache_bypass_count": int(
            meta.get("scope_ledger_compile_cache_bypass_count") or 0
        ),
        "scope_ledger_compile_transport_retry_count": int(
            meta.get("scope_ledger_compile_transport_retry_count") or 0
        ),
        "scope_ledger_compile_transport_failure_count": int(
            meta.get("scope_ledger_compile_transport_failure_count") or 0
        ),
        "scope_ledger_compile_transport_replays_per_envelope": int(
            meta.get("scope_ledger_compile_transport_replays_per_envelope") or 0
        ),
        "scope_ledger_compile_fresh_candidate_used": bool(
            meta.get("scope_ledger_compile_fresh_candidate_used")
        ),
        "scope_ledger_compile_fresh_candidate_trigger_codes": _list_text(
            meta.get("scope_ledger_compile_fresh_candidate_trigger_codes"),
            limit=32,
        ),
        "scope_ledger_compile_last_parseable_candidate_attempt": int(
            meta.get("scope_ledger_compile_last_parseable_candidate_attempt")
            or 0
        ),
        "scope_ledger_compile_last_parseable_candidate_status": _text(
            meta.get("scope_ledger_compile_last_parseable_candidate_status")
        ),
        "scope_ledger_compile_last_parseable_candidate_error_codes": _list_text(
            meta.get("scope_ledger_compile_last_parseable_candidate_error_codes"),
            limit=64,
        ),
        "scope_ledger_compile_stop_reason": _text(
            meta.get("scope_ledger_compile_stop_reason")
        )[:120],
        "scope_ledger_compile_global_status": _text(
            meta.get("scope_ledger_compile_global_status")
        ),
        "scope_ledger_compile_global_error_codes": _list_text(
            meta.get("scope_ledger_compile_global_error_codes"),
            limit=64,
        ),
        "scope_ledger_compile_attempts": [
            {
                key: item.get(key)
                for key in (
                    "attempt",
                    "candidate_mode",
                    "compilation_mode",
                    "phase",
                    "shard_index",
                    "shard_count",
                    "target_fact_count",
                    "status",
                    "raw_chars",
                    "finish_reason",
                    "user_input_fingerprint",
                    "source_topology_wire_present",
                    "source_topology_version",
                    "source_topology_fingerprint",
                    "source_topology_group_count",
                    "source_topology_relation_count",
                    "source_topology_anchored_fact_count",
                    "boundary_selection_version_wire",
                    "boundary_selection_fingerprint_wire",
                    "boundary_selection_count_wire",
                    "boundary_manifest_fingerprint_wire",
                    "parse_error_code",
                    "parse_error_type",
                    "parsed_type",
                    "contract_error_count",
                    "contract_error_codes",
                    "payload_fingerprint",
                )
                if item.get(key) not in (None, "", [])
            }
            for item in (meta.get("scope_ledger_compile_attempts") or [])[:40]
            if isinstance(item, dict)
        ],
        # A2 三段摘要保留阶段边界和冻结指纹，不透传模型候选正文。
        "scope_ledger_boundary_selection_status": _text(
            meta.get("scope_ledger_boundary_selection_status")
        ),
        "scope_ledger_boundary_selection_fingerprint": _text(
            meta.get("scope_ledger_boundary_selection_fingerprint")
        ),
        "scope_ledger_boundary_selection_count": int(
            meta.get("scope_ledger_boundary_selection_count") or 0
        ),
        "scope_ledger_membership_assignment_status": _text(
            meta.get("scope_ledger_membership_assignment_status")
        ),
        "scope_ledger_membership_assignment_fingerprint": _text(
            meta.get("scope_ledger_membership_assignment_fingerprint")
        ),
        "scope_ledger_membership_assignment_count": int(
            meta.get("scope_ledger_membership_assignment_count") or 0
        ),
        "scope_ledger_membership_none_count": int(
            meta.get("scope_ledger_membership_none_count") or 0
        ),
        "scope_ledger_boundary_manifest_status": _text(
            meta.get("scope_ledger_boundary_manifest_status")
        ),
        "scope_ledger_boundary_manifest_fingerprint": _text(
            meta.get("scope_ledger_boundary_manifest_fingerprint")
        ),
        "scope_ledger_boundary_count": int(
            meta.get("scope_ledger_boundary_count") or 0
        ),
        "scope_ledger_source_topology": {
            key: value
            for key, value in dict(
                meta.get("scope_ledger_source_topology") or {}
            ).items()
            if key
            in {
                "version",
                "fingerprint",
                "group_count",
                "relation_count",
                "anchored_fact_count",
            }
            and isinstance(value, (str, int, float, bool))
        },
        "scope_ledger_binding_shard_count": int(
            meta.get("scope_ledger_binding_shard_count") or 0
        ),
        "scope_ledger_binding_shard_budget_units": int(
            meta.get("scope_ledger_binding_shard_budget_units") or 0
        ),
        "scope_ledger_binding_oversized_fact_count": int(
            meta.get("scope_ledger_binding_oversized_fact_count") or 0
        ),
        "scope_ledger_binding_completed_shard_count": int(
            meta.get("scope_ledger_binding_completed_shard_count") or 0
        ),
        "scope_ledger_binding_failed_shard_index": int(
            meta.get("scope_ledger_binding_failed_shard_index") or 0
        ),
        "scope_ledger_binding_projected_context_scope_id_count": int(
            meta.get(
                "scope_ledger_binding_projected_context_scope_id_count"
            )
            or 0
        ),
        "scope_ledger_binding_shard_summaries": [
            {
                key: item.get(key)
                for key in (
                    "shard_index",
                    "status",
                    "target_fact_count",
                    "budget_units",
                    "target_fingerprint",
                    "candidate_attempt_count",
                    "envelope_count",
                    "physical_call_count",
                    "provider_call_count",
                    "cache_hit_count",
                    "cache_miss_count",
                    "validated_attempt",
                    "binding_count",
                    "projected_non_scope_context_binding_count",
                    "projected_non_scope_context_scope_id_count",
                    "payload_fingerprint",
                )
                if item.get(key) not in (None, "")
            }
            for item in (meta.get("scope_ledger_binding_shard_summaries") or [])[:32]
            if isinstance(item, dict)
        ],
        "scope_ledger_fingerprint": _text(meta.get("scope_ledger_fingerprint")),
        "semantic_contract_revalidated": bool(requirement_text),
        "semantic_contract_revalidation_reasons": list(
            revalidation.get("rejection_reasons") or []
        ),
    }
    if revalidation.get("valid") is not True:
        result["semantic_graph_rejections"] = list(
            revalidation.get("semantic_graph_rejections") or []
        )
        result["workflow_consistency_rejections"] = list(
            revalidation.get("workflow_consistency_rejections") or []
        )
    if not passed:
        result["message"] = (
            "当前需求语义编译失败，已终止生成，避免在缺失有效工作流契约时猜测主链。"
        )
    return result


def _failed_result(
    diagnostics: dict[str, Any],
    *,
    status: str,
    requirement_hash: str,
    error: Any = "",
    workflow_declaration_status: str = "",
    normalization_diagnostics: dict[str, Any] | None = None,
    semantic_contract: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    diagnostics["current_requirement_blueprint_status"] = status
    if _text(error):
        diagnostics["current_requirement_blueprint_error"] = _safe_error_preview(
            error,
            limit=240,
        )
    semantic_contract = dict(semantic_contract or empty_requirement_semantic_contract(status=status))
    semantic_contract["status"] = status
    semantic_contract["workflow_blueprints"] = []
    semantic_contract["source_content_hash"] = requirement_hash
    semantic_contract["semantic_compile_status"] = status
    semantic_contract["semantic_compile_success"] = False
    semantic_contract["workflow_declaration_status"] = (
        workflow_declaration_status or status or WORKFLOW_DECLARATION_INVALID
    )
    semantic_contract["workflow_absence_declared"] = False
    declaration_status = workflow_declaration_status or status or WORKFLOW_DECLARATION_INVALID
    diagnostics.update(dict(normalization_diagnostics or {}))
    diagnostics["requirement_semantic_contract"] = semantic_contract
    diagnostics["requirement_semantic_contract_status"] = status
    diagnostics["semantic_compile_status"] = status
    diagnostics["semantic_compile_success"] = False
    diagnostics["workflow_declaration_status"] = declaration_status
    diagnostics["workflow_absence_declared"] = False
    diagnostics.setdefault("raw_workflow_candidate_count", 0)
    diagnostics.setdefault("normalized_workflow_count", 0)
    diagnostics.setdefault("rejected_workflow_count", 0)
    diagnostics.setdefault("workflow_rejections", [])
    diagnostics.setdefault("workflow_rejection_reasons", [])
    diagnostics.setdefault("typed_state_rejections", [])
    diagnostics.setdefault("workflow_consistency_rejections", [])
    diagnostics.setdefault("semantic_graph_rejections", [])
    diagnostics.setdefault("semantic_graph_diagnostics", {})
    diagnostics.setdefault("verified_functional_module_count", 0)
    return [], diagnostics


def extract_current_requirement_blueprints(
    *,
    client: Any,
    requirement_text: str,
    db: Any = None,
    project_id: int | None = None,
    user_id: int | None = None,
    isolated_ai_runtime_factory: Callable[[], Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """按 A1 原子事实、A2 职责账本、B 语义图三阶段编译当前需求。"""

    requirement = _text(requirement_text)
    requirement_hash = _fingerprint(requirement)
    diagnostics: dict[str, Any] = {
        "current_requirement_blueprint_status": "skipped_empty_requirement",
        "current_requirement_blueprint_count": 0,
        "current_requirement_blueprint_step_count": 0,
        "current_requirement_blueprint_source": (
            CURRENT_REQUIREMENT_BLUEPRINT_REPOSITORY_SOURCE
        ),
        "semantic_pipeline_failed_stage": "",
    }
    max_tokens = current_requirement_blueprint_max_tokens()
    request_timeout_seconds = semantic_compilation_request_timeout_seconds()
    diagnostics["current_requirement_blueprint_max_tokens"] = int(max_tokens)
    diagnostics["semantic_compile_request_timeout_seconds"] = int(
        request_timeout_seconds
    )
    diagnostics.update(_extract_requirement_understanding_stats(requirement))
    if not requirement:
        diagnostics["semantic_pipeline_failed_stage"] = "fact_ledger"
        return _failed_result(
            diagnostics,
            status="skipped_empty_requirement",
            requirement_hash=requirement_hash,
        )

    evidence_source, evidence_source_diagnostics = (
        build_requirement_business_evidence_view(requirement)
    )
    diagnostics.update(evidence_source_diagnostics)
    diagnostics["semantic_compilation_input_chars"] = int(len(evidence_source))
    if not evidence_source:
        diagnostics["semantic_pipeline_failed_stage"] = "fact_ledger"
        return _failed_result(
            diagnostics,
            status="no_semantic_candidate",
            requirement_hash=requirement_hash,
            error="business_evidence_source_empty",
        )

    source_quote_catalog = _build_source_quote_catalog(evidence_source)
    source_quote_coverage = _source_quote_catalog_coverage(
        evidence_source,
        source_quote_catalog,
    )
    diagnostics["source_evidence_catalog"] = _source_evidence_catalog_diagnostic(
        source_quote_catalog,
        injected=False,
    )
    diagnostics["source_evidence_catalog_coverage"] = source_quote_coverage
    if not source_quote_catalog or source_quote_coverage.get("complete") is not True:
        diagnostics["semantic_pipeline_failed_stage"] = "fact_ledger"
        return _failed_result(
            diagnostics,
            status="source_evidence_catalog_invalid",
            requirement_hash=requirement_hash,
            error="source_evidence_catalog_incomplete",
        )
    if client is None or not hasattr(client, "generate_response"):
        diagnostics["semantic_pipeline_failed_stage"] = "fact_ledger"
        return _failed_result(
            diagnostics,
            status="skipped_no_client",
            requirement_hash=requirement_hash,
        )

    fact_result = compile_requirement_atomic_fact_ledger(
        client=client,
        source_evidence_catalog=source_quote_catalog,
        db=db,
        max_tokens=max_tokens,
        task_type="generation",
        request_timeout_seconds=float(request_timeout_seconds),
        worker_runtime_factory=isolated_ai_runtime_factory,
    )
    diagnostics.update(fact_result.diagnostics)
    if not fact_result.success:
        diagnostics["semantic_pipeline_failed_stage"] = "fact_ledger"
        return _failed_result(
            diagnostics,
            status=f"fact_ledger_{fact_result.status}",
            requirement_hash=requirement_hash,
            error=fact_result.status,
        )

    scope_result = compile_requirement_scope_ledger(
        client=client,
        normalized_fact_ledger=fact_result.normalized_ledger,
        source_evidence_catalog=source_quote_catalog,
        db=db,
        max_tokens=max_tokens,
        task_type="generation",
        request_timeout_seconds=float(request_timeout_seconds),
    )
    diagnostics.update(scope_result.diagnostics)
    if not scope_result.success:
        diagnostics["semantic_pipeline_failed_stage"] = "scope_ledger"
        return _failed_result(
            diagnostics,
            status=f"scope_ledger_{scope_result.status}",
            requirement_hash=requirement_hash,
            error=scope_result.status,
        )

    graph_result = compile_requirement_graph_stage(
        client=client,
        normalized_scope_ledger=scope_result.normalized_ledger,
        candidate_evaluator=lambda candidate: _evaluate_parsed_semantic_candidate(
            candidate,
            evidence_source=evidence_source,
            project_id=project_id,
            user_id=user_id,
        ),
        independent_recompile_code_resolver=(
            _evaluation_independent_recompile_codes
        ),
        db=db,
        max_tokens=max_tokens,
        task_type="generation",
        request_timeout_seconds=float(request_timeout_seconds),
        isolated_ai_runtime_factory=(
            isolated_ai_runtime_factory
        ),
    )
    diagnostics.update(graph_result.diagnostics)
    attempts = diagnostics.get("semantic_compile_attempts") or []
    diagnostics["current_requirement_blueprint_raw_chars"] = int(
        (attempts[-1].get("raw_chars") if attempts else 0) or 0
    )
    if not graph_result.success:
        diagnostics["semantic_pipeline_failed_stage"] = "graph"
        evaluation_status = _text(diagnostics.get("evaluation_status"))
        declaration_status = _text(
            diagnostics.get("workflow_declaration_status")
        )
        return _failed_result(
            diagnostics,
            status=evaluation_status or graph_result.status,
            requirement_hash=requirement_hash,
            error=graph_result.status,
            workflow_declaration_status=(
                declaration_status or evaluation_status or graph_result.status
            ),
        )

    semantic_evaluation = dict(graph_result.evaluation or {})
    blueprints = [
        dict(item)
        for item in (semantic_evaluation.get("blueprints") or [])
        if isinstance(item, dict)
    ]
    semantic_contract = dict(semantic_evaluation.get("semantic_contract") or {})
    normalization_diagnostics = dict(
        semantic_evaluation.get("normalization_diagnostics") or {}
    )
    declaration_status = (
        WORKFLOW_DECLARATION_APPLIED
        if blueprints
        else WORKFLOW_DECLARATION_INDEPENDENT_ONLY
    )
    semantic_contract["source_content_hash"] = requirement_hash
    semantic_contract["status"] = declaration_status
    semantic_contract["semantic_compile_status"] = declaration_status
    semantic_contract["semantic_compile_success"] = True
    semantic_contract["workflow_declaration_status"] = declaration_status
    semantic_contract["workflow_absence_declared"] = not bool(blueprints)
    diagnostics.update(
        {
            "current_requirement_blueprint_status": declaration_status,
            "current_requirement_blueprint_count": int(len(blueprints)),
            "current_requirement_blueprint_step_count": int(
                sum(
                    len(item.get("steps") or [])
                    for item in blueprints
                    if isinstance(item, dict)
                )
            ),
            "requirement_semantic_contract": semantic_contract,
            "requirement_semantic_contract_status": declaration_status,
            "requirement_semantic_module_count": int(
                len(
                    (semantic_contract.get("functional_architecture") or {}).get(
                        "functional_modules"
                    )
                    or []
                )
            ),
            "requirement_semantic_interaction_count": int(
                len(
                    (semantic_contract.get("functional_architecture") or {}).get(
                        "module_interactions"
                    )
                    or []
                )
            ),
            "requirement_semantic_graph_fact_count": int(
                len(semantic_contract.get("evidence_facts") or [])
            ),
            "requirement_semantic_graph_node_count": int(
                len(
                    (semantic_contract.get("semantic_graph") or {}).get("nodes")
                    or []
                )
            ),
            "requirement_semantic_graph_edge_count": int(
                len(
                    (semantic_contract.get("semantic_graph") or {}).get("edges")
                    or []
                )
            ),
            "semantic_compile_status": declaration_status,
            "semantic_compile_success": True,
            "workflow_declaration_status": declaration_status,
            "workflow_absence_declared": not bool(blueprints),
            **normalization_diagnostics,
        }
    )
    diagnostics["current_requirement_blueprint_ids"] = [
        str(item.get("id") or "")
        for item in blueprints
        if str(item.get("id") or "").strip()
    ][:5]
    return blueprints, diagnostics


def _has_current_requirement_semantic_contract(
    state: FeedbackControlState,
    *,
    requirement_text: str,
) -> bool:
    contract = (state.source_meta or {}).get("requirement_semantic_contract")
    if not isinstance(contract, dict):
        return False
    metadata_valid = bool(
        str(contract.get("source") or "") == CURRENT_REQUIREMENT_BLUEPRINT_REPOSITORY_SOURCE
        and contract.get("semantic_contract_version")
        == REQUIREMENT_SEMANTIC_CONTRACT_VERSION
        and isinstance(contract.get("evidence_facts"), list)
        and isinstance(contract.get("semantic_graph"), dict)
        and isinstance(contract.get("semantic_graph_validation"), dict)
        and contract["semantic_graph_validation"].get("publishable") is True
        and str(contract.get("source_content_hash") or "") == _fingerprint(requirement_text)
        and contract.get("semantic_compile_success") is True
        and str(contract.get("workflow_declaration_status") or "")
        in _SUCCESSFUL_WORKFLOW_DECLARATION_STATUSES
    )
    if not metadata_valid:
        return False
    return bool(
        _revalidate_existing_semantic_contract(
            contract,
            requirement_text=requirement_text,
        ).get("valid")
    )


def _without_workflow_blueprints(state: FeedbackControlState) -> FeedbackControlState:
    return FeedbackControlState(
        must_cover_rules=list(state.must_cover_rules),
        must_have_scenarios=list(state.must_have_scenarios),
        forbidden_patterns=list(state.forbidden_patterns),
        preferred_patterns=list(state.preferred_patterns),
        reuse_risks=list(state.reuse_risks),
        soft_constraints=list(state.soft_constraints),
        rule_quota=dict(state.rule_quota),
        quality_fix_hints=list(state.quality_fix_hints),
        workflow_blueprints=[],
        source_meta=dict(state.source_meta or {}),
    )


def merge_current_requirement_blueprint_control_state(
    control_state: FeedbackControlState | dict[str, Any] | None,
    *,
    client: Any,
    requirement_text: str,
    db: Any = None,
    project_id: int | None = None,
    user_id: int | None = None,
    isolated_ai_runtime_factory: Callable[[], Any] | None = None,
) -> FeedbackControlState:
    state = FeedbackControlState.from_any(control_state)
    if _has_current_requirement_semantic_contract(state, requirement_text=requirement_text):
        return state.merge(
            FeedbackControlState(
                source_meta={
                    "sources": ["current_requirement_blueprint"],
                    "current_requirement_blueprint_status": "skipped_existing_current_requirement_semantic_contract",
                    "current_requirement_blueprint_count": 0,
                    "current_requirement_blueprint_existing_count": int(len(state.workflow_blueprints)),
                }
            )
        )
    blueprints, diagnostics = extract_current_requirement_blueprints(
        client=client,
        requirement_text=requirement_text,
        db=db,
        project_id=project_id,
        user_id=user_id,
        isolated_ai_runtime_factory=(
            isolated_ai_runtime_factory
        ),
    )
    current_state = FeedbackControlState(
        workflow_blueprints=blueprints,
        source_meta={"sources": ["current_requirement_blueprint"], **diagnostics},
    )
    if blueprints:
        return _without_workflow_blueprints(state).merge(current_state)
    if diagnostics.get("semantic_compile_success") is True:
        return _without_workflow_blueprints(state).merge(current_state)
    return state.merge(current_state)


__all__ = [
    "CURRENT_REQUIREMENT_BLUEPRINT_REPOSITORY_SOURCE",
    "CURRENT_REQUIREMENT_BLUEPRINT_SOURCE_TYPE",
    "SEMANTIC_COMPILATION_ABORT_CODE",
    "current_requirement_blueprint_max_tokens",
    "evaluate_current_requirement_semantic_compilation",
    "extract_current_requirement_blueprints",
    "merge_current_requirement_blueprint_control_state",
    "normalize_current_requirement_blueprint_payload",
]
