"""独立进程只装载QA草稿模块，在线Worker与生产文件均保持原样。"""

import importlib.util
import json
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[4]
sys.path.insert(0, str(ROOT / "backend"))
for name, relative in (("modules.agent_platform.request_diagnostics", "modules/agent_platform/request_diagnostics.py"),
                       ("modules.agent_platform.sdk_adapter", "modules/agent_platform/sdk_adapter.py")):
    path = BASE / "draft-tree/backend" / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

import pytest
sys.exit(pytest.main([str(BASE / "draft-tree/backend/tests/agent_platform/test_request_boundary_retention_real_data.py"),
    str(ROOT / "backend/tests/agent_platform/test_pause_observability_real_data.py"),
    "-q", "--junitxml=" + str(BASE / "draft-tests.xml")]))
