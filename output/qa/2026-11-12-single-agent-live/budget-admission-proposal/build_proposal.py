"""仅生成 output 内的未应用补丁，不改正在执行的后端源码。"""

from pathlib import Path
import difflib
import hashlib
import json


ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
FILES = (
    "backend/modules/agent_platform/autonomous_budget.py",
    "backend/modules/agent_platform/registry.py",
    "backend/modules/agent_platform/request_diagnostics.py",
)


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError(f"补丁上下文不是唯一匹配: {old[:120]!r}")
    return source.replace(old, new, 1)


originals = {name: (ROOT / name).read_text(encoding="utf-8-sig") for name in FILES}
proposed = dict(originals)
name = FILES[0]
source = proposed[name]
source = replace_once(source, "        self._lock = Lock()\n        self._require_budget(self._charged)",
    "        self._lock = Lock()\n        # 只等待当前事件循环内的真实结算；未知请求的预留不属于可释放容量。\n"
    "        self._capacity_changed = asyncio.Event()\n        self._require_budget(self._charged)")
start = source.index("    async def on_llm_start(\n", source.index("class AutonomousBudgetHooks"))
end = source.index("    def settle_request(", start)
source = source[:start] + '''    def _notify_capacity_changed(self) -> None:
        # 替换通知世代再唤醒，避免结算恰好发生在登记等待与 await 之间时丢通知。
        with self._lock:
            previous = self._capacity_changed
            self._capacity_changed = asyncio.Event()
        previous.set()

    async def _reserve_request(
        self, context: RunContextWrapper[Any], reservation: dict[str, int],
        *, before_admission: Callable[[], None] | None = None,
    ) -> None:
        while True:
            # 预算等待后必须重新核对请求权限及执行权；检查失败不记一次尝试。
            if before_admission is not None:
                before_admission()
            with self._lock:
                if context in self._pending:
                    raise RuntimeError("同一模型上下文存在尚未结算的请求")
                self._require_budget(self._charged)
                self._require_budget(reservation)
                projected = {name: self._charged[name] + reservation[name] for name in _BUDGET_KEYS}
                if any(projected[name] > self._limits[name] for name in _BUDGET_KEYS):
                    # 次数是累计消耗，结算不能补回；没有在途请求也没有容量可等待。
                    if (not self._pending
                            or projected["attempted_requests"] > self._limits["attempted_requests"]):
                        self._require_budget(projected)
                    changed = self._capacity_changed
                else:
                    self._charged = projected
                    self._pending[context] = reservation
                    changed = None
            if changed is None:
                # 发送之前持久预留；失败时仍不允许模型请求继续。
                self._persist()
                return
            # 不持同步锁等待；现有硬超时、取消及失租监督会取消此等待。
            await changed.wait()

    async def on_llm_start(
        self,
        context: RunContextWrapper[Any],
        agent: Agent[Any],
        system_prompt: str | None,
        input_items: list[Any],
        *, before_admission: Callable[[], None] | None = None,
    ) -> None:
        reservation = model_request_reservation(
            agent=agent, system_prompt=system_prompt, input_items=input_items,
        )
        await self._reserve_request(context, reservation, before_admission=before_admission)

''' + source[end:]
source = replace_once(source, "        self._persist()\n        self._require_budget(self._charged)\n\n    def interrupt_requests",
    "        self._persist()\n        self._notify_capacity_changed()\n        self._require_budget(self._charged)\n\n    def interrupt_requests")
source = replace_once(source, "        if contexts:\n            self._persist()\n\n    async def on_llm_end",
    "        if contexts:\n            self._persist()\n            # 未知用量仍占额度，但等待者须知道已没有这批在途响应可等待。\n            self._notify_capacity_changed()\n\n    async def on_llm_end")
source = replace_once(source, "        system_prompt: str | None, input_items: list[Any],\n    ) -> None:\n        async with self._start_lock:",
    "        system_prompt: str | None, input_items: list[Any],\n        *, before_admission: Callable[[], None] | None = None,\n    ) -> None:\n        async with self._start_lock:")
source = replace_once(source, "            await self._parent.on_llm_start(context, agent, system_prompt, input_items)",
    "            await self._parent.on_llm_start(\n                context, agent, system_prompt, input_items, before_admission=before_admission,\n            )")
proposed[name] = source

name = FILES[1]
source = proposed[name]
source = replace_once(source, "        from .autonomous_budget import AgentSubtreeRequestBudgetExceeded",
    "        from .autonomous_budget import AgentExecutionBudgetExceeded, AgentSubtreeRequestBudgetExceeded")
source = replace_once(source, "        async def invoke(item, alternative, candidate):\n            async with semaphore:\n                self.require_request_allowed()",
    "        budget_failure: AgentExecutionBudgetExceeded | None = None\n"
    "        not_started_indexes: set[int] = set()\n\n"
    "        async def invoke(item, alternative, candidate, position):\n            nonlocal budget_failure\n            async with semaphore:\n"
    "                # 全局预算已确定拒绝后，不再启动本批排队中的专业会话。\n"
    "                if budget_failure is not None:\n                    not_started_indexes.add(position)\n                    return budget_failure\n                self.require_request_allowed()")
source = replace_once(source, "                    return error\n                except asyncio.CancelledError as error:",
    "                    return error\n                except AgentExecutionBudgetExceeded as error:\n"
    "                    if self.stop_on_failure:\n                        self.record_failure(error)\n                        raise\n"
    "                    # 拒绝新增请求不取消已经付费的兄弟；让真实响应及验收回执收敛。\n"
    "                    if budget_failure is None:\n                        budget_failure = error\n                    return error\n"
    "                except asyncio.CancelledError as error:")
source = replace_once(source, "        if transport_failures or (failures and not return_subtree_failures):",
    "        if budget_failure is not None or transport_failures or (failures and not return_subtree_failures):")
source = replace_once(source, "            # return_subtree_failures 只允许领域处理局部额度耗尽，连接故障仍交给运行时。\n            error = outputs[(transport_failures or failures)[0]]",
    "            # 全局预算拒绝不能作为领域成功返回；仅在已准入兄弟完成后交回运行时。\n"
    "            error = budget_failure if budget_failure is not None else outputs[(transport_failures or failures)[0]]")
source = replace_once(source, "outputs[position] = await invoke(current, alternate, validator)",
    "outputs[position] = await invoke(current, alternate, validator, position)")
source = replace_once(source, '"batch_progress": {"total_count": len(inputs), "failed_indexes": failures,',
    '"batch_progress": {"total_count": len(inputs),\n'
    '                                   "failed_indexes": [i for i in failures if i not in not_started_indexes],\n'
    '                                   "not_started_indexes": sorted(not_started_indexes),')
proposed[name] = source

name = FILES[2]
source = proposed[name]
source = replace_once(source, '''        context.context.require_request_allowed()
        require_model_request_claim(context.context)
        if self.budget_hooks is not None:
            await self.budget_hooks.on_llm_start(context, agent, system_prompt, input_items)
        self.diagnostics.start(context, agent, system_prompt, input_items)''',
'''        def before_admission() -> None:
            context.context.require_request_allowed()
            require_model_request_claim(context.context)

        if self.budget_hooks is not None:
            await self.budget_hooks.on_llm_start(
                context, agent, system_prompt, input_items, before_admission=before_admission,
            )
        else:
            before_admission()
        self.diagnostics.start(context, agent, system_prompt, input_items)''')
proposed[name] = source

patch = []
manifest = {"status": "draft_not_applied", "backend_files_written": 0, "files": {}}
for name in FILES:
    compile(proposed[name], name, "exec")
    patch.extend(difflib.unified_diff(originals[name].splitlines(True), proposed[name].splitlines(True),
                                    fromfile="a/" + name, tofile="b/" + name))
    manifest["files"][name] = {"original_sha256": hashlib.sha256((ROOT / name).read_bytes()).hexdigest(),
                               "proposed_text_sha256": hashlib.sha256(proposed[name].encode()).hexdigest()}
    # 验证器仅加载 output 中的预算候选；不覆盖后端模块或正在运行的 Worker。
    if name == FILES[0]:
        (HERE / "autonomous_budget_candidate.py").write_text(proposed[name], encoding="utf-8")
(HERE / "budget-admission.patch").write_text("".join(patch), encoding="utf-8")
(HERE / "proposal-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"patch": str(HERE / "budget-admission.patch"), "compiled_files": len(FILES),
                  "backend_files_written": 0}, ensure_ascii=False))
