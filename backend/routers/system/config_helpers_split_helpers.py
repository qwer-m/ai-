from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from core.ai.ai_client import ai_client
from core.ai.providers import DashScopeProvider, OpenAICompatibleProvider
from core.authn.security import config_encryption
from core.settings.config_manager import config_manager
from core.db.model_defs import SystemConfig
from core.processing.utils import logger

def normalize_provider(provider: str) -> str:
    return (provider or "").strip().lower()


def default_base_url(provider: str) -> Optional[str]:
    if provider == "openai":
        return "https://api.openai.com/v1"
    if provider == "deepseek":
        return "https://api.deepseek.com/v1"
    if provider == "local":
        return "http://localhost:11434/v1"
    return None


def normalize_metadata(config: Optional[SystemConfig]) -> dict[str, Any]:
    raw = config.metadata_info if config else {}
    if not isinstance(raw, dict):
        return {}
    return raw


def extract_target_nodes(metadata: dict[str, Any]) -> dict[str, Any]:
    targets = metadata.get("targets")
    if isinstance(targets, dict):
        return targets
    return {}


def normalize_tesseract_path(raw_path: Optional[str]) -> str:
    if not raw_path:
        return ""
    return str(raw_path).strip().strip('"').strip("'")


def iter_tesseract_candidates() -> list[str]:
    candidates: list[str] = []
    env_candidates = [
        os.environ.get("TESSERACT_PATH"),
        os.environ.get("TESSERACT_CMD"),
    ]
    for item in env_candidates:
        value = normalize_tesseract_path(item)
        if value:
            candidates.append(value)

    for exe_name in ("tesseract.exe", "tesseract"):
        discovered = shutil.which(exe_name)
        if discovered:
            candidates.append(discovered)

    candidates.extend(
        [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
    )

    uniq: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.normpath(normalize_tesseract_path(candidate)))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        uniq.append(candidate)
    return uniq


def auto_detect_tesseract_path() -> str:
    for candidate in iter_tesseract_candidates():
        check = validate_tesseract_path(candidate)
        if check.get("success"):
            expanded = os.path.expanduser(os.path.expandvars(normalize_tesseract_path(candidate)))
            resolved = shutil.which(expanded) or shutil.which(candidate) or expanded
            return normalize_tesseract_path(resolved)
    return ""


def validate_tesseract_path(path_value: Optional[str]) -> dict[str, Any]:
    normalized = normalize_tesseract_path(path_value)
    if not normalized:
        return {
            "type": "ocr",
            "label": "本地OCR引擎",
            "model": "tesseract(PATH)",
            "success": True,
            "latency": 0,
            "error": None,
        }

    expanded = os.path.expanduser(os.path.expandvars(normalized))
    binary = expanded
    if os.path.isabs(expanded):
        if not os.path.exists(expanded):
            return {
                "type": "ocr",
                "label": "本地OCR引擎",
                "model": normalized,
                "success": False,
                "latency": 0,
                "error": f"tesseract路径不存在：{expanded}",
            }
    else:
        discovered = shutil.which(expanded) or shutil.which(normalized)
        if discovered:
            binary = discovered
        elif os.path.exists(expanded):
            binary = expanded
        else:
            return {
                "type": "ocr",
                "label": "本地OCR引擎",
                "model": normalized,
                "success": False,
                "latency": 0,
                "error": f"找不到可执行文件：{normalized}",
            }

    started = time.time()
    try:
        proc = subprocess.run(
            [binary, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
        ok = proc.returncode == 0
        output = (proc.stdout or proc.stderr or "").strip()
        return {
            "type": "ocr",
            "label": "本地OCR引擎",
            "model": normalized,
            "success": ok,
            "latency": round((time.time() - started) * 1000, 2),
            "error": None if ok else (output or "tesseract --version执行失败"),
            "sample_response": output[:120] if output else "",
        }
    except Exception as e:
        return {
            "type": "ocr",
            "label": "本地OCR引擎",
            "model": normalized,
            "success": False,
            "latency": round((time.time() - started) * 1000, 2),
            "error": f"tesseract校验失败：{e}",
        }


def decrypt_stored_key(raw_value: Optional[str]) -> str:
    if not raw_value:
        return ""
    return config_encryption.decrypt(str(raw_value))
