import os
from sqlalchemy.orm import Session
from core.database import SessionLocal
from core.models import SystemConfig
from core.config import settings
from core.config_manager import config_manager

def check_active_config():
    print("=== AI 配置检查报告 ===\n")
    
    # 1. 检查数据库中的活跃配置
    db: Session = SessionLocal()
    try:
        active_config = config_manager.get_active_config(db)
        
        if active_config:
            print("[✅ 数据库配置] 发现活跃配置：")
            print(f"  - ID: {active_config.id}")
            print(f"  - 提供商 (Provider): {active_config.provider}")
            print(f"  - 模型名称 (Model Name): {active_config.model_name}")
            print(f"  - Base URL: {active_config.base_url or '默认 (None)'}")
            print(f"  - API Key (加密存储): {active_config.api_key[:10]}..." if active_config.api_key else "  - API Key: 未设置")
            
            # 尝试解密 API Key 验证
            try:
                decrypted_key = config_manager.get_decrypted_api_key(active_config)
                print(f"  - API Key 解密验证: 成功 (长度: {len(decrypted_key)})")
            except Exception as e:
                print(f"  - API Key 解密验证: 失败 ({e})")
                
            print(f"  - 更新时间: {active_config.updated_at}")
            print("\n结论：系统正在优先使用上述数据库配置。")
        else:
            print("[❌ 数据库配置] 未发现活跃配置 (is_active=1 的记录不存在)。")
            print("\n结论：系统将回退使用环境变量或默认设置。")
            
    except Exception as e:
        print(f"查询数据库失败: {e}")
    finally:
        db.close()

    print("\n------------------------------------------------\n")

    # 2. 检查环境变量配置 (作为后备)
    print("[ℹ️ 环境变量/默认配置] (仅在无数据库配置时生效)：")
    print(f"  - DASHSCOPE_API_KEY: {'已设置' if settings.DASHSCOPE_API_KEY else '未设置'}")
    if settings.DASHSCOPE_API_KEY:
         print(f"    (前缀: {settings.DASHSCOPE_API_KEY[:8]}...)")
    print(f"  - MODEL_NAME: {settings.MODEL_NAME}")
    print(f"  - VL_MODEL_NAME: {settings.VL_MODEL_NAME}")
    print(f"  - TURBO_MODEL_NAME: {settings.TURBO_MODEL_NAME}")

if __name__ == "__main__":
    check_active_config()
