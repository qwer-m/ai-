from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from core.processing.biz_key_extractor import extract_biz_key
from core.processing.semantic_chunking import split_semantic_text

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """业务分块结果。"""

    text: str
    module: str | None = None
    biz_key: str | None = None
    requirement_id: str | None = None
    test_case_id: str | None = None
    extra_metadata: dict[str, Any] = field(default_factory=dict)


def _non_empty_lines(text: str) -> list[str]:
    return [line.rstrip() for line in str(text or "").splitlines() if line.strip()]


def _safe_join(lines: list[str]) -> str:
    return "\n".join([line for line in lines if str(line or "").strip()]).strip()


def _extract_first_module_hint(text: str) -> str | None:
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # 中文注释：常见“模块：xxx”写法优先提取。
        m = re.search(r"(?:模块|功能模块|模块名称)\s*[:：]\s*([^\n]{1,40})", line, flags=re.I)
        if m:
            return m.group(1).strip()
    return None


class SemanticChunker:
    """语义分块兜底策略。"""

    def chunk(self, text: str) -> list[Chunk]:
        chunks = split_semantic_text(
            text=str(text or ""),
            max_chars=2000,
            min_chars=400,
        )
        return [Chunk(text=item.strip()) for item in chunks if str(item or "").strip()]


class RequirementChunker:
    """需求文档分块：按业务规则编号切分。"""

    _RULE_START_RE = re.compile(
        r"^\s*(?:\d+(?:\.\d+){0,3}[\.、)]|R(?:EQ)?[-_ ]?\d+|[-*\u2022])\s+",
        flags=re.I,
    )
    _REQ_ID_RE = re.compile(r"\b(?:REQ|R)[-_ ]?\d+\b", flags=re.I)

    def chunk(self, text: str) -> list[Chunk]:
        lines = _non_empty_lines(text)
        if not lines:
            return []

        blocks: list[list[str]] = []
        current: list[str] = []
        for line in lines:
            is_rule_start = bool(self._RULE_START_RE.match(line))
            if is_rule_start and current:
                blocks.append(current)
                current = [line]
            else:
                current.append(line)
        if current:
            blocks.append(current)

        # 中文注释：若没识别出规则边界，退回语义分块兜底。
        if len(blocks) <= 1:
            fallback = SemanticChunker().chunk(text)
            module_hint = _extract_first_module_hint(text)
            for item in fallback:
                chunk_module = _extract_first_module_hint(item.text) or module_hint
                item.module = chunk_module
                item.biz_key = extract_biz_key(item.text, chunk_module or "")
            logger.debug("RequirementChunker fallback semantic_chunks=%s", len(fallback))
            return fallback

        results: list[Chunk] = []
        module_hint = _extract_first_module_hint(text)
        for block in blocks:
            block_text = _safe_join(block)
            if not block_text:
                continue
            req_match = self._REQ_ID_RE.search(block_text[:160])
            requirement_id = req_match.group(0).upper().replace(" ", "") if req_match else None
            block_module = _extract_first_module_hint(block_text) or module_hint
            results.append(
                Chunk(
                    text=block_text,
                    module=block_module,
                    biz_key=extract_biz_key(block_text, block_module or ""),
                    requirement_id=requirement_id,
                )
            )

        logger.debug("RequirementChunker rules=%s", len(results))
        return results


class TestCaseChunker:
    """测试用例分块：保证单条用例不被拆分。"""

    _TC_START_RE = re.compile(r"^\s*(TC[-_ ]?[A-Za-z0-9]+)\b", flags=re.I)
    _LABEL_RE = re.compile(
        r"(?:^|\n)\s*(id|test_case_id|preconditions?|前置条件|steps?|步骤|expected(?:_result)?|预期结果)\s*[:：]",
        flags=re.I,
    )

    def _normalize_case_block(self, raw_text: str, case_id: str | None = None) -> Chunk:
        source = str(raw_text or "").strip()
        resolved_id = (case_id or "").strip()

        if not resolved_id:
            m = self._TC_START_RE.search(source)
            if m:
                resolved_id = m.group(1).upper().replace(" ", "")

        # 中文注释：结构化字段尽量抽取，缺失字段保留空字符串以维持统一格式。
        pre = self._extract_section(source, ["preconditions", "precondition", "前置条件"])
        steps = self._extract_section(source, ["steps", "step", "步骤"])
        exp = self._extract_section(source, ["expected_result", "expected", "预期结果"])

        if not steps:
            steps = source

        normalized = (
            f"id: {resolved_id or 'UNKNOWN'}\n"
            f"preconditions: {pre}\n"
            f"steps:\n{steps}\n"
            f"expected_result: {exp}"
        ).strip()

        module_hint = _extract_first_module_hint(source)
        return Chunk(
            text=normalized,
            module=module_hint,
            biz_key=extract_biz_key(source, module_hint or ""),
            test_case_id=(resolved_id or None),
        )

    def _extract_section(self, text: str, labels: list[str]) -> str:
        body = str(text or "")
        label_pattern = "|".join([re.escape(label) for label in labels])
        all_labels = r"id|test_case_id|preconditions?|前置条件|steps?|步骤|expected(?:_result)?|预期结果"
        pattern = re.compile(
            rf"(?:^|\n)\s*(?:{label_pattern})\s*[:：]\s*(.*?)(?=(?:\n\s*(?:{all_labels})\s*[:：])|\Z)",
            flags=re.I | re.S,
        )
        match = pattern.search(body)
        return match.group(1).strip() if match else ""

    def _chunk_from_json(self, text: str) -> list[Chunk]:
        source = str(text or "").strip()
        if not source.startswith("{") and not source.startswith("["):
            return []
        try:
            payload = json.loads(source)
        except Exception:
            return []

        cases: list[dict[str, Any]] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                keys = {str(k).lower() for k in node.keys()}
                if {"id", "test_case_id", "steps", "expected_result", "expected"} & keys:
                    cases.append(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        chunks: list[Chunk] = []
        for item in cases:
            case_id = str(item.get("id") or item.get("test_case_id") or "").strip()
            pre = item.get("preconditions") or item.get("precondition") or item.get("前置条件") or ""
            steps = item.get("steps") or item.get("步骤") or ""
            exp = item.get("expected_result") or item.get("expected") or item.get("预期结果") or ""
            raw = (
                f"id: {case_id or 'UNKNOWN'}\n"
                f"preconditions: {pre}\n"
                f"steps:\n{steps}\n"
                f"expected_result: {exp}"
            )
            chunks.append(self._normalize_case_block(raw, case_id=case_id))
        return chunks

    def _chunk_from_tabular(self, text: str) -> list[Chunk]:
        lines = _non_empty_lines(text)
        if len(lines) < 2:
            return []

        def split_cols(line: str) -> list[str]:
            if "\t" in line:
                return [col.strip() for col in line.split("\t")]
            if "|" in line:
                return [col.strip() for col in line.split("|")]
            if "," in line:
                return [col.strip() for col in line.split(",")]
            return []

        header_cols = split_cols(lines[0])
        if not header_cols:
            return []
        header_lower = [col.lower() for col in header_cols]

        id_idx = next((i for i, col in enumerate(header_lower) if col in {"id", "test_case_id", "用例id"}), None)
        pre_idx = next((i for i, col in enumerate(header_lower) if "pre" in col or "前置" in col), None)
        steps_idx = next((i for i, col in enumerate(header_lower) if "step" in col or "步骤" in col), None)
        exp_idx = next(
            (
                i
                for i, col in enumerate(header_lower)
                if "expected" in col or "预期" in col or "result" in col
            ),
            None,
        )
        if id_idx is None or steps_idx is None:
            return []

        chunks: list[Chunk] = []
        for line in lines[1:]:
            cols = split_cols(line)
            if not cols or len(cols) <= max(id_idx, steps_idx):
                continue
            case_id = cols[id_idx] if id_idx < len(cols) else ""
            pre = cols[pre_idx] if pre_idx is not None and pre_idx < len(cols) else ""
            steps = cols[steps_idx] if steps_idx < len(cols) else ""
            exp = cols[exp_idx] if exp_idx is not None and exp_idx < len(cols) else ""
            raw = (
                f"id: {case_id or 'UNKNOWN'}\n"
                f"preconditions: {pre}\n"
                f"steps:\n{steps}\n"
                f"expected_result: {exp}"
            )
            chunks.append(self._normalize_case_block(raw, case_id=case_id))
        return chunks

    def _chunk_from_tc_markers(self, text: str) -> list[Chunk]:
        lines = _non_empty_lines(text)
        if not lines:
            return []

        blocks: list[list[str]] = []
        current: list[str] = []
        current_case_id: str | None = None
        case_ids: list[str | None] = []

        for line in lines:
            m = self._TC_START_RE.match(line)
            if m and current:
                blocks.append(current)
                case_ids.append(current_case_id)
                current = [line]
                current_case_id = m.group(1).upper().replace(" ", "")
            else:
                current.append(line)
                if m:
                    current_case_id = m.group(1).upper().replace(" ", "")

        if current:
            blocks.append(current)
            case_ids.append(current_case_id)

        if len(blocks) <= 1:
            return []

        results: list[Chunk] = []
        for idx, block in enumerate(blocks):
            raw = _safe_join(block)
            if not raw:
                continue
            results.append(self._normalize_case_block(raw, case_id=case_ids[idx]))
        return results

    def chunk(self, text: str) -> list[Chunk]:
        raw = str(text or "").strip()
        if not raw:
            return []

        for parser in (self._chunk_from_json, self._chunk_from_tabular, self._chunk_from_tc_markers):
            chunks = parser(raw)
            if chunks:
                logger.debug("TestCaseChunker parser=%s chunks=%s", parser.__name__, len(chunks))
                return chunks

        fallback = SemanticChunker().chunk(raw)
        results = [self._normalize_case_block(item.text, case_id=None) for item in fallback]
        logger.debug("TestCaseChunker fallback semantic_chunks=%s", len(results))
        return results


class BusinessChunkerDispatcher:
    """按文档类型分发业务切分器。"""

    def __init__(self) -> None:
        self._fallback = SemanticChunker()
        self._chunkers = {
            "requirement": RequirementChunker(),
            "testcase": TestCaseChunker(),
        }

    def _normalize_doc_type(self, doc_type: str) -> str:
        lowered = str(doc_type or "").strip().lower()
        if lowered in {"test_case", "testcase", "test-case"}:
            return "testcase"
        if lowered in {"requirement", "product_requirement", "incomplete"}:
            return "requirement"
        return "fallback"

    def chunk(self, doc_type: str, text: str) -> list[Chunk]:
        normalized = self._normalize_doc_type(doc_type)
        chunker = self._chunkers.get(normalized)
        if not chunker:
            logger.debug("BusinessChunkerDispatcher use fallback doc_type=%s", doc_type)
            return self._fallback.chunk(text)

        chunks = chunker.chunk(text)
        if chunks:
            logger.debug(
                "BusinessChunkerDispatcher doc_type=%s normalized=%s chunks=%s",
                doc_type,
                normalized,
                len(chunks),
            )
            return chunks

        logger.debug(
            "BusinessChunkerDispatcher doc_type=%s normalized=%s fallback_empty",
            doc_type,
            normalized,
        )
        return self._fallback.chunk(text)
