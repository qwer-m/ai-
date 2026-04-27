from __future__ import annotations

from typing import Any

from modules.memory_fabric.adapters import (
    MySQLEpisodicStore,
    MySQLRuleStore,
    RedisWorkingStore,
    SemanticStore,
)
from modules.memory_fabric.contracts import MemoryContext, MemoryFabric


class DefaultMemoryFabric(MemoryFabric):
    def __init__(
        self,
        *,
        working_store: RedisWorkingStore | None = None,
        episodic_store: MySQLEpisodicStore | None = None,
        semantic_store: SemanticStore | None = None,
        rule_store: MySQLRuleStore | None = None,
    ) -> None:
        self._working_store = working_store or RedisWorkingStore()
        self._episodic_store = episodic_store or MySQLEpisodicStore()
        self._semantic_store = semantic_store or SemanticStore()
        self._rule_store = rule_store or MySQLRuleStore()

    def _validate_scope(self, ctx: MemoryContext) -> None:
        if not str(ctx.user_id or "").strip():
            raise ValueError("memory access requires user_id")
        if not str(ctx.project_id or "").strip():
            raise ValueError("memory access requires project_id")

    def read_working(self, key: str, ctx: MemoryContext) -> Any:
        self._validate_scope(ctx)
        return self._working_store.read(key, ctx)

    def write_working(self, key: str, value: Any, ctx: MemoryContext, ttl: int | None = None) -> None:
        self._validate_scope(ctx)
        self._working_store.write(key, value, ctx, ttl=ttl)

    def read_episodic(self, query: Any, ctx: MemoryContext) -> Any:
        self._validate_scope(ctx)
        return self._episodic_store.read(query if isinstance(query, dict) else {}, ctx)

    def write_episodic(self, record: Any, ctx: MemoryContext) -> None:
        self._validate_scope(ctx)
        self._episodic_store.write(record if isinstance(record, dict) else {}, ctx)

    def read_semantic(self, query: Any, ctx: MemoryContext) -> Any:
        self._validate_scope(ctx)
        return self._semantic_store.read(query if isinstance(query, dict) else {}, ctx)

    def write_semantic(self, doc: Any, ctx: MemoryContext) -> None:
        self._validate_scope(ctx)
        self._semantic_store.write(doc if isinstance(doc, dict) else {}, ctx)

    def read_rule(self, query: Any, ctx: MemoryContext) -> Any:
        self._validate_scope(ctx)
        return self._rule_store.read(query if isinstance(query, dict) else {}, ctx)

    def write_rule(self, rule_state: Any, ctx: MemoryContext) -> None:
        self._validate_scope(ctx)
        self._rule_store.write(rule_state if isinstance(rule_state, dict) else {}, ctx)

