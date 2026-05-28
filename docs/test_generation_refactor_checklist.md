# Test Generation Refactor Checklist

## Goal

Reduce duplicate generation, remove hardcoded governance data, and route human positive/negative feedback into the generation pipeline through stable structured controls.

## Progress

- [x] Create a shared scenario policy registry for duplicate governance.
- [x] Move new domain scenario families out of code into `scenario_registry_data.json`.
- [x] Let coverage governance use registered scenario families and caps.
- [x] Let judge duplicate detection reuse registered scenario families.
- [x] Let priority sample pool negative `redundant_case` feedback produce structured scenario caps.
- [x] Let priority sample pool positive feedback produce structured must-have scenario families.
- [x] Add registry governance metadata: source, status, documents.
- [x] Add registry judge policy fields: threshold and cross-module behavior.
- [x] Add registry safety checks for duplicate keys and unknown domains.
- [x] Migrate first coverage/judge duplicate-policy batch into registry data.
- [x] Migrate first judge-only duplicate-policy batch into registry data.
- [x] Migrate second judge-only duplicate-policy batch into registry data.
- [x] Migrate fourth shared duplicate-policy batch into registry data.

## Next Work

- [ ] Continue migrating older coverage scenario patterns into registry data in small verified batches.
- [ ] Continue migrating older judge-only duplicate scenario patterns into registry data in small verified batches.
- [ ] Add a controlled persistence path from recurring sample-pool signals into registry candidates.
- [ ] Split mode strategy constants from generation logic into configuration data.
- [ ] Split priority and P0 anchor rules from postprocess logic into configuration data.
- [ ] Split expected-result quality rules from postprocess logic into configuration data.
- [ ] Add diagnostics that explain which registry policy changed each generated batch.
- [ ] Verify streaming output and normalized persisted output for one fresh full generation.

## Verification Baseline

- `backend/tests/generation/test_scenario_policy_registry.py`
- `backend/tests/generation/test_feedback_control_switch.py`
- `backend/tests/rag/generation/test_judge_repairer.py`
- `backend/tests/generation/test_case_structure_analyzer.py`
- `backend/tests/generation/test_priority_sample_pool_store_delete.py`
- `backend/tests/generation/test_final_case_learning_service.py`
- `backend/tests/generation/test_priority_pool_regression.py`
