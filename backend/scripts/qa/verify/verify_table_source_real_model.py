"""重提取真实 Run 指定页的当前来源分片；默认只准备，--execute 才调用项目模型。"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import fields, is_dataclass, replace
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import text

from core.ai.ai_client import get_client_for_user
from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentRun, AgentToolDefinition
from modules.agent_platform.action_journal import action_state_hash
from modules.agent_platform.autonomous_budget import AutonomousBudgetHooks
from modules.agent_platform.contracts import parse_execution_definition
from modules.agent_platform.definition_repository import AgentDefinitionRepository
from modules.agent_platform.request_diagnostics import ModelRequestDiagnostics, safe_exception_evidence
from modules.agent_platform.runtime import _agent_request_reservation
from modules.agent_platform.sdk_adapter import AgentOutputContract, resolve_agent_model_metadata, run_agent_async
from modules.agent_platform.test_generation_builtin_specs import BUILTIN_AGENT_SPECS, BUILTIN_TOOL_SPECS, BUILTIN_WORKFLOW_SPECS
from scripts.qa.verify.verify_generation_agent_real_model import provider_error_message
from scripts.qa.verify.verify_review_agent_real_model import builtin_definition
from scripts.qa.verify.verify_visual_source_real_data import _replay_source_tools


def _json_value(value):
    """只序列化 SDK 已提供的请求内容，不读取客户端连接或认证配置。"""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"无法完整保存 SDK 内容: {type(value).__name__}")


class SourceTranscript(ModelRequestDiagnostics):
    """保留实际 SDK 请求正文和模型响应，HTTP 密钥及连接参数不进入日志。"""

    def __init__(self, path: Path, fragment_index: int):
        super().__init__(on_event=self._record)
        self.path = path
        self.fragment_index = fragment_index

    def _record(self, event, item):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"fragment_index": self.fragment_index, "event": event, **item}, ensure_ascii=False) + "\n")
        print(json.dumps({"fragment_index": self.fragment_index, "event": event,
                          "status": item.get("status"), "sequence": item.get("sequence")}, ensure_ascii=False), flush=True)

    def start(self, context, agent, system_prompt, input_items):
        super().start(context, agent, system_prompt, input_items)
        self._record("sdk_request_content", {
            "system_prompt": system_prompt, "input_items": _json_value(input_items),
        })

    def finish(self, context, response):
        self._record("sdk_response_content", {"response": _json_value(response)})
        super().finish(context, response)


async def verify(run_id: int, page_number: int, output: Path, *, execute: bool):
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    started = perf_counter()
    input_path, transcript_path = output.with_suffix(".inputs.json"), output.with_suffix(".requests.jsonl")
    if any(path.exists() for path in (output, input_path, transcript_path)):
        raise FileExistsError("报告或输入/请求证据已存在，请使用新路径")
    report = {
        "status": "failed", "source_run_id": run_id, "page_number": page_number,
        "execution_mode": "real_model_execution" if execute else "prepare_only_no_model",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "online_database_writes": 0, "mysql_transaction": "READ ONLY",
        "input_path": str(input_path.resolve()), "transcript_path": str(transcript_path.resolve()),
        "scope_limit": "仅验证指定页当前来源分片的真实语义提取和生产锚点验收；不等于整轮规划、生成或正式交付。",
        "fragments": [],
    }
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            run = db.get(AgentRun, run_id)
            if run is None:
                raise ValueError("来源 Run 不存在")
            original_run_hash = action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status])
            evidence, prepared, base_context = _replay_source_tools(db, run)
            selected = [
                (index, item) for index, item in enumerate(prepared["text_items"])
                if any(page["page_number"] == page_number for page in item.get("pages") or [])
            ]
            if not selected:
                raise ValueError("指定页没有真实文本分片，不能替换为构造输入")
            if any(len(item["pages"]) != 1 for _, item in selected):
                raise ValueError("指定页与相邻页共用真实批次；本脚本不裁剪已有来源批次")
            graph_spec = next(spec for spec in BUILTIN_WORKFLOW_SPECS if spec["workflow_key"] == "test_generation")
            graph = parse_execution_definition(graph_spec["definition"])
            node = next(node for node in graph.nodes if node.node_key == "source_text")
            spec = next(spec for spec in BUILTIN_AGENT_SPECS if spec["agent_key"] == node.reference_key)
            stored = AgentDefinitionRepository(db).get_agent(project_id=run.project_id, agent_key=node.reference_key)
            if stored is None:
                raise ValueError("项目缺少真实来源 Agent 定义")
            definition = builtin_definition(AgentDefinition, spec, id=stored.id, user_id=run.user_id, project_id=run.project_id)
            definition.model = stored.model
            tool_specs = {spec["tool_key"]: spec for spec in BUILTIN_TOOL_SPECS}
            tools = [builtin_definition(AgentToolDefinition, tool_specs[key], user_id=run.user_id, project_id=run.project_id)
                     for key in definition.runtime_config["tool_keys"]]
            client = get_client_for_user(run.user_id, db)
            report.update({
                "document_id": evidence["source"]["document_id"],
                "source_sha256": evidence["source"]["content_hash"],
                "model": resolve_agent_model_metadata(db=db, user_id=run.user_id, agent_definition=definition),
                "source_input_sha256": action_state_hash([item for _, item in selected]),
                "definition_sha256": action_state_hash(spec),
                "fragment_count": len(selected),
            })
            input_path.write_text(json.dumps({
                "source_run_id": run_id, "page_number": page_number,
                "source_input_sha256": report["source_input_sha256"],
                "agent_spec": spec, "tool_specs": [tool_specs[key] for key in definition.runtime_config["tool_keys"]],
                "source_fragments": [{"source_item_index": index, "input": item} for index, item in selected],
                "source_artifacts": base_context.artifacts,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            for index, item in selected:
                context = replace(base_context, node_key=node.node_key, tool_calls=[], executed_tools=[])
                reservation = _agent_request_reservation(definition=definition, tools=tools, input_payload=item)
                record = {"source_item_index": index, "input_sha256": action_state_hash(item),
                          "request_reservation": reservation, "status": "prepared"}
                report["fragments"].append(record)
                if not execute:
                    continue
                hooks = AutonomousBudgetHooks(reservation)
                diagnostics = SourceTranscript(transcript_path, index)
                contract = AgentOutputContract(deepcopy(item), node.map_config)
                try:
                    result = await run_agent_async(
                        db=db, agent_definition=definition, tool_definitions=tools, execution_context=context,
                        input_payload=deepcopy(item), request_timeout_seconds=definition.runtime_config["request_timeout_seconds"],
                        skip_output_postprocessor=True, output_contract=contract, request_budget=reservation,
                        shared_budget_hooks=hooks, request_diagnostics=diagnostics, model_client=client,
                        cache_node=node,
                    )
                    record.update({"candidate": result.output, "tool_calls": result.tool_calls,
                                   "model_usage": result.usage, "execution_semantic_hash": result.cache_semantic_hash})
                    normalized = contract.validate(definition, context, deepcopy(result.output))
                    record.update(status="passed", normalized_output=normalized,
                                  normalized_sha256=action_state_hash(normalized))
                except Exception as error:
                    record.update(status="failed", error_type=type(error).__name__,
                                  exception_chain=safe_exception_evidence(error),
                                  provider_error_message_redacted=provider_error_message(error),
                                  tool_calls=deepcopy(context.tool_calls))
                    if isinstance(error, (ValueError, AssertionError)):
                        record["error_message"] = str(error)[:4000]
                    raise
                finally:
                    record["budget_usage"] = hooks.snapshot()
                    record["model_requests"] = diagnostics.requests
                    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            assert not db.new and not db.dirty and not db.deleted
            db.refresh(run)
            assert original_run_hash == action_state_hash([run.input_payload, run.run_context, run.output_payload, run.status])
            report.update(status="passed" if execute else "prepared", source_run_unchanged=True)
        except Exception as error:
            report.update(status="failed", error_type=type(error).__name__, exception_chain=safe_exception_evidence(error))
            if isinstance(error, (ValueError, AssertionError)):
                report["error_message"] = str(error)[:4000]
        finally:
            db.rollback()
    report["elapsed_seconds"] = round(perf_counter() - started, 3)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--page-number", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(verify(args.run_id, args.page_number, args.output, execute=args.execute))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "fragments"}, ensure_ascii=False, indent=2))
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
