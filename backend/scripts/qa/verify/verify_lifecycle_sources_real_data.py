"""只读提取真实运行，在独立内存数据库验证来源身份与生命周期，不调用模型。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from core.db.database import SessionLocal
from core.db.model_defs import (
    AgentApproval, AgentNodeRun, AgentRun, AgentRunEvent,
    AgentWorkflowDefinition, KnowledgeDocument,
)
from modules.agent_platform.lifecycle import (
    InvalidRunTransition, renew_run_lease, transition_run,
)
from modules.agent_platform.repository import AgentPlatformRepository
from modules.agent_platform.results import persisted_test_generation_result
from modules.agent_platform.sources import (
    SOURCE_ARTIFACT_KEY, SourceSnapshot, assert_same_source, persisted_source_snapshot,
)


@compiles(LONGTEXT, "sqlite")
def sqlite_longtext(_type: Any, _compiler: Any, **_options: Any) -> str:
    """隔离回放仅转换存储方言，不改变真实 ORM 模型或文本内容。"""
    return "TEXT"


def copy_record(record: Any) -> Any:
    """按真实 ORM 列复制，不改写需求、用例、审批内容。"""
    return type(record)(**{
        column.key: deepcopy(getattr(record, column.key))
        for column in record.__table__.columns
    })


@contextmanager
def isolated_repository(*records: Any) -> Iterator[AgentPlatformRepository]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    models = (
        AgentWorkflowDefinition, KnowledgeDocument, AgentRun,
        AgentNodeRun, AgentRunEvent, AgentApproval,
    )
    try:
        for model in models:
            model.__table__.create(engine)
        with Session(engine, expire_on_commit=False) as db:
            db.add_all([copy_record(record) for record in records])
            db.commit()
            yield AgentPlatformRepository(db)
    finally:
        engine.dispose()


def verify_sources(
    online: Session, selected: AgentRun, history: list[AgentRun],
) -> dict[str, Any]:
    snapshot = persisted_source_snapshot(selected)
    assert snapshot is not None and snapshot.document_id is not None
    assert SourceSnapshot.from_dict(snapshot.to_dict()) == snapshot
    document = online.get(KnowledgeDocument, snapshot.document_id)
    workflow = online.get(AgentWorkflowDefinition, selected.workflow_definition_id)
    assert document is not None and workflow is not None
    historical = []
    for run in history:
        source = persisted_source_snapshot(run)
        historical.append({
            "run_id": run.id, "status": run.status,
            "source_key": source.key if source else None,
        })

    current = SourceSnapshot.from_document(document)
    different_historical_run = next((
        run for run in history
        if (source := persisted_source_snapshot(run)) is not None and source.key != snapshot.key
    ), None)
    if different_historical_run is not None:
        other_source = persisted_source_snapshot(different_historical_run)
        assert other_source is not None
        try:
            assert_same_source(snapshot, other_source)
        except ValueError:
            pass
        else:
            raise AssertionError("两个真实但不同的来源不能被认为是同一来源")
    online_repo = AgentPlatformRepository(online)
    reusable = online_repo.get_latest_successful_run_for_source(
        project_id=selected.project_id, user_id=selected.user_id,
        workflow_key=workflow.workflow_key, requirement_doc_id=document.id,
    )
    if reusable is not None:
        reused_source = persisted_source_snapshot(reusable)
        assert reused_source is not None and reused_source.key == current.key
        assert persisted_test_generation_result(reusable) is not None

    with isolated_repository(selected, document, workflow) as repo:
        copied = repo.get_run(run_id=selected.id)
        assert copied is not None
        before = repo.get_latest_successful_run_for_source(
            project_id=selected.project_id, user_id=selected.user_id,
            workflow_key=workflow.workflow_key, requirement_doc_id=document.id,
        )
        if snapshot.key == current.key:
            assert before is not None and before.id == selected.id
        else:
            assert before is None
            try:
                assert_same_source(snapshot, current)
            except ValueError:
                pass
            else:
                raise AssertionError("历史快照与当前文档不同，不能复用原候选")

        # 故障注入只移除副本中的来源证据，当前真实文档和业务产物保持原样。
        context = deepcopy(copied.run_context or {})
        output = deepcopy(copied.output_payload or {})
        for container in (context, output):
            artifacts = dict(container.get("artifacts") or {})
            artifacts.pop(SOURCE_ARTIFACT_KEY, None)
            if isinstance(artifacts.get("requirement_evidence"), dict):
                artifacts["requirement_evidence"].pop("source", None)
            generation = artifacts.get("test_generation")
            if isinstance(generation, dict) and isinstance(generation.get("evidence"), dict):
                generation["evidence"].pop("source", None)
            container["artifacts"] = artifacts
        copied.run_context, copied.output_payload = context, output
        repo.db.commit()
        assert persisted_source_snapshot(copied) is None
        projected = repo._source_run_rows(
            project_id=selected.project_id, user_id=selected.user_id,
            workflow_definition_ids=[workflow.id], statuses={"success"},
        )
        assert len(projected) == 1 and projected[0][2] is None
        assert repo.get_latest_successful_run_for_source(
            project_id=selected.project_id, user_id=selected.user_id,
            workflow_key=workflow.workflow_key, requirement_doc_id=document.id,
        ) is None
        assert repo.resolve_source_snapshot(
            project_id=selected.project_id, input_payload=dict(selected.input_payload),
        ) == current

    return {
        "selected_run_id": selected.id,
        "document_id": document.id,
        "persisted_source_key": snapshot.key,
        "current_document_source_key": current.key,
        "latest_reusable_run_id": reusable.id if reusable else None,
        "historical_runs": historical,
        "snapshot_roundtrip": "passed",
        "repository_reuse": "passed",
        "different_real_source_rejected_run_id": (
            different_historical_run.id if different_historical_run is not None else None
        ),
        "missing_snapshot_rejected_despite_existing_document": "passed",
    }


def inject_running_state(run: AgentRun, now: datetime) -> None:
    """只在隔离副本注入运行状态，业务 JSON 不变。"""
    run.status = "running"
    run.finished_at = None
    run.claim_token = f"verification-only-{run.id}"
    run.task_id = f"verification-only-task-{run.id}"
    assert renew_run_lease(run, now=now, lease_seconds=60)


def verify_lifecycle(source: AgentRun, latest_event: AgentRunEvent | None) -> dict[str, Any]:
    records = (source, latest_event) if latest_event is not None else (source,)
    steps: list[str] = []
    rejected: list[str] = []
    with isolated_repository(*records) as repo:
        run = repo.get_run_for_update(run_id=source.id)
        assert run is not None
        original_business = deepcopy((run.input_payload, run.run_context, run.output_payload))
        at = datetime.utcnow().replace(microsecond=0)
        baseline_sequence = latest_event.sequence if latest_event is not None else 0

        def move(target: str) -> None:
            previous = run.status
            changed = transition_run(
                repo, run, target, event_type="verification_state_transition",
                payload={"source_run_id": source.id, "from": previous, "to": target}, now=at,
            )
            assert changed
            repo.commit()
            steps.append(f"{previous}->{target}")
            events = repo.db.scalars(select(AgentRunEvent).where(
                AgentRunEvent.run_id == run.id,
                AgentRunEvent.sequence > baseline_sequence,
            ).order_by(AgentRunEvent.sequence)).all()
            assert [event.sequence for event in events] == list(
                range(baseline_sequence + 1, baseline_sequence + len(steps) + 1)
            )
            assert events[-1].payload["to"] == target
            assert events[-1].created_at is not None
            assert run.finished_at == (at if target in {"success", "failed", "cancelled"} else None)
            if target != "running":
                assert run.claim_token is None and run.heartbeat_at is None and run.lease_expires_at is None
            if target == "pending":
                assert run.task_id is None

        # 真实成功运行的业务数据不变，仅把调度状态退回起点做隔离回放。
        run.status, run.finished_at = "pending", None
        move("running")
        inject_running_state(run, at)
        assert run.lease_expires_at == at + timedelta(seconds=60)
        move("waiting_approval")
        assert not renew_run_lease(run, now=at, lease_seconds=60)
        move("pending")
        move("running")
        inject_running_state(run, at)
        move("pending")
        move("running")
        inject_running_state(run, at)
        move("success")
        assert run.current_node_key is None

        for terminal in ("success", "failed", "cancelled"):
            if terminal != "success":
                inject_running_state(run, at)
                move(terminal)
            before = (run.status, run.finished_at, run.claim_token, len(steps))
            assert not renew_run_lease(run, now=at, lease_seconds=60)
            assert not transition_run(
                repo, run, terminal, event_type="verification_duplicate",
                payload={}, now=at,
            )
            for invalid in ("pending", "running"):
                try:
                    transition_run(repo, run, invalid, event_type="verification_invalid", payload={}, now=at)
                except InvalidRunTransition:
                    rejected.append(f"{terminal}->{invalid}")
                else:
                    raise AssertionError("终态运行被迟到操作重新激活")
            assert (run.status, run.finished_at, run.claim_token, len(steps)) == before
        assert (run.input_payload, run.run_context, run.output_payload) == original_business
        assert repo.db.scalar(select(AgentRunEvent.id).where(
            AgentRunEvent.event_type.in_(["verification_duplicate", "verification_invalid"]),
        ).limit(1)) is None
    return {
        "source_run_id": source.id, "transitions": steps,
        "terminal_reactivations_rejected": rejected,
        "lease_event_finished_at_consistency": "passed",
        "business_payload_preserved": "passed",
        "mysql_row_lock_concurrency": "not_tested_in_sqlite",
    }


def verify_approval(online: Session) -> dict[str, Any]:
    approval = online.scalars(select(AgentApproval).order_by(AgentApproval.id.desc()).limit(1)).first()
    if approval is None:
        return {"status": "not_tested", "reason": "数据库没有真实审批记录，未构造审批数据"}
    source = online.get(AgentRun, approval.run_id)
    node = online.get(AgentNodeRun, approval.node_run_id)
    assert source is not None and node is not None
    with isolated_repository(source, node, approval) as repo:
        copied_run = repo.get_run(run_id=source.id)
        copied_approval = repo.list_approvals(run_id=source.id)[0]
        assert copied_run is not None
        request_before = deepcopy(copied_approval.request_payload)
        at = datetime.utcnow().replace(microsecond=0)
        inject_running_state(copied_run, at)
        # 只把真实审批副本的状态置为待决，保留其完整原始请求。
        copied_approval.status = "pending"
        transition_run(
            repo, copied_run, "cancelled", event_type="verification_approval_closed",
            payload={"source_approval_id": approval.id}, now=at,
        )
        repo.commit()
        assert copied_approval.status == "rejected" and copied_approval.decided_at == at
        assert copied_approval.decision_payload["run_status"] == "cancelled"
        assert copied_approval.request_payload == request_before
    return {"status": "passed", "source_run_id": source.id, "source_approval_id": approval.id}


def verify_node_finalization(online: Session, source: AgentRun) -> dict[str, Any]:
    node_ids = online.scalars(select(AgentNodeRun.id).where(
        AgentNodeRun.run_id == source.id,
    ).order_by(AgentNodeRun.id).limit(6)).all()
    nodes = [online.get(AgentNodeRun, node_id) for node_id in node_ids]
    assert all(node is not None for node in nodes)
    if len(nodes) < 6:
        return {"status": "not_tested", "reason": "当前真实运行不足六个节点，未构造节点内容"}
    injected_statuses = ("pending", "running", "waiting_approval", "success", "failed", "cancelled")
    results = []
    for terminal in ("failed", "cancelled"):
        with isolated_repository(source, *nodes) as repo:
            run = repo.get_run(run_id=source.id)
            assert run is not None
            at = datetime.utcnow().replace(microsecond=0)
            inject_running_state(run, at)
            loaded_nodes = [repo.db.get(AgentNodeRun, original.id) for original in nodes]
            assert all(node is not None for node in loaded_nodes)
            preserved = {}
            business = {}
            for node, injected_status in zip(loaded_nodes, injected_statuses):
                assert node is not None
                # 只注入状态和相应结束时间，不伪造节点输入、模型输出或检查点。
                node.status = injected_status
                node.finished_at = None if injected_status in injected_statuses[:3] else (node.finished_at or at)
                preserved[node.id] = (node.status, node.finished_at, node.error_message)
                business[node.id] = deepcopy((node.input_payload, node.output_payload, node.sdk_state))
            reason = "隔离回放：运行终止"
            transition_run(
                repo, run, terminal, event_type="verification_node_finalization",
                payload={"source_run_id": source.id}, now=at, error_message=reason,
            )

            def assert_node_state(node: AgentNodeRun, index: int) -> None:
                expected = (terminal, at, reason) if index < 3 else preserved[node.id]
                assert (node.status, node.finished_at, node.error_message) == expected
                assert (node.input_payload, node.output_payload, node.sdk_state) == business[node.id]

            # 在提交或主动 refresh 之前检查，证明批量更新已同步会话中加载的对象。
            for index, node in enumerate(loaded_nodes):
                assert node is not None
                assert repo.db.get(AgentNodeRun, node.id) is node
                assert_node_state(node, index)
            repo.commit()
            with Session(repo.db.get_bind()) as persisted:
                for index, node in enumerate(loaded_nodes):
                    assert node is not None
                    stored = persisted.get(AgentNodeRun, node.id)
                    assert stored is not None
                    assert_node_state(stored, index)
            results.append({
                "run_terminal": terminal,
                "unfinished_states_finalized": list(injected_statuses[:3]),
                "terminal_states_preserved": list(injected_statuses[3:]),
                "loaded_orm_and_database_agree": "passed",
                "business_payload_preserved": "passed",
            })
    return {
        "status": "passed", "source_run_id": source.id,
        "real_node_ids": [node.id for node in nodes], "replays": results,
    }


def verify_stale_worker_lease(source: AgentRun) -> dict[str, Any]:
    from modules.agent_platform.runtime import _renew_progress_lease, _RunCancelled

    rejected = []
    for terminal in ("success", "failed", "cancelled"):
        with isolated_repository(source) as worker_repo:
            worker_run = worker_repo.get_run(run_id=source.id)
            assert worker_run is not None
            at = datetime.utcnow().replace(microsecond=0)
            inject_running_state(worker_run, at)
            worker_repo.commit()
            old_claim = worker_run.claim_token
            # 另一个真实 ORM 会话结束运行，使工作会话持有已过期的运行对象。
            with Session(worker_repo.db.get_bind()) as control_db:
                control_repo = AgentPlatformRepository(control_db)
                current = control_repo.get_run_for_update(run_id=source.id)
                assert current is not None
                transition_run(
                    control_repo, current, terminal, event_type="verification_external_finish",
                    payload={"source_run_id": source.id}, now=at,
                )
                control_repo.commit()
            assert worker_run.status == "running" and worker_run.claim_token == old_claim
            try:
                _renew_progress_lease(worker_repo, worker_run)
            except _RunCancelled:
                assert terminal == "cancelled"
            except RuntimeError as exc:
                assert terminal in {"success", "failed"} and "已不属于当前执行器" in str(exc)
            else:
                raise AssertionError("终态运行被过期工作会话重新续租")
            assert worker_run.status == terminal
            assert worker_run.claim_token is None and worker_run.heartbeat_at is None
            assert worker_run.lease_expires_at is None
            with Session(worker_repo.db.get_bind()) as persisted:
                stored = persisted.get(AgentRun, source.id)
                assert stored is not None and stored.status == terminal
                assert stored.claim_token is None and stored.lease_expires_at is None
                assert stored.finished_at == at
            rejected.append(terminal)
    return {
        "status": "passed", "source_run_id": source.id,
        "terminal_runs_reject_stale_worker_renewal": rejected,
        "mysql_row_lock_concurrency": "not_tested_in_sqlite",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, help="默认选择最近一个有文档来源证据的成功运行")
    args = parser.parse_args()
    with SessionLocal(autoflush=False) as online:
        online.execute(text("SET TRANSACTION READ ONLY"))
        history = online.scalars(select(AgentRun).order_by(AgentRun.id.desc()).limit(50)).all()
        selected = next((
            run for run in history
            if run.status == "success" and (args.run_id is None or run.id == args.run_id)
            and (snapshot := persisted_source_snapshot(run)) is not None
            and snapshot.document_id is not None and persisted_test_generation_result(run) is not None
        ), None)
        if selected is None:
            raise ValueError("未找到可回放的真实成功文档运行")
        latest_event = online.scalars(select(AgentRunEvent).where(
            AgentRunEvent.run_id == selected.id,
        ).order_by(AgentRunEvent.sequence.desc()).limit(1)).first()
        report = {
            "sources": verify_sources(online, selected, history),
            "lifecycle": verify_lifecycle(selected, latest_event),
            "node_finalization": verify_node_finalization(online, selected),
            "stale_worker_lease": verify_stale_worker_lease(selected),
            "approval": verify_approval(online),
            "online_transaction": "READ ONLY",
            "online_database_writes": 0,
            "model_calls": 0,
            "replay_database": "sqlite+pysqlite:///:memory:",
        }
        assert not online.new and not online.dirty and not online.deleted
        online.rollback()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
