"""在独立进程加载暂停观测草稿，生产文件与运行中的 Worker 保持冻结。"""

import importlib.util
from pathlib import Path
import sys


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[3]
sys.path.insert(0, str(ROOT / "backend"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, BASE / "pause-observability-draft-tree" / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)


load("modules.agent_platform.sdk_adapter", "backend/modules/agent_platform/sdk_adapter.py")
load("modules.agent_platform.runtime", "backend/modules/agent_platform/runtime.py")

import pytest

raise SystemExit(pytest.main([
    str(BASE / "pause-observability-draft-tree/backend/tests/agent_platform/test_pause_observability_real_data.py"),
    "-q", "--disable-warnings", f"--junitxml={BASE / 'pause-observability-draft-tests.xml'}",
]))
