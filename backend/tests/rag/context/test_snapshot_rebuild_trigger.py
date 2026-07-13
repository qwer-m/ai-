from modules.knowledge_base_components.document.document_ops import _trigger_snapshot_rebuild_async


class _FakeModule:
    """中文注释：模拟知识库门面，仅记录入参。"""

    def __init__(self):
        self.calls = []

    def enqueue_context_snapshot_rebuild(self, **kwargs):
        self.calls.append(kwargs)
        return {"queued": True, "reason": "queued", "task_id": "task-x"}


class _FailModule:
    """中文注释：模拟触发异常，验证不会向上抛出。"""

    def enqueue_context_snapshot_rebuild(self, **kwargs):
        raise RuntimeError("enqueue failed")


def test_trigger_snapshot_rebuild_async_success():
    mod = _FakeModule()
    _trigger_snapshot_rebuild_async(
        mod,
        project_id=8,
        user_id=1,
        reason="document_added",
        db=object(),
    )
    assert len(mod.calls) == 1
    assert mod.calls[0]["project_id"] == 8
    assert mod.calls[0]["rebuild_reason_hint"] == "document_added"


def test_trigger_snapshot_rebuild_async_swallow_exception():
    mod = _FailModule()
    # 中文注释：触发失败也不应中断主流程。
    _trigger_snapshot_rebuild_async(
        mod,
        project_id=9,
        user_id=2,
        reason="document_deleted",
        db=object(),
    )
