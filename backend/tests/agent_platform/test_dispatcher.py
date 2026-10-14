"""只读取得真实投递记录，在脱离会话的 ORM 副本上验证任务与认领身份边界。"""

from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from core.db.database import engine
from core.db.model_defs import AgentRun
from modules.agent_platform.dispatcher import (
    _can_fail_unclaimed_run,
    _can_record_dispatched_task,
)
from modules.agent_platform.lifecycle import TERMINAL_RUN_STATUSES, new_claim_token


def _copy_run(run: AgentRun) -> AgentRun:
    """复制真实 ORM 列，测试中的生命周期变化不关联线上数据库会话。"""
    return AgentRun(**{
        column.key: deepcopy(getattr(run, column.key))
        for column in AgentRun.__table__.columns
    })


@pytest.fixture(scope="module")
def dispatched_runs() -> tuple[AgentRun, AgentRun]:
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            # 先读取小字段并排序，避免让数据库排序完整产物 JSON。
            identities = connection.execute(select(AgentRun.id, AgentRun.task_id).where(
                AgentRun.task_id.is_not(None), AgentRun.task_id != "",
            ).order_by(AgentRun.id.desc())).all()
            selected_ids = []
            task_ids = set()
            for run_id, task_id in identities:
                if task_id not in task_ids:
                    selected_ids.append(run_id)
                    task_ids.add(task_id)
                if len(selected_ids) == 2:
                    break
            if len(selected_ids) < 2:
                pytest.skip("真实数据库缺少两条具有不同 task_id 的 Agent Run，不能验证跨任务隔离")
            records = connection.execute(select(AgentRun.__table__).where(
                AgentRun.id.in_(selected_ids),
            )).mappings().all()
    except OperationalError as error:
        pytest.skip(f"真实数据库当前不可读取，未使用替代数据：{type(error).__name__}")
    by_id = {record["id"]: AgentRun(**deepcopy(dict(record))) for record in records}
    if len(by_id) != 2:
        pytest.skip("读取期间真实投递记录已不存在，未构造替代记录")
    return by_id[selected_ids[0]], by_id[selected_ids[1]]


def test_dispatch_failure_only_terminates_unclaimed_pending_run(dispatched_runs) -> None:
    source, _ = dispatched_runs
    run = _copy_run(source)
    run.status, run.task_id, run.claim_token = "pending", None, None
    assert _can_fail_unclaimed_run(run)

    run.claim_token = new_claim_token()
    assert not _can_fail_unclaimed_run(run)
    run.status = "running"
    assert not _can_fail_unclaimed_run(run)

    run.status, run.task_id, run.claim_token = "pending", source.task_id, None
    assert not _can_fail_unclaimed_run(run)
    run.task_id = None
    for status in TERMINAL_RUN_STATUSES:
        run.status = status
        assert not _can_fail_unclaimed_run(run), status


def test_dispatch_result_does_not_replace_another_executor_claim(dispatched_runs) -> None:
    source, other = dispatched_runs
    assert source.task_id != other.task_id
    run = _copy_run(source)
    run.status, run.task_id, run.claim_token = "pending", None, None
    assert _can_record_dispatched_task(run, source.task_id)

    run.claim_token = new_claim_token()
    assert run.claim_token != source.task_id
    assert not _can_record_dispatched_task(run, source.task_id)
    run.status, run.task_id = "running", source.task_id
    assert _can_record_dispatched_task(run, source.task_id)
    assert not _can_record_dispatched_task(run, other.task_id)
    run.task_id = None
    assert not _can_record_dispatched_task(run, source.task_id)

    run.task_id = source.task_id
    for status in TERMINAL_RUN_STATUSES:
        run.status = status
        assert not _can_record_dispatched_task(run, source.task_id), status
