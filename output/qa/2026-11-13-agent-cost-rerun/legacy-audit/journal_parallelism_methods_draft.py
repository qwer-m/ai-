    def managed_parallelism(self, *, scope_key, scope):
        """读取执行控制头，不展开领域状态或历史专业产物。"""
        if scope_key != action_state_hash(scope):
            raise ActionStateConflict("受管并发作用域摘要不一致")
        with self._transaction() as node:
            checkpoint = self._checkpoint(node)
            if checkpoint is None:
                raise ActionStateConflict("受管并发控制缺少检查点")
            records = checkpoint.get("managed_parallelism", {})
            if not isinstance(records, dict):
                raise ActionStateConflict("受管并发控制目录无效")
            record = records.get(scope_key)
            if record is None:
                return None
            if not isinstance(record, dict) or record.get("scope") != scope or not isinstance(record.get("policy"), dict):
                raise ActionStateConflict("受管并发控制不属于当前真实模型作用域")
            return deepcopy(record["policy"])

    def save_managed_parallelism(self, *, scope_key, scope, policy, adjustment=None):
        """执行权校验与分支沿用现有事务；不改领域哈希或已用预算。"""
        if scope_key != action_state_hash(scope) or not isinstance(policy, dict):
            raise ActionStateConflict("受管并发控制的作用域或快照无效")
        with self._transaction() as node:
            checkpoint = self._checkpoint(node, fork=True)
            if checkpoint is None:
                raise ActionStateConflict("受管并发控制缺少检查点")
            records = deepcopy(checkpoint.get("managed_parallelism", {}))
            existing = records.get(scope_key)
            if existing is not None and existing.get("scope") != scope:
                raise ActionStateConflict("同一并发作用域不能绑定不同模型")
            record = {"scope": deepcopy(scope), "policy": deepcopy(policy)}
            if existing == record:
                return
            records[scope_key] = record
            checkpoint["managed_parallelism"] = records
            self._save(node, checkpoint)
            if adjustment is not None:
                self.repo.append_event(run_id=self.run_id, node_run_id=self.node_run_id,
                    event_type="managed_concurrency_adjusted",
                    payload={"scope_key": scope_key, "scope": deepcopy(scope), **deepcopy(adjustment)})
