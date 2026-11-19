"""在独立 Python 进程加载隔离草稿，不替换运行中的服务源码。"""

import importlib.util
from pathlib import Path
import sys


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[3]
sys.path.insert(0, str(ROOT / "backend"))


def load(name, relative_path):
    path = BASE / "concurrency-draft-tree" / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)


load("modules.agent_platform.retry_policy", "backend/modules/agent_platform/retry_policy.py")
load("modules.agent_platform.sdk_adapter", "backend/modules/agent_platform/sdk_adapter.py")

import pytest

raise SystemExit(pytest.main([
    str(BASE / "concurrency-draft-tree/backend/tests/agent_platform/test_managed_concurrency_real_data.py"),
    "-q", "--disable-warnings", f"--junitxml={BASE / 'managed-concurrency-draft-tests.xml'}",
]))
