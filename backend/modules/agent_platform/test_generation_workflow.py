"""注册当前单主Agent的领域动作；历史多层生成工作流不再提供执行入口。"""


def register_test_generation_tools(registry):
    from . import test_generation_agent as generation

    for name in ("read_source", "view_source_image", "commit_coverage_plan", "append_cases", "patch_cases",
                 "inspect_cases", "report_issue", "request_review", "request_confirmation", "finish_generation"):
        registry.register(f"testing.generation.{name}", getattr(generation, name), state_only=True)
    for name in ("initialize_generation_state", "observe_generation_state", "validate_generation_resolution",
                 "managed_generation_artifacts", "generation_model_material"):
        registry.register(f"testing.{name}", getattr(generation, name))
