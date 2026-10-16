"""只读回放真实 Run 的图文来源链路，验证资产、压缩恢复和锚点契约，不调用模型。"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jsonschema import Draft202012Validator
from sqlalchemy import text

from core.db.database import SessionLocal
from core.db.model_defs import AgentRun, AgentWorkflowDefinition
from modules.agent_platform.contracts import WorkflowGraph, parse_execution_definition
from modules.agent_platform.registry import BUILTIN_TOOL_SPECS, ToolExecutionContext, tool_registry
from modules.agent_platform.runtime import _node_input
from modules.agent_platform.sources import SOURCE_ARTIFACT_KEY, persisted_source_snapshot
from modules.agent_platform import test_generation_semantics as semantics
from modules.agent_platform.test_generation_schemas import (
    SOURCE_ANCHOR_SCHEMA,
    SOURCE_SEMANTICS_INPUT_SCHEMA,
)
from modules.knowledge_base_components.document.document_asset_service import (
    document_page_text,
    load_document_manifest,
)


def _replay_source_tools(db: Any, run: AgentRun) -> tuple[Any, Any, ToolExecutionContext]:
    """复用数据库工作流的入参映射和当前注册工具，避免脚本复制生产数据流。"""

    snapshot = persisted_source_snapshot(run)
    if snapshot is None or snapshot.kind != "knowledge_document":
        raise ValueError("需要绑定真实含图需求文档的 Run，并保留其来源快照")
    workflow = db.get(AgentWorkflowDefinition, run.workflow_definition_id)
    if workflow is None or workflow.workflow_key != "test_generation":
        raise ValueError("需要真实 test_generation 工作流的 Run")
    graph = parse_execution_definition(workflow.definition)
    if not isinstance(graph, WorkflowGraph):
        raise ValueError("当前验证需要包含来源准备节点的真实 DAG 工作流")
    context = ToolExecutionContext(
        db=db,
        user_id=run.user_id,
        project_id=run.project_id,
        run_id=run.id,
        node_key="",
        run_input=deepcopy(run.input_payload),
        # 只继承可信来源身份；压缩视图由真实输入重新计算，避免复用历史缓存。
        artifacts={SOURCE_ARTIFACT_KEY: snapshot.to_dict()},
    )
    outputs: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for handler_key in (
        "testing.resolve_requirement_evidence",
        "testing.prepare_source_semantics",
    ):
        spec = next(spec for spec in BUILTIN_TOOL_SPECS if spec["handler_key"] == handler_key)
        nodes = [node for node in graph.nodes if node.reference_key == spec["tool_key"]]
        if len(nodes) != 1 or nodes[0].node_type != "tool":
            raise ValueError(f"工作流必须包含唯一确定性来源工具节点: {handler_key}")
        node = nodes[0]
        arguments = deepcopy(_node_input(run, node, outputs))
        Draft202012Validator(spec["input_schema"]).validate(arguments)
        context.node_key = node.node_key
        output = tool_registry.resolve(handler_key)(context, arguments)
        Draft202012Validator(spec["output_schema"]).validate(output)
        outputs[node.node_key] = output
        results.append(output)
    return results[0], results[1], context


def verify_run(run_id: int) -> dict[str, Any]:
    """核对每一个真实图片块和允许引用的正文块，不构造模型业务事实。"""

    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            run = db.get(AgentRun, run_id)
            if run is None:
                raise ValueError(f"数据库中不存在真实 Run: run_id={run_id}")
            evidence, prepared, context = _replay_source_tools(db, run)
            document_id = int(evidence["source"]["document_id"])
            manifest = load_document_manifest(document_id)
            manifest_pages = {int(page["page_number"]): page for page in manifest["pages"]}
            images = {
                (page_number, str(block["block_id"])): block
                for page_number, page in manifest_pages.items()
                for block in page["blocks"]
                if block.get("type") == "image"
            }
            if not images:
                raise ValueError(
                    f"需要真实含图文档进行验证，当前文档没有原生图片块: document_id={document_id}"
                )
            image_evidence = [
                item for item in evidence["evidence_catalog"]["items"] if "image_region" in item
            ]
            catalog_images = set()
            for item in image_evidence:
                assert len(item["block_ids"]) == 1, "图片证据必须指向唯一真实图片块"
                identity = (int(item["page_number"]), str(item["block_ids"][0]))
                assert identity not in catalog_images, f"图片证据重复: {identity}"
                catalog_images.add(identity)
                assert identity in images, f"图片证据引用未知资产块: {identity}"
                assert item["document_id"] == document_id
                assert item["image_region"] == images[identity]["bbox"]
                assert item["page_image_sha256"] == manifest_pages[identity[0]]["image_sha256"]
                assert item["asset_source_sha256"] == manifest["source_sha256"]
            assert catalog_images == set(images), "证据目录遗漏了真实图片块"

            anchor_validator = Draft202012Validator(SOURCE_ANCHOR_SCHEMA)
            input_validator = Draft202012Validator(SOURCE_SEMANTICS_INPUT_SCHEMA)
            model_images: set[tuple[int, str]] = set()
            retained_images: set[tuple[int, str]] = set()
            namespaces: dict[str, tuple[int, tuple[str, ...]]] = {}
            text_anchor_count = 0
            image_anchor_count = 0
            page_summaries = []
            for item in [*prepared["text_items"], *prepared["vision_items"]]:
                input_validator.validate(item)
                # 恢复原页前先验证模型实际收到的块，防止恢复过程掩盖压缩遗漏。
                for model_page in item.get("pages") or [item]:
                    model_blocks = {block["block_id"]: block for block in model_page["blocks"]}
                    for scope in model_page["source_scopes"]:
                        assert set(scope["allowed_block_ids"]).issubset(model_blocks)
                    for block_id, block in model_blocks.items():
                        if block.get("type") == "image":
                            identity = (int(model_page["page_number"]), str(block_id))
                            assert identity in images and block["bbox"] == images[identity]["bbox"]
                            model_images.add(identity)
                        else:
                            span = block["source_span"]
                            assert model_page["page_text"][span["start"]:span["end"]] == block["text"]
                hydrated = semantics._hydrate_source_semantics_item(context, deepcopy(item))
                input_validator.validate(hydrated)
                for page in hydrated.get("pages") or [hydrated]:
                    page_number = int(page["page_number"])
                    assert page["document_id"] == document_id
                    assert page["page_text"] == document_page_text(document_id, page_number)
                    assert page["page_image_sha256"] == manifest_pages[page_number]["image_sha256"]
                    assert page["asset_source_sha256"] == manifest["source_sha256"]
                    allowed = {
                        str(block_id)
                        for scope in page["source_scopes"]
                        for block_id in scope["allowed_block_ids"]
                    }
                    blocks = {str(block["block_id"]): block for block in page["blocks"]}
                    raw_blocks = {
                        str(block["block_id"]): block for block in manifest_pages[page_number]["blocks"]
                    }
                    assert allowed, f"来源页没有允许引用的真实块: page_number={page_number}"
                    for block_id in sorted(allowed):
                        assert block_id in raw_blocks and block_id in blocks
                        raw_anchor = {
                            "document_id": document_id,
                            "page_number": page_number,
                            "block_id": block_id,
                        }
                        normalized = semantics._validated_document_anchor(raw_anchor, page)
                        anchor, _, scope_ids = normalized
                        anchor_validator.validate(anchor)
                        assert len(scope_ids) == 1, f"来源锚点作用域不唯一: {block_id}"
                        assert semantics._validated_document_anchor(deepcopy(anchor), page) == normalized
                        if blocks[block_id].get("type") == "image":
                            image_anchor_count += 1
                            retained_images.add((page_number, block_id))
                            assert blocks[block_id]["bbox"] == raw_blocks[block_id]["bbox"]
                            assert anchor["image_region"] == raw_blocks[block_id]["bbox"]
                            assert "quote" not in anchor and "source_span" not in anchor
                        else:
                            text_anchor_count += 1
                            assert blocks[block_id]["source_span"] == raw_blocks[block_id]["source_span"]
                            assert blocks[block_id]["text"] == raw_blocks[block_id]["text"]
                    namespace = semantics._fact_namespace(page)
                    scope_identity = (page_number, tuple(sorted(allowed)))
                    assert namespace not in namespaces or namespaces[namespace] == scope_identity
                    namespaces[namespace] = scope_identity
                    page_summaries.append({
                        "page_number": page_number,
                        "allowed_blocks": len(allowed),
                        "fact_namespace": namespace,
                    })
            assert model_images == set(images), "模型实际输入遗漏了真实图片证据"
            assert retained_images == set(images), "压缩或页面恢复遗漏了真实图片证据"
            assert not db.new and not db.dirty and not db.deleted, "只读验证不得修改数据库对象"
            return {
                "run_id": run_id,
                "document_id": document_id,
                "evidence_count": len(evidence["evidence_catalog"]["items"]),
                "manifest_image_blocks": len(images),
                "model_image_blocks": len(model_images),
                "retained_image_blocks": len(retained_images),
                "text_items": len(prepared["text_items"]),
                "vision_items": len(prepared["vision_items"]),
                "validated_text_anchors": text_anchor_count,
                "validated_image_anchors": image_anchor_count,
                "compressed_pages_restored": len(
                    context.artifacts.get("source_semantics_source_pages") or {}
                ),
                "pages": page_summaries,
                "tool_input_output_schemas_valid": True,
                "anchor_renormalization_idempotent": True,
                "database_read_only": True,
                "model_calls": 0,
            }
        finally:
            db.rollback()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True, help="数据库中的真实测试生成 Run ID")
    options = parser.parse_args()
    if options.run_id < 1:
        parser.error("--run-id 必须为正整数")
    print(json.dumps(verify_run(options.run_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
