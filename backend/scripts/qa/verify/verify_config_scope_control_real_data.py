"""只读真实配置与来源，验证费用控制、作用域和尝试身份；禁止外部网络请求。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from jose import jwt
from sqlalchemy import text

from core.ai.reasoning import (
    config_validation_fingerprint, read_config_validation, sign_config_validation,
    _validation_signing_key,
)
from core.db.database import SessionLocal
from core.db.model_defs import AgentDefinition, AgentRun, Project, SystemConfig
from core.ai.ai_client import get_client_for_user
from modules.agent_platform.run_contract import execution_attempt_number, model_route_snapshot
from modules.knowledge_base_components.context.context_memory_repository import (
    ContextMemoryRepository, _owned_scope_id,
)
from modules.knowledge_base_components.ports.context_memory_port import ContextQuery
from routers.system.config_helpers import extract_target_nodes, normalize_metadata
from routers.system.config_models import ConfigValidateRequest
from routers.system.config_routes_runtime import _validate_model_config


def main() -> None:
    blocked_external_connections: list[str] = []

    def forbid_external_network(event: str, arguments: tuple) -> None:
        if event == "socket.connect":
            address = arguments[1]
            if isinstance(address, tuple) and str(address[0]) not in {"127.0.0.1", "::1", "localhost"}:
                blocked_external_connections.append(str(address[0]))
                raise RuntimeError("此验证禁止外部网络请求")

    sys.addaudithook(forbid_external_network)
    report = {"verification": "真实配置的零付费控制与作用域核验", "model_requests": 0,
              "database_writes": 0, "checks": {}}
    with SessionLocal(autoflush=False) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        config_id = db.query(SystemConfig.id).filter(SystemConfig.is_active == 1).order_by(SystemConfig.id.desc()).first()
        if config_id is None:
            raise RuntimeError("没有真实有效配置，不能进行本次验证")
        config = db.get(SystemConfig, config_id[0])
        user_id = int(config.user_id)
        projects = db.query(Project.id).filter(Project.user_id == user_id).order_by(Project.id).all()
        if len(projects) < 2:
            raise RuntimeError("需要两个真实已授权项目核对不同作用域")
        project_id, other_project_id = projects[0][0], projects[1][0]
        assert _owned_scope_id(user_id=user_id, project_id=project_id, scope="project", scope_id=str(project_id)) == str(project_id)
        assert _owned_scope_id(user_id=user_id, project_id=project_id, scope="user", scope_id=str(user_id)) == str(user_id)
        rejections = 0
        for scope, scope_id in [("project", str(other_project_id)), ("user", str(other_project_id)), ("global", None)]:
            try:
                _owned_scope_id(user_id=user_id, project_id=project_id, scope=scope, scope_id=scope_id)
            except ValueError:
                rejections += 1
        assert rejections == 3
        report["checks"]["scope_identity_rejections"] = rejections
        repository = ContextMemoryRepository(db)
        records = repository.list_active(query=ContextQuery(text="来源治理", user_id=user_id, project_id=project_id),
                                          source_kinds=["long_term_memory", "user_preference"])
        assert all(record.scope == "global" or record.user_id == user_id for record in records)
        report["checks"]["current_memory_rows"] = len(records)
        report["checks"]["scope_limit"] = "写入身份确定性核验通过；当前无跨用户真实记录，不声称多租户泄漏实测已完成。"

        # 仅验证签名、绑定与有效期。内容取自现有真实配置元数据，不代表新模型能力验证。
        metadata = normalize_metadata(config)
        routes = [{"provider": config.provider, "model": config.model_name, "endpoint": config.base_url}]
        fingerprint = config_validation_fingerprint(user_id, routes)
        details = {"reasoning": metadata.get("reasoning", {})}
        token = sign_config_validation(user_id=user_id, fingerprint=fingerprint, details=details)
        assert read_config_validation(token, user_id=user_id, fingerprint=fingerprint) == details
        assert read_config_validation(token, user_id=user_id, fingerprint="0" * 64) is None
        assert read_config_validation(token, user_id=user_id + 1, fingerprint=fingerprint) is None
        expired = jwt.encode({"purpose": "model-config-validation", "version": 1,
                              "user_id": user_id, "fingerprint": fingerprint, "details": details,
                              "iat": datetime.now(timezone.utc) - timedelta(hours=2),
                              "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
                             _validation_signing_key(), algorithm="HS256")
        assert read_config_validation(expired, user_id=user_id, fingerprint=fingerprint) is None
        forged = jwt.encode({"purpose": "model-config-validation", "version": 1,
                             "user_id": user_id, "fingerprint": fingerprint, "details": details,
                             "iat": datetime.now(timezone.utc),
                             "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                            "dev-secret-key-change-in-production", algorithm="HS256")
        assert read_config_validation(forged, user_id=user_id, fingerprint=fingerprint) is None
        report["checks"]["validation_signature_user_config_expiry"] = True
        report["checks"]["public_default_secret_cannot_forge_validation"] = True
        definition_id = db.query(AgentDefinition.id).filter_by(agent_key="test_business_planner", enabled=True).order_by(AgentDefinition.id.desc()).first()
        if definition_id is None:
            raise RuntimeError("没有真实规划 Agent 定义")
        definition = db.get(AgentDefinition, definition_id[0])
        client = get_client_for_user(user_id, db)
        route_snapshot = model_route_snapshot(client, definition)
        assert route_snapshot and all("reasoning_effort" in route for route in route_snapshot.values())
        for route, identity in route_snapshot.items():
            provider = getattr(client, {"main": "provider", "review": "review_provider", "turbo": "turbo_provider", "vision": "vl_provider"}[route]) or client.provider
            assert identity["reasoning_effort"] == (getattr(provider, "reasoning_effort", None)
                                                     if identity["model"] == provider.model else None)
        report["checks"]["actual_reasoning_route_identity_frozen"] = True

        targets = extract_target_nodes(metadata)
        reasoning = metadata.get("reasoning", {})
        request_values = {"provider": config.provider, "model_name": config.model_name,
                          "base_url": config.base_url, "turbo_model_name": config.turbo_model_name,
                          "review_model_name": targets.get("review", {}).get("model_name"),
                          "vl_model_name": config.vl_model_name,
                          "reasoning_effort": reasoning.get("main", {}).get("effort")}
        for route, prefix in [("turbo", "turbo"), ("review", "review"), ("vision", "vl")]:
            target = targets.get(route, {})
            for key in ("follow_main", "provider", "base_url"):
                if key in target:
                    request_values[f"{prefix}_{key}"] = target[key]
            request_values[f"{prefix}_reasoning_effort"] = reasoning.get(route, {}).get("effort")
        request = ConfigValidateRequest(**request_values)
        result = _validate_model_config(request, db, user_id, for_save=True)
        assert result["valid"] is False and "保存不会自动发起付费请求" in result["error"]
        report["checks"]["save_without_matching_evidence_sends_no_request"] = True

        identities = db.query(AgentRun.id, AgentRun.run_context["logical_task_id"].as_string(),
                              AgentRun.run_context["attempt_id"].as_string()).filter(AgentRun.user_id == user_id).all()
        for run_id, logical_task_id, identity in identities:
            context = {"logical_task_id": logical_task_id, "attempt_id": identity}
            number = execution_attempt_number(context)
            # 只改变界面计数的投影，真实身份的解析结果不受它影响。
            assert execution_attempt_number({**context, "run_attempt": 1, "attempt_display_offset": number - 1}) == number
        report["checks"]["real_run_identities_checked"] = len(identities)
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    assert not blocked_external_connections
    report["blocked_external_connection_attempts"] = len(blocked_external_connections)
    report["verified_at"] = datetime.now(timezone.utc).isoformat()
    path = Path(__file__).resolve().parents[4] / "output/qa/2026-11-11-config-scope-control-real-data.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
