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

# Load existing environment variables
load_dotenv()

class ConfigEncryption:
    """
    配置加密类
    使用 Fernet 算法进行加解密。
    """
    def __init__(self, key: str = None):
        if not key:
            key = os.getenv("CONFIG_ENCRYPTION_KEY")
        
        if not key:
            raise RuntimeError("Missing CONFIG_ENCRYPTION_KEY. Call initialize_encryption_key() first.")
            
        self.fernet = Fernet(key.encode() if isinstance(key, str) else key)

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
        except InvalidToken:
            logger.warning("Decryption failed: Invalid Token")
            raise

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
    key = os.getenv("CONFIG_ENCRYPTION_KEY")
    
    if not key:
        logger.warning("CONFIG_ENCRYPTION_KEY not found. Generating new key...")
        key = Fernet.generate_key().decode()
        
        # Save to .env file
        env_path = Path(".env")
        if not env_path.exists():
            env_path.touch()
            
        set_key(env_path, "CONFIG_ENCRYPTION_KEY", key)
        os.environ["CONFIG_ENCRYPTION_KEY"] = key
        logger.info(f"New encryption key generated and saved to {env_path.absolute()}")
    
    return ConfigEncryption(key)

# Global encryption instance
try:
    config_encryption = initialize_encryption_key()
except Exception as e:
    logger.error(f"Failed to initialize encryption: {e}")
    config_encryption = None
