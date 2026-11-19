"""只读真实Run63/doc287：验证工具与独立评审传输真实像素，不生成模型回执。"""

import asyncio
import base64
from copy import deepcopy
import hashlib
import io
import json
from types import SimpleNamespace

from agents import Agent, ModelSettings, RunContextWrapper
from agents.items import ItemHelpers
from agents.models.chatcmpl_converter import Converter
from agents.tool import FunctionTool, ToolOutputImage, ToolOutputText
from PIL import Image
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from core.db.database import engine
from core.db.model_defs import AgentNodeRun, AgentRun, KnowledgeDocument
from modules.agent_platform.autonomous_budget import (
    input_protocol_reservation, model_request_context_bytes, model_request_input_reservation,
)
from modules.agent_platform.generation_evidence import source_image_descriptor
from modules.agent_platform.registry import ToolExecutionContext
from modules.agent_platform.runtime import _agent_input_token_upper_bound
from modules.agent_platform.sdk_adapter import _chat_tool_images, _render_image_tool_result, _runner_input
from modules.agent_platform.sources import persisted_source_snapshot
from modules.agent_platform.test_generation_agent import initialize_generation_state, view_source_image
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS
from modules.knowledge_base_components.document.document_asset_service import (
    document_page_image_details, document_page_image_path, load_document_manifest,
)


_PROTECTED = text("""SELECT id, status,
 SHA2(CAST(input_payload AS CHAR),256) AS input_hash,
 SHA2(CAST(run_context AS CHAR),256) AS context_hash,
 SHA2(CAST(output_payload AS CHAR),256) AS output_hash
 FROM agent_runs WHERE id=63""")


@pytest.fixture(scope="module")
def actual_source():
    """SQL层禁止任何写入；不改历史Run的输入、状态或冻结契约。"""
    commands = []

    def only_select(connection, cursor, statement, parameters, context, executemany):
        command = statement.lstrip().split(None, 1)[0].upper()
        assert command == "SELECT", f"真实图像CPU验证禁止数据库写入：{command}"
        commands.append(command)

    with engine.connect() as connection:
        connection.exec_driver_sql("START TRANSACTION READ ONLY")
        event.listen(connection, "before_cursor_execute", only_select)
        try:
            with Session(bind=connection, autoflush=False, expire_on_commit=False) as db:
                protected = dict(db.execute(_PROTECTED).mappings().one())
                run = db.get(AgentRun, 63)
                assert run.status == "failed"
                source = persisted_source_snapshot(run).to_dict()
                assert source["document_id"] == 287
                manifest = load_document_manifest(source["document_id"])
                page = next(page for page in manifest["pages"]
                            if any(block.get("type") == "image" for block in page["blocks"]))
                descriptor = source_image_descriptor(source, page["page_number"])
                path = document_page_image_path(source["document_id"], page["page_number"])
                raw = path.read_bytes()
                block_ids = [block["block_id"] for block in descriptor["blocks"] if block.get("type") == "image"]
                details = document_page_image_details(source["document_id"], page["page_number"], block_ids)
                assert details and hashlib.sha256(raw).hexdigest() == descriptor["page_image_sha256"]
                action_metadata = db.scalar(select(
                    AgentNodeRun.sdk_state["action_checkpoint"]["projection"]["current_action"],
                ).where(AgentNodeRun.id == 418, AgentNodeRun.run_id == 63))
                assert action_metadata["tool_call_id"] and action_metadata["tool_key"]
                other_document_id = db.scalar(select(KnowledgeDocument.id).where(
                    KnowledgeDocument.id != source["document_id"],
                ).order_by(KnowledgeDocument.id).limit(1))
                assert other_document_id is not None
                yield SimpleNamespace(db=db, run=run, source=source, descriptor=descriptor,
                                      image_bytes=[raw, *(detail["image_bytes"] for detail in details)],
                                      image_path=path, commands=commands, action_metadata=action_metadata,
                                      other_document_id=other_document_id)
                assert dict(db.execute(_PROTECTED).mappings().one()) == protected
                assert not db.new and not db.dirty and not db.deleted
                assert hashlib.sha256(path.read_bytes()).hexdigest() == descriptor["page_image_sha256"]
                assert commands and set(commands) == {"SELECT"}
        finally:
            connection.rollback()
            event.remove(connection, "before_cursor_execute", only_select)


def _context(actual):
    arguments = {key: deepcopy(actual.run.input_payload[key])
                 for key in ("requirement", "requirement_doc_id", "case_budget")}
    arguments["batch_case_limit"] = 20
    context = ToolExecutionContext(db=actual.db, user_id=actual.run.user_id,
                                   project_id=actual.run.project_id, run_id=actual.run.id,
                                   node_key="agent_network", run_input=arguments)
    initialize_generation_state(context, arguments)
    return context


def _tool_outputs(actual, *, corrupt_field=None):
    context = _context(actual)
    spec = next(spec for spec in BUILTIN_TOOL_SPECS if spec["tool_key"] == "view_source_image")

    async def invoke(wrapper, arguments):
        # 实际执行当前领域工具；该工具只在内存记录已看页码，不使用模型替身。
        result = view_source_image(wrapper.context, json.loads(arguments))
        if corrupt_field is not None:
            result["page_image"] = _corrupt_descriptor(actual, corrupt_field)
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    tool = FunctionTool(name=spec["tool_key"], description=spec["description"],
                        params_json_schema=spec["input_schema"], on_invoke_tool=invoke)
    rendered = _render_image_tool_result(tool, SimpleNamespace(**spec), context)
    arguments = json.dumps({"page_number": actual.descriptor["page_number"]})
    output = asyncio.run(rendered.on_invoke_tool(RunContextWrapper(context=context), arguments))
    return context, output


def _decode_image(url):
    prefix, encoded = url.split(",", 1)
    assert prefix.startswith("data:image/") and prefix.endswith(";base64")
    return base64.b64decode(encoded, validate=True)


def _corrupt_descriptor(actual, field):
    descriptor = deepcopy(actual.descriptor)
    if field == "document_id":
        descriptor[field] = actual.other_document_id
    else:
        # 只损坏控制输入的身份，不改资产或历史，不制造新的模型结果。
        descriptor[field] = ("0" if descriptor[field][0] != "0" else "1") + descriptor[field][1:]
    return descriptor


def _assert_pixels(urls, expected):
    actual = [_decode_image(url) for url in urls]
    assert actual == expected
    for received, original in zip(actual, expected, strict=True):
        assert hashlib.sha256(received).digest() == hashlib.sha256(original).digest()
        with Image.open(io.BytesIO(received)) as left, Image.open(io.BytesIO(original)) as right:
            assert left.size == right.size and left.mode == right.mode
            assert left.tobytes() == right.tobytes()


def _sdk_items(outputs, actual):
    # 只调用SDK的结构化内容转换器，不构造模型function_call或模型响应。
    converted = ItemHelpers._convert_tool_output(outputs)
    assert isinstance(converted, list)
    # 历史真实调用身份仅作关联控制数据；图像来自当前CPU工具，不冒充原模型执行结果。
    return [{"type": "function_call_output", "call_id": actual.action_metadata["tool_call_id"], "output": converted}]


def test_main_image_tool_transports_actual_page_and_original_pdf_details(actual_source, record_property):
    context, outputs = _tool_outputs(actual_source)
    images = [item for item in outputs if isinstance(item, ToolOutputImage)]
    _assert_pixels([image.image_url for image in images], actual_source.image_bytes)
    assert all(image.detail == "high" for image in images)
    saved = json.loads(next(item.text for item in outputs if isinstance(item, ToolOutputText)))
    assert saved["page_image"] == actual_source.descriptor
    assert saved["viewed_page_numbers"] == [actual_source.descriptor["page_number"]]
    assert context.action_state["image_pages"] == {str(actual_source.descriptor["page_number"]): actual_source.descriptor}
    assert "base64," not in json.dumps(context.action_state, ensure_ascii=False)
    record_property("actual_page_number", actual_source.descriptor["page_number"])
    record_property("actual_image_sha256", actual_source.descriptor["page_image_sha256"])
    record_property("actual_image_count", len(images))
    record_property("model_requests", 0)


def test_review_document_evidence_transports_same_actual_images(actual_source):
    context = _context(actual_source)
    payload = {"source_ref": actual_source.source, "image_pages": [actual_source.descriptor]}
    rendered = _runner_input(execution_context=context, input_payload=payload,
                             runtime_config={"input_mode": "document_evidence"})
    images = [part["image_url"] for part in rendered[0]["content"] if part["type"] == "input_image"]
    _assert_pixels(images, actual_source.image_bytes)
    converted = Converter.items_to_messages(rendered)
    converted_images = [part["image_url"]["url"] for message in converted
                        for part in message["content"] if part["type"] == "image_url"]
    _assert_pixels(converted_images, actual_source.image_bytes)


def test_chat_sdk_converter_preserves_actual_tool_images_and_call_association(actual_source, record_property):
    _, outputs = _tool_outputs(actual_source)
    items = _sdk_items(outputs, actual_source)
    before = deepcopy(items)
    # 原SDK默认只留工具文字，这是真实传输缺口的CPU复现。
    old_messages = Converter.items_to_messages(items)
    assert all(part["type"] == "text" for message in old_messages for part in message["content"])
    transferred = _chat_tool_images(items)
    messages = Converter.items_to_messages(transferred)
    assert items == before
    tool_message, image_message = messages
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == actual_source.action_metadata["tool_call_id"]
    assert image_message["role"] == "user"
    assert actual_source.action_metadata["tool_call_id"] in image_message["content"][0]["text"]
    assert all(part["type"] == "text" for part in tool_message["content"])
    _assert_pixels([part["image_url"]["url"] for part in image_message["content"]
                    if part["type"] == "image_url"], actual_source.image_bytes)
    record_property("historical_metadata_origin", "Run63/Node418，仅用于CPU调用身份关联")
    record_property("new_model_receipt_created", False)


@pytest.mark.parametrize("field", ["document_id", "asset_source_sha256", "page_image_sha256"])
def test_actual_descriptor_corruption_rejected_at_source_boundary(actual_source, field):
    descriptor = _corrupt_descriptor(actual_source, field)
    with pytest.raises(ValueError, match="冻结的需求来源|页面图像指纹"):
        _runner_input(execution_context=_context(actual_source),
                      input_payload={"image_pages": [descriptor]}, runtime_config={"input_mode": "document_evidence"})


@pytest.mark.parametrize("field", ["document_id", "asset_source_sha256", "page_image_sha256"])
def test_main_tool_descriptor_corruption_also_rejected(actual_source, field):
    with pytest.raises(ValueError, match="冻结的需求来源|页面图像指纹"):
        _tool_outputs(actual_source, corrupt_field=field)


def test_duplicate_actual_review_page_rejected(actual_source):
    with pytest.raises(ValueError, match="重复装载同一页面"):
        _runner_input(execution_context=_context(actual_source),
                      input_payload={"image_pages": [actual_source.descriptor, actual_source.descriptor]},
                      runtime_config={"input_mode": "document_evidence"})


def test_sdk_image_input_budget_counts_visual_reservation_not_base64_characters(actual_source, record_property):
    _, outputs = _tool_outputs(actual_source)
    items = _sdk_items(outputs, actual_source)
    coordinator = next(spec for spec in BUILTIN_AGENT_SPECS if spec["agent_key"] == "test_generation_coordinator")
    agent = Agent(name=coordinator["name"], instructions=coordinator["instructions"],
                  model="gpt-4o",
                  model_settings=ModelSettings(max_tokens=coordinator["runtime_config"]["max_output_tokens"]))
    with_images = model_request_input_reservation(agent=agent, system_prompt=agent.instructions, input_items=items)
    without_encoded_pixels = deepcopy(items)
    image_count = 0
    for part in without_encoded_pixels[0]["output"]:
        if part["type"] == "input_image":
            del part["image_url"]
            image_count += 1
    assert image_count == len(actual_source.image_bytes)
    # 同样图像类型和数量，移除编码数据不影响费用准入；诊断字节数仍保留真实大小。
    assert with_images == model_request_input_reservation(
        agent=agent, system_prompt=agent.instructions, input_items=without_encoded_pixels)
    assert model_request_context_bytes(agent=agent, system_prompt=agent.instructions, input_items=items) > with_images
    record_property("actual_base64_bytes", sum(len(part.get("image_url", "")) for part in items[0]["output"]))
    record_property("input_reservation", with_images)


def test_review_runtime_preflight_reserves_actual_full_page_and_detail_images(actual_source):
    payload = {"image_pages": [actual_source.descriptor]}
    definition = SimpleNamespace(instructions="", output_schema={}, runtime_config={"input_mode": "document_evidence"})
    amount = _agent_input_token_upper_bound(definition=definition, tools=[], input_payload=payload)
    envelope = {"instructions": "", "output_schema": {}, "runtime_config": definition.runtime_config,
                "tools": [], "input": payload}
    from modules.agent_platform.token_counting import estimate_json_tokens
    text_tokens = estimate_json_tokens(envelope)
    assert amount == text_tokens + input_protocol_reservation(len(actual_source.image_bytes), text_tokens=text_tokens)
