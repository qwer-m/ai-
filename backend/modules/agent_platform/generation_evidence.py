"""测试生成的原始证据和真实人工批准；不调用模型，不解释或压缩需求。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import math
import re
import unicodedata
from typing import Any

from sqlalchemy import select

from core.db.model_defs import AgentApproval, AgentArtifactBlob, AgentNodeRun, AgentRun, KnowledgeDocument
from modules.knowledge_base_components.document.document_asset_service import (
    document_page_image_path, document_page_text, load_document_manifest,
)
from .action_journal import action_state_hash
from .checkpoint_storage import ArtifactStore, content_hash
from .sources import SOURCE_ARTIFACT_KEY, SourceSnapshot, assert_same_source, persisted_source_snapshot


def _east_asian_letter(character):
    """识别无需空格分词的汉字、假名及部首；不吞掉韩文或西文词界。"""
    name = unicodedata.name(character, "") if character else ""
    return (name.startswith(("CJK UNIFIED IDEOGRAPH-", "CJK COMPATIBILITY IDEOGRAPH-", "HIRAGANA ", "KATAKANA "))
            and unicodedata.category(character).startswith("L")) or name.startswith(("CJK RADICAL ", "KANGXI RADICAL "))


def quote_presentation(text):
    """引用容纳东亚文字的版面空白，保留西文词界、数字分隔和硬段落边界。"""
    value = unicodedata.normalize("NFKC", text)

    def whitespace(match):
        gap = match.group(0)
        if "\f" in gap:
            return "\f"
        if chr(0x2029) in gap or len(re.findall(r"\r\n|[\r\n\u2028]", gap)) > 1:
            return "\n\n"
        if "\t" in gap:
            return "\t"
        left = value[match.start() - 1] if match.start() else ""
        right = value[match.end()] if match.end() < len(value) else ""
        return "" if _east_asian_letter(left) or _east_asian_letter(right) else " "

    return re.sub(r"\s+", whitespace, value).strip()


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}必须是非空字符串")
    return value


def _hash(value: Any, name: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)):
        raise ValueError(f"{name}必须是完整SHA256")
    return value


def _region(value: Any) -> dict[str, float]:
    if not isinstance(value, dict) or set(value) != {"x", "y", "width", "height"}:
        raise ValueError("图片范围必须包含完整的归一化坐标")
    if any(type(number) not in (int, float) or not math.isfinite(number) for number in value.values()):
        raise ValueError("图片范围必须是有限数值")
    result = {key: float(number) for key, number in value.items()}
    if (result["x"] < 0 or result["y"] < 0 or result["width"] <= 0 or result["height"] <= 0
            or result["x"] + result["width"] > 1.000001 or result["y"] + result["height"] > 1.000001):
        raise ValueError("图片范围超出页面")
    return result


def _manifest(source: dict[str, Any]) -> dict[str, Any]:
    manifest = load_document_manifest(source["document_id"])
    if (manifest.get("schema_version") != 3 or manifest.get("document_id") != source["document_id"]
            or manifest.get("source_sha256") != source["content_hash"]):
        raise ValueError("原始页面资产与本次冻结来源不一致")
    return manifest


def _page_blocks(page: dict[str, Any], text: str, source_hash: str) -> list[dict[str, Any]]:
    blocks, known = [], set()
    for original in page.get("blocks", []):
        if not isinstance(original, dict):
            raise ValueError("原始页面块必须是对象")
        block = deepcopy(original)
        identity = _text(block.get("block_id"), "原始块编号")
        if identity in known or block.get("asset_source_sha256") != source_hash:
            raise ValueError("原始页面块重复或不属于本次来源")
        known.add(identity)
        if block.get("type") == "image":
            block["bbox"] = _region(block.get("bbox"))
        elif "source_span" in block:
            span = block["source_span"]
            if (not isinstance(span, dict) or set(span) != {"start", "end"}
                    or any(type(span[key]) is not int for key in span)
                    or not 0 <= span["start"] <= span["end"] <= len(text)
                    or block.get("text") != text[span["start"]:span["end"]]):
                raise ValueError("原始页面块的文字与坐标范围不一致")
        blocks.append(block)
    return blocks


def build_evidence_catalog(source: dict[str, Any], requirement: str) -> dict[str, Any]:
    """只组织原文和原图锚点，不生成事实、摘要、治理或测试规划。"""
    if source["kind"] == "inline":
        return {"document_id": None, "items": [{
            "evidence_id": "EV-0001", "kind": "text", "text": requirement,
            "document_id": None, "page_number": None, "block_ids": [],
            "source_offset_start": 0, "source_offset_end": len(requirement),
            "asset_source_sha256": source["content_hash"],
        }]}
    manifest = _manifest(source)
    items, seen = [], set()
    for page in sorted(manifest["pages"], key=lambda value: value["page_number"]):
        number = page["page_number"]
        if type(number) is not int or number < 1 or number in seen:
            raise ValueError("原始页面编号无效或重复")
        seen.add(number)
        page_hash = _hash(page.get("image_sha256"), "页面图像摘要")
        text = document_page_text(source["document_id"], number)
        if not isinstance(text, str):
            raise ValueError("原始页面正文不是字符串")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != _hash(page.get("text_sha256"), "页面文字摘要"):
            raise ValueError("实际原文与冻结页面文字摘要不一致")
        blocks = _page_blocks(page, text, source["content_hash"])
        common = {"document_id": source["document_id"], "page_number": number,
                  "asset_source_sha256": source["content_hash"], "page_image_sha256": page_hash}
        if text:
            items.append({"evidence_id": f"EV-{len(items) + 1:04d}", "kind": "text", "text": text,
                          **common, "block_ids": [block["block_id"] for block in blocks if block.get("type") != "image"],
                          "source_offset_start": 0, "source_offset_end": len(text)})
        images = [block for block in blocks if block.get("type") == "image"]
        if not text and not images:
            images = [{"bbox": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}}]
        for block in images:
            items.append({"evidence_id": f"EV-{len(items) + 1:04d}", "kind": "image", "text": "",
                          **common, "block_ids": [block["block_id"]] if "block_id" in block else [],
                          "source_offset_start": 0, "source_offset_end": 0, "image_region": block["bbox"]})
    if not items:
        raise ValueError("本次来源没有可读取的原始证据")
    return {"document_id": source["document_id"], "items": items}


def _approval_state_exists(context, approval, source_run) -> None:
    node = context.db.get(AgentNodeRun, approval.node_run_id)
    if node is None or node.run_id != source_run.id:
        raise ValueError("人工批准缺少真实所属节点")
    state_hash = _hash(approval.request_payload.get("state_hash"), "人工批准状态摘要")
    store = ArtifactStore(context.db, user_id=context.user_id, project_id=context.project_id)
    blob = context.db.get(AgentArtifactBlob, store._ref(state_hash)["id"])
    if (blob is None or blob.user_id != context.user_id or blob.project_id != context.project_id
            or blob.sha256 != state_hash or blob.payload_hash != content_hash(blob.payload)):
        raise ValueError("人工批准缺少同用户同项目的不可变状态证明")


def approval_evidence_id(approval_id: int, item_number: int) -> str:
    """引用使用真实审批及原问题序号；完整问题身份另存，不让模型复写技术摘要。"""
    if any(type(value) is not int or value < 1 for value in (approval_id, item_number)):
        raise ValueError("批准来源必须绑定真实审批和正整数问题序号")
    return f"AP-{approval_id}-{item_number}"


def approved_source_decisions(context, source_ref: dict[str, Any], current_run) -> list[dict[str, Any]]:
    """按首次执行的UTC时钟冻结批准，不混用数据库本地创建时间。"""
    if current_run.started_at is None:
        raise ValueError("本次运行尚未开始，无法冻结批准目录")
    rows = context.db.execute(select(AgentApproval, AgentRun).join(
        AgentRun, AgentApproval.run_id == AgentRun.id,
    ).where(
        AgentApproval.status == "approved", AgentApproval.decided_by_user_id == context.user_id,
        AgentApproval.decided_at.is_not(None), AgentApproval.decided_at <= current_run.started_at,
        AgentRun.user_id == context.user_id, AgentRun.project_id == context.project_id,
    ).order_by(AgentApproval.id)).all()
    decisions = []
    expected = SourceSnapshot.from_dict(source_ref)
    for approval, source_run in rows:
        request = approval.request_payload
        if not isinstance(request, dict) or request.get("kind") != "source_conflicts":
            continue
        if persisted_source_snapshot(source_run) != expected:
            continue
        _approval_state_exists(context, approval, source_run)
        items = request.get("items")
        answers = (approval.decision_payload or {}).get("conflict_answers")
        if (not isinstance(items, list) or not items or not isinstance(answers, dict)
                or any(not isinstance(item, dict) for item in items)
                or len({item.get("id") for item in items}) != len(items)
                or set(answers) != {item.get("id") for item in items}):
            raise ValueError("人工批准的问题集合与真实答复不一致")
        for item_number, item in enumerate(items, 1):
            identity = _text(item.get("id"), "原批准问题身份")
            resolution = _text(answers[identity], "原批准答复")
            evidence_id = approval_evidence_id(approval.id, item_number)
            decisions.append({
                "evidence_id": evidence_id, "kind": "approved_decision",
                "approval_id": approval.id, "origin_run_id": source_run.id,
                "origin_node_id": approval.node_run_id, "original_issue_id": identity,
                "decided_by_user_id": approval.decided_by_user_id, "decided_at": approval.decided_at.isoformat(),
                "approval_state_hash": request["state_hash"], "source_ref": deepcopy(source_ref),
                "question": deepcopy(item), "resolution": resolution,
                "text": f"原始问题：{item.get('reason', '')}\n适用范围：{item.get('coverage_fragment', '')}\n人工批准原话：{resolution}",
            })
    return decisions


def prepare_generation_evidence(context, arguments: dict[str, Any]) -> dict[str, Any]:
    """读取本次固定来源和已批准口径；不写业务数据库。"""
    run = context.db.get(AgentRun, context.run_id)
    if run is None or (run.user_id, run.project_id) != (context.user_id, context.project_id):
        raise ValueError("生成来源的用户、项目或运行无效")
    document_id = arguments.get("requirement_doc_id")
    if document_id is not None:
        if type(document_id) is not int or document_id < 1:
            raise ValueError("需求文档编号无效")
        document = context.db.get(KnowledgeDocument, document_id)
        if (document is None or document.project_id != context.project_id
                or document.user_id not in (None, 0, context.user_id)):
            raise ValueError("需求文档不存在或不属于当前用户项目")
        snapshot = SourceSnapshot.from_document(document)
        requirement = _text(document.content, "原始需求正文")
        task_requirement = arguments.get("requirement", "")
        if not isinstance(task_requirement, str):
            raise ValueError("本轮用户补充必须是字符串")
    else:
        requirement = _text(arguments.get("requirement"), "原始需求正文").strip()
        snapshot = SourceSnapshot.from_text(requirement)
        task_requirement = ""
    frozen = persisted_source_snapshot(run)
    if frozen is None:
        raise ValueError("本次运行没有冻结的真实来源")
    assert_same_source(frozen, snapshot)
    source = snapshot.to_dict()
    if SOURCE_ARTIFACT_KEY in context.artifacts:
        assert_same_source(snapshot, SourceSnapshot.from_dict(context.artifacts[SOURCE_ARTIFACT_KEY]))
    catalog = build_evidence_catalog(source, requirement)
    if task_requirement.strip():
        # 补充只属于当前运行，不能改写PDF指纹、冒充人工批准或从历史任务自动复用。
        text_hash = hashlib.sha256(task_requirement.encode("utf-8")).hexdigest()
        catalog["items"].append({
            "evidence_id": "TR-0001", "kind": "task_requirement",
            "origin": "run_input.requirement", "text": task_requirement, "content_hash": text_hash,
            "user_id": context.user_id, "project_id": context.project_id, "run_id": context.run_id,
            "document_id": None, "page_number": None, "block_ids": [],
            "source_offset_start": 0, "source_offset_end": len(task_requirement),
        })
    approvals = approved_source_decisions(context, source, run)
    catalog["items"].extend(deepcopy(approvals))
    context.artifacts[SOURCE_ARTIFACT_KEY] = deepcopy(source)
    context.artifacts["requirement_evidence"] = {"source": deepcopy(source), "evidence_catalog": deepcopy(catalog)}
    return {"source_ref": source, "requirement": requirement, "evidence_catalog": catalog,
            "approved_decisions": approvals}


def source_image_descriptor(source_ref: dict[str, Any], page_number: int) -> dict[str, Any]:
    if source_ref["kind"] != "knowledge_document" or type(page_number) is not int or page_number < 1:
        raise ValueError("只有当前文档的真实页码可以查看原图")
    manifest = _manifest(source_ref)
    page = next((page for page in manifest["pages"] if page["page_number"] == page_number), None)
    if page is None:
        raise ValueError("页码不属于本次冻结文档")
    image_hash = _hash(page["image_sha256"], "页面图像摘要")
    image = document_page_image_path(source_ref["document_id"], page_number)
    if hashlib.sha256(image.read_bytes()).hexdigest() != image_hash:
        raise ValueError("实际页面图像与冻结资产摘要不一致")
    text = document_page_text(source_ref["document_id"], page_number)
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != _hash(page.get("text_sha256"), "页面文字摘要"):
        raise ValueError("实际页面文字与冻结资产摘要不一致")
    # 正文已在原始证据目录保存；图像描述只携带真实局部渲染所需锚点。
    blocks = [{key: deepcopy(block[key]) for key in ("block_id", "type", "bbox")}
              for block in _page_blocks(page, text, source_ref["content_hash"]) if block.get("type") == "image"]
    return {"source_kind": "document", "document_id": source_ref["document_id"], "page_number": page_number,
            "asset_source_sha256": source_ref["content_hash"], "page_image_sha256": image_hash,
            "region": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            "blocks": blocks}
