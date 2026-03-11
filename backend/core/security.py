"""
安全加密模块 (Security Module)

该模块负责敏感数据的加密存储和内存保护。
主要功能：
1. 配置文件加密 (ConfigEncryption): 使用 Fernet (对称加密) 保护 API Key 等敏感配置。
2. 密钥管理 (initialize_encryption_key): 自动生成或加载加密密钥，并保存到 .env 文件。
3. 内存安全字符串 (SecureString): 封装敏感字符串，鼓励显式清理 (虽然 Python GC 机制限制了完全擦除)。

调用关系：
- 被 `core.config_manager` 调用以加密/解密 API Key。
"""

import os
import gc
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv, set_key
from core.utils import logger

_CORE_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _CORE_DIR.parent
_PROJECT_DIR = _BACKEND_DIR.parent
_BACKEND_ENV_PATH = _BACKEND_DIR / ".env"
_PROJECT_ENV_PATH = _PROJECT_DIR / ".env"

# Load backend/.env first, then project root .env, independent of current working directory.
load_dotenv(_BACKEND_ENV_PATH)
load_dotenv(_PROJECT_ENV_PATH)


def _strip_quotes(value: str) -> str:
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def _read_keys_from_env_file(env_path: Path) -> list[str]:
    if not env_path.exists():
        return []

    keys: list[str] = []
    try:
        for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip().lstrip("\ufeff") != "CONFIG_ENCRYPTION_KEY":
                continue
            parsed = _strip_quotes(value)
            if parsed:
                keys.append(parsed)
    except Exception as e:
        logger.warning(f"Failed to read encryption keys from {env_path}: {e}")
    return keys


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen = set()
    ordered: list[str] = []
    for item in values:
        normalized = _strip_quotes(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _collect_candidate_keys() -> list[str]:
    file_keys = _read_keys_from_env_file(_BACKEND_ENV_PATH) + _read_keys_from_env_file(_PROJECT_ENV_PATH)
    env_key = _strip_quotes(os.getenv("CONFIG_ENCRYPTION_KEY", ""))
    ordered: list[str] = []
    if env_key:
        ordered.append(env_key)
    ordered.extend(file_keys)
    return _dedupe_keep_order(ordered)

class ConfigEncryption:
    """
    配置加密类
    使用 Fernet 算法进行加解密。
    """
    def __init__(self, key: str = None, fallback_keys: list[str] | None = None):
        if not key:
            key = os.getenv("CONFIG_ENCRYPTION_KEY")

        key = _strip_quotes(key or "")
        if not key:
            raise RuntimeError("Missing CONFIG_ENCRYPTION_KEY. Call initialize_encryption_key() first.")

        self.fernet = Fernet(key.encode() if isinstance(key, str) else key)
        self._fallback_fernets: list[Fernet] = []
        for item in fallback_keys or []:
            fallback = _strip_quotes(item or "")
            if not fallback or fallback == key:
                continue
            try:
                self._fallback_fernets.append(Fernet(fallback.encode()))
            except Exception as e:
                logger.warning(f"Ignored invalid fallback CONFIG_ENCRYPTION_KEY: {e}")

    def encrypt(self, data: str) -> str:
        """Encrypt string data"""
        if not data:
            return ""
        if not isinstance(data, str):
            data = str(data)
        return self.fernet.encrypt(data.encode()).decode()

    def decrypt(self, encrypted_data: str) -> str:
        """Decrypt string data"""
        if not encrypted_data:
            return ""
        try:
            return self.fernet.decrypt(encrypted_data.encode()).decode()
        except InvalidToken as primary_error:
            for fallback in self._fallback_fernets:
                try:
                    value = fallback.decrypt(encrypted_data.encode()).decode()
                    logger.warning(
                        "Decrypted saved AI API key using fallback CONFIG_ENCRYPTION_KEY. "
                        "Please re-save config to rotate to current key."
                    )
                    return value
                except InvalidToken:
                    continue

            logger.warning("Saved AI API key cannot be decrypted because CONFIG_ENCRYPTION_KEY does not match the stored data.")
            raise primary_error

class SecureString:
    """
    A wrapper to minimize sensitive data exposure in memory.
    Note: In Python, strings are immutable, so we can't truly erase them from memory
    immediately, but this wrapper encourages explicit cleanup.
    """
    def __init__(self, value: str):
        self._value = value
    
    def get(self) -> str:
        return self._value
    
    def clear(self):
        """Explicitly remove reference to allow GC"""
        self._value = None
        
    def __del__(self):
        self.clear()
    
    def __str__(self):
        return "******"
        
    def __repr__(self):
        return "SecureString(******)"

def initialize_encryption_key() -> ConfigEncryption:
    """
    Initialize encryption key.
    If CONFIG_ENCRYPTION_KEY is not set in env, generate one and save to .env
    """
    candidate_keys = _collect_candidate_keys()
    key = candidate_keys[0] if candidate_keys else ""
    fallback_keys = candidate_keys[1:] if len(candidate_keys) > 1 else []

    if len(candidate_keys) > 1:
        logger.warning(
            f"Multiple CONFIG_ENCRYPTION_KEY values detected ({len(candidate_keys)} total). "
            "Using primary key with fallback decryption enabled."
        )

    if not key:
        logger.warning("CONFIG_ENCRYPTION_KEY not found. Generating new key...")
        key = Fernet.generate_key().decode()

        # Persist to backend/.env to avoid cwd-dependent writes.
        env_path = _BACKEND_ENV_PATH
        if not env_path.exists():
            env_path.touch()

        set_key(env_path, "CONFIG_ENCRYPTION_KEY", key)
        logger.info(f"New encryption key generated and saved to {env_path.absolute()}")

    os.environ["CONFIG_ENCRYPTION_KEY"] = key
    return ConfigEncryption(key, fallback_keys=fallback_keys)

# Global encryption instance
try:
    config_encryption = initialize_encryption_key()
except Exception as e:
    logger.error(f"Failed to initialize encryption: {e}")
    config_encryption = None
