import os
import gc
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv, set_key
from core.utils import logger

# Load existing environment variables
load_dotenv()

class ConfigEncryption:
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
