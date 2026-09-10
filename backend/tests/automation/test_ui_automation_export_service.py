import ast
import json
from pathlib import Path

from modules.automation_components.services.ui_automation_export_service import UIAutomationExportService


SCRIPT = '''"""真实 UI 操作脚本。"""
from __future__ import annotations
import os

PHONE = os.getenv("TIANTIANLIAN_PHONE", "")

class LoginPage:
    def __init__(self, driver):
        self.driver = driver

    def login_as_guest(self):
        return True


def main():
    page = LoginPage(object())
    assert page.login_as_guest()


if __name__ == "__main__":
    main()
'''


def test_export_operations_are_isolated_by_real_project(tmp_path: Path) -> None:
    exporter = UIAutomationExportService(tmp_path / "ai ui自动化")

    tiantianlian = exporter.export_operation(
        project_id=2,
        project_name="天天练",
        operation_name="游客登录",
        description="关闭手机登录弹窗后进入游客界面",
        steps=["点击手机登录", "关闭登录弹窗", "验证游客首页"],
        script=SCRIPT,
        automation_type="app",
        target="com.leleketang.SchoolFantasy/org.cocos2dx.cpp.AppActivity",
    )
    reading_room = exporter.export_operation(
        project_id=8,
        project_name="未来书房",
        operation_name="游客登录",
        description="验证游客入口",
        steps=["进入游客入口"],
        script=SCRIPT,
        automation_type="web",
        target="http://127.0.0.1:5173",
    )

    assert Path(tiantianlian["root_dir"]).name == "天天练"
    assert Path(reading_room["root_dir"]).name == "未来书房"
    assert tiantianlian["root_dir"] != reading_room["root_dir"]
    assert Path(tiantianlian["script_path"]).is_file()
    assert Path(tiantianlian["page_paths"][0]).is_file()
    ast.parse(Path(tiantianlian["script_path"]).read_text(encoding="utf-8"))
    ast.parse(Path(tiantianlian["page_paths"][0]).read_text(encoding="utf-8"))

    manifest = json.loads((Path(tiantianlian["root_dir"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["project"] == {
        "project_id": 2,
        "project_name": "天天练",
        "platform_source": "AI测试平台",
    }
    assert manifest["operations"][0]["name"] == "游客登录"
    assert manifest["operations"][0]["architecture"] == "page_object"
    env_example = (Path(tiantianlian["root_dir"]) / ".env.example").read_text(encoding="utf-8")
    assert "TIANTIANLIAN_PHONE=" in env_example


def test_export_upserts_the_same_operation(tmp_path: Path) -> None:
    exporter = UIAutomationExportService(tmp_path)
    arguments = dict(
        project_id=2,
        project_name="天天练",
        operation_name="游客登录",
        description="游客登录",
        steps=["点击手机登录"],
        script=SCRIPT,
        automation_type="app",
        target="test-app",
    )
    exporter.export_operation(**arguments)
    exporter.export_operation(**{**arguments, "steps": ["点击手机登录", "关闭弹窗"]})

    manifest = json.loads((tmp_path / "天天练" / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["operations"]) == 1
    assert manifest["operations"][0]["steps"] == ["点击手机登录", "关闭弹窗"]


def test_prepare_existing_page_object_operation_uses_manifest_entry(tmp_path: Path) -> None:
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
    orchestration_script = Path(exported["script_path"]).read_text(encoding="utf-8")
    assert not any(isinstance(node, ast.ClassDef) for node in ast.parse(orchestration_script).body)

    prepared = exporter.prepare_operation_for_execution(
        project_id=2,
        project_name="天天练",
        operation_name="游客登录",
        description="游客登录",
        steps=["点击手机登录", "关闭弹窗"],
        script=orchestration_script,
        automation_type="app",
        target="com.leleketang.SchoolFantasy/org.cocos2dx.cpp.AppActivity",
    )

    assert prepared["script_path"] == exported["script_path"]
    assert prepared["page_paths"] == exported["page_paths"]
