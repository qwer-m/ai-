"""Collect real UI structure before asking the model to generate selectors."""

from __future__ import annotations

import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from typing import Any


def collect_web_runtime_context(target_url: str) -> str:
    """Read visible semantic elements from the real target page."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            response = page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
            if response is not None and response.status >= 400:
                raise RuntimeError(f"目标页面返回 HTTP {response.status}: {target_url}")
            page.locator("body").wait_for(state="visible", timeout=15_000)
            elements: list[dict[str, Any]] = page.locator(
                "input, button, a, select, textarea, [role], [aria-label], [data-testid]"
            ).evaluate_all(
                """
                (nodes) => nodes
                  .filter((node) => {
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden'
                      && rect.width > 0 && rect.height > 0;
                  })
                  .slice(0, 200)
                  .map((node) => {
                    const labels = node.labels ? Array.from(node.labels).map((label) => label.innerText.trim()) : [];
                    return {
                      tag: node.tagName.toLowerCase(),
                      type: node.getAttribute('type') || '',
                      role: node.getAttribute('role') || '',
                      accessible_name: node.getAttribute('aria-label') || labels.join(' ') || node.innerText?.trim() || '',
                      placeholder: node.getAttribute('placeholder') || '',
                      test_id: node.getAttribute('data-testid') || '',
                      id: node.id || '',
                      name: node.getAttribute('name') || '',
                      href: node.getAttribute('href') || '',
                    };
                  })
                """
            )
            observed = {
                "final_url": page.url,
                "title": page.title(),
                "elements": elements,
            }
            return json.dumps(observed, ensure_ascii=False, indent=2)
        finally:
            browser.close()


def _adb_command(device_id: str | None, *args: str) -> list[str]:
    command = ["adb"]
    if device_id:
        command.extend(["-s", device_id])
    command.extend(args)
    return command


def _current_app(device_id: str | None) -> dict[str, str]:
    result = subprocess.run(
        _adb_command(device_id, "shell", "dumpsys", "window", "displays"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    match = re.search(r"mCurrentFocus=Window\{.*?\s+([\w.]+)/([\w.]+)", result.stdout)
    if not match:
        match = re.search(r"mFocusedApp=.*?\s+([\w.]+)/([\w.]+)", result.stdout)
    if not match:
        return {}
    return {"package": match.group(1), "activity": match.group(2)}


def collect_app_runtime_context(
    target: str = "",
    device_id: str | None = None,
    visual_asset_group: str | None = None,
) -> str:
    """Read the current Android UIAutomator hierarchy from a real device."""
    device_id = (device_id or os.environ.get("APPIUM_UDID", "")).strip() or None

    if "/" in target:
        launch = subprocess.run(
            _adb_command(device_id, "shell", "am", "start", "-W", "-n", target),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if launch.returncode != 0 or "Error:" in launch.stdout:
            raise RuntimeError(f"无法启动真实目标应用 {target}: {(launch.stderr or launch.stdout).strip()}")

    dump = subprocess.run(
        _adb_command(device_id, "shell", "uiautomator", "dump", "/sdcard/window_dump.xml"),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if dump.returncode != 0:
        raise RuntimeError(f"读取 Android UI hierarchy 失败: {dump.stderr.strip()}")

    hierarchy = subprocess.run(
        _adb_command(device_id, "exec-out", "cat", "/sdcard/window_dump.xml"),
        capture_output=True,
        timeout=30,
    )
    if hierarchy.returncode != 0:
        raise RuntimeError("无法从真实设备读取 window_dump.xml。")

    root = ET.fromstring(hierarchy.stdout.decode("utf-8", errors="replace"))
    elements = []
    for node in root.iter("node"):
        attributes = node.attrib
        if not any(attributes.get(key) for key in ("text", "content-desc", "resource-id")):
            continue
        elements.append(
            {
                "class": attributes.get("class", ""),
                "text": attributes.get("text", ""),
                "accessibility_id": attributes.get("content-desc", ""),
                "resource_id": attributes.get("resource-id", ""),
                "clickable": attributes.get("clickable", "false"),
                "enabled": attributes.get("enabled", "false"),
                "bounds": attributes.get("bounds", ""),
            }
        )
        if len(elements) >= 200:
            break
    current_app = _current_app(device_id)
    expected_activity = target.split("/", 1)[1] if "/" in target else ""
    activity = expected_activity or current_app.get("activity", "")
    render_engine = "cocos" if "cocos2dx" in activity.lower() else "native_or_unknown"

    from modules.automation_components.services.ui_visual_asset_service import list_visual_asset_catalogs

    catalogs = list_visual_asset_catalogs()
    if visual_asset_group:
        catalogs = [item for item in catalogs if item.get("group") == visual_asset_group]
    elif len(catalogs) > 1:
        catalogs = []

    observed = {
        "device_id": device_id,
        "target": target,
        "current_app": current_app,
        "target_is_foreground": not target
        or (
            target.split("/", 1)[0] == current_app.get("package")
            and (not expected_activity or expected_activity == current_app.get("activity"))
        ),
        "render_engine": render_engine,
        "native_elements": elements,
        "visual_asset_catalogs": catalogs,
        "generation_rule": (
            "native_elements 中存在目标时使用原生定位；目标不在原生层且 visual_asset_catalogs 有对应资产时，"
            "使用 runtime.ui_hybrid_runtime 的资产名称；两者都不存在时停止生成并要求先采集视觉资产。"
        ),
    }
    return json.dumps(observed, ensure_ascii=False, indent=2)


def collect_ui_runtime_context(
    target: str,
    automation_type: str,
    *,
    device_id: str | None = None,
    visual_asset_group: str | None = None,
) -> str:
    if automation_type == "web":
        return collect_web_runtime_context(target)
    return collect_app_runtime_context(target, device_id=device_id, visual_asset_group=visual_asset_group)
