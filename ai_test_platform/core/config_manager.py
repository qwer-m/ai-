"""
配置管理模块 (Config Manager Module)

该模块负责管理系统的 LLM 配置 (SystemConfig)，包括创建、激活、查询和解密。
主要功能：
1. 创建配置 (create_config): 支持加密存储 API Key。
2. 激活配置 (activate_config): 确保用户同一时间只有一个激活配置。
3. 获取激活配置 (get_active_config): 优先获取用户级配置，支持回退到全局默认配置。
4. 密钥解密 (get_decrypted_api_key): 提供安全的密钥解密访问。

线程安全：
- 使用 `threading.RLock` 确保激活操作的原子性。
"""

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
        """
        创建新的系统配置 (Create Config)
        
        Args:
            db: 数据库会话。
            provider: 模型提供商 (dashscope, openai 等)。
            model_name: 模型名称。
            api_key: API 密钥 (将被加密存储)。
            base_url: 自定义 Base URL。
            activate: 是否立即激活。
            vl_model_name: 视觉模型名称。
            turbo_model_name: 快速模型名称。
            user_id: 关联的用户 ID。
            
        Returns:
            SystemConfig: 创建的配置对象。
        """
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
        active_query = db.query(SystemConfig).filter(SystemConfig.is_active == 1)

        if user_id is None:
            config = (
                active_query.filter(SystemConfig.user_id.is_(None))
                .order_by(SystemConfig.updated_at.desc(), SystemConfig.id.desc())
                .first()
            )
            if config:
                return config
            return active_query.order_by(SystemConfig.updated_at.desc(), SystemConfig.id.desc()).first()

        config = (
            active_query.filter(SystemConfig.user_id == user_id)
            .order_by(SystemConfig.updated_at.desc(), SystemConfig.id.desc())
            .first()
        )
        if config:
            return config

        return (
            active_query.filter(SystemConfig.user_id.is_(None))
            .order_by(SystemConfig.updated_at.desc(), SystemConfig.id.desc())
            .first()
        )

    def activate_config(self, db: Session, config_id: int, user_id: int = None):
        """
        激活指定配置 (Activate Config)
        
        原子操作：
        1. 验证配置是否存在且归属于当前用户。
        2. 将该用户所有其他配置设为非激活 (is_active=0)。
        3. 将目标配置设为激活 (is_active=1)。
        
        Args:
            db: 数据库会话。
            config_id: 目标配置 ID。
            user_id: 用户 ID。
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
