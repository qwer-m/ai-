"""在生成阶段从真实设备截图创建可离线执行的视觉模板。"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from core.ai.ai_client import get_client_for_user
from modules.automation_components.services.ui_automation_export_service import get_ui_automation_export_root


def _safe_name(value: str, field: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", (value or "").strip()).strip("_").lower()
    if not cleaned:
        raise ValueError(f"{field} 只能包含可转换为文件名的字符。")
    return cleaned[:80]


def _adb(device_id: str | None, *args: str, binary: bool = False) -> subprocess.CompletedProcess:
    command = ["adb"]
    if device_id:
        command.extend(["-s", device_id])
    command.extend(args)
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=not binary,
        encoding=None if binary else "utf-8",
        timeout=30,
    )


def _capture_real_screen(device_id: str | None) -> np.ndarray:
    completed = _adb(device_id, "exec-out", "screencap", "-p", binary=True)
    image = cv2.imdecode(np.frombuffer(completed.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("ADB 返回的真实设备截图无法解析。")
    return image


def _write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        raise RuntimeError(f"无法写入视觉资产：{path}")
    path.write_bytes(encoded.tobytes())


def _extract_json_object(response: str) -> dict[str, Any]:
    cleaned = (response or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.IGNORECASE | re.DOTALL)
    candidates = [fenced.group(1).strip()] if fenced else []
    if "{" in cleaned and "}" in cleaned:
        candidates.append(cleaned[cleaned.find("{") : cleaned.rfind("}") + 1])
    candidates.append(cleaned)
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError(f"视觉模型没有返回有效 JSON：{cleaned[:300]}")


def _validated_box(payload: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    raw = payload.get("box") or payload.get("bbox") or payload.get("bounding_box")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ValueError(f"视觉模型结果缺少 box=[x1,y1,x2,y2]：{payload}")
    x1, y1, x2, y2 = (int(round(float(value))) for value in raw)
    x1, x2 = sorted((max(0, x1), min(width, x2)))
    y1, y2 = sorted((max(0, y1), min(height, y2)))
    if x2 - x1 < 8 or y2 - y1 < 8:
        raise ValueError(f"视觉模型返回的目标区域过小或越界：{raw}")
    return x1, y1, x2, y2


def _expanded_crop(box: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    pad_x = max(4, int((x2 - x1) * 0.06))
    pad_y = max(4, int((y2 - y1) * 0.06))
    return max(0, x1 - pad_x), max(0, y1 - pad_y), min(width, x2 + pad_x), min(height, y2 + pad_y)


def _search_region(box: tuple[int, int, int, int], width: int, height: int) -> list[float]:
    x1, y1, x2, y2 = box
    margin_x = max(width * 0.08, (x2 - x1) * 1.5)
    margin_y = max(height * 0.08, (y2 - y1) * 1.5)
    return [
        round(max(0.0, (x1 - margin_x) / width), 4),
        round(max(0.0, (y1 - margin_y) / height), 4),
        round(min(1.0, (x2 + margin_x) / width), 4),
        round(min(1.0, (y2 + margin_y) / height), 4),
    ]


def capture_visual_asset(
    *,
    group_name: str,
    asset_name: str,
    element_description: str,
    db,
    user_id: int,
    device_id: str | None = None,
    image_model: str | None = None,
    threshold: float = 0.82,
) -> dict[str, Any]:
    """AI 只在采集阶段确定边界；保存后的模板运行时不再调用 AI。"""
    group = _safe_name(group_name, "group_name")
    asset = _safe_name(asset_name, "asset_name")
    description = (element_description or "").strip()
    if not description:
        raise ValueError("element_description 不能为空。")
    threshold = float(threshold)
    if not 0 < threshold <= 1:
        raise ValueError("threshold 必须位于 (0, 1]。")

    root = get_ui_automation_export_root()
    authoring_dir = root / "artifacts" / "authoring"
    asset_dir = root / "assets" / group
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    screenshot_path = authoring_dir / f"{group}_{asset}_{timestamp}.png"
    image = _capture_real_screen(device_id)
    height, width = image.shape[:2]
    _write_image(screenshot_path, image)

    client = get_client_for_user(user_id, db)
    prompt = (
        "你正在为移动端 UI 自动化采集稳定的视觉模板。"
        f"请在这张真实设备截图中定位：{description}。"
        "返回且只返回 JSON："
        '{"box":[x1,y1,x2,y2],"confidence":0.0,"reason":"简短依据"}。'
        "坐标必须是截图像素坐标，框应完整包含目标控件本身，不要包含大面积背景。"
        f"截图尺寸为 {width}x{height}。"
    )
    response = client.analyze_image(f"file://{screenshot_path}", prompt, db=db, model=image_model)
    located = _extract_json_object(response)
    box = _validated_box(located, width, height)
    crop_box = _expanded_crop(box, width, height)
    x1, y1, x2, y2 = crop_box
    template_path = asset_dir / f"{asset}.png"
    _write_image(template_path, image[y1:y2, x1:x2])

    manifest_path = asset_dir / "visual_assets.json"
    manifest: dict[str, Any] = {
        "version": 1,
        "baseline": {"width": width, "height": height},
        "assets": {},
    }
    if manifest_path.exists():
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and isinstance(loaded.get("assets"), dict):
            manifest = loaded
            manifest["version"] = 1
            manifest["baseline"] = {"width": width, "height": height}
    manifest["assets"][asset] = {
        "file": template_path.name,
        "description": description,
        "region": _search_region(box, width, height),
        "threshold": threshold,
        "grayscale": False,
        "scale_tolerance": 0.1,
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_box": list(box),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "group": group,
        "asset": asset,
        "description": description,
        "template_path": str(template_path),
        "manifest_path": str(manifest_path),
        "source_screenshot": str(screenshot_path),
        "box": list(box),
        "region": manifest["assets"][asset]["region"],
        "threshold": threshold,
        "model_confidence": located.get("confidence"),
        "runtime_ai_required": False,
    }


def list_visual_asset_catalogs() -> list[dict[str, Any]]:
    root = get_ui_automation_export_root() / "assets"
    if not root.exists():
        return []
    catalogs: list[dict[str, Any]] = []
    for manifest_path in sorted(root.glob("*/visual_assets.json")):
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        catalogs.append(
            {
                "group": manifest_path.parent.name,
                "manifest_path": str(manifest_path),
                "baseline": payload.get("baseline"),
                "assets": payload.get("assets") or {},
            }
        )
    return catalogs
