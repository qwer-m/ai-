class _ManagedSessionConcurrency:
    """仅对新的受管专家会话限流；嵌套咨询与回执重放不重复占许可。"""

    def __init__(self, *, policy: AdaptiveConcurrency, on_change: Callable) -> None:
        self.policy = policy
        self._on_change = on_change
        self._condition = asyncio.Condition()
        self._active = 0

    @asynccontextmanager
    async def session(self):
        async with self._condition:
            await self._condition.wait_for(lambda: self._active < self.policy.effective_concurrency)
            self._active += 1
        try:
            yield
        except Exception as error:
            kind = model_transport_failure_kind(error)
            if kind is not None:
                async with self._condition:
                    adjustment = self.policy.record_failure(kind)
                    self._on_change(self.policy.snapshot(), adjustment)
            raise
        else:
            async with self._condition:
                adjustment = self.policy.record_success()
                self._on_change(self.policy.snapshot(), adjustment)
        finally:
            async with self._condition:
                self._active -= 1
                self._condition.notify_all()


def _managed_session_scope(client: Any, definition: AgentDefinition) -> dict[str, str]:
    """用真实模型与 provider 指纹关联压力，不按 Agent 名称或路由别名建隔离副本。"""
    provider, model_name, _ = _resolved_agent_model(client, definition)
    provider_identity = getattr(client, "_provider_cache_identity", None)
    if not callable(provider_identity):
        raise ValueError("受管会话并发作用域缺少真实 provider 指纹")
    provider_hash = str(provider_identity(provider))
    if not provider_hash:
        raise ValueError("受管会话并发作用域的 provider 指纹为空")
    return {"provider_hash": provider_hash, "model": model_name,
            "provider_class": f"{type(provider).__module__}.{type(provider).__qualname__}",
            "wire_api": str(getattr(provider, "wire_api", "") or "")}
