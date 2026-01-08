import threading
from sqlalchemy.orm import Session
from core.models import SystemConfig
from core.security import config_encryption
from core.utils import logger
from datetime import datetime

class ConfigManager:
    def __init__(self):
        self._lock = threading.RLock()

    def create_config(self, db: Session, provider: str, model_name: str, api_key: str, base_url: str = None, activate: bool = True, vl_model_name: str = None, turbo_model_name: str = None, user_id: int = None) -> SystemConfig:
        """Create a new system configuration"""
        # Encrypt API Key
        encrypted_key = config_encryption.encrypt(api_key) if api_key else None
        
        new_config = SystemConfig(
            provider=provider,
            model_name=model_name,
            vl_model_name=vl_model_name,
            turbo_model_name=turbo_model_name,
            api_key=encrypted_key,
            base_url=base_url,
            is_active=0, # Will be set by activate_config if needed
            version=1, # Initial version
            user_id=user_id
        )
        db.add(new_config)
        db.commit()
        db.refresh(new_config)
        
        if activate:
            self.activate_config(db, new_config.id, user_id)
            db.refresh(new_config)
            
        return new_config

    def get_active_config(self, db: Session, user_id: int = None) -> SystemConfig:
        """Get the currently active configuration for the user"""
        if user_id is None:
            return None
        return db.query(SystemConfig).filter(SystemConfig.is_active == 1, SystemConfig.user_id == user_id).first()

    def activate_config(self, db: Session, config_id: int, user_id: int = None):
        """
        Atomically activate a configuration.
        """
        with self._lock:
            try:
                # 1. Get target config
                target_config = db.query(SystemConfig).filter(SystemConfig.id == config_id, SystemConfig.user_id == user_id).first()
                if not target_config:
                    raise ValueError(f"Config {config_id} not found")

                # 2. Deactivate all others for this user
                db.query(SystemConfig).filter(SystemConfig.is_active == 1, SystemConfig.user_id == user_id).update({"is_active": 0})
                
                # 3. Activate target
                target_config.is_active = 1
                target_config.updated_at = datetime.now()
                
                db.commit()
                logger.info(f"Configuration {config_id} activated successfully for user {user_id}.")
                
                # 4. Trigger global client reload (This should be handled by the caller or an event system)
                # For now, we assume the caller will handle the AIClient update to avoid circular imports here.
                return target_config
            except Exception as e:
                db.rollback()
                logger.error(f"Failed to activate config {config_id}: {e}")
                raise

    def get_decrypted_api_key(self, config: SystemConfig) -> str:
        """Helper to decrypt API key"""
        if not config.api_key:
            return ""
        return config_encryption.decrypt(config.api_key)

config_manager = ConfigManager()
