import json
from pathlib import Path

import pytest

from core.db.model_defs import UITestCase
from modules.automation_components.services.ui_automation_export_service import UIAutomationExportService
from modules.automation_components.services.ui_test_case_service import UITestCaseService


SCRIPT = """
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LoginPage:
    def __init__(self, driver):
        self.driver = driver

    def login(self):
        return True


def main():
    assert LoginPage(object()).login()


if __name__ == "__main__":
    main()
"""


def _case(case_id: int, name: str, node_type: str, parent_id: int | None = None) -> UITestCase:
    return UITestCase(
        id=case_id,
        project_id=2,
        name=name,
        type=node_type,
        parent_id=parent_id,
        automation_type="app",
    )


def test_hierarchy_validation_allows_two_folders_and_third_level_file() -> None:
    rows = [
        _case(1, "登录", "folder"),
        _case(2, "游客", "folder", 1),
        _case(3, "游客登录", "file", 2),
    ]

    UITestCaseService.validate_hierarchy(rows=rows)

    with pytest.raises(ValueError, match="第三级只能"):
        UITestCaseService.validate_hierarchy(
            rows=[*rows, _case(4, "不允许的三级目录", "folder", 2)],
        )
    with pytest.raises(ValueError, match="子层级"):
        UITestCaseService.validate_hierarchy(rows=rows, moving_id=1, parent_id=2, node_type="folder")


def test_sync_hierarchy_moves_real_script_and_updates_manifest(tmp_path: Path) -> None:
    exporter = UIAutomationExportService(tmp_path)
    exported = exporter.export_operation(
        project_id=2,
        project_name="天天练",
        operation_name="游客登录",
        description="游客登录",
        steps=["点击手机登录", "关闭弹窗"],
        script=SCRIPT,
        automation_type="app",
        target="com.leleketang.SchoolFantasy/org.cocos2dx.cpp.AppActivity",
    )
    old_script = Path(exported["script_path"])
    filename = old_script.name
    first = _case(10, "登录", "folder")
    second = _case(11, "游客", "folder", 10)
    operation = _case(12, "游客登录", "file", 11)

    moved = exporter.sync_project_hierarchy(
        project_id=2,
        project_name="天天练",
        cases=[first, second, operation],
    )

    nested_script = tmp_path / "天天练" / "scripts" / "登录" / "游客" / filename
    assert moved[12] == str(nested_script)
    assert nested_script.is_file()
    assert not old_script.exists()
    manifest_path = tmp_path / "天天练" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == 3
    assert manifest["operations"][0]["test_case_id"] == 12
    assert manifest["operations"][0]["hierarchy"] == ["登录", "游客"]
    assert manifest["operations"][0]["script"] == f"scripts/登录/游客/{filename}"
    assert "Path(__file__).resolve().parents[3]" in nested_script.read_text(encoding="utf-8")

    second.parent_id = None
    exporter.sync_project_hierarchy(
        project_id=2,
        project_name="天天练",
        cases=[first, second, operation],
    )
    moved_again = tmp_path / "天天练" / "scripts" / "游客" / filename
    assert moved_again.is_file()
    assert not nested_script.exists()
    assert "Path(__file__).resolve().parents[2]" in moved_again.read_text(encoding="utf-8")
