"""独立 Appium 测试包使用的原生/视觉混合定位运行时。"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

import cv2
import numpy as np
from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.actions import interaction
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def log_event(event_type: str, **payload: Any) -> None:
    print(json.dumps({"type": event_type, **payload}, ensure_ascii=False), flush=True)


def run_adb(udid: str, *args: str, binary: bool = False) -> str | bytes:
    completed = subprocess.run(
        ["adb", "-s", udid, *args],
        check=True,
        capture_output=True,
        text=not binary,
        encoding=None if binary else "utf-8",
    )
    return completed.stdout if binary else completed.stdout.strip()


def select_online_device(configured_udid: str | None = None) -> str:
    configured = (configured_udid or os.environ.get("APPIUM_UDID", "")).strip()
    completed = subprocess.run(
        ["adb", "devices"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    devices = [line.split()[0] for line in completed.stdout.splitlines()[1:] if line.endswith("\tdevice")]
    if configured:
        if configured not in devices:
            raise RuntimeError(f"指定设备不在线：{configured}；当前在线设备：{devices}")
        return configured
    if len(devices) != 1:
        raise RuntimeError(f"未指定 APPIUM_UDID 时需要且只能有一台在线设备，当前检测到 {len(devices)} 台。")
    return devices[0]


def _status_url(server_url: str) -> str:
    parsed = urlsplit(server_url.rstrip("/"))
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/status", "", ""))


def resolve_appium_server_url(configured_url: str | None = None) -> str:
    """通过真实 /status 探测明确选择 Appium 1 或 Appium 2 地址。"""
    supplied = (configured_url or os.environ.get("APPIUM_SERVER_URL", "http://127.0.0.1:4723")).rstrip("/")
    candidates = [supplied]
    if supplied.endswith("/wd/hub"):
        candidates.append(supplied[: -len("/wd/hub")])
    else:
        candidates.append(f"{supplied}/wd/hub")

    failures: list[str] = []
    for candidate in dict.fromkeys(candidates):
        try:
            with urlopen(_status_url(candidate), timeout=3) as response:  # noqa: S310 - 仅探测用户配置的 Appium 地址
                if 200 <= response.status < 300:
                    log_event("environment", component="appium", status="ready", server_url=candidate)
                    return candidate
        except Exception as exc:  # 保留所有真实探测结果，最后统一报错
            failures.append(f"{candidate}: {exc}")
    raise RuntimeError("Appium 服务不可用：" + "；".join(failures))


def create_android_driver(
    *,
    package: str,
    activity: str,
    udid: str,
    server_url: str | None = None,
    device_name: str | None = None,
    no_reset: bool = True,
) -> webdriver.Remote:
    resolved_server = resolve_appium_server_url(server_url)
    options = UiAutomator2Options().load_capabilities(
        {
            "platformName": "Android",
            "automationName": "UiAutomator2",
            "deviceName": device_name or os.environ.get("APPIUM_DEVICE_NAME", "Android Device"),
            "udid": udid,
            "appPackage": package,
            "appActivity": activity,
            "noReset": no_reset,
            "newCommandTimeout": 180,
        }
    )
    return webdriver.Remote(resolved_server, options=options)


def decode_png(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError("无法解析设备截图。")
    return image


def read_image(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"无法读取视觉模板：{path}")
    return image


def write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        raise RuntimeError(f"无法写入图像：{path}")
    path.write_bytes(encoded.tobytes())


@dataclass(frozen=True)
class VisualAsset:
    name: str
    path: Path
    region: tuple[float, float, float, float]
    threshold: float
    grayscale: bool = False
    scale_tolerance: float = 0.1


class VisualAssetCatalog:
    def __init__(self, manifest_path: str | Path):
        self.manifest_path = Path(manifest_path).resolve()
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if int(payload.get("version") or 0) != 1:
            raise ValueError(f"不支持的视觉资产清单版本：{payload.get('version')}")
        baseline = payload.get("baseline") or {}
        self.baseline_size = (int(baseline.get("width") or 0), int(baseline.get("height") or 0))
        if min(self.baseline_size) <= 0:
            raise ValueError("视觉资产清单缺少有效 baseline.width/height。")

        self.assets: dict[str, VisualAsset] = {}
        for name, raw in (payload.get("assets") or {}).items():
            region = tuple(float(value) for value in raw.get("region", (0, 0, 1, 1)))
            if len(region) != 4 or not (0 <= region[0] < region[2] <= 1 and 0 <= region[1] < region[3] <= 1):
                raise ValueError(f"视觉资产 {name} 的 region 无效：{region}")
            threshold = float(raw.get("threshold", 0.82))
            if not 0 < threshold <= 1:
                raise ValueError(f"视觉资产 {name} 的 threshold 无效：{threshold}")
            asset_path = (self.manifest_path.parent / str(raw["file"])).resolve()
            if not asset_path.is_file():
                raise FileNotFoundError(f"视觉模板不存在：{asset_path}")
            self.assets[name] = VisualAsset(
                name=name,
                path=asset_path,
                region=region,
                threshold=threshold,
                grayscale=bool(raw.get("grayscale", False)),
                scale_tolerance=max(0.0, min(0.25, float(raw.get("scale_tolerance", 0.1)))),
            )

    def get(self, name: str) -> VisualAsset:
        try:
            return self.assets[name]
        except KeyError as exc:
            raise KeyError(f"视觉资产清单中不存在：{name}") from exc


class HybridAppSession:
    """同一脚本中统一执行原生控件和 Cocos/Canvas 视觉控件。"""

    def __init__(
        self,
        driver: webdriver.Remote,
        catalog: VisualAssetCatalog,
        artifact_dir: str | Path,
        *,
        poll_frequency: float = 0.4,
    ):
        self.driver = driver
        self.catalog = catalog
        self.artifact_dir = Path(artifact_dir).resolve()
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.poll_frequency = max(0.1, float(poll_frequency))

    def capture(self, name: str) -> Path:
        path = self.artifact_dir / name
        write_image(path, decode_png(self.driver.get_screenshot_as_png()))
        log_event("screenshot", path=str(path), name=name)
        return path

    def native_click(self, resource_id: str, *, timeout: float = 15.0, optional: bool = False) -> bool:
        try:
            element = WebDriverWait(self.driver, timeout, poll_frequency=self.poll_frequency).until(
                EC.element_to_be_clickable((AppiumBy.ID, resource_id))
            )
            element.click()
            log_event("step", action="native_click", details=f"点击原生控件 {resource_id}", status="success")
            return True
        except Exception:
            if optional:
                log_event("step", action="native_click", details=f"可选原生控件未出现 {resource_id}", status="not_present")
                return False
            raise

    def _scaled_templates(self, template: np.ndarray, image: np.ndarray, tolerance: float) -> Iterable[np.ndarray]:
        baseline_width, baseline_height = self.catalog.baseline_size
        height, width = image.shape[:2]
        base = cv2.resize(
            template,
            None,
            fx=width / baseline_width,
            fy=height / baseline_height,
            interpolation=cv2.INTER_AREA,
        )
        factors = [1.0]
        if tolerance > 0:
            factors.extend([1.0 - tolerance / 2, 1.0 + tolerance / 2, 1.0 - tolerance, 1.0 + tolerance])
        seen: set[tuple[int, int]] = set()
        for factor in factors:
            candidate = cv2.resize(base, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
            size = (candidate.shape[1], candidate.shape[0])
            if min(size) > 0 and size not in seen:
                seen.add(size)
                yield candidate

    def _locate_once(self, asset: VisualAsset) -> tuple[float, tuple[int, int, int, int], np.ndarray]:
        image = decode_png(self.driver.get_screenshot_as_png())
        height, width = image.shape[:2]
        left, top, right, bottom = (
            int(width * asset.region[0]),
            int(height * asset.region[1]),
            int(width * asset.region[2]),
            int(height * asset.region[3]),
        )
        search = image[top:bottom, left:right]
        template = read_image(asset.path)
        best_score = -1.0
        best_box = (0, 0, 0, 0)
        for candidate in self._scaled_templates(template, image, asset.scale_tolerance):
            if search.shape[0] < candidate.shape[0] or search.shape[1] < candidate.shape[1]:
                continue
            match_search = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY) if asset.grayscale else search
            match_template = cv2.cvtColor(candidate, cv2.COLOR_BGR2GRAY) if asset.grayscale else candidate
            result = cv2.matchTemplate(match_search, match_template, cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(result)
            if score > best_score:
                x1, y1 = left + location[0], top + location[1]
                best_score = float(score)
                best_box = (x1, y1, x1 + candidate.shape[1], y1 + candidate.shape[0])
        return best_score, best_box, image

    def wait_visual(self, name: str, *, timeout: float = 30.0) -> tuple[int, int, float]:
        asset = self.catalog.get(name)
        last: dict[str, Any] = {"score": -1.0, "box": (0, 0, 0, 0), "image": None}

        def matched(_: webdriver.Remote) -> tuple[int, int, float] | bool:
            score, box, image = self._locate_once(asset)
            last.update(score=score, box=box, image=image)
            if score < asset.threshold:
                return False
            x1, y1, x2, y2 = box
            annotated = image.copy()
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 5)
            evidence = self.artifact_dir / f"match_{name}.png"
            write_image(evidence, annotated)
            log_event("screenshot", path=str(evidence), name=evidence.name)
            return (x1 + x2) // 2, (y1 + y2) // 2, score

        try:
            return WebDriverWait(self.driver, timeout, poll_frequency=self.poll_frequency).until(matched)
        except Exception as exc:
            if isinstance(last.get("image"), np.ndarray):
                failure = self.artifact_dir / f"not_found_{name}.png"
                write_image(failure, last["image"])
                log_event("screenshot", path=str(failure), name=failure.name)
            raise AssertionError(
                f"视觉目标 {name} 未出现，最高置信度 {float(last['score']):.4f}，阈值 {asset.threshold:.2f}"
            ) from exc

    def visual_tap(self, name: str, *, timeout: float = 30.0) -> float:
        x, y, score = self.wait_visual(name, timeout=timeout)
        finger = PointerInput(interaction.POINTER_TOUCH, "finger")
        actions = ActionBuilder(self.driver, mouse=finger, duration=0)
        actions.pointer_action.move_to_location(x, y)
        actions.pointer_action.pointer_down()
        actions.pointer_action.pause(0.12)
        actions.pointer_action.release()
        actions.perform()
        log_event(
            "step",
            action="visual_tap",
            details=f"点击视觉目标 {name}",
            status="success",
            asset=name,
            confidence=round(score, 4),
            coordinates=[x, y],
        )
        return score

    def assert_visual(self, name: str, *, timeout: float = 30.0) -> float:
        _, _, score = self.wait_visual(name, timeout=timeout)
        log_event(
            "step",
            action="visual_assert",
            details=f"视觉断言通过 {name}",
            status="success",
            asset=name,
            confidence=round(score, 4),
        )
        return score
