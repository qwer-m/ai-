"""在同一隔离QA模块树运行相关既有真实回归，不加载生产改稿或调用模型。"""

from pathlib import Path
import runpy
import pytest

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[4]
# 只执行模块声明以装载QA tree，不执行该文件的21项测试。
runpy.run_path(str(BASE / "test_closed_feedback_real.py"))
raise SystemExit(pytest.main([
    str(ROOT / "backend/tests/agent_platform/test_planning_dependencies_real_data.py"),
    str(ROOT / "backend/tests/agent_platform/test_generation_request_order_real_data.py"),
    str(ROOT / "backend/tests/agent_platform/test_source_conflict_proof_real_data.py"),
    "-q", "--disable-warnings", "--maxfail=2", "--junitxml=" + str(BASE / "closed-related-regression.xml"),
]))
